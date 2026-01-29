# YouTube Audio API

Docker 部署的 YouTube 音频下载服务，提供 RESTful API 接口，支持下载 YouTube 视频的音频和字幕。

## 功能特性

- **RESTful API** - 完整的任务管理接口，X-API-Key 鉴权
- **音频下载** - M4A 格式，128kbps 高质量音频
- **字幕提取** - JSON 格式，优先中英文字幕
- **灵活下载模式** - 支持仅音频/仅字幕/完整模式
- **智能资源复用** - 文件级缓存，同视频资源跨任务共享
- **多下载器降级** - yt-dlp + TikHub API 双重保障，自动降级切换
- **熔断器保护** - 智能熔断机制，避免持续性故障影响服务
- **IP 熔断机制** - 被动探测型分级 IP 熔断，智能应对 YouTube 风控
- **风控绕过** - TLS 指纹模拟 + PO Token 机制
- **任务队列** - 异步处理，支持并发控制和错误重试
- **双模式通知** - Webhook 回调 + 轮询查询
- **企业微信** - 任务状态实时通知，支持敏感词审核
- **自动清理** - 文件 60 天自动过期清理
- **人工上传** - 手动上传音频/视频，自动封装/转码并管理
- **Cookie 管理** - 支持 YouTube Cookie 文件动态管理
- **视频资源管理** - 完整的视频资源 CRUD 操作

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python 包管理器)
- Docker & Docker Compose
- 代理服务（开发环境需要）

### 本地开发

```bash
# 1. 克隆项目
git clone <repo-url>
cd youtube-audio-api

# 2. 安装 uv（如果尚未安装）
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Linux/Mac
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. 复制配置文件
cp .env.example .env
# 编辑 .env，填入必要配置

# 4. 启动开发环境 (Windows)
.\scripts\dev.ps1
# 或者手动运行
uv sync && uv run uvicorn src.main:app --host 127.0.0.1 --port 8011

# Linux/Mac
chmod +x scripts/dev.sh
./scripts/dev.sh
```

### Docker 部署

```bash
# 1. 复制生产配置
cp .env.example .env
# 编辑 .env

# 2. 构建镜像（如果需要）
docker build -t youtube-api:latest .

# 3. 启动服务
docker-compose -f docker/docker-compose.prod.yml up -d

# 4. 查看日志
docker-compose -f docker/docker-compose.prod.yml logs -f youtube-api
```

### 开发环境 Docker

```bash
# 启动开发环境（包含热重载）
docker-compose -f docker-compose.dev.yml up -d
```

## API 文档

启动服务后访问 Swagger UI：http://localhost:8000/docs

### 接口概览

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/api/v1/tasks` | 创建下载任务 | 需要 |
| GET | `/api/v1/tasks` | 列出任务 | 需要 |
| GET | `/api/v1/tasks/{task_id}` | 查询任务详情 | 需要 |
| DELETE | `/api/v1/tasks/{task_id}` | 取消任务 | 需要 |
| GET | `/api/v1/files/{file_id}` | 下载文件 | 公开 |
| POST | `/api/v1/manual-upload` | 人工上传音频 | 需要 |
| GET | `/api/v1/manual-uploads` | 列出人工上传 | 需要 |
| GET | `/api/v1/manual-uploads/{video_id}` | 查看上传详情 | 需要 |
| DELETE | `/api/v1/manual-uploads/{video_id}` | 删除人工上传 | 需要 |
| GET | `/api/v1/video-resources` | 列出视频资源 | 需要 |
| GET | `/api/v1/video-resources/{video_id}` | 视频资源详情 | 需要 |
| DELETE | `/api/v1/video-resources/{video_id}` | 删除视频资源 | 需要 |
| GET | `/api/v1/videos/{video_id}/info` | 查询视频元数据 | 需要 |
| GET | `/api/v1/settings/config` | 获取系统配置 | 公开 |
| GET | `/api/v1/settings/cookie` | 获取 Cookie 信息 | 需要 |
| PUT | `/api/v1/settings/cookie` | 更新 Cookie | 需要 |
| POST | `/api/v1/settings/cookie/validate` | 验证 Cookie 格式 | 需要 |
| GET | `/health` | 健康检查 | 公开 |
| GET | `/admin` | 管理界面 | 公开 |

### 鉴权方式

```
Header: X-API-Key: your-api-key
```

### 人工上传配置

可通过环境变量控制人工上传开关与限制：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MANUAL_UPLOAD_ENABLED` | `true` | 是否启用人工上传 |
| `MANUAL_UPLOAD_MAX_SIZE_MB` | `500` | 单文件最大大小（MB） |
| `MANUAL_UPLOAD_ALLOWED_VIDEO_FORMATS` | `.mp4,.webm,.mkv,.avi,.mov` | 允许的视频格式 |
| `MANUAL_UPLOAD_ALLOWED_AUDIO_FORMATS` | `.m4a,.mp3,.aac,.opus,.wav,.flac,.ogg` | 允许的音频格式 |

### 创建下载任务

**请求示例 - 普通任务**
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

**请求示例 - 紧急任务（优先处理）**
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

**请求参数**

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `video_url` | string | 是 | - | YouTube 视频 URL |
| `priority` | string | 否 | normal | 任务优先级：`urgent`（紧急，立即处理）或 `normal`（普通，正常排队） |
| `include_audio` | boolean | 否 | true | 是否下载音频 |
| `include_transcript` | boolean | 否 | true | 是否获取字幕 |
| `callback_url` | string | 否 | - | Webhook 回调 URL |
| `callback_secret` | string | 否 | - | HMAC 签名密钥（8-256字符） |

**任务优先级说明**

系统采用智能优先级队列机制，根据用户指定优先级和任务类型自动计算：

| 队列优先级 | 任务类型 | 说明 | 适用场景 |
|-----------|----------|------|----------|
| **0（最高）** | `urgent`（任何类型） | 紧急任务，全局最高优先级 | 用户实时等待、VIP 用户、重要业务 |
| **1** | `normal` + 仅字幕 | 普通字幕任务（轻量级，低风控） | 常规字幕请求 |
| **2** | `normal` + 音频/混合 | 普通音频/混合任务（重量级，高风控） | 常规音频下载 |
| **3（最低）** | _(系统内部)_ | 重试任务 | 自动重试的失败任务 |

优先级特性：
- **urgent 最优先**：无论音频还是字幕，urgent 任务都在队列最前
- **字幕优先策略**：normal 字幕任务优先于 normal 音频任务（风控考量）
- **分级间隔**：字幕任务间隔 20-40s，音频任务间隔 60-600s
- **自动识别**：系统根据 `include_audio/include_transcript` 自动判断任务类型

