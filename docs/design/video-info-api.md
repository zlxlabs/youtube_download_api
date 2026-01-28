# 视频元数据 API 设计方案（方案B）

## 1. 功能概述

新增 `/api/v1/videos/{video_id}/info` 端点，用于快速获取视频元数据（标题、时长、作者等），无需下载文件。

### 核心特性

- ✅ **实时响应**：< 1 秒返回（相比下载任务的 10-60 秒）
- ✅ **数据库缓存**：元数据永久缓存到 `video_resources` 表
- ✅ **智能降级**：优先 YouTube Data API，降级到 ytdlp/tikhub
- ✅ **API Key 前置检查**：未配置时自动降级到其他下载器
- ✅ **频率限制**：防止配额耗尽

---

## 2. API 端点设计

### 2.1 基本信息

```
GET /api/v1/videos/{video_id}/info
```

**功能**：获取 YouTube 视频元数据（不下载文件）

**鉴权**：需要 `X-API-Key` Header

**参数**：
- `video_id`（路径参数，必填）：YouTube 视频 ID（11位字符）

**Query 参数**（可选）：
- `force_refresh=false`：是否强制刷新缓存（默认使用缓存）

---

### 2.2 响应示例

#### 成功响应（缓存命中）

```json
{
  "video_id": "dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "duration": 213,
    "description": "Official music video...",
    "upload_date": "20091025",
    "view_count": 1500000000,
    "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
  },
  "cached": true,
  "metadata_source": "cached",
  "fetched_at": "2026-01-27T10:00:00Z"
}
```

#### 成功响应（缓存未命中）

```json
{
  "video_id": "dQw4w9WgXcQ",
  "video_info": { ... },
  "cached": false,
  "metadata_source": "youtube_data_api",  // 或 "ytdlp" / "tikhub"
  "fetched_at": "2026-01-28T12:00:00Z"
}
```

#### 错误响应

**404 Not Found**：视频不存在
```json
{
  "detail": "Video not found: invalid_id"
}
```

**429 Too Many Requests**：速率限制
```json
{
  "detail": "Rate limit exceeded. Please try again later."
}
```

**503 Service Unavailable**：所有下载器不可用
```json
{
  "detail": "Unable to fetch video metadata. All downloaders unavailable."
}
```

---

## 3. 配置设计

### 3.1 环境变量

```bash
# .env

# YouTube Data API v3 配置
YOUTUBE_DATA_API_KEY=AIzaSy...  # 可选，未配置时自动降级到 ytdlp

# 元数据优先级配置（已存在，复用）
METADATA_PRIORITY=youtube_data_api,ytdlp,tikhub

# 速率限制（可选）
VIDEO_INFO_RATE_LIMIT=10/minute  # 每分钟最多 10 次请求
```

### 3.2 配置类更新

```python
# src/config.py

class Settings(BaseSettings):
    # 现有配置...

    # YouTube Data API v3（新增）
    youtube_data_api_key: Optional[str] = Field(
        default=None,
        description="YouTube Data API v3 Key (optional, for metadata fetching)"
    )

    # 元数据优先级（已存在，保持不变）
    metadata_priority: str = Field(
        default="youtube_data_api,ytdlp,tikhub",
        description="Metadata downloader priority order"
    )

    # 视频信息 API 速率限制（新增）
    video_info_rate_limit: str = Field(
        default="10/minute",
        description="Rate limit for video info API"
    )
```

---

## 4. 数据流程设计

### 4.1 完整流程图

```
用户请求
  ↓
检查 API Key 鉴权
  ↓
检查 video_id 格式
  ↓
检查速率限制
  ↓
┌─────────────────────────────────┐
│  检查数据库缓存                  │
│  (video_resources 表)            │
└─────────────────────────────────┘
  ↓                          ↓
  缓存命中                   缓存未命中
  ↓                          ↓
  直接返回                   调用 DownloaderManager.get_metadata()
  (cached=true)              ↓
                             ┌─────────────────────────────┐
                             │  智能下载器选择              │
                             │  1. YouTube Data API        │
                             │  2. ytdlp（降级）           │
                             │  3. tikhub（降级）          │
                             └─────────────────────────────┘
                             ↓
                             获取元数据成功
                             ↓
                             保存到 video_resources
                             ↓
                             返回结果
                             (cached=false)
```

