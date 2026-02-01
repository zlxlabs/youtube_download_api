# 配置总览

本文档提供所有配置项的详细说明，帮助您根据实际需求调整系统行为。

## 目录

- [配置文件结构](#配置文件结构)
- [环境变量列表](#环境变量列表)
- [核心配置](#核心配置)
- [存储配置](#存储配置)
- [下载器配置](#下载器配置)
- [熔断器配置](#熔断器配置)
- [通知配置](#通知配置)
- [Cookie 配置](#cookie-配置)
- [人工上传配置](#人工上传配置)
- [高级配置](#高级配置)

---

## 配置文件结构

### .env 文件

系统使用 `.env` 文件管理所有配置项：

```bash
# ====== 必需配置 ======
API_KEY=your-api-key-here

# ====== 服务配置 ======
HOST=0.0.0.0
PORT=8000
DEBUG=false

# ====== 存储配置 ======
DATA_DIR=./data
FILE_RETENTION_DAYS=60

# ====== 时区 ======
TZ=Asia/Shanghai

# ====== 外部访问地址 ======
BASE_URL=http://localhost:8000

# ====== TikHub API ======
TIKHUB_API_KEY=

# ====== PO Token 服务 ======
POT_SERVER_URL=http://pot-provider:4416

# ====== 下载器配置 ======
CDP_ENABLED=false
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub

# ====== 熔断器配置 ======
CIRCUIT_BREAKER_ENABLED=true

# ====== 企微通知 ======
WECOM_WEBHOOK_URL=

# ====== Cookie 管理 ======
COOKIE_FILE=./cookies.txt
```

### 配置文件模板

项目提供 `.env.example` 文件作为配置模板：

```bash
cp .env.example .env
```

---

## 环境变量列表

### 按功能分类

| 功能模块 | 环境变量 | 说明 |
|---------|---------|------|
| **核心配置** | `API_KEY` | API 鉴权密钥（必需） |
| | `HOST` | 服务监听地址 |
| | `PORT` | 服务监听端口 |
| | `DEBUG` | 调试模式 |
| | `BASE_URL` | 文件下载链接基础URL |
| **存储配置** | `DATA_DIR` | 数据存储目录 |
| | `FILE_RETENTION_DAYS` | 文件保留天数 |
| | `TZ` | 时区配置 |
| **TikHub API** | `TIKHUB_API_KEY` | TikHub API 密钥 |
| | `TIKHUB_CACHE_TTL_HOURS` | TikHub 缓存时间 |
| **PO Token** | `POT_SERVER_URL` | PO Token 服务地址 |
| **CDP 下载器** | `CDP_ENABLED` | 启用 CDP 下载器 |
| | `CDP_URLS` | CDP 端点列表 |
| | `CDP_ENABLE_MULTIPART` | 启用分片下载 |
| | `CDP_MULTIPART_MIN_SIZE` | 分片下载最小文件阈值 |
| | `CDP_MULTIPART_CHUNKS` | 分片数量 |
| | `CDP_TRANSCODE_TO_M4A` | 是否转码为 m4a 格式（默认关闭，保留原始格式） |
| **下载器优先级** | `AUDIO_DOWNLOAD_PRIORITY` | 音频下载器优先级 |
| | `DOWNLOADER_PRIORITY` | 下载器优先级 |
| **熔断器** | `CIRCUIT_BREAKER_ENABLED` | 启用熔断器 |
| | `CIRCUIT_BREAKER_THRESHOLD` | 熔断器失败阈值 |
| | `CIRCUIT_BREAKER_TIMEOUT` | 熔断器超时时间 |
| **IP 熔断器** | `MIN_WAIT_BEFORE_RETRY` | 最小等待时间 |
| | `MAX_RETRY_INTERVAL` | 重试间隔 |
| **间隔策略** | `TRANSCRIPT_INTERVAL_MIN` | 字幕任务最小间隔 |
| | `TRANSCRIPT_INTERVAL_MAX` | 字幕任务最大间隔 |
| | `AUDIO_INTERVAL_MIN` | 音频任务最小间隔 |
| | `AUDIO_INTERVAL_MAX` | 音频任务最大间隔 |
| **企微通知** | `WECOM_WEBHOOK_URL` | 企业微信 Webhook URL |
| | `WECOM_MODERATION_ENABLED` | 启用敏感词审核 |
| | `WECOM_MODERATION_URLS` | 敏感词库 URL 列表 |
| | `WECOM_MODERATION_STRATEGY` | 审核策略 |
| **Cookie** | `COOKIE_FILE` | Cookie 文件路径 |
| **人工上传** | `MANUAL_UPLOAD_ENABLED` | 是否启用人工上传 |
| | `MANUAL_UPLOAD_MAX_SIZE_MB` | 单文件最大大小 |
| | `MANUAL_UPLOAD_ALLOWED_VIDEO_FORMATS` | 允许的视频格式 |
| | `MANUAL_UPLOAD_ALLOWED_AUDIO_FORMATS` | 允许的音频格式 |

---

## 核心配置

### API_KEY（必需）

API 鉴权密钥，用于保护 API 接口。

**类型**：string
**必填**：是

**配置示例**：
```bash
# 使用随机字符串
API_KEY=your-secret-api-key-here

# 使用 openssl 生成强密钥
API_KEY=$(openssl rand -hex 32)
```

**安全建议**：
- 使用至少 32 位的随机字符串
- 定期更换 API Key
- 不要提交到代码仓库

**使用方式**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/tasks"
```

---

### HOST 和 PORT

服务监听地址和端口。

**类型**：string / integer
**必填**：否
**默认值**：`0.0.0.0` / `8000`

**配置示例**：
```bash
HOST=0.0.0.0
PORT=8000
```

**说明**：
- `0.0.0.0`：监听所有网络接口（Docker 生产环境）
- `127.0.0.1`：仅监听本地（本地开发环境）

---

### DEBUG

调试模式开关。

**类型**：boolean
**必填**：否
**默认值**：`false`

**配置示例**：
```bash
# 开发环境
DEBUG=true

# 生产环境
DEBUG=false
```

**影响**：
- `true`：启用详细日志、Swagger UI、热重载
- `false`：仅输出 INFO 级别日志、禁用 Swagger UI

---

### BASE_URL

文件下载链接基础URL。

**类型**：string
**必填**：否
**默认值**：`http://localhost:8000`

**配置示例**：
```bash
# 本地开发
BASE_URL=http://localhost:8000

# 生产环境
BASE_URL=https://your-domain.com
```

**说明**：
- 用于生成文件下载链接
- 企业微信通知中使用此 URL
- 确保外部可访问

---

## 存储配置

### DATA_DIR

数据存储目录。

**类型**：string
**必填**：否
**默认值**：`./data`

**配置示例**：
```bash
# 本地开发
DATA_DIR=./data

# Docker 环境
DATA_DIR=/app/data

# 自定义路径
DATA_DIR=/var/lib/youtube-api
```

**目录结构**：
```
data/
├── db.sqlite              # SQLite 数据库
├── files/
│   ├── audio/             # 音频文件
│   └── transcript/        # 字幕文件
├── logs/                  # 日志文件
└── cookies.txt            # YouTube Cookie 文件
```

---

### FILE_RETENTION_DAYS

文件保留天数。

**类型**：integer
**必填**：否
**默认值**：`60`

**配置示例**：
```bash
# 开发环境（快速清理）
FILE_RETENTION_DAYS=1

# 生产环境（保留 60 天）
FILE_RETENTION_DAYS=60

# 长期保留
FILE_RETENTION_DAYS=180
```

**说明**：
- 文件超过此天数后自动删除
- 不影响数据库中的元数据
- 建议根据磁盘空间调整

---

### TZ

时区配置。

**类型**：string
**必填**：否
**默认值**：`Asia/Shanghai`

**配置示例**：
```bash
# 中国时区
TZ=Asia/Shanghai

# UTC 时区
TZ=UTC

# 美国东部时间
TZ=America/New_York
```

**影响**：
- 时间戳显示
- 日志记录时间
- 定时任务时间

---

## 下载器配置

### CDP_ENABLED

启用 CDP 下载器（Chrome DevTools Protocol）。

**类型**：boolean
**必填**：否
**默认值**：`false`

**配置示例**：
```bash
# 启用 CDP 下载器
CDP_ENABLED=true

# 禁用 CDP 下载器
CDP_ENABLED=false
```

**详细配置**：[CDP 下载器配置](./downloaders.md)

---

### AUDIO_DOWNLOAD_PRIORITY

音频下载器优先级顺序。

**类型**：string（逗号分隔）
**必填**：否
**默认值**：`cdp,ytdlp,tikhub`

**配置示例**：
```bash
# 优先使用 CDP（推荐）
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub

# 仅使用 ytdlp
AUDIO_DOWNLOAD_PRIORITY=ytdlp

# 仅使用 TikHub（最稳定）
AUDIO_DOWNLOAD_PRIORITY=tikhub

# 自定义顺序
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub,cdp
```

**支持的下载器**：
- `cdp`：Chrome DevTools Protocol 下载器
- `ytdlp`：yt-dlp 本地下载器
- `tikhub`：TikHub API 服务

**详细配置**：[下载器配置](./downloaders.md)

---

## 熔断器配置

### CIRCUIT_BREAKER_ENABLED

启用下载器熔断器保护。

**类型**：boolean
**必填**：否
**默认值**：`true`

**配置示例**：
```bash
# 启用熔断器
CIRCUIT_BREAKER_ENABLED=true

# 禁用熔断器（不推荐）
CIRCUIT_BREAKER_ENABLED=false
```

**详细配置**：[熔断器配置](./circuit-breakers.md)

---

## 通知配置

### WECOM_WEBHOOK_URL

企业微信 Webhook URL。

**类型**：string
**必填**：否
**默认值**：无

**配置示例**：
```bash
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

**获取 Webhook URL**：
1. 进入企业微信群机器人设置
2. 点击"添加机器人"
3. 复制 Webhook 地址

**通知内容**：
- 任务完成通知
- 任务失败通知
- CDP 连接失败通知
- 熔断器触发通知

详细配置：[监控与日志](../operations/monitoring.md)

---

## Cookie 配置

### COOKIE_FILE

YouTube Cookie 文件路径。

**类型**：string
**必填**：否
**默认值**：无

**配置示例**：
```bash
# 本地文件
COOKIE_FILE=./cookies.txt

# Docker 环境
COOKIE_FILE=/app/data/cookies.txt
```

**详细配置**：[高级配置](./advanced.md#cookie-管理)

---

## 人工上传配置

### MANUAL_UPLOAD_ENABLED

是否启用人工上传功能。

**类型**：boolean
**必填**：否
**默认值**：`true`

**配置示例**：
```bash
# 启用人工上传
MANUAL_UPLOAD_ENABLED=true

# 禁用人工上传
MANUAL_UPLOAD_ENABLED=false
```

---

### MANUAL_UPLOAD_MAX_SIZE_MB

单文件最大大小（MB）。

**类型**：integer
**必填**：否
**默认值**：`500`

**配置示例**：
```bash
# 限制 100MB
MANUAL_UPLOAD_MAX_SIZE_MB=100

# 允许大文件
MANUAL_UPLOAD_MAX_SIZE_MB=1000
```

---

### MANUAL_UPLOAD_ALLOWED_VIDEO_FORMATS

允许上传的视频格式。

**类型**：string（逗号分隔）
**必填**：否
**默认值**：`.mp4,.webm,.mkv,.avi,.mov`

**配置示例**：
```bash
# 允许的格式
MANUAL_UPLOAD_ALLOWED_VIDEO_FORMATS=.mp4,.webm,.mkv,.avi,.mov
```

---

### MANUAL_UPLOAD_ALLOWED_AUDIO_FORMATS

允许上传的音频格式。

**类型**：string（逗号分隔）
**必填**：否
**默认值**：`.m4a,.mp3,.aac,.opus,.wav,.flac,.ogg`

**配置示例**：
```bash
# 允许的格式
MANUAL_UPLOAD_ALLOWED_AUDIO_FORMATS=.m4a,.mp3,.aac,.opus,.wav,.flac,.ogg
```

---

## 高级配置

### DRY_RUN

干跑模式（跳过下载，用于测试）。

**类型**：boolean
**必填**：否
**默认值**：`false`

**配置示例**：
```bash
# 启用干跑模式
DRY_RUN=true
```

**说明**：
- 跳过实际下载
- 仅模拟任务流程
- 用于测试和调试

---

### DOWNLOAD_CONCURRENCY

下载并发数（预留，暂未实现）。

**类型**：integer
**必填**：否
**默认值**：`1`

**配置示例**：
```bash
DOWNLOAD_CONCURRENCY=1
```

---

## 相关文档

- [快速开始指南](../quick-start.md)
- [CDP 下载器配置](./downloaders.md)
- [熔断器配置](./circuit-breakers.md)
- [高级配置](./advanced.md)
- [部署指南](../deployment.md)
