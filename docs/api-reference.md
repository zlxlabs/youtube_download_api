# API 参考文档

本文档提供完整的 API 接口说明，包括请求/响应格式、鉴权方式和错误处理。

## 目录

- [API 概览](#api-概览)
- [鉴权方式](#鉴权方式)
- [任务管理接口](#任务管理接口)
- [文件下载接口](#文件下载接口)
- [人工上传接口](#人工上传接口)
- [视频资源接口](#视频资源接口)
- [视频元数据接口](#视频元数据接口)
- [统计接口](#统计接口)
- [设置接口](#设置接口)
- [管理接口](#管理接口)
- [错误码说明](#错误码说明)
- [Webhook 回调](#webhook-回调)

---

## API 概览

### 基础信息

| 项目 | 说明 |
|------|------|
| Base URL | `http://localhost:8000`（默认） |
| 协议 | HTTP/1.1 |
| 数据格式 | JSON |
| 字符编码 | UTF-8 |
| API 文档 | http://localhost:8000/docs（Swagger UI） |

### 接口列表

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| **任务管理** ||||
| POST | `/api/v1/tasks` | 创建下载任务（视频不可下载时返回 422） | 需要 |
| GET | `/api/v1/tasks` | 列出任务 | 需要 |
| GET | `/api/v1/tasks/{task_id}` | 查询任务详情 | 需要 |
| DELETE | `/api/v1/tasks/{task_id}` | 取消任务 | 需要 |
| **文件下载** ||||
| GET | `/api/v1/files/{file_id}` | 下载文件 | 公开 |
| **人工上传** ||||
| POST | `/api/v1/manual-upload` | 人工上传音频 | 需要 |
| GET | `/api/v1/manual-uploads` | 列出人工上传 | 需要 |
| GET | `/api/v1/manual-uploads/{video_id}` | 查看上传详情 | 需要 |
| DELETE | `/api/v1/manual-uploads/{video_id}` | 删除人工上传 | 需要 |
| **视频资源** ||||
| GET | `/api/v1/video-resources` | 列出视频资源 | 需要 |
| GET | `/api/v1/video-resources/{video_id}` | 视频资源详情 | 需要 |
| DELETE | `/api/v1/video-resources/{video_id}` | 删除视频资源 | 需要 |
| **视频元数据** ||||
| GET | `/api/v1/videos/{video_id}/info` | 查询视频元数据 | 需要 |
| **统计** ||||
| GET | `/api/v1/stats/downloads` | 下载失败归因统计 | 需要 |
| **设置** ||||
| GET | `/api/v1/settings/config` | 获取系统配置 | 公开 |
| GET | `/api/v1/settings/cookie` | 获取 Cookie 信息 | 需要 |
| PUT | `/api/v1/settings/cookie` | 更新 Cookie | 需要 |
| POST | `/api/v1/settings/cookie/validate` | 验证 Cookie 格式 | 需要 |
| **管理** ||||
| GET | `/health` | 健康检查 | 公开 |
| GET | `/admin` | 管理界面 | 公开 |

---

## 鉴权方式

### API Key 鉴权

所有需要鉴权的接口都使用 `X-API-Key` 请求头进行鉴权。

**请求头格式**：
```
X-API-Key: your-api-key
```

**示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/tasks"
```

### 配置 API Key

在 `.env` 文件中配置：

```bash
API_KEY=your-secret-api-key-here
```

**安全建议**：
- 使用强随机字符串（至少 32 位）
- 定期更换 API Key
- 生产环境通过环境变量注入，不要提交到代码仓库

---

## 任务管理接口

### 创建下载任务

创建一个新的视频下载任务。

**接口**：`POST /api/v1/tasks`

**请求参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `video_url` | string | 是 | - | YouTube 视频 URL |
| `priority` | string | 否 | `normal` | 任务优先级：`urgent`（紧急）或 `normal`（普通） |
| `include_audio` | boolean | 否 | `true` | 是否下载音频 |
| `include_transcript` | boolean | 否 | `true` | 是否获取字幕 |
| `callback_url` | string | 否 | - | Webhook 回调 URL |
| `callback_secret` | string | 否 | - | HMAC 签名密钥（8-256字符） |

**请求示例 - 普通任务**：
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "include_audio": true,
    "include_transcript": true,
    "callback_url": "https://your-server.com/webhook",
    "callback_secret": "your-hmac-secret"
  }'
```

**请求示例 - 紧急任务**：
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "priority": "urgent",
    "include_audio": true,
    "include_transcript": true
  }'
```

**响应示例 - 创建成功（新任务）**：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "video_id": "dQw4w9WgXcQ",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "priority": "normal",
  "position": 0,
  "estimated_wait": 30,
  "cache_hit": false
}
```

**响应示例 - 缓存命中（直接完成）**：
```json
{
  "task_id": null,
  "status": "completed",
  "video_id": "dQw4w9WgXcQ",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "duration": 213,
    "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
  },
  "files": {
    "audio": {
      "url": "/api/v1/files/abc123.m4a",
      "size": 3456789,
      "format": "m4a",
      "bitrate": 128
    },
    "transcript": {
      "url": "/api/v1/files/def456.srt",
      "size": 12345,
      "format": "srt",
      "language": "en"
    }
  },
  "cache_hit": true,
  "request": {
    "include_audio": true,
    "include_transcript": true
  },
  "result": {
    "has_transcript": true,
    "reused_audio": true,
    "reused_transcript": true
  },
  "message": "Resources retrieved from cache"
}
```

**响应示例 - 422 视频不可下载（前置拦截）**：

创建任务前，系统会同步做一次元数据前置检查（precheck），提前拦截直播中/预约首播/已删除/私有/地区限制等已知不可下载的视频，避免客户端异步等待到下载阶段才收到失败反馈。检查本身严格 fail-open：探测超时或所有下载器探测失败时一律放行、照常建任务，只有明确判定不可下载时才返回 422。

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=LIVE_VIDEO_ID",
    "include_audio": true,
    "include_transcript": true
  }'
```

```json
{
  "detail": {
    "error_code": "VIDEO_LIVE_STREAM",
    "message": "Video is a live broadcast (status: live), not available for download",
    "video_id": "LIVE_VIDEO_ID"
  }
}
```

拦截信息位于 `detail` 字段内（FastAPI 标准错误包装），包含 `error_code` / `message` / `video_id` 三个字段。可能返回的 `error_code`：`VIDEO_UNAVAILABLE` / `VIDEO_PRIVATE` / `VIDEO_REGION_BLOCKED` / `VIDEO_LIVE_STREAM`（直播中或预约首播）。

**前置检查配置**：默认启用，可通过 `PRECHECK_ENABLED` / `PRECHECK_TIMEOUT` 调整，详见 [配置总览 - 任务前置检查配置](./configuration/overview.md#任务前置检查配置)。

### 任务优先级说明

系统采用智能优先级队列机制：

| 队列优先级 | 任务类型 | 说明 | 适用场景 |
|-----------|----------|------|----------|
| **0（最高）** | `urgent`（任何类型） | 紧急任务，全局最高优先级 | 用户实时等待、VIP 用户 |
| **1** | `normal` + 仅字幕 | 普通字幕任务（轻量级） | 常规字幕请求 |
| **2** | `normal` + 音频/混合 | 普通音频/混合任务（重量级） | 常规音频下载 |
| **3（最低）** | _(系统内部)_ | 重试任务 | 自动重试的失败任务 |

**优先级特性**：
- `urgent` 最优先：无论音频还是字幕，urgent 任务都在队列最前
- 字幕优先策略：normal 字幕任务优先于 normal 音频任务（风控考量）
- 分级间隔：字幕任务间隔 20-40s，音频任务间隔 60-600s

### 下载模式说明

| include_audio | include_transcript | 行为 |
|---------------|-------------------|------|
| `true` | `true` | 下载音频 + 获取字幕（默认） |
| `true` | `false` | 仅下载音频 |
| `false` | `true` | 仅获取字幕，若无字幕则自动下载音频 |
| `false` | `false` | 无效请求，返回错误 |

### 列出任务

获取任务列表，支持分页和筛选。

**接口**：`GET /api/v1/tasks`

**查询参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `status` | string | 否 | - | 按状态筛选：`pending`/`downloading`/`completed`/`failed`/`cancelled` |
| `limit` | integer | 否 | `20` | 每页数量 |
| `offset` | integer | 否 | `0` | 偏移量 |

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/tasks?status=completed&limit=10&offset=0"
```

**响应示例**：
```json
{
  "total": 100,
  "tasks": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "completed",
      "video_id": "dQw4w9WgXcQ",
      "video_info": {
        "title": "Rick Astley - Never Gonna Give You Up"
      },
      "created_at": "2025-12-12T10:00:00Z",
      "completed_at": "2025-12-12T10:01:30Z"
    }
  ]
}
```

### 查询任务详情

获取单个任务的详细信息。

**接口**：`GET /api/v1/tasks/{task_id}`

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000"
```

**响应示例 - 已完成（音频+字幕）**：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "video_id": "dQw4w9WgXcQ",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "priority": "normal",
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
  "files": {
    "audio": {
      "url": "/api/v1/files/abc123.m4a",
      "size": 3456789,
      "format": "m4a",
      "bitrate": 128,
      "language": null
    },
    "transcript": {
      "url": "/api/v1/files/def456.srt",
      "size": 12345,
      "format": "srt",
      "bitrate": null,
      "language": "en"
    }
  },
  "error": null,
  "cache_hit": false,
  "request": {
    "include_audio": true,
    "include_transcript": true
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": false
  },
  "position": null,
  "estimated_wait": null,
  "progress": null,
  "created_at": "2025-12-12T10:00:00Z",
  "started_at": "2025-12-12T10:00:05Z",
  "completed_at": "2025-12-12T10:01:30Z",
  "expires_at": "2025-02-10T10:01:30Z",
  "message": null
}
```

**响应示例 - 部分成功**：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": true,
    "failure_details": {
      "audio": {
        "success": false,
        "error": {
          "code": "RATE_LIMITED",
          "message": "Rate limited by YouTube",
          "retry_count": 1
        }
      },
      "transcript": {
        "success": true
      }
    }
  },
  "files": {
    "audio": null,
    "transcript": {
      "url": "/api/v1/files/def456.srt",
      "size": 12345,
      "format": "srt",
      "language": "en"
    }
  }
}
```

### 取消任务

取消一个待处理或正在下载的任务。

**接口**：`DELETE /api/v1/tasks/{task_id}`

**请求示例**：
```bash
curl -X DELETE -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000"
```

**响应示例**：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "cancelled",
  "message": "Task cancelled successfully"
}
```

### 任务状态说明

| 状态 | 说明 |
|------|------|
| `pending` | 等待下载 |
| `downloading` | 下载中 |
| `completed` | 已完成（成功或部分成功） |
| `failed` | 失败（已重试） |
| `cancelled` | 已取消 |

### 资源复用机制

系统采用文件级缓存策略，同一视频的资源可跨任务复用：

```
第一次请求 video_id=ABC (audio+transcript)
  → 下载音频，获取字幕
  → 存储: video_resources[ABC] → files[audio], files[transcript]

第二次请求 video_id=ABC (audio only)
  → 检测到音频已存在
  → 立即返回缓存，无需下载

第三次请求 video_id=ABC (transcript only)
  → 检测到字幕已存在
  → 立即返回缓存，无需下载
```

响应中的 `result` 字段表明资源来源：

| 字段 | 说明 |
|------|------|
| `reused_audio` | 音频是否来自缓存 |
| `reused_transcript` | 字幕是否来自缓存 |
| `partial_success` | 是否为部分成功 |
| `failure_details` | 详细的成功/失败信息（部分成功时） |

**同视频活跃任务的复用规则**：若同一 `video_id` 已存在活跃任务（`pending`/`downloading`），系统只有在该活跃任务的 `include_audio`/`include_transcript` 已经覆盖本次新请求所需资源时才会直接复用返回该任务；若覆盖不足（例如活跃任务只请求了音频，新请求还需要字幕），则不复用，照常创建一个新任务（重叠部分仍会命中文件级缓存，开销很小）。

---

## 文件下载接口

### 下载文件

下载音频或字幕文件。

**接口**：`GET /api/v1/files/{file_id}`

**说明**：
- 无需鉴权
- 支持 Range 请求（断点续传）
- 文件保留 60 天后自动删除

**请求示例**：
```bash
# 下载音频
curl -o audio.m4a http://localhost:8000/api/v1/files/abc123.m4a

# 下载字幕
curl -o transcript.srt http://localhost:8000/api/v1/files/def456.srt

# 断点续传
curl -r 0-1048576 -o audio_partial.m4a http://localhost:8000/api/v1/files/abc123.m4a
```

**响应**：
- Content-Type: audio/mp4（m4a）、audio/webm 或 application/json（字幕）
- Content-Length: 文件大小
- Content-Disposition: attachment; filename="..."

---

## 人工上传接口

详细文档请参考：[人工上传指南](./guides/manual-upload.md)

### 上传文件

上传音频或视频文件，自动转换为 m4a 格式。

**接口**：`POST /api/v1/manual-upload`

**请求示例**：
```bash
curl -X POST http://localhost:8000/api/v1/manual-upload \
  -H "X-API-Key: your-api-key" \
  -F "video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  -F "file=@/path/to/video.mp4" \
  -F "title=Custom Title" \
  -F "author=Channel Name"
```

**响应示例**：
```json
{
  "video_id": "dQw4w9WgXcQ",
  "file_id": "abc123-4567-89ef-0123-456789abcdef",
  "title": "Custom Title",
  "author": "Channel Name",
  "size": 3456789,
  "format": "m4a",
  "original_format": "mp4",
  "created_at": "2026-01-28T12:00:00Z"
}
```

### 列出上传

获取人工上传列表。

**接口**：`GET /api/v1/manual-uploads`

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads?limit=20&offset=0"
```

### 查看上传详情

获取单个上传的详细信息。

**接口**：`GET /api/v1/manual-uploads/{video_id}`

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads/dQw4w9WgXcQ"
```

### 删除上传

删除一个人工上传。

**接口**：`DELETE /api/v1/manual-uploads/{video_id}`

**请求示例**：
```bash
curl -X DELETE -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads/dQw4w9WgXcQ"
```

---

## 视频资源接口

### 列出视频资源

获取视频资源列表，支持搜索。

**接口**：`GET /api/v1/video-resources`

**查询参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `search` | string | 否 | - | 搜索关键词（标题/作者） |
| `limit` | integer | 否 | `20` | 每页数量 |
| `offset` | integer | 否 | `0` | 偏移量 |

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/video-resources?search=keyword&limit=20&offset=0"
```

**响应示例**：
```json
{
  "total": 50,
  "resources": [
    {
      "video_id": "dQw4w9WgXcQ",
      "title": "Rick Astley - Never Gonna Give You Up",
      "author": "Rick Astley",
      "duration": 213,
      "audio_file_id": "abc123",
      "transcript_file_id": "def456",
      "created_at": "2025-12-12T10:00:00Z"
    }
  ]
}
```

### 获取视频资源详情

**接口**：`GET /api/v1/video-resources/{video_id}`

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/video-resources/dQw4w9WgXcQ"
```

**响应示例**：
```json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Rick Astley - Never Gonna Give You Up",
  "author": "Rick Astley",
  "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
  "duration": 213,
  "description": "Official music video...",
  "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
  "audio_file_id": "abc123",
  "audio_size": 3456789,
  "audio_format": "m4a",
  "transcript_file_id": "def456",
  "transcript_size": 12345,
  "transcript_format": "srt",
  "created_at": "2025-12-12T10:00:00Z"
}
```

### 删除视频资源

删除一个视频资源及其关联文件。

**接口**：`DELETE /api/v1/video-resources/{video_id}`

**请求示例**：
```bash
curl -X DELETE -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/video-resources/dQw4w9WgXcQ"
```

---

## 视频元数据接口

详细文档请参考：[视频元数据查询](./guides/video-metadata.md)

### 查询视频元数据

快速获取 YouTube 视频元数据，无需下载文件。

**接口**：`GET /api/v1/videos/{video_id}/info`

**特性**：
- 响应时间 < 1 秒
- 元数据永久缓存
- 智能降级（YouTube Data API → ytdlp → TikHub）

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/videos/dQw4w9WgXcQ/info"
```

**响应示例**：
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
  "cached": false,
  "metadata_source": "youtube_data_api",
  "fetched_at": "2026-01-28T12:00:00Z"
}
```

**响应字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `video_id` | string | YouTube 视频 ID |
| `video_info` | object | 视频元数据对象 |
| `cached` | boolean | `true` = 从数据库缓存读取，`false` = 实时获取 |
| `metadata_source` | string | 元数据来源：`cached` / `youtube_data_api` / `ytdlp` / `tikhub` |
| `fetched_at` | datetime | 元数据获取/更新时间 |

---

## 统计接口

### 下载失败归因统计

聚合最近 N 天的任务数据，回答"下载器成功率是多少""失败主要卡在哪个 error_code"这类运营问题，避免依赖翻查日志逐条统计。全部通过 SQL `GROUP BY` 聚合查询完成，不在应用层遍历全表。

**接口**：`GET /api/v1/stats/downloads`

**查询参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `days` | integer | 否 | `30` | 统计时间窗口（天数），取值范围 1-365 |

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/stats/downloads?days=7"
```

**响应示例**：
```json
{
  "days": 7,
  "total": 520,
  "by_status": {
    "completed": 460,
    "failed": 42,
    "pending": 10,
    "downloading": 5,
    "cancelled": 3
  },
  "failures_by_error_code": {
    "VIDEO_UNAVAILABLE": 12,
    "VIDEO_PRIVATE": 3,
    "RATE_LIMITED": 15,
    "NETWORK_ERROR": 8,
    "POT_TOKEN_FAILED": 4
  },
  "failure_split": {
    "content_level": 15,
    "system_level": 27,
    "content_level_ratio": 0.357,
    "system_level_ratio": 0.643
  },
  "by_downloader": {
    "audio_downloader": {
      "cdp": 380,
      "ytdlp": 60,
      "tikhub": 15,
      "unknown": 5
    },
    "transcript_downloader": {
      "cdp": 350,
      "tikhub": 90,
      "unknown": 20
    }
  },
  "weekly_trend": [
    {"week": "2026-W27", "completed": 210, "failed": 18},
    {"week": "2026-W28", "completed": 250, "failed": 24}
  ]
}
```

**响应字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `days` | integer | 统计时间窗口（天数） |
| `total` | integer | 窗口内任务总数 |
| `by_status` | object | 任务状态 -> 计数 |
| `failures_by_error_code` | object | 失败任务 error_code -> 计数 |
| `failure_split.content_level` | integer | 内容级失败数（`error_code` 以 `VIDEO_` 开头，视频本身问题，重试/换下载器解决不了） |
| `failure_split.system_level` | integer | 系统级失败数（其余 error_code，理论上可通过技术手段改善） |
| `failure_split.content_level_ratio` | float | 内容级失败占比（0-1） |
| `failure_split.system_level_ratio` | float | 系统级失败占比（0-1） |
| `by_downloader.audio_downloader` | object | 音频下载器名称 -> 任务数 |
| `by_downloader.transcript_downloader` | object | 字幕下载器名称 -> 任务数 |
| `weekly_trend` | array | 按周的完成/失败趋势，每项含 `week` / `completed` / `failed` |

**说明**：
- `by_downloader` 中下载器为 `NULL`（未实际下载，例如命中文件级缓存复用）统一归为字符串 `"unknown"`，同时也覆盖历史遗留数据。
- `weekly_trend` 的 `week` 格式形如 `"2026-W28"`，使用 SQLite `strftime('%Y', ...) || '-W' || strftime('%W', ...)` 拼接（公历年 + 周一起始的年内周序 00-53），**不是**严格 ISO 8601 周号（`%G-%V`）。这是为兼容旧版本 SQLite（3.46 以下不支持 `%G`/`%V`）做的折衷，年末/年初边界的周标签可能与真正的 ISO 周号有 1 周误差。

**错误响应**：

| 状态码 | 说明 |
|--------|------|
| 401 / 403 | 鉴权失败 |
| 422 | `days` 超出 1-365 范围 |
| 503 | 数据库错误 |
| 500 | 其他意外错误 |

---

## 设置接口

### 获取系统配置

获取当前系统配置信息。

**接口**：`GET /api/v1/settings/config`

**请求示例**：
```bash
curl "http://localhost:8000/api/v1/settings/config"
```

**响应示例**：
```json
{
  "timezone": "Asia/Shanghai",
  "debug": false,
  "file_retention_days": 60,
  "audio_quality": 128,
  "transcript_interval_min": 20,
  "transcript_interval_max": 40,
  "audio_interval_min": 60,
  "audio_interval_max": 600
}
```

### 获取 Cookie 信息

获取当前 YouTube Cookie 状态。

**接口**：`GET /api/v1/settings/cookie`

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/settings/cookie"
```

**响应示例**：
```json
{
  "cookie_file": "./cookies.txt",
  "exists": true,
  "line_count": 15,
  "last_modified": "2026-01-28T12:00:00Z"
}
```

### 更新 Cookie

更新 YouTube Cookie 文件内容。

**接口**：`PUT /api/v1/settings/cookie`

**请求参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `content` | string | 是 | - | Cookie 文件内容（Netscape 格式） |
| `create_backup` | boolean | 否 | `true` | 是否创建备份 |

**请求示例**：
```bash
curl -X PUT http://localhost:8000/api/v1/settings/cookie \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "content": "# Netscape HTTP Cookie File\n...\n",
    "create_backup": true
  }'
```

**响应示例**：
```json
{
  "success": true,
  "message": "Cookie updated successfully",
  "backup_file": "./cookies.txt.backup.20260128120000"
}
```

### 验证 Cookie 格式

验证 Cookie 文件格式是否正确。

**接口**：`POST /api/v1/settings/cookie/validate`

**请求参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `content` | string | 是 | - | Cookie 文件内容 |

**请求示例**：
```bash
curl -X POST http://localhost:8000/api/v1/settings/cookie/validate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "content": "# Netscape HTTP Cookie File\n...\n"
  }'
```

**响应示例**：
```json
{
  "valid": true,
  "errors": [],
  "warnings": ["缺少 Netscape HTTP Cookie File header"],
  "line_count": 15
}
```

---

## 管理接口

### 健康检查

检查服务运行状态。

**接口**：`GET /health`

**请求示例**：
```bash
curl "http://localhost:8000/health"
```

**响应示例**：
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2026-01-28T12:00:00Z"
}
```

### 管理界面

访问 Web 管理界面。

**接口**：`GET /admin`

**访问方式**：浏览器访问 http://localhost:8000/admin

**功能模块**：
- 任务管理：查看所有下载任务，支持搜索和状态筛选
- 视频资源：管理所有视频资源，查看详情和删除
- 人工上传：手动上传音频/视频文件
- Cookie 管理：查看和更新 YouTube Cookie 文件
- 系统配置：查看系统配置和状态

---

## 错误码说明

| 错误码 | 说明 | 可重试 |
|--------|------|--------|
| `VIDEO_UNAVAILABLE` | 视频不存在/已删除 | 否 |
| `VIDEO_PRIVATE` | 私有视频 | 否 |
| `VIDEO_REGION_BLOCKED` | 地区限制 | 否 |
| `VIDEO_AGE_RESTRICTED` | 年龄限制 | 否 |
| `VIDEO_LIVE_STREAM` | 直播流 | 否 |
| `DOWNLOAD_FAILED` | 下载失败 | 是 |
| `RATE_LIMITED` | 被限流 | 是 |
| `NETWORK_ERROR` | 网络错误 | 是 |
| `POT_TOKEN_FAILED` | PO Token 失败 | 是 |

**错误响应示例**：
```json
{
  "error": {
    "code": "VIDEO_UNAVAILABLE",
    "message": "Video not found or removed",
    "details": {
      "video_id": "invalid_video_id"
    }
  }
}
```

---

## Webhook 回调

下载完成/失败后，系统会 POST 到指定的 `callback_url`。

### 请求格式

```http
POST {callback_url}
Content-Type: application/json
X-Signature: sha256=xxxxxxxx
X-Task-Id: 550e8400-e29b-41d4-a716-446655440000
X-Timestamp: 1702357425
```

**请求体**：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "video_id": "dQw4w9WgXcQ",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "duration": 213
  },
  "files": {
    "audio": {
      "url": "/api/v1/files/abc123.m4a",
      "size": 3456789
    },
    "transcript": {
      "url": "/api/v1/files/def456.srt",
      "size": 12345
    }
  },
  "error": null
}
```

### 签名验证

使用 HMAC-SHA256 验证回调真实性。

**Python 示例**：
```python
import hmac
import hashlib

def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)

# 使用示例
signature = request.headers.get("X-Signature")
secret = "your-hmac-secret"
body = await request.body()

if not verify_signature(body, signature, secret):
    raise HTTPException(401, "Invalid signature")
```

**Node.js 示例**：
```javascript
const crypto = require('crypto');

function verifySignature(body, signature, secret) {
  const expected = crypto
    .createHmac('sha256', secret)
    .update(body)
    .digest('hex');

  return `sha256=${expected}` === signature;
}
```

详细文档请参考：[Webhook 集成指南](./guides/webhook-integration.md)

---

## 相关文档

- [快速开始指南](./quick-start.md)
- [配置总览](./configuration/overview.md)
- [人工上传指南](./guides/manual-upload.md)
- [视频元数据查询](./guides/video-metadata.md)
- [Webhook 集成](./guides/webhook-integration.md)