**间隔策略说明**

系统根据刚完成的任务类型选择等待间隔：
- **字幕任务完成后**：等待 20-40 秒（短间隔）
- **音频/混合任务完成后**：等待 60-600 秒（长间隔）

这种设计基于风控考量：字幕下载为轻量级 API 调用，音频下载为大文件流式传输。

**下载模式说明**

| include_audio | include_transcript | 行为 |
|---------------|-------------------|------|
| `true` | `true` | 下载音频 + 获取字幕（默认） |
| `true` | `false` | 仅下载音频 |
| `false` | `true` | 仅获取字幕，若无字幕则自动下载音频 |
| `false` | `false` | 无效请求，返回错误 |

**资源复用机制**

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
| `partial_success` | 是否为部分成功（音频失败但字幕成功） |
| `failure_details` | 详细的成功/失败信息（部分成功时） |

**缓存命中判断**

当所有请求的资源都已存在时，响应会有以下特征：
- `task_id: null` - 没有创建新任务
- `cache_hit: true` - 明确标识为缓存命中
- `status: "completed"` - 直接返回完成状态

**部分成功说明**

当请求音频和字幕，但仅部分成功时（例如：音频失败但字幕成功），系统会：
- 将任务标记为 `completed`（非 failed）
- `result.partial_success = true`
- `result.failure_details` 包含详细的成功/失败信息
- 成功的资源可以被后续请求复用

---

## 人工上传

适用于已自行下载音频/视频，直接上传并生成可下载音频文件的场景。

### 功能说明

- 支持上传音频或视频文件，统一输出为 `m4a`
- 如果原始音频为 **AAC**（常见于 mp4），将优先 **直接封装（remux）**，速度更快
- 上传后自动写入 `video_resources`，并可在管理界面查看/删除
- 自动从 YouTube 获取视频元数据（可选手动提供）

### 管理界面

浏览器访问：`http://localhost:8000/admin`

管理界面包含以下功能模块：
- **任务管理** - 查看所有下载任务，支持搜索和状态筛选
- **视频资源** - 管理所有视频资源，查看详情和删除
- **人工上传** - 手动上传音频/视频文件
- **Cookie 管理** - 查看和更新 YouTube Cookie 文件
- **系统配置** - 查看系统配置和状态

### 支持格式

- 视频：`.mp4`, `.webm`, `.mkv`, `.avi`, `.mov`
- 音频：`.m4a`, `.mp3`, `.aac`, `.opus`, `.wav`, `.flac`, `.ogg`

### API 示例

**上传文件**
```bash
curl -X POST http://localhost:8000/api/v1/manual-upload \
  -H "X-API-Key: your-api-key" \
  -F "video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  -F "file=@/path/to/video.mp4" \
  -F "title=Custom Title" \
  -F "author=Channel Name"
```

**列出上传**
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads?limit=20&offset=0"
```

**删除上传**
```bash
curl -X DELETE -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads/dQw4w9WgXcQ"
```

**查看上传详情**
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads/dQw4w9WgXcQ"
```

**响应**
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

### 视频资源管理

视频资源管理 API 提供完整的 CRUD 操作，用于管理所有已下载的视频资源。

**列出视频资源**
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/video-resources?search=keyword&limit=20&offset=0"
```

**获取视频资源详情**
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/video-resources/dQw4w9WgXcQ"
```

**删除视频资源**
```bash
curl -X DELETE -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/video-resources/dQw4w9WgXcQ"
```

---

## 视频元数据查询

快速获取 YouTube 视频元数据（标题、作者、时长等），无需下载文件。

### 功能特性

- ✅ **快速响应**：< 1 秒返回（相比下载任务的 30-120 秒）
- ✅ **数据库缓存**：元数据永久缓存，重复查询无需 API 调用
- ✅ **智能降级**：优先使用 YouTube Data API，自动降级到 ytdlp/tikhub
- ✅ **无需下载**：仅获取元数据，不占用存储空间

### 配置 YouTube Data API（可选）

配置官方 API 可获得更快的响应速度和更高的稳定性：

```bash
# .env
YOUTUBE_DATA_API_KEY=AIzaSy...  # 从 Google Cloud Console 获取

# 未配置时自动降级到 ytdlp（免费但可能较慢）
```

**获取 API Key**：
1. 访问 [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. 创建项目并启用 YouTube Data API v3
3. 创建 API Key
4. 配额：10,000 units/天（videos.list = 1 unit）

### 查询视频元数据

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

### 使用场景

**场景1：批量视频信息采集**
```bash
# 快速获取多个视频的元数据
for video_id in dQw4w9WgXcQ abc123xyz; do
  curl -H "X-API-Key: your-api-key" \
    "http://localhost:8000/api/v1/videos/$video_id/info" | jq '.video_info.title'
done
```

**场景2：预检查视频可用性**
```bash
# 下载前先检查视频是否存在
VIDEO_ID="dQw4w9WgXcQ"
if curl -s -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/videos/$VIDEO_ID/info" | jq -e '.video_info.title'; then
  echo "Video exists, proceed to download"
else
  echo "Video not found"
fi
```

**场景3：获取视频时长**
```bash
# 获取视频时长（秒）
curl -s -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/videos/dQw4w9WgXcQ/info" | jq '.video_info.duration'
```

### 与下载任务的区别

| 特性 | 元数据查询 `/videos/{id}/info` | 下载任务 `/tasks` |
|------|-------------------------------|------------------|
| 用途 | 查询视频信息 | 下载音频/字幕 |
| 响应时间 | < 1 秒 | 30-120 秒 |
| 返回内容 | 仅元数据 | 元数据 + 文件下载链接 |
| 存储占用 | 无 | 有（音频/字幕文件） |
| 适用场景 | 信息查询、批量采集 | 实际下载文件 |

---

## Cookie 管理

系统支持 YouTube Cookie 文件的动态管理，提高下载成功率。

### Cookie 获取方式

推荐使用浏览器扩展导出 Cookies：

1. **使用浏览器扩展**
   - 推荐扩展：[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldkbehn)
   - 访问 YouTube 并登录
   - 导出 `youtube.com` 的 cookies 到文件

2. **使用无痕模式（推荐）**
   - 打开新的无痕窗口并登录 YouTube
   - 在同一标签页访问 `https://www.youtube.com/robots.txt`
   - 导出 cookies，然后关闭无痕窗口
   - 这样可以避免 cookies 被浏览器轮换

**注意事项**：
- 使用账户的 cookies 可能导致账户被临时或永久封禁
- 请谨慎使用下载频率和数量
- 仅在必要时使用，或使用临时账户
- 更多详情请参考：[yt-dlp Wiki - Cookies](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)

### API 示例

**获取 Cookie 信息**
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/settings/cookie"
```

**更新 Cookie**
```bash
curl -X PUT http://localhost:8000/api/v1/settings/cookie \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "content": "# Netscape HTTP Cookie File\n...\n",
    "create_backup": true
  }'