### 4.2 缓存策略

#### 缓存读取逻辑

```python
# 1. 检查 video_resources 表
video_resource = await db.get_video_resource(video_id)

# 2. 缓存命中条件
if video_resource and video_resource.title:
    # 有完整元数据，直接返回
    return cached_response(video_resource)

# 3. 缓存未命中或不完整
# 调用 DownloaderManager.get_metadata()
```

#### 缓存写入逻辑

```python
# 获取元数据后，保存到数据库
await db.update_video_resource(
    video_id=video_id,
    video_info=video_info,
    has_native_transcript=None,  # 元数据模式不检查字幕
)
```

#### 缓存失效策略

- **永久缓存**：视频基本信息（标题、作者、时长等）不会改变
- **强制刷新**：提供 `force_refresh=true` 参数强制更新
- **自动失效**：无需自动失效（视频元数据基本不变）

---

## 5. 下载器优先级与降级

### 5.1 优先级配置

```python
METADATA_PRIORITY=youtube_data_api,ytdlp,tikhub
```

### 5.2 降级逻辑

| 场景 | 行为 | 下载器选择 |
|------|------|-----------|
| YouTube Data API Key 已配置 | 优先使用官方 API | youtube_data_api → ytdlp → tikhub |
| YouTube Data API Key 未配置 | 自动跳过，直接降级 | ytdlp → tikhub |
| 配额耗尽（quotaExceeded） | 触发熔断器，自动降级 | ytdlp → tikhub |
| 所有下载器失败 | 返回 503 错误 | - |

### 5.3 前置检查逻辑

```python
# DownloaderManager 已实现的逻辑
# src/downloaders/manager.py

async def get_metadata(self, video_url: str, video_id: str) -> VideoMetadata:
    """
    获取视频元数据（智能降级）。

    流程：
    1. 检查 METADATA_PRIORITY 配置
    2. 遍历下载器列表
    3. 检查下载器是否可用（is_available）
       - youtube_data_api: 检查 API Key 是否配置
       - ytdlp: 始终可用
       - tikhub: 检查 API Key 是否配置
    4. 尝试获取元数据
    5. 失败则降级到下一个下载器
    """
    for downloader in self._get_metadata_downloaders():
        if not downloader.is_available:
            logger.info(f"[{downloader.name}] Not available, skipping")
            continue

        try:
            metadata = await downloader.fetch_metadata(video_url, video_id)
            return metadata
        except DownloaderError as e:
            logger.warning(f"[{downloader.name}] Failed: {e.message}")
            continue

    raise AllDownloadersFailed("All metadata downloaders failed")
```

---

## 6. API Key 检查设计

### 6.1 检查时机

**在下载器初始化时检查**（推荐，已实现）

```python
# src/downloaders/youtube_data_api_downloader.py

class YoutubeDataApiDownloader(BaseDownloader):
    @property
    def is_available(self) -> bool:
        """
        检查下载器是否可用。

        返回 False 时，DownloaderManager 会自动跳过此下载器。
        """
        return bool(self.api_key)
```

**优势**：
- ✅ 无需在路由层额外检查
- ✅ 自动降级到 ytdlp/tikhub
- ✅ 用户无感知（始终能获取元数据）

### 6.2 用户通知（可选）

如果需要明确告知用户降级原因，可在响应中添加 `warnings` 字段：

```json
{
  "video_id": "dQw4w9WgXcQ",
  "video_info": { ... },
  "cached": false,
  "metadata_source": "ytdlp",  // ← 注意：不是 youtube_data_api
  "warnings": [
    "YouTube Data API key not configured, using fallback downloader"
  ],
  "fetched_at": "2026-01-28T12:00:00Z"
}
```

