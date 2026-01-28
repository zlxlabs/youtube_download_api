# 元数据模式可行性分析

## 背景

引入 YouTube Data API v3 下载器后，系统可以快速获取视频元数据（标题、时长、作者等），无需下载音频或字幕文件。

**核心问题**：是否允许 `include_audio=false` 和 `include_transcript=false`，仅返回视频元数据？

---

## 方案对比

### 方案A：允许 `audio=false & transcript=false`

**实现方式**：在现有 `/tasks` 端点中允许两者都为 false

**优点**：
- ✅ 最小改动，复用现有架构
- ✅ 统一的 API 入口
- ✅ 复用任务队列和缓存机制

**缺点**：
- ❌ 语义混淆：`/tasks` 暗示"创建下载任务"，纯元数据不需要任务
- ❌ 性能浪费：元数据获取 < 1秒，进入任务队列反而增加延迟
- ❌ 与现有 fallback 逻辑冲突
- ❌ 需要大量边界条件处理

---

### 方案B：新增独立端点（推荐）⭐

**实现方式**：新增 `/api/v1/videos/{video_id}/info` 端点

**优点**：
- ✅ 语义清晰：明确是"查询视频信息"
- ✅ 不进任务队列，实时返回（< 1秒）
- ✅ 不影响现有下载流程和 fallback 逻辑
- ✅ 架构清晰，易于维护

**缺点**：
- ❌ 需要新增端点和路由
- ❌ 需要更新 API 文档

---

## 方案B 详细设计（推荐）

### 1. API 设计

#### 新增端点

```
GET /api/v1/videos/{video_id}/info
```

**功能**：获取视频元数据（不下载文件）

**鉴权**：需要 API Key

**参数**：
- `video_id`（路径参数）：YouTube 视频 ID

**返回**：
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
    "thumbnail": "https://i.ytimg.com/..."
  },
  "cached": true,
  "metadata_source": "youtube_data_api",
  "fetched_at": "2026-01-28T12:00:00Z"
}
```

#### 与现有端点的区别

| 特性 | `/tasks` | `/videos/{video_id}/info` |
|------|---------|--------------------------|
| 用途 | 创建下载任务 | 查询视频元数据 |
| 返回 | 任务 + 文件 | 仅元数据 |
| 异步 | 是（进队列） | 否（实时返回） |
| 耗时 | 30-120秒 | < 1秒 |
| 缓存 | 文件级缓存 | 元数据缓存 |

---

### 2. 实现方案

#### 2.1 新增路由

```python
# src/api/video_info_routes.py
from fastapi import APIRouter, HTTPException, status
from src.api.deps import ApiKeyDep
from src.api.schemas import VideoInfoDetailResponse
from src.downloaders.manager import DownloaderManager

router = APIRouter(prefix="/api/v1", tags=["Video Info"])

@router.get(
    "/videos/{video_id}/info",
    response_model=VideoInfoDetailResponse,
    summary="Get video metadata",
    description="Retrieve video metadata without downloading files. "
                "Uses cache if available, otherwise fetches from YouTube.",
)
async def get_video_info(
    video_id: str,
    _: ApiKeyDep,
    db: Database = Depends(get_db),
    downloader_manager: DownloaderManager = Depends(get_downloader_manager),
) -> VideoInfoDetailResponse:
    """
    获取视频元数据（不下载文件）。

    流程：
    1. 检查 video_resources 缓存
    2. 缓存未命中 → 调用 DownloaderManager.get_metadata()
    3. 保存元数据到 video_resources
    4. 返回结果
    """
    # 1. 检查缓存
    video_resource = await db.get_video_resource(video_id)

    if video_resource and video_resource.title:
        logger.info(f"Video metadata cache hit: {video_id}")
        return VideoInfoDetailResponse(
            video_id=video_id,
            video_info=VideoInfoResponse(
                title=video_resource.title,
                author=video_resource.author,
                channel_id=video_resource.channel_id,
                duration=video_resource.duration,
                description=video_resource.description,
                upload_date=video_resource.upload_date,
                view_count=video_resource.view_count,
                thumbnail=video_resource.thumbnail,
            ),
            cached=True,
            metadata_source="cached",
            fetched_at=video_resource.updated_at,
        )

    # 2. 缓存未命中，获取元数据
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        metadata = await downloader_manager.get_metadata(
            video_url=video_url,
            video_id=video_id,
        )

        # 3. 保存到 video_resources
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
            has_native_transcript=None,  # 元数据模式不获取字幕信息
        )

        # 4. 返回结果
        return VideoInfoDetailResponse(
            video_id=video_id,
            video_info=VideoInfoResponse(**video_info.dict()),
            cached=False,
            metadata_source=metadata.source or "unknown",
            fetched_at=datetime.now(timezone.utc),
        )

    except DownloaderError as e:
        # 视频不存在、API 错误等
        if e.error_code == ErrorCode.VIDEO_UNAVAILABLE:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Video not found: {video_id}",
            )
        elif e.error_code == ErrorCode.RATE_LIMITED:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limited, please try again later",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch metadata: {e.message}",
            )