```

**验证 Cookie 格式**
```bash
curl -X POST http://localhost:8000/api/v1/settings/cookie/validate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "content": "# Netscape HTTP Cookie File\n...\n"
  }'
```

**Cookie 验证响应**
```json
{
  "valid": true,
  "errors": [],
  "warnings": ["缺少 Netscape HTTP Cookie File header"],
  "line_count": 15
}
```

---

## IP 熔断机制

系统采用被动探测型 IP 熔断机制，智能应对 YouTube 风控，提高下载成功率。

### 工作原理

```
YouTube 风控检测
    ↓
检测到 403 错误
    ↓
┌─────────────────────────────────┐
│   IP 熔断器状态机            │
│                             │
│  NORMAL (正常)                │
│    ↓ 音频任务 403             │
│  AUDIO_BANNED (音频熔断)      │
│    ↓ 字幕任务也 403           │
│  FULLY_BANNED (全局熔断)      │
│                             │
│  恢复机制：                   │
│  - 等待 MIN_WAIT_BEFORE_RETRY │
│  - 被动探测（利用实际任务）    │
│  - 探测成功 → 降级/恢复       │
└─────────────────────────────────┘
```

### 熔断级别

| 级别 | 说明 | 允许任务 | 探测策略 |
|------|------|----------|----------|
| `NORMAL` | 正常状态 | 所有任务 | - |
| `AUDIO_BANNED` | 音频熔断 | 仅字幕任务 | 利用字幕任务探测 |
| `FULLY_BANNED` | 全局熔断 | 无任务 | 等待时间到期后探测 |

### 被动探测机制

IP 熔断器采用**被动探测**策略：
- **不主动发起测试请求**，避免额外的风控风险
- **利用实际任务**进行探测恢复
- 智能决策：
  - `AUDIO_BANNED` 时：允许字幕任务执行，作为探测
  - `FULLY_BANNED` 时：等待时间到期后，允许下一个任务作为探测
  - 探测成功：降级或恢复到正常状态
  - 探测失败：延长熔断时间，避免频繁尝试

### 配置参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MIN_WAIT_BEFORE_RETRY` | 3600 | 最小等待时间（秒），触发熔断后必须等待这么久才允许重试 |
| `MAX_RETRY_INTERVAL` | 1800 | 重试间隔（秒），失败后至少等待这么久才允许下次尝试 |

### 实际效果

```
场景：YouTube 检测到自动化下载

时刻 10:00 - 音频任务返回 403
           → 触发 AUDIO_BANNED

时刻 10:00-11:00 - 仅允许字幕任务
                  → 字幕任务成功执行
                  → 说明 IP 没有完全被封

时刻 11:00 - 音频任务再次 403
           → 升级到 FULLY_BANNED

时刻 11:00-12:00 - 等待 60 分钟
                  → 所有任务暂停

时刻 12:00 - 允许字幕任务探测
           → 字幕成功
           → 降级到 AUDIO_BANNED

时刻 12:30 - 字幕任务失败
           → 延长熔断时间
```

---

## 系统配置

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `API_KEY` | 是 | - | API 鉴权密钥 |
| `WECOM_WEBHOOK_URL` | 否 | - | 企业微信 Webhook URL |
| `WECOM_MODERATION_ENABLED` | 否 | false | 启用敏感词审核 |
| `WECOM_MODERATION_URLS` | 否 | - | 敏感词库 URL 列表（逗号分隔） |
| `WECOM_MODERATION_STRATEGY` | 否 | pinyin_reverse | 审核策略：block/replace/pinyin_reverse |
| `HOST` | 否 | 0.0.0.0 | 服务监听地址 |
| `PORT` | 否 | 8000 | 服务监听端口 |
| `DEBUG` | 否 | false | 调试模式 |
| `BASE_URL` | 否 | http://localhost:8000 | 文件下载链接基础URL（通知中使用） |
| `POT_SERVER_URL` | 否 | http://pot-provider:4416 | PO Token 服务地址 |
| `HTTP_PROXY` | 否 | - | HTTP 代理（开发环境） |
| `HTTPS_PROXY` | 否 | - | HTTPS 代理（开发环境） |
| `DOWNLOAD_CONCURRENCY` | 否 | 1 | 下载并发数（预留，暂未实现） |
| `TRANSCRIPT_INTERVAL_MIN` | 否 | 20 | 字幕任务最小间隔（秒） |
| `TRANSCRIPT_INTERVAL_MAX` | 否 | 40 | 字幕任务最大间隔（秒） |
| `AUDIO_INTERVAL_MIN` | 否 | 60 | 音频/混合任务最小间隔（秒） |
| `AUDIO_INTERVAL_MAX` | 否 | 600 | 音频/混合任务最大间隔（秒） |
| `TASK_INTERVAL_MIN` | 否 | 60 | 任务最小间隔（秒）**[已弃用]** |
| `TASK_INTERVAL_MAX` | 否 | 600 | 任务最大间隔（秒）**[已弃用]** |
| `AUDIO_QUALITY` | 否 | 128 | 音频比特率 (kbps) |
| `DATA_DIR` | 否 | ./data | 数据存储目录 |
| `FILE_RETENTION_DAYS` | 否 | 60 | 文件保留天数 |
| `TZ` | 否 | Asia/Shanghai | 时区配置 |
| `TIKHUB_API_KEY` | 否 | - | TikHub API 密钥（用于下载降级） |
| **CDP 下载器配置** ||||
| `CDP_ENABLED` | 否 | false | 启用 CDP 下载器 |
| `CDP_URLS` | 否 | http://127.0.0.1:9222 | CDP 端点列表（逗号分隔，支持多实例） |
| `CDP_TIMEOUT` | 否 | 30 | CDP 连接超时（秒） |
| `CDP_FAILOVER_STRATEGY` | 否 | sequential | 故障转移策略（sequential/random） |
| `CDP_USE_CURL_CFFI` | 否 | true | 使用 curl_cffi TLS 指纹模拟 |
| `CDP_ENABLE_POT_TOKEN` | 否 | false | 启用 poToken 支持 |
| `CDP_ENABLE_MULTIPART` | 否 | false | 启用分片多线程下载 |
| `CDP_MULTIPART_CHUNKS` | 否 | 6 | 分片数量（推荐 4-8） |
| `CDP_MULTIPART_MIN_SIZE` | 否 | 10 | 分片下载最小文件阈值（MB） |
| `CDP_HEALTH_CHECK_INTERVAL` | 否 | 300 | 健康检查间隔（秒） |
| `CDP_CONNECTION_RETRY` | 否 | 3 | 连接重试次数 |
| `CDP_CIRCUIT_FAILURE_THRESHOLD` | 否 | 3 | 熔断器失败阈值 |
| `CDP_CIRCUIT_TIMEOUT` | 否 | 1800 | 熔断器超时（秒，30分钟） |
| `CDP_CIRCUIT_HALF_OPEN_SUCCESS` | 否 | 2 | 半开状态成功阈值 |
| `CDP_NOTIFY_COOLDOWN` | 否 | 3600 | 通知冷却时间（秒，1小时） |
| **下载器配置** ||||
| `DOWNLOADER_PRIORITY` | 否 | ytdlp,tikhub | 下载器优先级顺序 |
| `AUDIO_DOWNLOAD_PRIORITY` | 否 | cdp,ytdlp,tikhub | 音频下载器优先级 |
| `CIRCUIT_BREAKER_ENABLED` | 否 | true | 启用熔断器保护 |
| `CIRCUIT_BREAKER_THRESHOLD` | 否 | 5 | 熔断器失败阈值 |
| `CIRCUIT_BREAKER_TIMEOUT` | 否 | 1800 | 熔断器超时（秒） |
| `CIRCUIT_BREAKER_HALF_OPEN_CALLS` | 否 | 3 | 半开状态最大调用次数 |
| `COOKIE_FILE` | 否 | - | Cookie 文件路径 |
| `DRY_RUN` | 否 | false | 干跑模式（跳过下载） |

