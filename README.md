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
- **风控绕过** - TLS 指纹模拟 + PO Token 机制
- **任务队列** - 异步处理，支持并发控制和错误重试
- **双模式通知** - Webhook 回调 + 轮询查询
- **企业微信** - 任务状态实时通知
- **自动清理** - 文件 60 天自动过期清理

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
cp .env.example .env.development
# 编辑 .env.development，填入必要配置

# 4. 启动开发环境 (Windows)
.\scripts\dev.ps1
# 或者手动运行
uv sync && $env:ENV_FILE=".env.development"; uv run uvicorn src.main:app --reload --host 127.0.0.1 --port 8000

# Linux/Mac
chmod +x scripts/dev.sh
./scripts/dev.sh
```

### Docker 部署

```bash
# 1. 复制生产配置
cp .env.example .env.production
# 编辑 .env.production

# 2. 构建并启动
docker-compose up -d --build

# 3. 查看日志
docker-compose logs -f youtube-api
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
| GET | `/health` | 健康检查 | 公开 |

### 鉴权方式

```
Header: X-API-Key: your-api-key
```

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

**缓存命中判断**

当所有请求的资源都已存在时，响应会有以下特征：
- `task_id: null` - 没有创建新任务
- `cache_hit: true` - 明确标识为缓存命中
- `status: "completed"` - 直接返回完成状态

**响应**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "video_id": "dQw4w9WgXcQ",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "priority": "normal",
  "video_info": null,
  "files": null,
  "error": null,
  "cache_hit": false,
  "request": {
    "include_audio": true,
    "include_transcript": true
  },
  "result": null,
  "position": 3,
  "estimated_wait": 180,
  "progress": null,
  "created_at": "2025-12-12T10:00:00Z",
  "started_at": null,
  "completed_at": null,
  "expires_at": null,
  "message": null
}
```

### 查询任务状态

**请求**
```bash
curl http://localhost:8000/api/v1/tasks/{task_id} \
  -H "X-API-Key: your-api-key"
```

**响应 - 已完成（音频+字幕模式）**
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
    "reused_transcript": false
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

**响应 - 已完成（仅字幕模式，视频有字幕）**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
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
    "audio": null,
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
    "include_audio": false,
    "include_transcript": true
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false
  },
  "position": null,
  "estimated_wait": null,
  "progress": null,
  "created_at": "2025-12-12T10:00:00Z",
  "started_at": "2025-12-12T10:00:05Z",
  "completed_at": "2025-12-12T10:00:10Z",
  "expires_at": "2025-02-10T10:00:10Z",
  "message": null
}
```