---

## 7. 速率限制设计

### 7.1 为什么需要速率限制

- **保护配额**：YouTube Data API 每日配额 10,000 units
- **防止滥用**：防止恶意批量查询
- **公平使用**：确保所有用户都能访问

### 7.2 实现方式

使用 `slowapi` 库（FastAPI 官方推荐）：

```python
# src/api/video_info_routes.py

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.get("/videos/{video_id}/info")
@limiter.limit("10/minute")  # 每分钟最多 10 次
async def get_video_info(...):
    ...
```

### 7.3 配额监控（可选，未来扩展）

```python
# 监控 YouTube Data API 使用情况
class QuotaMonitor:
    def __init__(self):
        self.daily_usage = 0
        self.daily_limit = 10000

    def record_usage(self, cost: int = 1):
        self.daily_usage += cost

        if self.daily_usage >= self.daily_limit * 0.8:
            logger.warning(
                f"YouTube Data API quota usage: {self.daily_usage}/{self.daily_limit}"
            )
```

---

## 8. Schema 设计

### 8.1 响应 Schema

```python
# src/api/schemas.py

class VideoInfoDetailResponse(BaseModel):
    """视频元数据详细响应"""

    video_id: str = Field(..., description="YouTube 视频 ID")

    video_info: VideoInfoResponse = Field(
        ...,
        description="视频元数据"
    )

    cached: bool = Field(
        ...,
        description="是否从缓存读取（true=数据库缓存，false=实时获取）"
    )

    metadata_source: str = Field(
        ...,
        description="元数据来源：cached / youtube_data_api / ytdlp / tikhub"
    )

    fetched_at: datetime = Field(
        ...,
        description="元数据获取/更新时间"
    )

    warnings: Optional[list[str]] = Field(
        default=None,
        description="警告信息（如：降级原因）"
    )

    model_config = {"from_attributes": True}
```

### 8.2 VideoInfoResponse（复用现有）

```python
# src/api/schemas.py（已存在，无需修改）

class VideoInfoResponse(BaseModel):
    """Video information in response."""

    title: Optional[str] = None
    author: Optional[str] = None
    channel_id: Optional[str] = None
    duration: Optional[int] = Field(None, description="Duration in seconds")
    description: Optional[str] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    thumbnail: Optional[str] = None
```

---

## 9. 路由实现设计

### 9.1 文件结构

```
src/api/
├── routes.py              # 现有任务路由
├── video_info_routes.py   # 新增：视频信息路由
├── manual_upload_routes.py
└── ...
```

### 9.2 核心逻辑伪代码