### 开发环境配置示例

```bash
# .env
DEBUG=true
API_KEY=dev-test-key-12345
POT_SERVER_URL=http://localhost:4416
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
WECOM_WEBHOOK_URL=

TRANSCRIPT_INTERVAL_MIN=10
TRANSCRIPT_INTERVAL_MAX=30
AUDIO_INTERVAL_MIN=30
AUDIO_INTERVAL_MAX=180
FILE_RETENTION_DAYS=1
TZ=Asia/Shanghai
BASE_URL=http://localhost:8011
```

### 获取系统配置

```bash
curl "http://localhost:8000/api/v1/settings/config"
```

**响应**
```json
{
  "timezone": "Asia/Shanghai",
  "debug": false,
  "file_retention_days": 60
}
```

---

## 多下载器配置

系统支持多种下载方式，并提供自动降级机制，以提高下载成功率和服务可靠性。

### 支持的下载器

| 下载器 | 说明 | 优点 | 缺点 | 成本 |
|--------|------|------|------|------|
| **CDP** | Chrome DevTools Protocol | 真实浏览器指纹、降低 403 风险 | 需要外部 Chrome | 免费 |
| **yt-dlp** | 本地 yt-dlp 库 | 免费、功能强大 | 可能遇到 YouTube 限流 | 免费 |
| **TikHub** | TikHub API 服务 | 稳定、不受限流影响 | 需要 API key | 0.002$/次 |

### 工作原理

```
请求下载
  │
  ├─> 1. 尝试 CDP（优先，如果启用）
  │   ├─ 真实浏览器 Cookies + TLS 指纹模拟
  │   ├─ 成功 → 返回结果
  │   ├─ HTTP 403（本地 IP 问题）→ 直接失败，不降级
  │   ├─ 连接失败 → 降级到 yt-dlp
  │   └─ 其他失败 → 降级到 yt-dlp
  │
  ├─> 2. 尝试 yt-dlp（降级）
  │   ├─ 成功 → 返回结果
  │   ├─ HTTP 403（本地 IP 问题）→ 直接失败，不降级
  │   └─ 其他失败（限流/网络）→ 继续降级
  │
  ├─> 3. 尝试 TikHub（降级）
  │   ├─ 成功 → 返回结果
  │   ├─ HTTP 403（本地 IP 问题）→ 直接失败
  │   └─ 失败 → 返回错误
  │
  └─ 所有下载器都失败 → 任务失败
```

### 智能降级策略

- **403 错误优化**：当任意下载器报 HTTP 403 错误时，说明本地网络 IP 有问题（被封禁或地区限制），此时系统会立即停止尝试其他下载器，因为所有渠道都会检测目的地 IP，继续尝试也必然失败，这样可以：
  - 节省时间：避免无意义的重试和降级
  - 降低成本：避免浪费 API 调用配额（如 TikHub）
  - 明确问题：快速识别是本地网络环境问题而非下载器问题

- **其他错误降级**：对于网络超时、连接失败等临时性错误，系统会继续尝试下一个下载器，提高成功率

### 熔断器保护

系统采用熔断器模式保护下载器，避免持续性故障影响服务：

**熔断器状态机**

```
CLOSED（正常）
  ├─ 连续失败 5 次 → OPEN（熔断）
  │
OPEN（熔断）
  ├─ 拒绝所有请求，直接跳过该下载器
  ├─ 等待 30 分钟 → HALF_OPEN（半开）
  │
HALF_OPEN（半开）
  ├─ 允许 3 次测试请求
  ├─ 连续成功 2 次 → CLOSED（恢复）
  └─ 失败 → OPEN（重新熔断）
```

**实际效果**

```
场景：yt-dlp 遇到 YouTube 限流

时刻 10:00 - yt-dlp 连续失败 5 次
           → 熔断器开启

时刻 10:00-10:30 - 所有任务直接使用 TikHub
                 → 跳过 yt-dlp，节省时间

时刻 10:30 - 熔断器恢复，重新尝试 yt-dlp
```

### 配置示例

**场景 1：仅使用 yt-dlp（免费）**

```bash
DOWNLOADER_PRIORITY=ytdlp
TIKHUB_API_KEY=  # 不配置
```

**场景 2：yt-dlp + TikHub 双重保障（推荐）**

```bash
DOWNLOADER_PRIORITY=ytdlp,tikhub
TIKHUB_API_KEY=your-api-key-here
```