**响应 - 已完成（仅字幕模式，视频无字幕，自动下载音频）**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
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
    "transcript": null
  },
  "error": null,
  "cache_hit": false,
  "request": {
    "include_audio": false,
    "include_transcript": true
  },
  "result": {
    "has_transcript": false,
    "audio_fallback": true,
    "reused_audio": false,
    "reused_transcript": false
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

**响应 - 缓存命中（资源已存在，立即返回）**
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

**客户端处理建议**

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

### Webhook 回调

下载完成/失败后，系统会 POST 到指定的 `callback_url`：

```http
POST {callback_url}
Content-Type: application/json
X-Signature: sha256=xxxxxxxx
X-Task-Id: 550e8400-e29b-41d4-a716-446655440000
X-Timestamp: 1702357425
```

**签名验证**（Python 示例）
```python
import hmac
import hashlib

def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

## 配置说明

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
| `POT_SERVER_URL` | 否 | http://pot-provider:4416 | PO Token 服务地址 |
| `HTTP_PROXY` | 否 | - | HTTP 代理（开发环境） |
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
| `TIKHUB_API_KEY` | 否 | - | TikHub API 密钥（用于下载降级） |
| `DOWNLOADER_PRIORITY` | 否 | ytdlp,tikhub | 下载器优先级顺序 |
| `CIRCUIT_BREAKER_ENABLED` | 否 | true | 启用熔断器保护 |
| `CIRCUIT_BREAKER_THRESHOLD` | 否 | 5 | 熔断器失败阈值 |
| `CIRCUIT_BREAKER_TIMEOUT` | 否 | 1800 | 熔断器超时（秒） |
| `CIRCUIT_BREAKER_HALF_OPEN_CALLS` | 否 | 3 | 半开状态最大调用次数 |
| `COOKIE_FILE` | 否 | - | Cookie 文件路径 |
| `DRY_RUN` | 否 | false | 干跑模式（跳过下载） |

### 开发环境配置示例

```bash
# .env.development
DEBUG=true
API_KEY=dev-test-key-12345
POT_SERVER_URL=http://localhost:4416
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
WECOM_WEBHOOK_URL=

TASK_INTERVAL_MIN=10
TASK_INTERVAL_MAX=30
FILE_RETENTION_DAYS=1
```

### 敏感词审核配置

企业微信通知支持敏感词审核功能，可以自动处理消息中的敏感内容。

**审核策略说明**

| 策略 | 说明 |
|------|------|
| `block` | 检测到敏感词时拒绝发送，发送告警消息 |
| `replace` | 将敏感词替换为 `[敏感词]` |
| `pinyin_reverse` | 将敏感词转换为拼音混淆形式（默认） |

**配置示例**

```bash
# 启用敏感词审核
WECOM_MODERATION_ENABLED=true
# 敏感词库 URL（支持多个，逗号分隔）
WECOM_MODERATION_URLS=https://example.com/words1.txt,https://example.com/words2.txt
# 审核策略：pinyin_reverse（拼音混淆）
WECOM_MODERATION_STRATEGY=pinyin_reverse
```

**敏感词文件格式**

每行一个敏感词，支持注释（以 `#` 开头）：

```text
# 这是注释
敏感词1
敏感词2
```

### 多下载器配置

系统支持多种下载方式，并提供自动降级机制，以提高下载成功率和服务可靠性。

#### 支持的下载器

| 下载器 | 说明 | 优点 | 缺点 | 成本 |
|--------|------|------|------|------|
| **yt-dlp** | 本地 yt-dlp 库 | 免费、功能强大 | 可能遇到 YouTube 限流 | 免费 |
| **TikHub** | TikHub API 服务 | 稳定、不受限流影响 | 需要 API key | 0.002$/次 |

#### 工作原理

```
请求下载
  │
  ├─> 1. 尝试 yt-dlp（优先）
  │   ├─ 成功 → 返回结果
  │   └─ 失败（限流/错误）→ 继续
  │
  ├─> 2. 尝试 TikHub（降级）
  │   ├─ 成功 → 返回结果
  │   └─ 失败 → 返回错误
  │
  └─ 所有下载器都失败 → 任务失败
```

#### 熔断器保护

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

#### 配置示例

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

#### 监控和统计

系统会记录每个下载器的成功/失败情况，可通过日志查看：

```
[INFO] DownloaderManager initialized with 2 downloader(s): ['ytdlp', 'tikhub']
[INFO] Trying downloader: ytdlp (circuit: closed)
[WARNING] ✗ ytdlp failed: RATE_LIMITED - Rate limited by YouTube
[INFO] Falling back to next downloader...
[INFO] Trying downloader: tikhub (circuit: closed)
[INFO] ✓ Download succeeded with tikhub (success rate: 98.5%)
```

熔断器状态可通过健康检查接口查询（TODO：待实现）。

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
# 1. 导出 YouTube Cookies
yt-dlp --cookies-from-browser chrome --cookies cookies.txt "https://www.youtube.com"

# 2. 配置环境变量
COOKIE_FILE=./cookies.txt
```

### youtube cookie 获取方式
Extractors · yt-dlp/yt-dlp Wiki:  https://github.com/yt-dlp/yt-dlp/wiki/extractors

Caution

---

By using your account with yt-dlp, you run the risk of it being banned (temporarily or permanently). Be mindful with the request rate and amount of downloads you make with an account.  
Use it only when necessary, or consider using a throwaway account.

---

Note

This is only necessary for content that requires an account to access, such as private playlists, age-restricted videos and members-only content.

If you are unfamiliar with the basics of exporting cookies and passing them to yt-dlp, then first see [How do I pass cookies to yt-dlp?](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)

---

YouTube rotates account cookies frequently on open YouTube browser tabs as a security measure.  
To export cookies that will remain working with yt-dlp, you will need to export cookies in such a way that they are never rotated.

---

One way to do this is through a private browsing/incognito window:

1. Open a new private browsing/incognito window and log into YouTube
2. In same window and same tab from step 1, navigate to `https://www.youtube.com/robots.txt` (this should be the **only** private/incognito browsing tab open)
3. Export `youtube.com` cookies from the browser, then **close the private browsing/incognito window** so that the session is never opened in the browser again.

Note

Do **NOT** use the `--cookies COOKIEFILE --cookies-from-browser BROWSER` method (as described in the above FAQ link) to export your cookies to a cookiefile. This will export **all** of your regular browser cookies, but **not** the cookies from this private/incognito YouTube session. Instead, use one of the browser extensions recommended in the [FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp).


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
3. 降低下载频率（增大 `TASK_INTERVAL_MIN/MAX`）

**Q: PO Token 请求失败**

A: 检查 POT Provider 服务：
```bash
# 测试 POT Provider
curl -X POST http://localhost:4416/get_pot \
  -H "Content-Type: application/json" \
  -d '{"client": "web", "video_id": "dQw4w9WgXcQ"}'
```

## 项目结构

```
youtube-audio-api/
├── docker-compose.yml          # 生产部署
├── docker-compose.dev.yml      # 开发环境
├── Dockerfile
├── requirements.txt
├── .env.example
├── scripts/
│   ├── dev.ps1                 # Windows 开发脚本
│   └── dev.sh                  # Linux/Mac 开发脚本
├── src/
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置管理
│   ├── api/
│   │   ├── routes.py           # API 路由
│   │   ├── deps.py             # 依赖注入
│   │   └── schemas.py          # 数据模型
│   ├── core/
│   │   ├── downloader.py       # yt-dlp 封装
│   │   └── worker.py           # 下载 Worker
│   ├── db/
│   │   ├── database.py         # SQLite 操作
│   │   └── models.py           # 数据模型
│   ├── services/
│   │   ├── task_service.py     # 任务服务
│   │   ├── file_service.py     # 文件服务
│   │   ├── callback_service.py # 回调服务
│   │   └── notify.py           # 通知服务
│   └── utils/
│       ├── logger.py           # 日志
│       └── helpers.py          # 工具函数
├── data/                       # 运行时数据
│   ├── db.sqlite
│   └── files/
└── tests/
```

## 任务状态

| 状态 | 说明 |
|------|------|
| `pending` | 等待下载 |
| `downloading` | 下载中 |
| `completed` | 已完成 |
| `failed` | 失败（已重试） |
| `cancelled` | 已取消 |

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

## 测试

```bash
# 运行所有测试
pytest

# 运行带覆盖率
pytest --cov=src --cov-report=html

# 跳过集成测试
pytest -m "not integration"
```

## 技术栈

| 组件 | 技术 | 版本 |
|------|------|------|
| Web 框架 | FastAPI | ≥0.104 |
| ASGI 服务器 | uvicorn | ≥0.24 |
| 下载核心 | yt-dlp | ≥2025.05.22 |
| TLS 指纹 | curl_cffi | ≥0.6 |
| PO Token | bgutil-ytdlp-pot-provider | latest |
| 数据库 | SQLite + aiosqlite | ≥0.19 |
| 配置管理 | pydantic-settings | ≥2.0 |
| 定时任务 | APScheduler | ≥3.10 |
| 日志 | loguru | ≥0.7 |
| HTTP 客户端 | httpx | ≥0.25 |

## 注意事项

### 安全

- API Key 不要提交到代码仓库
- 文件使用 UUID 防止枚举攻击
- 客户端需验证 Webhook HMAC 签名

### 性能

- 默认单并发，避免触发 YouTube 风控
- 任务间隔随机，模拟人类行为
- SQLite 足够处理日均 60 次下载

### 可靠性

- 服务重启自动恢复未完成任务
- 可重试错误自动指数退避重试
- Webhook 失败自动重试 3 次

## License

MIT