```python
# src/api/video_info_routes.py

@router.get("/videos/{video_id}/info")
@limiter.limit(settings.video_info_rate_limit)
async def get_video_info(
    video_id: str,
    force_refresh: bool = False,
    _: ApiKeyDep,
    db: Database = Depends(get_db),
    downloader_manager: DownloaderManager = Depends(get_downloader_manager),
) -> VideoInfoDetailResponse:
    """
    获取视频元数据（不下载文件）。

    流程：
    1. 验证 video_id 格式
    2. 检查数据库缓存（除非 force_refresh=true）
    3. 缓存命中 → 直接返回
    4. 缓存未命中 → 调用 DownloaderManager.get_metadata()
    5. 保存元数据到 video_resources
    6. 返回结果
    """

    # 1. 验证 video_id 格式
    if not is_valid_video_id(video_id):
        raise HTTPException(400, "Invalid video ID format")

    # 2. 检查缓存（除非强制刷新）
    if not force_refresh:
        video_resource = await db.get_video_resource(video_id)

        if video_resource and video_resource.title:
            # 缓存命中
            return VideoInfoDetailResponse(
                video_id=video_id,
                video_info=build_video_info(video_resource),
                cached=True,
                metadata_source="cached",
                fetched_at=video_resource.updated_at,
            )

    # 3. 缓存未命中，获取元数据
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

        # 调用 DownloaderManager（自动处理降级）
        metadata = await downloader_manager.get_metadata(
            video_url=video_url,
            video_id=video_id,
        )

        # 4. 保存到数据库
        video_info = VideoInfo(
            title=metadata.title,
            author=metadata.author,
            channel_id=metadata.channel_id,
            duration=metadata.duration,
            description=metadata.description,
            upload_date=metadata.upload_date,
            view_count=metadata.view_count,
            thumbnail=metadata.thumbnail,
        )

        await db.update_video_resource(
            video_id=video_id,
            video_info=video_info,
            has_native_transcript=None,  # 元数据模式不检查字幕
        )

        # 5. 返回结果
        return VideoInfoDetailResponse(
            video_id=video_id,
            video_info=VideoInfoResponse(**video_info.dict()),
            cached=False,
            metadata_source=metadata.source or "unknown",
            fetched_at=datetime.now(timezone.utc),
        )

    except AllDownloadersFailed as e:
        raise HTTPException(503, f"Unable to fetch metadata: {e.message}")
    except DownloaderError as e:
        if e.error_code == ErrorCode.VIDEO_UNAVAILABLE:
            raise HTTPException(404, f"Video not found: {video_id}")
        elif e.error_code == ErrorCode.RATE_LIMITED:
            raise HTTPException(429, "Rate limited, please try again later")
        else:
            raise HTTPException(500, f"Failed to fetch metadata: {e.message}")
```

---

## 10. 依赖注入设计

### 10.1 获取 DownloaderManager 实例

```python
# src/api/video_info_routes.py

# 全局变量（在 app 启动时设置）
_downloader_manager: Optional[DownloaderManager] = None

def set_downloader_manager(manager: DownloaderManager) -> None:
    """设置 DownloaderManager 实例（在 app 启动时调用）"""
    global _downloader_manager
    _downloader_manager = manager

def get_downloader_manager() -> DownloaderManager:
    """获取 DownloaderManager 实例"""
    if _downloader_manager is None:
        raise RuntimeError("DownloaderManager not initialized")
    return _downloader_manager

DownloaderManagerDep = Annotated[DownloaderManager, Depends(get_downloader_manager)]
```

### 10.2 在 main.py 中初始化

```python
# src/main.py

from src.api import video_info_routes
from src.downloaders.manager import DownloaderManager

# 启动时初始化
@app.on_event("startup")
async def startup_event():
    # ... 现有初始化代码 ...

    # 初始化 DownloaderManager
    downloader_manager = DownloaderManager(settings, db)
    video_info_routes.set_downloader_manager(downloader_manager)

    logger.info("DownloaderManager initialized for video info routes")

# 注册路由
app.include_router(video_info_routes.router)
```

---

## 11. 错误处理设计

### 11.1 错误类型映射

| 异常类型 | HTTP 状态码 | 说明 |
|---------|-----------|------|
| `ValueError` (invalid video_id) | 400 | 视频 ID 格式错误 |
| `VIDEO_UNAVAILABLE` | 404 | 视频不存在/已删除 |
| `VIDEO_PRIVATE` | 403 | 私有视频 |
| `RATE_LIMITED` | 429 | 速率限制 |
| `AllDownloadersFailed` | 503 | 所有下载器不可用 |
| 其他异常 | 500 | 服务器内部错误 |

### 11.2 错误响应示例

```json
// 404 Not Found
{
  "detail": "Video not found: invalid_video_id"
}

// 429 Too Many Requests
{
  "detail": "Rate limit exceeded. Please try again later.",
  "retry_after": 60
}

// 503 Service Unavailable
{
  "detail": "Unable to fetch video metadata. All downloaders unavailable.",
  "available_downloaders": []
}
```

---

## 12. 测试设计

### 12.1 单元测试