**场景 3：仅使用 TikHub（最稳定）**

```bash
DOWNLOADER_PRIORITY=tikhub
TIKHUB_API_KEY=your-api-key-here
```

**场景 4：自定义熔断器参数**

```bash
# 更激进的熔断策略（适合频繁限流的环境）
CIRCUIT_BREAKER_THRESHOLD=3        # 3 次失败即熔断
CIRCUIT_BREAKER_TIMEOUT=900        # 15 分钟后恢复

# 更保守的熔断策略（适合偶尔限流的环境）
CIRCUIT_BREAKER_THRESHOLD=10       # 10 次失败才熔断
CIRCUIT_BREAKER_TIMEOUT=3600       # 1 小时后恢复
```

**场景 5：禁用熔断器（不推荐）**

```bash
CIRCUIT_BREAKER_ENABLED=false
```

---

## CDP 下载器配置

CDP (Chrome DevTools Protocol) 下载器通过真实浏览器指纹降低 YouTube 403 风控，是推荐的音频下载首选方案。

### 核心优势

- **真实浏览器指纹**：使用外部 Chrome 获取真实 Cookies 和 Headers
- **TLS 指纹模拟**：curl_cffi 模拟浏览器 TLS 握手
- **降低 403 风险**：预期降低 403 错误率 60-80%
- **多实例故障转移**：支持多个 Chrome 实例，自动切换
- **熔断器保护**：智能熔断机制，避免持续性故障
- **分片多线程下载**：可选的分片并发下载（推荐大文件）

### 快速开始

#### 1. 启动外部 Chrome（Mac/Linux）

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check &

# Linux
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check &

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir=%TEMP%\chrome-cdp ^
  --no-first-run ^
  --no-default-browser-check

# 验证 CDP 可用
curl http://localhost:9222/json/version
```

#### 2. 配置环境变量

```bash
# .env
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222  # 本地开发
# CDP_URLS=http://192.168.1.100:9222,http://192.168.1.101:9222  # 生产环境（多实例）

# 可选：启用分片下载（推荐）
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_CHUNKS=6  # 默认 6 个分片

# 可选：启用 poToken 支持（双重保障）
CDP_ENABLE_POT_TOKEN=false

# 下载器优先级（CDP 优先）
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

#### 3. 安装依赖

```bash
# 使用 uv（推荐）
uv add playwright curl-cffi

# 或使用 pip
pip install playwright curl-cffi

# 注意：不需要安装 playwright 的浏览器（使用外部 Chrome）
```

### 配置参数详解

#### 基础配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_ENABLED` | `false` | 启用 CDP 下载器（必需） |
| `CDP_URLS` | `http://127.0.0.1:9222` | CDP 端点（支持多个，逗号分隔） |
| `CDP_TIMEOUT` | `30` | 连接超时（秒） |
| `CDP_FAILOVER_STRATEGY` | `sequential` | 故障转移策略：sequential（顺序）或 random（随机） |

#### 功能开关

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_USE_CURL_CFFI` | `true` | 使用 curl_cffi TLS 指纹模拟（推荐） |
| `CDP_ENABLE_POT_TOKEN` | `false` | 启用 poToken 支持（可选，需要 POT Server） |
| `CDP_ENABLE_MULTIPART` | `false` | 启用分片多线程下载 |

#### 分片下载配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_MULTIPART_CHUNKS` | `6` | 分片数量（推荐 4-8） |
| `CDP_MULTIPART_MIN_SIZE` | `10` | 最小文件阈值（MB，小于此值不分片） |

#### 熔断器配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_CIRCUIT_FAILURE_THRESHOLD` | `3` | 连续失败阈值 |
| `CDP_CIRCUIT_TIMEOUT` | `1800` | 熔断时长（秒，30分钟） |
| `CDP_CIRCUIT_HALF_OPEN_SUCCESS` | `2` | 半开状态成功阈值 |

#### 健康检查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_HEALTH_CHECK_INTERVAL` | `300` | 健康检查间隔（秒） |
| `CDP_CONNECTION_RETRY` | `3` | 连接重试次数 |
| `CDP_NOTIFY_COOLDOWN` | `3600` | 通知冷却时间（秒） |

### 配置示例

#### 示例 1：基础配置（本地开发）

```bash
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222
CDP_USE_CURL_CFFI=true
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

#### 示例 2：生产环境（多实例 + 分片下载）

```bash
CDP_ENABLED=true
CDP_URLS=http://192.168.1.100:9222,http://192.168.1.101:9222
CDP_FAILOVER_STRATEGY=sequential
CDP_USE_CURL_CFFI=true
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_CHUNKS=6
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

#### 示例 3：完整配置（所有功能）

```bash
# 基础配置
CDP_ENABLED=true
CDP_URLS=http://192.168.1.100:9222,http://192.168.1.101:9222
CDP_TIMEOUT=30
CDP_FAILOVER_STRATEGY=sequential

# 功能开关
CDP_USE_CURL_CFFI=true
CDP_ENABLE_POT_TOKEN=false
CDP_ENABLE_MULTIPART=true

# 分片配置
CDP_MULTIPART_CHUNKS=6
CDP_MULTIPART_MIN_SIZE=10

# 熔断器
CDP_CIRCUIT_FAILURE_THRESHOLD=3
CDP_CIRCUIT_TIMEOUT=1800
CDP_CIRCUIT_HALF_OPEN_SUCCESS=2

# 健康检查
CDP_HEALTH_CHECK_INTERVAL=300
CDP_CONNECTION_RETRY=3
CDP_NOTIFY_COOLDOWN=3600

# 下载器优先级
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

### 工作流程

```
1. [连接阶段]
   ├─> 检查熔断器状态
   ├─> 获取共享 Browser 实例（多实例故障转移）
   └─> 连接失败 → 发送企微通知 → 降级到 ytdlp

2. [Context 创建阶段]
   ├─> 创建独立的 BrowserContext（每任务独立）
   └─> 设置 User-Agent、Viewport 等

3. [Cookie 导出阶段]
   ├─> 访问视频页面（刷新登录态）
   ├─> 导出 Cookies（每次导出新 cookie）
   └─> 写入临时文件

4. [Headers 提取阶段]
   ├─> 监听 Network 请求
   ├─> 捕获 googlevideo.com 音频请求
   └─> 提取真实 Headers

5. [音频 URL 提取阶段]
   ├─> yt-dlp + cookies 解析视频
   ├─> 获取 bestaudio 格式
   └─> 可选：注入 poToken

