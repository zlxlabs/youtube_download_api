# 元数据缓存与下载器优化方案

> **项目规模**: 个人项目，每天处理 ≤30 个视频
> **设计原则**: 简洁优先，避免过度设计
> **创建时间**: 2025-01-28

---

## 📋 目录

- [需求背景](#需求背景)
- [当前问题](#当前问题)
- [架构设计](#架构设计)
- [接口设计](#接口设计)
- [缓存策略](#缓存策略)
- [优先级配置](#优先级配置)
- [实施步骤](#实施步骤)
- [成本优化效果](#成本优化效果)

---

## 需求背景

### 业务场景

项目需要处理两类操作：

1. **获取基础信息（元数据）**
   - 场景：人工上传时获取视频标题、作者等
   - 需求：快速、免费
   - 频率：高（几乎每个视频都需要）

2. **下载字幕和音频**
   - 场景：Worker 处理下载任务
   - 需求：可靠、支持降级
   - 频率：中等

### 工具特性对比

| 工具 | 获取元数据 | 下载资源 | 成本 | 稳定性 | 本地 IP 风险 |
|------|----------|---------|------|--------|-------------|
| **ytdlp** | 免费，可能触发风控 | 免费 | $0 | 中等 | 高（直接请求） |
| **TikHub** | 付费 API 调用 | 付费，返回直链 | $0.002/次 | 高 | 低（仅下载时） |

**关键洞察**：
- TikHub 获取信息（API）≠ 下载资源（暴露本地 IP）
- TikHub API 返回：元数据 + 下载链接（有效期 6-12h）
- ytdlp 获取信息本身可能触发风控

---

## 当前问题

### 问题 1：元数据获取策略错误

```python
# metadata_service.py 当前实现
if tikhub_available:
    # ❌ 优先使用 TikHub（付费）
    metadata = await tikhub.fetch_metadata(video_id)
else:
    # ✅ 降级到 ytdlp（免费）
    metadata = await ytdlp.fetch_metadata(video_id)
```

**问题**：每次都优先调用付费 API，即使 ytdlp 能成功。

**成本影响**（每天 30 个视频）：
```
当前：30 个 × $0.002 = $0.06/天 = $1.8/月
优化后：30 个 × 80% ytdlp 成功 = 6 个 × $0.002 = $0.012/天 = $0.36/月
节省：80%
```

### 问题 2：缺少元数据缓存

```python
# 当前流程
每次调用 → 重复 API 调用 → 重复成本

# 期望流程
第 1 次 → API 调用 → 写入数据库
第 2-N 次 → 数据库读取（免费）
```

**问题**：同一视频重复请求会重复调用 API。

### 问题 3：TikHub API 重复调用

```python
# 场景：人工上传后立即下载
时刻 1：获取元数据 → TikHub API 调用（$0.002）
时刻 2：下载音频 → TikHub API 再次调用（$0.002）
# 总成本：$0.004（本可以只调用一次）
```

### 问题 4：管理层功能不完整

- DownloaderManager 有 `download_with_fallback()`
- ❌ 缺少 `get_metadata()` 方法
- MetadataService 绕过 Manager 直接调用下载器

---

## 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────┐
│              调用层 (Services/Routes)                │
│  - ManualUploadService: 人工上传                    │
│  - Worker: 下载任务                                 │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│           管理层 (DownloaderManager)                │
│  统一入口：                                          │
│  - get_metadata()        ← 新增                    │
│  - download()                                       │
│  职责：                                              │
│  - 优先级选择                                        │
│  - 降级策略                                          │
│  - 熔断器保护                                        │
│  - 缓存协调                                          │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│              下载器层 (Downloaders)                  │
│  YtdlpDownloader:                                   │
│  - fetch_metadata()      ← 重命名                  │
│  - download_resources()  ← 重命名                  │
│  TikHubDownloader:                                  │
│  - fetch_metadata()      ← 重命名                  │
│  - download_resources()  ← 重命名                  │
│  - _api_cache (简单内存缓存)                        │
└─────────────────────────────────────────────────────┘
```

### 缓存架构（极简版）

```
元数据缓存（永久）：
┌──────────────────────────────────────┐
│  数据库 (video_resources 表)         │
│  - 主键：video_id                    │
│  - 字段：video_info (JSON)           │
│  - 生命周期：永久（除非手动删除）      │
│  - 查询性能：5ms（主键索引）          │
└──────────────────────────────────────┘

TikHub API 响应缓存（短期）：
┌──────────────────────────────────────┐
│  内存 Dict (TikHubDownloader 内部)   │
│  - 结构：{video_id: (response, time)}│
│  - TTL：3 小时                       │
│  - 用途：避免短期内重复 API 调用      │
└──────────────────────────────────────┘
```

**设计理由**（针对每天 30 个视频）：
- ✅ **无需内存缓存层**：数据库 5ms 查询足够快
- ✅ **无需 LRU 淘汰**：一年 10,800 个视频，5.4 MB，可忽略
- ✅ **无需定期清理**：TikHub 缓存自动过期检查

---

## 接口设计

### BaseDownloader（下载器基类）

```python
class BaseDownloader(ABC):
    """下载器抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """下载器名称：ytdlp / tikhub"""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """是否可用（检查 API key 等）"""
        pass

    @abstractmethod
    async def fetch_metadata(
        self,
        video_url: str,
        video_id: str,
    ) -> Optional[VideoMetadata]:
        """
        仅获取视频元数据（不下载任何文件）。

        用途：
        - 人工上传时获取视频信息
        - 快速检查视频可用性

        Returns:
            VideoMetadata 或 None（失败时）
        """
        pass

    @abstractmethod
    async def download_resources(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> DownloaderResult:
        """
        下载音频和/或字幕。

        Returns:
            DownloaderResult（包含元数据和文件路径）
        """
        pass
```

### DownloaderManager（管理层）

```python
class DownloaderManager:
    """下载器管理器（统一入口）"""

    async def get_metadata(
        self,
        video_url: str,
        video_id: str,
        priority: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[VideoMetadata]:
        """
        获取视频元数据。

        缓存策略：
        1. 如果 force_refresh=False，先查数据库
        2. 数据库未命中，按优先级调用下载器
        3. 成功后写入数据库

        Args:
            priority: 自定义优先级（如 "ytdlp,tikhub"）
                     默认使用 settings.metadata_priority
            force_refresh: 强制从 API 刷新（跳过数据库）

        Returns:
            VideoMetadata 或 None
        """
        pass

    async def download(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
        priority: Optional[str] = None,
    ) -> DownloaderResult:
        """
        下载音频和/或字幕。

        智能优先级：
        - 仅字幕：使用 settings.transcript_only_priority
        - 包含音频：使用 settings.audio_download_priority

        缓存策略：
        - 元数据：优先从数据库读取
        - TikHub 链接：检查内存缓存（3h）

        Args:
            priority: 自定义优先级，覆盖默认策略

        Returns:
            DownloaderResult
        """
        pass
```

---

## 缓存策略

### 1. 元数据缓存（数据库）

#### 存储位置
- 表：`video_resources`
- 字段：`video_info` (JSON)
- 主键：`video_id`

#### 缓存流程

```python
# 获取元数据
async def get_metadata(video_id: str, force_refresh: bool = False):
    # 1. 检查数据库（除非强制刷新）
    if not force_refresh:
        resource = await db.get_video_resource(video_id)
        if resource and resource.video_info:
            return convert_to_metadata(resource.video_info)

    # 2. 数据库未命中，调用 API
    for downloader in downloaders:  # 按优先级
        try:
            metadata = await downloader.fetch_metadata(video_url, video_id)
            if metadata:
                # 3. 写入数据库
                await save_metadata_to_db(video_id, metadata)
                return metadata
        except Exception:
            continue

    return None
```

#### 过期策略
- **默认**：永久有效（元数据极少变化）
- **可选**：提供 `force_refresh=True` 参数手动刷新

#### 性能表现
```
首次获取：ytdlp API → 1-2 秒
重复获取：数据库查询 → 5ms
性能提升：200-400 倍
```

### 2. TikHub API 响应缓存（内存）

#### 存储结构
```python
class TikHubDownloader:
    # 简单 Dict 缓存
    _api_cache: Dict[str, Tuple[dict, datetime]] = {}
    _cache_ttl_hours = 3  # 保守 TTL
```

#### 缓存流程

```python
async def fetch_metadata(video_id: str):
    # 1. 检查缓存
    if video_id in self._api_cache:
        cached_response, cached_time = self._api_cache[video_id]
        age = datetime.now() - cached_time

        if age < timedelta(hours=self._cache_ttl_hours):
            # 使用缓存
            return parse_metadata(cached_response)

    # 2. 调用 API
    api_response = await self._fetch_video_info(video_id, ...)

    # 3. 缓存响应
    self._api_cache[video_id] = (api_response, datetime.now())

    return parse_metadata(api_response)
```

#### 过期策略
- **TTL**：3 小时（保守值）
- **实际**：YouTube 链接通常 6-12 小时有效
- **清理**：可选，自动检查过期时间

#### 使用场景
```
场景：人工上传后立即下载

时刻 1：上传 → 调用 get_metadata()
  → TikHub API 调用 → 缓存响应 → $0.002

时刻 2：下载 → 调用 download()
  → 检查缓存 → 命中 → 直接使用链接下载 → $0

节省：$0.002（50%）
```

---

## 优先级配置

### 配置参数

```bash
# .env

# ========== 下载器优先级配置 ==========
# 元数据获取优先级（优先免费）
METADATA_PRIORITY=ytdlp,tikhub

# 仅字幕下载优先级（TikHub 更稳定，风控低）
TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp

# 音频下载优先级（优先免费）
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub

# ========== TikHub 缓存配置 ==========
# API 响应缓存时长（小时）
TIKHUB_CACHE_TTL_HOURS=3
```

### 优先级策略

| 操作类型 | 默认优先级 | 理由 |
|---------|-----------|------|
| 获取元数据 | ytdlp → tikhub | 优先免费，节省成本 |
| 仅字幕下载 | tikhub → ytdlp | TikHub 更稳定，字幕风控低 |
| 音频下载 | ytdlp → tikhub | 优先免费，ytdlp 降级保障 |

### 自定义优先级

```python
# 场景 1：默认优先级
metadata = await manager.get_metadata(video_url, video_id)
# 使用 METADATA_PRIORITY=ytdlp,tikhub

# 场景 2：自定义优先级
metadata = await manager.get_metadata(
    video_url,
    video_id,
    priority="tikhub,ytdlp"  # 强制 TikHub 优先
)

# 场景 3：强制刷新
metadata = await manager.get_metadata(
    video_url,
    video_id,
    force_refresh=True  # 跳过数据库缓存
)
```

---

## 实施步骤

### 阶段 1：接口重命名

**修改文件**：
- `src/downloaders/base.py`
- `src/downloaders/ytdlp_downloader.py`
- `src/downloaders/tikhub_downloader.py`

**变更内容**：
```python
# 旧接口
async def get_video_metadata(...) -> Optional[dict]
async def download(...) -> DownloaderResult

# 新接口
async def fetch_metadata(...) -> Optional[VideoMetadata]
async def download_resources(...) -> DownloaderResult
```

### 阶段 2：TikHub 添加缓存

**修改文件**：`src/downloaders/tikhub_downloader.py`

**新增代码**：
```python
class TikHubDownloader:
    def __init__(self, settings: Settings):
        # ... 原有代码

        # 新增：简单内存缓存
        self._api_cache: Dict[str, Tuple[dict, datetime]] = {}
        self._cache_ttl_hours = getattr(settings, 'tikhub_cache_ttl_hours', 3)

    async def fetch_metadata(self, video_url: str, video_id: str):
        # 1. 检查缓存
        if video_id in self._api_cache:
            cached_response, cached_time = self._api_cache[video_id]
            age = datetime.now() - cached_time
            if age < timedelta(hours=self._cache_ttl_hours):
                return self._parse_video_metadata(cached_response, video_id)

        # 2. 调用 API
        api_response = await self._fetch_video_info(video_id, ...)

        # 3. 缓存响应
        self._api_cache[video_id] = (api_response, datetime.now())

        return self._parse_video_metadata(api_response, video_id)

    async def download_resources(self, ...):
        # 复用缓存逻辑
        # ...
```

### 阶段 3：DownloaderManager 新增方法

**修改文件**：`src/downloaders/manager.py`

**新增代码**：
```python
class DownloaderManager:
    async def get_metadata(
        self,
        video_url: str,
        video_id: str,
        priority: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[VideoMetadata]:
        """获取元数据（数据库优先）"""

        # 1. 检查数据库
        if not force_refresh:
            resource = await self.db.get_video_resource(video_id)
            if resource and resource.video_info:
                logger.info(f"Metadata from database: {video_id}")
                return self._convert_db_to_metadata(video_id, resource.video_info)

        # 2. 调用下载器（按优先级）
        effective_priority = priority or self.settings.metadata_priority
        downloaders = self._get_downloaders_by_priority(effective_priority)

        for downloader in downloaders:
            try:
                metadata = await downloader.fetch_metadata(video_url, video_id)
                if metadata:
                    # 3. 写入数据库
                    await self._save_metadata_to_db(video_id, metadata)
                    return metadata
            except Exception as e:
                continue

        return None
```

### 阶段 4：更新调用方

**修改文件**：
- `src/services/manual_upload_service.py`
- `src/core/worker.py`

**变更内容**：
```python
# 旧代码
metadata = await metadata_service.fetch_youtube_metadata(video_url, video_id)

# 新代码
metadata = await downloader_manager.get_metadata(video_url, video_id)
```

### 阶段 5：配置更新

**修改文件**：
- `.env.example`
- `.env`
- `src/config.py`

**新增配置**：
```python
# src/config.py
class Settings(BaseSettings):
    # 下载器优先级
    metadata_priority: str = "ytdlp,tikhub"
    transcript_only_priority: str = "tikhub,ytdlp"
    audio_download_priority: str = "ytdlp,tikhub"

    # TikHub 缓存
    tikhub_cache_ttl_hours: int = 3
```

### 阶段 6：删除旧代码（可选）

**可删除**：
- `src/services/metadata_service.py`（功能已整合到 Manager）

**注意**：确保所有调用方已迁移到新接口。

---

## 成本优化效果

### 优化前（当前实现）

```
每天 30 个视频，假设：
- 100% 使用 TikHub 获取元数据
- 50% 短期内重复调用（人工上传 + 下载）

元数据调用：30 × 2 次 = 60 次
成本：60 × $0.002 = $0.12/天 = $3.6/月
```

### 优化后（新方案）

```
每天 30 个视频，假设：
- 80% ytdlp 成功（免费）
- 20% ytdlp 失败 → TikHub
- 数据库缓存命中 → 重复调用为 0

第 1 次获取元数据：
- ytdlp 成功：24 个（$0）
- TikHub 成功：6 个（$0.012）

第 2-N 次获取：
- 数据库缓存：30 个（$0）

TikHub API 调用短期复用：
- 原本：6 个 × 2 次 = 12 次
- 缓存后：6 个 × 1 次 = 6 次
- 节省：6 次

总成本：6 × $0.002 = $0.012/天 = $0.36/月
```

### 成本对比

| 指标 | 优化前 | 优化后 | 节省 |
|------|--------|--------|------|
| 每日成本 | $0.12 | $0.012 | 90% |
| 每月成本 | $3.6 | $0.36 | 90% |
| 年度成本 | $43.2 | $4.32 | 90% |

### 性能提升

| 场景 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 首次获取元数据 | 1-2 秒 | 1-2 秒 | - |
| 重复获取元数据 | 1-2 秒 | 5ms | 200-400x |
| 短期内重复下载 | 2 次 API | 1 次 API | 50% |

---

## 附录

### A. 数据库结构

```sql
-- video_resources 表（已存在）
CREATE TABLE video_resources (
    video_id TEXT PRIMARY KEY,              -- YouTube 视频 ID
    video_info TEXT,                        -- JSON 存储元数据
    has_native_transcript INTEGER,          -- 是否有原生字幕
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- video_info JSON 结构
{
  "title": "视频标题",
  "author": "作者名",
  "channel_id": "频道 ID",
  "duration": 180,
  "description": "描述",
  "upload_date": "20251208",
  "view_count": 2022,
  "thumbnail": "https://..."
}
```

### B. 关键类型定义

```python
@dataclass
class VideoMetadata:
    """统一的视频元数据结构"""
    video_id: str
    title: Optional[str]
    author: Optional[str]
    channel_id: Optional[str]
    duration: Optional[int]
    description: Optional[str]
    upload_date: Optional[str]
    view_count: Optional[int]
    thumbnail: Optional[str]
    source_downloader: str  # "ytdlp" / "tikhub" / "database"

@dataclass
class DownloaderResult:
    """下载结果"""
    success: bool
    downloader: str
    video_metadata: VideoMetadata
    audio_path: Optional[Path]
    transcript_path: Optional[Path]
    has_transcript: bool
    partial_success: bool = False
    audio_error: Optional[str] = None
```

### C. 配置示例

```bash
# .env.example

# ========== 下载器配置 ==========
# 下载器优先级（逗号分隔）
METADATA_PRIORITY=ytdlp,tikhub
TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub

# TikHub API
TIKHUB_API_KEY=your-api-key-here
TIKHUB_CACHE_TTL_HOURS=3

# yt-dlp 配置
COOKIE_FILE=./cookies.txt  # 可选
POT_SERVER_URL=http://pot-provider:4416

# ========== 熔断器配置 ==========
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT=1800
```

### D. 测试检查清单

- [ ] 元数据首次获取（ytdlp 成功）
- [ ] 元数据首次获取（ytdlp 失败 → TikHub 降级）
- [ ] 元数据重复获取（数据库缓存命中）
- [ ] 元数据强制刷新（force_refresh=True）
- [ ] TikHub API 响应缓存（短期内复用）
- [ ] 下载任务自动选择优先级
- [ ] 自定义优先级覆盖
- [ ] 熔断器正常工作

---

**文档版本**: v1.0
**最后更新**: 2025-01-28
**维护者**: 项目负责人