```

#### 2.2 新增 Schema

```python
# src/api/schemas.py

class VideoInfoDetailResponse(BaseModel):
    """视频元数据详细响应"""

    video_id: str = Field(..., description="YouTube 视频 ID")
    video_info: VideoInfoResponse = Field(..., description="视频元数据")
    cached: bool = Field(..., description="是否从缓存读取")
    metadata_source: str = Field(
        ...,
        description="元数据来源：youtube_data_api / ytdlp / tikhub / cached"
    )
    fetched_at: datetime = Field(..., description="获取时间")
```

#### 2.3 注册路由

```python
# src/main.py

from src.api import video_info_routes

app.include_router(video_info_routes.router)
```

---

### 3. 优势分析

#### 3.1 性能优势

| 场景 | 方案A（进队列） | 方案B（独立端点） |
|------|----------------|------------------|
| 首次查询 | 等待队列 + 获取元数据 ≈ 10-60秒 | 获取元数据 ≈ 0.5-1秒 |
| 缓存命中 | 检查缓存 ≈ 10-50ms | 检查缓存 ≈ 10-50ms |

**性能提升**：首次查询快 10-60 倍

#### 3.2 架构优势

- **职责分离**：
  - `/tasks` → 下载任务（异步，重量级）
  - `/videos/{id}/info` → 元数据查询（同步，轻量级）

- **无侵入**：不影响现有下载流程、fallback 逻辑、任务队列

- **易扩展**：未来可以支持批量查询、字幕列表查询等

#### 3.3 用户体验优势

```python
# 方案A：需要轮询任务状态
response = create_task(url, audio=False, transcript=False)
task_id = response.task_id

# 需要轮询等待
while True:
    task = get_task(task_id)
    if task.status == "completed":
        metadata = task.video_info
        break
    time.sleep(1)

# 方案B：实时返回
metadata = get_video_info(video_id)  # 立即返回
```

---

### 4. 配额管理

#### 4.1 配额监控

YouTube Data API v3 每日配额：10,000 units
- 每次 `videos.list` 消耗：1 unit
- 可支持：10,000 次/天 = 417 次/小时 = 7 次/分钟

#### 4.2 配额保护策略

```python
# 添加请求频率限制
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.get("/videos/{video_id}/info")
@limiter.limit("10/minute")  # 每分钟最多 10 次
async def get_video_info(...):
    ...
```

#### 4.3 缓存策略

- **永久缓存**：元数据存储在 `video_resources` 表，永久有效
- **优先缓存**：先检查缓存，缓存命中率预计 > 80%
- **实际消耗**：预计每天仅消耗 100-500 units（新视频）

---

### 5. 与现有系统的整合

#### 5.1 复用现有组件

- ✅ **DownloaderManager.get_metadata()**：已实现，直接调用
- ✅ **video_resources 表**：已存在，直接读写
- ✅ **元数据缓存**：已实现（数据库级别）
- ✅ **METADATA_PRIORITY 配置**：已支持优先级配置

#### 5.2 不影响现有流程

- ✅ **下载任务**：保持原有逻辑，不受影响
- ✅ **Fallback 机制**：不触发，纯查询操作
- ✅ **任务队列**：不占用队列资源
- ✅ **IP 熔断器**：元数据 API 不受 IP 限流影响

---

## 方案A 详细分析（不推荐）

如果坚持方案A（允许 `audio=false & transcript=false`），需要处理以下问题：

### 问题1：语义混淆

```python
# 用户调用
POST /api/v1/tasks
{
  "video_url": "...",
  "include_audio": false,
  "include_transcript": false
}

# 问题：这是在"创建任务"还是"查询元数据"？
# 返回的 task_id 有什么意义？
```

### 问题2：任务队列浪费

```python
# 当前流程
请求 → 创建任务 → 进队列 → Worker 处理 → 获取元数据 → 返回
耗时：10-60 秒