6. [下载阶段]
   ├─> 优先：分片多线程下载（如启用）
   ├─> 降级：curl_cffi 下载（TLS 指纹 + 真实 Headers）
   └─> 兜底：yt-dlp 直接下载

7. [清理阶段]
   ├─> 删除临时 cookies 文件
   ├─> 关闭 Context（自动清理资源）
   └─> Browser 保持连接（供其他任务复用）
```

### 熔断器机制

```
状态机：
CLOSED（正常）
  ├─ 连续失败 3 次 → OPEN（熔断）

OPEN（熔断）
  ├─ 拒绝所有请求，直接降级到 ytdlp
  ├─ 等待 30 分钟 → HALF_OPEN（半开）

HALF_OPEN（半开）
  ├─ 允许测试请求
  ├─ 连续成功 2 次 → CLOSED（恢复）
  └─ 失败 → OPEN（重新熔断）
```

### 企微通知

CDP 下载器支持以下通知：

1. **连接失败通知**：CDP 无法连接到 Chrome 时发送（含频率限制）
2. **熔断器打开通知**：连续失败触发熔断时发送（@ 所有人）
3. **恢复通知**：熔断器恢复正常时发送

### 故障排查

#### 连接失败

```bash
# 1. 检查 Chrome 是否运行
curl http://localhost:9222/json/version

# 2. 检查防火墙
# macOS
sudo pfctl -d  # 临时禁用防火墙

# Linux
sudo ufw status
sudo ufw allow 9222/tcp

# 3. 查看 Chrome 日志
# 访问 chrome://inspect
```

#### 下载 403

```bash
# 1. 确认 Chrome 已登录 YouTube
# 访问 http://localhost:9222 查看页面列表

# 2. 检查 cookies
# 查看日志中的 "Exported X cookies" 信息

# 3. 降低频率
AUDIO_INTERVAL_MIN=120
AUDIO_INTERVAL_MAX=300
```

#### 熔断器频繁触发

```bash
# 调整熔断器参数
CDP_CIRCUIT_FAILURE_THRESHOLD=5  # 提高阈值
CDP_CIRCUIT_TIMEOUT=3600  # 延长恢复时间
```

### 性能优化

#### 启用分片下载

```bash
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_CHUNKS=6  # 6 个分片并发
```

**效果**：
- 23.59MB 文件，6 分片并发下载成功
- 总耗时 47 秒（含前期准备）
- 下载速度 0.50 MB/s
- 未触发反爬检测（无 403）

#### 多实例负载均衡

```bash
# 配置多个 Chrome 实例
CDP_URLS=http://192.168.1.100:9222,http://192.168.1.101:9222,http://192.168.1.102:9222
CDP_FAILOVER_STRATEGY=random  # 随机选择实例
```

### 详细文档

更多信息请参考：
- [CDP 下载器设计文档](docs/cdp_downloader_design.md)
- [CDP 快速开始指南](docs/cdp_quick_start.md)

---

## 下载器架构与智能优先级

### 架构概览

系统采用三层架构，实现灵活的下载器管理和智能优先级策略：

```
┌─────────────────────────────────────────────────────────┐
│                  调用层 (Services/Routes)                │
│  - ManualUploadService: 人工上传                         │
│  - Worker: 下载任务                                      │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│           管理层 (DownloaderManager)                     │
│  统一入口：                                               │
│  - get_metadata()      ← 仅获取元数据                   │
│  - download_resources() ← 下载资源                       │
│  核心职责：                                               │
│  - 优先级选择（可配置）                                   │
│  - 降级策略（自动切换）                                   │
│  - 缓存协调（数据库 + 内存）                              │
│  - 熔断器保护                                             │
│  - 并发控制（防止重复调用）                               │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
┌───────▼─────┐ ┌───▼────┐ ┌────▼──────┐
│ YtdlpDown.  │ │TikHub  │ │ [新下载器]│
│ (免费)      │ │(付费)  │ │ (扩展)    │
└─────────────┘ └────────┘ └───────────┘
```

### 双层缓存策略

系统使用两层缓存优化性能和成本：

| 缓存类型 | 存储位置 | TTL | 用途 | 优势 |
|---------|---------|-----|------|------|
| **元数据缓存** | 数据库 (video_resources) | 永久 | 避免重复元数据获取 | 永久有效，跨任务共享 |
| **API 响应缓存** | 内存 (TTLCache) | 3小时 | 复用 TikHub 下载链接 | 短期内无需重复 API 调用 |

**缓存流程示例**：

```
首次获取元数据：
  → DownloaderManager.get_metadata()
  → 数据库未命中
  → ytdlp API 调用（免费，1-2秒）
  → 写入数据库（永久保存）

重复获取元数据：
  → DownloaderManager.get_metadata()
  → 数据库命中！
  → 直接返回（免费，5ms）
  → 性能提升：200-400倍
```

### 场景化优先级策略

系统针对不同场景使用不同的下载器优先级，平衡成本、性能和稳定性：

#### 场景 1：仅获取元数据

**配置**: `METADATA_PRIORITY=ytdlp,tikhub`
**策略**: 优先免费方案，节省成本

```
用途：人工上传时获取视频标题、作者等信息
流程：
  1. 检查数据库缓存 → 命中则直接返回（5ms）
  2. 尝试 ytdlp（免费） → 成功率 80%
  3. 降级 tikhub（$0.002） → 成功率 95%
  4. 写入数据库（永久保存）

成本：首次 $0.0004/个（平均）
      重复：$0（数据库缓存）
```

#### 场景 2：音频 + 字幕（完整模式）

**配置**: `AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub`
**策略**: 优先免费方案，大文件下载

```
特点：
  - 大文件下载（30-60秒）
  - 风控风险高
  - 优先免费，降级保障

流程：
  1. 元数据从数据库读取（复用缓存）
  2. 尝试 ytdlp 下载音频+字幕
  3. 失败 → 降级 tikhub
  4. 支持部分成功（字幕成功，音频失败）
```

#### 场景 3：仅音频

**配置**: `AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub`
**策略**: 与场景2相同

```
特点：与完整模式相同，只是跳过字幕获取
```

#### 场景 4：仅字幕 ⭐

**配置**: `TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp`
**策略**: **优先 TikHub**（与其他场景相反）

```
为什么优先 TikHub？
  ✓ 字幕获取是轻量级操作（API 调用，无大文件）
  ✓ TikHub 更稳定，风控风险低
  ✓ 不涉及大文件下载，不暴露本地 IP
  ✓ 成本可接受：$0.002/次
  ✓ 避免 ytdlp 字幕失败 → 自动下载音频（浪费）