```python
# tests/api/test_video_info_routes.py

class TestVideoInfoRoutes:
    async def test_get_video_info_cached(self):
        """测试缓存命中"""
        # 准备：数据库中已有元数据
        # 请求：GET /api/v1/videos/{video_id}/info
        # 断言：cached=true, metadata_source="cached"

    async def test_get_video_info_youtube_data_api(self):
        """测试 YouTube Data API 获取元数据"""
        # 准备：配置 API Key，缓存为空
        # 请求：GET /api/v1/videos/{video_id}/info
        # 断言：cached=false, metadata_source="youtube_data_api"

    async def test_get_video_info_fallback_to_ytdlp(self):
        """测试降级到 ytdlp"""
        # 准备：不配置 YouTube Data API Key
        # 请求：GET /api/v1/videos/{video_id}/info
        # 断言：cached=false, metadata_source="ytdlp"

    async def test_get_video_info_not_found(self):
        """测试视频不存在"""
        # 请求：GET /api/v1/videos/invalid_id/info
        # 断言：HTTP 404

    async def test_get_video_info_rate_limit(self):
        """测试速率限制"""
        # 连续请求超过限制次数
        # 断言：HTTP 429

    async def test_get_video_info_force_refresh(self):
        """测试强制刷新"""
        # 准备：数据库中已有旧元数据
        # 请求：GET /api/v1/videos/{video_id}/info?force_refresh=true
        # 断言：cached=false, 数据已更新
```

### 12.2 集成测试

```python
class TestVideoInfoIntegration:
    async def test_full_flow_with_youtube_data_api(self):
        """测试完整流程：YouTube Data API → 缓存 → 读取"""
        # 1. 首次请求（YouTube Data API）
        # 2. 验证数据库已保存
        # 3. 第二次请求（缓存命中）

    async def test_downloader_fallback_chain(self):
        """测试下载器降级链"""
        # 1. YouTube Data API 失败
        # 2. 降级到 ytdlp
        # 3. 验证结果正确
```

---

## 13. 文档更新

### 13.1 README.md

新增章节：

```markdown
### 视频元数据查询

快速获取视频元数据（标题、时长、作者等），无需下载文件。

**获取视频信息**
\`\`\`bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/videos/dQw4w9WgXcQ/info"
\`\`\`

**响应**
\`\`\`json
{
  "video_id": "dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "duration": 213,
    ...
  },
  "cached": true,
  "metadata_source": "youtube_data_api",
  "fetched_at": "2026-01-28T12:00:00Z"
}
\`\`\`

**配置 YouTube Data API（可选）**
\`\`\`bash
YOUTUBE_DATA_API_KEY=AIzaSy...  # 提高获取速度，未配置时自动降级
\`\`\`
```

### 13.2 Swagger UI

自动生成 API 文档，包含：
- 端点描述
- 参数说明
- 响应示例
- 错误码说明

---

## 14. 实施检查清单

### 配置层
- [ ] `config.py`: 新增 `youtube_data_api_key` 配置项
- [ ] `config.py`: 新增 `video_info_rate_limit` 配置项
- [ ] `.env.example`: 添加配置说明

### Schema 层
- [ ] `schemas.py`: 新增 `VideoInfoDetailResponse`
- [ ] 复用现有 `VideoInfoResponse`（无需修改）

### 路由层
- [ ] `video_info_routes.py`: 新建文件
- [ ] 实现 `GET /videos/{video_id}/info` 端点
- [ ] 实现 `set_downloader_manager()` 依赖注入
- [ ] 实现速率限制（slowapi）
- [ ] 实现错误处理

### 主程序
- [ ] `main.py`: 导入 `video_info_routes`
- [ ] `main.py`: 注册路由
- [ ] `main.py`: 启动时初始化 DownloaderManager

### 数据库层
- [ ] 复用现有 `get_video_resource()`（无需修改）
- [ ] 复用现有 `update_video_resource()`（无需修改）

