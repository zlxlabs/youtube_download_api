# 视频元数据 API 简化方案

## 1. 元数据存储逻辑确认 ✅

### 当前存储机制

**存储表**：`video_resources`

**字段结构**：
```sql
CREATE TABLE video_resources (
    video_id TEXT PRIMARY KEY,           -- YouTube 视频 ID
    video_info TEXT,                     -- JSON 格式的元数据（标题、作者、时长等）
    has_native_transcript INTEGER,       -- 是否有原生字幕（0/1/NULL）
    created_at TEXT NOT NULL,            -- 创建时间
    updated_at TEXT NOT NULL             -- 更新时间
);
```

**video_info JSON 示例**：
```json
{
  "title": "Rick Astley - Never Gonna Give You Up",
  "author": "Rick Astley",
  "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
  "duration": 213,
  "description": "Official music video...",
  "upload_date": "20091025",
  "view_count": 1500000000,
  "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
}
```

### 存储流程确认

```python
# 1. Worker 下载完成后调用（已实现）
await self.db.update_video_resource(
    video_id=video_id,
    video_info=video_info,
    has_native_transcript=has_native_transcript,
)

# 2. update_video_resource 实现（database.py:358）
async def update_video_resource(self, video_id, video_info, has_native_transcript):
    updates = ["updated_at = ?"]
    params = [now]

    if video_info is not None:
        updates.append("video_info = ?")
        params.append(json.dumps(video_info.to_dict()))  # ✅ 保存为 JSON

    if has_native_transcript is not None:
        updates.append("has_native_transcript = ?")
        params.append(1 if has_native_transcript else 0)

    await self.execute(
        f"UPDATE video_resources SET {', '.join(updates)} WHERE video_id = ?",
        tuple(params),
    )
```

### ✅ 确认结论

**当前逻辑**：
- ✅ 元数据 **会** 保存到数据库（`video_resources` 表）
- ✅ 使用 JSON 格式存储（`video_info` 字段）
- ✅ 永久保存（无 TTL，直到手动删除）
- ✅ 自动更新 `updated_at` 时间戳

**触发条件**：
- 下载任务完成时（worker.py:492）
- 调用 `DownloaderManager.get_metadata()` 后需手动保存

**缓存读取**：
```python
# 已实现（database.py:313）
video_resource = await db.get_video_resource(video_id)
if video_resource and video_resource.video_info:
    # 缓存命中，video_info 包含完整元数据
    return cached_metadata
```

---

## 2. 简化 API 设计

### 2.1 核心端点

```
GET /api/v1/videos/{video_id}/info
```

**功能**：获取视频元数据（不下载文件）

**鉴权**：需要 `X-API-Key`

---

### 2.2 响应结构

```json
{
  "video_id": "dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "duration": 213,
    "description": "...",
    "upload_date": "20091025",
    "view_count": 1500000000,
    "thumbnail": "https://i.ytimg.com/..."
  },
  "cached": true,
  "metadata_source": "youtube_data_api",
  "fetched_at": "2026-01-28T12:00:00Z"
}
```

---

### 2.3 配置

```bash
# .env

# YouTube Data API v3（可选）
YOUTUBE_DATA_API_KEY=AIzaSy...  # 未配置时自动降级到 ytdlp
```

---

### 2.4 核心逻辑

```python
# src/api/video_info_routes.py

@router.get("/videos/{video_id}/info")
async def get_video_info(video_id: str, ...):
    """
    获取视频元数据。

    流程：
    1. 检查数据库缓存（video_resources）
    2. 缓存命中 → 直接返回
    3. 缓存未命中 → 调用 DownloaderManager.get_metadata()
    4. 保存到数据库
    5. 返回结果
    """

    # 1. 检查缓存
    video_resource = await db.get_video_resource(video_id)
    if video_resource and video_resource.video_info:
        return VideoInfoDetailResponse(
            video_id=video_id,
            video_info=build_response(video_resource.video_info),
            cached=True,
            metadata_source="cached",
            fetched_at=video_resource.updated_at,
        )

    # 2. 缓存未命中，获取元数据
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    metadata = await downloader_manager.get_metadata(video_url, video_id)

    # 3. 保存到数据库
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
        has_native_transcript=None,
    )

    # 4. 返回结果
    return VideoInfoDetailResponse(
        video_id=video_id,
        video_info=VideoInfoResponse(**video_info.dict()),
        cached=False,
        metadata_source=metadata.source or "unknown",
        fetched_at=datetime.now(timezone.utc),
    )
```

---

## 3. API Key 检查（自动）

**前置检查方式**：利用下载器的 `is_available` 属性

```python
# youtube_data_api_downloader.py（已实现）
@property
def is_available(self) -> bool:
    return bool(self.api_key)  # ✅ API Key 未配置时返回 False

# DownloaderManager.get_metadata()（已实现）
for downloader in self._get_metadata_downloaders():
    if not downloader.is_available:
        continue  # ✅ 自动跳过未配置的下载器
```

**降级流程**：
- ✅ `YOUTUBE_DATA_API_KEY` 已配置 → 优先使用
- ✅ `YOUTUBE_DATA_API_KEY` 未配置 → 自动降级到 ytdlp
- ✅ ytdlp 失败 → 继续降级到 tikhub
- ✅ 所有失败 → 返回 503 错误

**无需在路由层额外检查**，下载器管理器自动处理。

---

## 4. 实施清单（简化）