流程：
  1. 尝试 tikhub 获取字幕（$0.002）
  2. 检查内存缓存（3小时TTL）→ 可能命中（$0）
  3. 失败 → 降级 ytdlp

特殊逻辑（ytdlp）：
  - 如果无字幕 → 自动下载音频作为 fallback
  - result.audio_fallback = true
```

### 优先级配置示例

#### 示例 1：成本优先（推荐个人项目）

```bash
# 元数据：优先免费
METADATA_PRIORITY=ytdlp,tikhub

# 仅字幕：优先稳定（成本可接受）
TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp

# 音频：优先免费
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub

# TikHub 缓存：3小时（节省重复调用）
TIKHUB_CACHE_TTL_HOURS=3
```

**成本分析**（每天30个视频）：
```
- 元数据：24个 ytdlp 成功（$0） + 6个 tikhub（$0.012）
- 字幕任务：3个 × $0.002 = $0.006
- 音频任务：大部分 ytdlp 成功（$0）
- 总成本：~$0.02/天 = $0.6/月
```

#### 示例 2：稳定性优先（推荐商业项目）

```bash
# 全部优先 TikHub（最稳定）
METADATA_PRIORITY=tikhub,ytdlp
TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp
AUDIO_DOWNLOAD_PRIORITY=tikhub,ytdlp
```

**成本分析**（每天30个视频）：
```
- 所有操作优先 TikHub
- 总成本：~$2-3/月
- 优势：最高成功率，最少用户等待
```

#### 示例 3：完全免费

```bash
# 仅使用 ytdlp
METADATA_PRIORITY=ytdlp
TRANSCRIPT_ONLY_PRIORITY=ytdlp
AUDIO_DOWNLOAD_PRIORITY=ytdlp

# 不配置 TikHub API key
TIKHUB_API_KEY=
```

### 扩展性设计

系统架构支持无限扩展下载器，添加新下载器仅需 **5 步**：

#### 步骤 1：创建下载器类

```python
# src/downloaders/new_downloader.py
from src.downloaders.base import BaseDownloader

class NewDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "newapi"

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def fetch_metadata(self, video_url, video_id):
        # 实现元数据获取
        pass

    async def download_resources(self, ...):
        # 实现资源下载
        pass
```

#### 步骤 2：注册到管理器

```python
# src/downloaders/manager.py
from .new_downloader import NewDownloader

class DownloaderManager:
    def _init_downloaders(self):
        # ...
        elif name == "newapi":
            downloader = NewDownloader(self.settings)
            # ...
```

#### 步骤 3-5：配置、文档、测试

```bash
# config.py
NEWAPI_API_KEY: Optional[str] = None

# .env.example
NEWAPI_API_KEY=your-key-here
METADATA_PRIORITY=newapi,ytdlp,tikhub

# tests/test_new_downloader.py
async def test_fetch_metadata():
    # 测试新下载器
    pass
```

**扩展示例**：

```bash
# 添加专门的元数据 API（免费且快速）
METADATA_PRIORITY=metaapi,ytdlp,tikhub
# metaapi: 0.3秒，免费
# ytdlp:  1-2秒，免费
# tikhub: 0.5秒，$0.002
```

---

## 敏感词审核配置

企业微信通知支持敏感词审核功能，可以自动处理消息中的敏感内容。

### 审核策略说明

| 策略 | 说明 |
|------|------|
| `block` | 检测到敏感词时拒绝发送，发送告警消息 |
| `replace` | 将敏感词替换为 `[敏感词]` |
| `pinyin_reverse` | 将敏感词转换为拼音混淆形式（默认） |

### 配置示例

```bash
# 启用敏感词审核
WECOM_MODERATION_ENABLED=true
# 敏感词库 URL（支持多个，逗号分隔）
WECOM_MODERATION_URLS=https://example.com/words1.txt,https://example.com/words2.txt
# 审核策略：pinyin_reverse（拼音混淆）
WECOM_MODERATION_STRATEGY=pinyin_reverse
```

### 敏感词文件格式

每行一个敏感词，支持注释（以 `#` 开头）：

```text
# 这是注释
敏感词1
敏感词2
```

---

## PO Token 配置

YouTube 使用 PO (Proof of Origin) Token 来验证请求来源。本项目集成了 `bgutil-ytdlp-pot-provider` 来自动获取 PO Token。

### 工作原理

```
┌─────────────┐    请求 PO Token    ┌─────────────────┐
│   yt-dlp    │ ──────────────────> │  bgutil HTTP    │
│  (下载器)   │ <────────────────── │  (POT Provider) │
└─────────────┘    返回 Token       └─────────────────┘
                                            │
                                            v
                                    ┌───────────────┐
                                    │   YouTube     │
                                    │   BotGuard    │
                                    └───────────────┘
```

### 配置方式

#### 方式一：仅 PO Token Provider（当前默认）

无需额外配置，系统会自动通过 `POT_SERVER_URL` 获取 PO Token。

```bash
# .env
POT_SERVER_URL=http://localhost:4416  # 开发环境
POT_SERVER_URL=http://pot-provider:4416  # Docker 生产环境
```

#### 方式二：Cookies + PO Token（推荐）

使用 Cookies 可以大幅提高下载成功率：

```bash
# 1. 导出 YouTube Cookies（参考上文 Cookie 获取方式）

# 2. 配置环境变量
COOKIE_FILE=./cookies.txt
```

### 配置逻辑

| 场景 | Cookies | PO Token 请求 | 成功率 |
|------|---------|---------------|--------|
| 有 Cookies | 是 | 按需自动 | 高 |
| 无 Cookies | 否 | 强制请求 | 中 |

### 本地开发 POT Provider

开发环境需要单独启动 POT Provider 服务：

```bash
# 使用 Docker 启动 bgutil POT Provider
docker run -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider

# 验证服务
curl http://localhost:4416/ping
```

### 常见问题

**Q: 日志显示 "Sign in to confirm you're not a bot"**

A: 这表示 YouTube 检测到自动化请求。解决方案：
1. 确保 POT Provider 服务正常运行
2. 配置有效的 Cookies 文件
3. 降低下载频率（增大间隔配置）
4. 检查 IP 熔断器状态

**Q: PO Token 请求失败**

A: 检查 POT Provider 服务：
```bash
# 测试 POT Provider
curl -X POST http://localhost:4416/get_pot \
  -H "Content-Type: application/json" \
  -d '{"client": "web", "video_id": "dQw4w9WgXcQ"}'
```