### 下载器层
- [ ] 复用现有 `DownloaderManager.get_metadata()`（无需修改）
- [ ] 复用现有 `YoutubeDataApiDownloader`（无需修改）
- [ ] 验证 `is_available` 检查逻辑

### 测试层
- [ ] `test_video_info_routes.py`: 单元测试
- [ ] `test_video_info_integration.py`: 集成测试

### 文档层
- [ ] `README.md`: 新增视频信息 API 章节
- [ ] `LOGIC_MATRIX.md`: 更新逻辑矩阵
- [ ] Swagger UI: 验证自动生成的文档

---

## 15. 性能预估

### 响应时间

| 场景 | 耗时 | 说明 |
|------|------|------|
| 缓存命中 | 10-50ms | 数据库查询 |
| YouTube Data API | 500ms-1s | 官方 API |
| ytdlp 降级 | 1-2s | 爬虫获取 |
| tikhub 降级 | 500ms-1s | 第三方 API |

### 配额使用

**YouTube Data API**：
- 每日配额：10,000 units
- 单次查询：1 unit
- 缓存命中率：预计 80%+
- 实际消耗：100-500 units/天（远低于配额）

**速率限制**：
- 配置：10 次/分钟
- 最大：600 次/小时
- 远低于 YouTube Data API 配额

---

## 16. 潜在问题与解决方案

### 问题1：配额耗尽

**现象**：YouTube Data API 返回 `quotaExceeded`

**解决**：
1. DownloaderManager 自动降级到 ytdlp
2. 熔断器触发，24 小时内跳过 YouTube Data API
3. 用户无感知（始终能获取元数据）

### 问题2：所有下载器失败

**现象**：网络故障、所有 API 都不可用

**解决**：
1. 返回 503 Service Unavailable
2. 提示稍后重试
3. 如果有缓存，建议使用 `force_refresh=false` 读取缓存

### 问题3：缓存数据过期

**现象**：视频标题/描述被作者修改

**解决**：
1. 默认使用缓存（视频元数据基本不变）
2. 需要更新时，使用 `force_refresh=true`
3. 未来可考虑 TTL 策略（如 30 天）

---

## 17. 与现有系统的兼容性

### 不影响现有功能
- ✅ 下载任务流程：完全独立，无影响
- ✅ Fallback 机制：不触发
- ✅ 任务队列：不占用
- ✅ IP 熔断器：YouTube Data API 不受影响

### 复用现有组件
- ✅ `DownloaderManager`：复用元数据获取逻辑
- ✅ `video_resources` 表：复用缓存机制
- ✅ `VideoInfoResponse` Schema：复用响应结构
- ✅ API Key 鉴权：复用现有 `ApiKeyDep`

---

## 18. 后续扩展方向

### 阶段1：基础功能（当前）
- 单个视频元数据查询
- 数据库缓存
- 智能降级

### 阶段2：增强功能（可选）
- 批量查询：`POST /videos/batch-info`
- 字幕列表：返回可用字幕语言
- 缓存 TTL：可选的缓存失效策略

### 阶段3：监控与优化（可选）
- 配额监控：实时监控 YouTube Data API 使用情况
- 性能监控：Prometheus + Grafana
- 缓存预热：预先加载热门视频元数据

---

## 审阅要点

请重点审阅以下设计：

1. **API Key 前置检查**：是否在 `is_available` 检查即可？还是需要额外验证？
2. **缓存策略**：永久缓存是否合理？是否需要 TTL 或手动失效？
3. **降级策略**：YouTube Data API 不可用时，降级到 ytdlp 是否符合预期？
4. **错误处理**：404/429/503 错误码是否合理？
5. **速率限制**：10次/分钟是否合适？
6. **依赖注入**：DownloaderManager 的注入方式是否合理？
7. **响应结构**：`VideoInfoDetailResponse` Schema 是否需要调整？

**是否有需要调整的地方，或者可以开始实施？**