### 必须修改（5 项）
- [ ] `config.py`: 新增 `youtube_data_api_key: Optional[str]`
- [ ] `schemas.py`: 新增 `VideoInfoDetailResponse`
- [ ] `video_info_routes.py`: 新建文件（~100 行）
- [ ] `main.py`: 注册路由 + 依赖注入
- [ ] `.env.example`: 添加配置说明

### 无需修改（复用现有）
- ✅ `DownloaderManager.get_metadata()` 已实现
- ✅ `db.get_video_resource()` 已实现
- ✅ `db.update_video_resource()` 已实现
- ✅ `YoutubeDataApiDownloader` 已实现
- ✅ `video_resources` 表已存在

---

## 5. 精简设计决策

### 缓存策略
- **永久缓存**（视频元数据基本不变）
- 无 TTL，无自动失效
- ~~不实现 `force_refresh` 参数~~（个人项目，简化）

### 速率限制
- ~~暂不实现~~（个人项目，API Key 配额足够）
- YouTube Data API 自带配额限制（10,000/天）

### 错误处理
- 404：视频不存在
- 503：所有下载器不可用
- ~~429：速率限制~~（暂不实现）

### 响应字段
- ~~移除 `warnings` 字段~~（简化）
- 保留核心字段：`video_id`, `video_info`, `cached`, `metadata_source`, `fetched_at`

---

## 6. 实施代码框架

### 6.1 Config 修改

```python
# src/config.py

class Settings(BaseSettings):
    # ... 现有配置 ...

    # YouTube Data API v3（新增）
    youtube_data_api_key: Optional[str] = Field(
        default=None,
        description="YouTube Data API v3 Key (optional)"
    )
```

### 6.2 Schema 新增

```python
# src/api/schemas.py

class VideoInfoDetailResponse(BaseModel):
    """视频元数据响应"""

    video_id: str
    video_info: VideoInfoResponse
    cached: bool
    metadata_source: str  # cached / youtube_data_api / ytdlp / tikhub
    fetched_at: datetime
```

### 6.3 路由实现

```python
# src/api/video_info_routes.py

from fastapi import APIRouter, Depends, HTTPException
from src.api.deps import ApiKeyDep, get_db, get_downloader_manager

router = APIRouter(prefix="/api/v1", tags=["Video Info"])

@router.get("/videos/{video_id}/info", response_model=VideoInfoDetailResponse)
async def get_video_info(
    video_id: str,
    _: ApiKeyDep,
    db: Database = Depends(get_db),
    downloader_manager: DownloaderManager = Depends(get_downloader_manager),
):
    """获取视频元数据（不下载文件）"""

    # 1. 验证 video_id 格式
    if not re.match(r'^[a-zA-Z0-9_-]{11}$', video_id):
        raise HTTPException(400, "Invalid video ID format")

    # 2. 检查缓存
    video_resource = await db.get_video_resource(video_id)
    if video_resource and video_resource.video_info:
        return VideoInfoDetailResponse(
            video_id=video_id,
            video_info=_build_video_info_response(video_resource.video_info),
            cached=True,
            metadata_source="cached",
            fetched_at=video_resource.updated_at,
        )

    # 3. 缓存未命中，获取元数据
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        metadata = await downloader_manager.get_metadata(video_url, video_id)

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
            has_native_transcript=None,
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
        raise HTTPException(503, f"Unable to fetch metadata: {str(e)}")
    except DownloaderError as e:
        if e.error_code == ErrorCode.VIDEO_UNAVAILABLE:
            raise HTTPException(404, f"Video not found: {video_id}")
        raise HTTPException(500, f"Failed to fetch metadata: {e.message}")


def _build_video_info_response(video_info: VideoInfo) -> VideoInfoResponse:
    """构建响应对象"""
    return VideoInfoResponse(
        title=video_info.title,
        author=video_info.author,
        channel_id=video_info.channel_id,
        duration=video_info.duration,
        description=video_info.description,
        upload_date=video_info.upload_date,
        view_count=video_info.view_count,
        thumbnail=video_info.thumbnail,
    )


# 依赖注入
_downloader_manager: Optional[DownloaderManager] = None

def set_downloader_manager(manager: DownloaderManager):
    global _downloader_manager
    _downloader_manager = manager

def get_downloader_manager() -> DownloaderManager:
    if _downloader_manager is None:
        raise RuntimeError("DownloaderManager not initialized")
    return _downloader_manager
```

### 6.4 Main.py 修改

```python
# src/main.py

from src.api import video_info_routes
from src.downloaders.manager import DownloaderManager

@app.on_event("startup")
async def startup_event():
    # ... 现有初始化代码 ...

    # 初始化 DownloaderManager（如果还没有）
    downloader_manager = DownloaderManager(settings, db)
    video_info_routes.set_downloader_manager(downloader_manager)

# 注册路由
app.include_router(video_info_routes.router)
```

---

## 7. 总结

### ✅ 元数据存储逻辑确认
- **已保存到数据库**：`video_resources` 表，JSON 格式
- **永久保存**：无 TTL，直到手动删除
- **自动更新**：`updated_at` 时间戳

### ✅ 简化设计
- 移除速率限制（个人项目）
- 移除强制刷新参数（简化）
- 移除 warnings 字段（简化）
- 保留核心功能：缓存 + 降级

### ✅ 实施清单
- 仅需修改 5 个文件（~150 行新代码）
- 复用现有组件，无侵入
- 预计工作量：1-2 小时

**是否确认开始实施？**