---

## 查询任务状态

### 请求

```bash
curl http://localhost:8000/api/v1/tasks/{task_id} \
  -H "X-API-Key: your-api-key"
```

### 响应示例

**已完成（音频+字幕模式）**
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

**部分成功（音频失败，字幕成功）**
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
      "bitrate": null,
      "language": "en"
    }
  }
}
```

**缓存命中（资源已存在，立即返回）**
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
  "cache_hit": true,
  "request": {
    "include_audio": true,
    "include_transcript": true
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": true,
    "reused_transcript": true
  },
  "position": null,
  "estimated_wait": null,
  "progress": null,
  "created_at": "2025-12-12T10:00:00Z",
  "started_at": null,
  "completed_at": "2025-12-12T10:00:00Z",
  "expires_at": "2025-02-10T10:00:00Z",
  "message": "Resources retrieved from cache"
}
```

### 客户端处理建议

```python
response = create_task(video_url)

if response.cache_hit:
    # 缓存命中，直接使用文件
    print("Cache hit! Files ready to use.")
else:
    # 新任务创建，需要轮询状态
    task_id = response.task_id
    while response.status in ["pending", "downloading"]:
        response = get_task(task_id)
        time.sleep(5)
```

---

## Webhook 回调

下载完成/失败后，系统会 POST 到指定的 `callback_url`：

```http
POST {callback_url}
Content-Type: application/json
X-Signature: sha256=xxxxxxxx
X-Task-Id: 550e8400-e29b-41d4-a716-446655440000
X-Timestamp: 1702357425
```

### 签名验证（Python 示例）

```python
import hmac
import hashlib

def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

---

## 项目结构

```
youtube-audio-api/
├── docker/                          # Docker 配置
│   ├── docker-compose.prod.yml      # 生产环境
│   └── docker-compose.dev.yml      # 开发环境
├── Dockerfile
├── pyproject.toml                   # 项目配置（使用 uv）
├── uv.lock                         # 依赖锁定文件
├── .env.example
├── scripts/
│   ├── dev.ps1                    # Windows 开发脚本
│   └── dev.sh                     # Linux/Mac 开发脚本
├── src/
│   ├── main.py                    # FastAPI 入口
│   ├── config.py                  # 配置管理
│   ├── __init__.py                 # 版本信息
│   ├── api/
│   │   ├── routes.py              # 主要 API 路由
│   │   ├── deps.py                # 依赖注入
│   │   ├── schemas.py             # 数据模型
│   │   ├── settings_routes.py      # Settings API
│   │   ├── manual_upload_routes.py # 人工上传 API
│   │   └── video_resource_routes.py # 视频资源 API
│   ├── core/
│   │   ├── downloader.py          # 下载器入口
│   │   ├── worker.py              # 下载 Worker
│   │   ├── ip_ban_breaker.py      # IP 熔断器
│   │   └── ip_ban_models.py       # IP 熔断模型
│   ├── downloaders/                # 多下载器实现
│   │   ├── base.py               # 下载器基类
│   │   ├── manager.py            # 下载器管理器
│   │   ├── circuit_breaker.py     # 下载器熔断器
│   │   ├── ytdlp_downloader.py   # yt-dlp 实现
│   │   ├── tikhub_downloader.py  # TikHub 实现
│   │   ├── cdp/                  # CDP 下载器（模块化）
│   │   │   ├── __init__.py       # 导出 CDPDownloader
│   │   │   ├── downloader.py     # 主下载器（协调者）
│   │   │   ├── audio_downloader.py # 音频下载逻辑
│   │   │   ├── human_behavior.py # 人类行为模拟
│   │   │   └── models.py         # CDP 专用模型
│   │   ├── models.py             # 下载器模型
│   │   └── exceptions.py         # 下载器异常
│   ├── db/
│   │   ├── database.py            # SQLite 操作
│   │   └── models.py             # 数据模型
│   ├── services/
│   │   ├── task_service.py        # 任务服务
│   │   ├── file_service.py        # 文件服务
│   │   ├── callback_service.py    # 回调服务
│   │   ├── notify.py             # 通知服务
│   │   ├── manual_upload_service.py # 人工上传服务
│   │   ├── transcode_service.py   # 转码服务
│   │   ├── metadata_service.py    # 元数据服务
│   │   └── tikhub_service.py     # TikHub 服务
│   ├── static/                    # 静态资源
│   │   └── admin/                # 管理界面
│   └── utils/
│       ├── logger.py              # 日志
│       └── helpers.py             # 工具函数
├── data/                           # 运行时数据
│   ├── db.sqlite
│   ├── files/
│   │   ├── audio/
│   │   └── transcript/
│   └── logs/
└── tests/
```

---

## 任务状态

| 状态 | 说明 |
|------|------|
| `pending` | 等待下载 |
| `downloading` | 下载中 |
| `completed` | 已完成 |
| `failed` | 失败（已重试） |
| `cancelled` | 已取消 |

---

## 错误码

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

---

## 测试

```bash
# 运行所有测试
pytest

# 运行带覆盖率
pytest --cov=src --cov-report=html

# 跳过集成测试
pytest -m "not integration"
```

---

## 技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| Web 框架 | FastAPI | ≥0.104 |
| ASGI 服务器 | uvicorn | ≥0.24 |
| 下载核心 | yt-dlp | ≥2026.1.19 |
| TLS 指纹 | curl_cffi | ≥0.14.0 |
| PO Token | bgutil-ytdlp-pot-provider | latest |
| 数据库 | SQLite + aiosqlite | ≥0.19 |
| 配置管理 | pydantic-settings | ≥2.0 |
| 定时任务 | APScheduler | ≥3.10 |
| 日志 | loguru | ≥0.7 |
| HTTP 客户端 | httpx | ≥0.25 |
| 包管理 | uv | latest |
| 通知 | wecom-notifier | ≥0.2.0 |

---

## 注意事项

### 安全

- API Key 不要提交到代码仓库
- 文件使用 UUID 防止枚举攻击
- 客户端需验证 Webhook HMAC 签名
- Cookie 文件包含敏感信息，注意保护

### 性能

- 默认单并发，避免触发 YouTube 风控
- 任务间隔随机，模拟人类行为
- SQLite 足够处理日均 60 次下载
- IP 熔断机制避免频繁请求

### 可靠性

- 服务重启自动恢复未完成任务
- 可重试错误自动指数退避重试
- Webhook 失败自动重试 3 次
- 部分成功场景支持资源复用

---

## License

MIT