# 理想流程（纯元数据）
请求 → 获取元数据 → 返回
耗时：0.5-1 秒
```

### 问题3：Fallback 逻辑冲突

```python
# worker.py line 389-408
if not need_audio and need_transcript and not result.has_transcript:
    # Audio Fallback：字幕不存在时下载音频
    audio_fallback = True
    result = await self.downloader_manager.download_with_fallback(
        include_audio=True,  # 强制下载音频
        include_transcript=False,
    )

# 问题：如果用户明确 audio=false & transcript=false
# 是否应该触发任何 fallback？
# 答案：不应该，但需要额外判断条件
```

### 问题4：Response 结构不一致

```python
# 正常下载任务
{
  "task_id": "xxx",
  "status": "completed",
  "files": {
    "audio": {...},
    "transcript": {...}
  },
  "video_info": {...}
}

# 纯元数据请求
{
  "task_id": "xxx",  # ← 这个 task_id 有意义吗？
  "status": "completed",
  "files": {
    "audio": null,  # ← 都是 null，是否应该省略？
    "transcript": null
  },
  "video_info": {...},
  "metadata_only": true  # ← 需要新增标识
}
```

### 问题5：实现复杂度

需要修改的地方：
1. ✅ 移除 `schemas.py` 参数验证
2. ✅ `worker.py` 新增 `_execute_metadata_only()` 分支
3. ✅ `task_service.py` 增加纯元数据快速路径
4. ✅ `database.py` 调整 `update_task_completed()` 逻辑
5. ✅ 所有 fallback 逻辑增加条件判断
6. ✅ 更新 Response Schema
7. ✅ 更新大量文档和测试

---

## 推荐方案总结

### ✅ 推荐：方案B（新增独立端点）

**理由**：
1. **语义清晰**：`/videos/{id}/info` 明确是查询元数据
2. **性能最优**：不进队列，实时返回（< 1秒）
3. **架构清晰**：职责分离，易于维护
4. **无侵入**：不影响现有下载流程

**实现成本**：
- 新增 1 个路由文件（~100 行）
- 新增 1 个 Schema（~10 行）
- 更新 API 文档

**预计工作量**：2-3 小时

---

### ❌ 不推荐：方案A（允许 audio=false & transcript=false）

**理由**：
1. 语义混淆，用户体验差
2. 性能浪费（元数据查询进任务队列）
3. 需要大量边界条件处理
4. 与现有 fallback 逻辑冲突
5. 实现复杂度高

**实现成本**：
- 修改 5-7 个核心文件
- 增加大量条件判断
- 更新大量测试用例

**预计工作量**：1-2 天

---

## 实施建议

### 阶段1：实现方案B（推荐）

1. **新增端点** `/api/v1/videos/{video_id}/info`
2. **复用现有组件**：
   - `DownloaderManager.get_metadata()`
   - `video_resources` 表缓存
3. **添加频率限制**：防止 API 配额耗尽
4. **更新文档**：README + Swagger

### 阶段2：优化（可选）

1. **批量查询**：支持 `/videos/batch-info`
2. **字幕列表查询**：返回可用字幕语言
3. **配额监控**：实时监控 YouTube Data API 配额使用情况
4. **内存缓存**：高频视频元数据缓存到 Redis/内存

---

## 示例代码

### 客户端使用示例

```python
# 场景1：仅查询视频信息（快速）
metadata = client.get_video_info("dQw4w9WgXcQ")
print(f"Title: {metadata.video_info.title}")
print(f"Duration: {metadata.video_info.duration}s")

# 场景2：下载音频（常规流程）
task = client.create_task(
    video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    include_audio=True,
    include_transcript=False,
)
# 轮询任务状态...

# 场景3：预检查 + 下载（组合使用）
# 先快速检查视频是否存在
try:
    metadata = client.get_video_info(video_id)
except NotFoundError:
    print("Video not found")
    return

# 确认存在后再创建下载任务
task = client.create_task(video_url, ...)
```

---

## 结论

**最终推荐**：方案B（新增独立端点 `/videos/{video_id}/info`）

**核心优势**：
- ✅ 职责分离，架构清晰
- ✅ 性能最优（< 1秒返回）
- ✅ 实现简单，无侵入
- ✅ 用户体验好

**下一步**：
1. 创建 `src/api/video_info_routes.py`
2. 新增 `VideoInfoDetailResponse` Schema
3. 注册路由到 `main.py`
4. 更新 README 和 Swagger 文档
