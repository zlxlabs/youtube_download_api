# YouTube Audio API

Docker 部署的 YouTube 音频下载服务，提供 RESTful API 接口，支持下载 YouTube 视频的音频和字幕。

## 功能特性

- **RESTful API** - 完整的任务管理接口，X-API-Key 鉴权
- **音频下载** - M4A/WebM 格式，128kbps 高质量音频（可配置是否转码）
- **字幕提取** - JSON 格式，优先中文字幕（zh-Hans > zh-Hant > zh > en）
- **灵活下载模式** - 支持仅音频/仅字幕/完整模式
- **智能资源复用** - 文件级缓存，同视频资源跨任务共享
- **多下载器降级** - CDP + yt-dlp + TikHub API 三重保障，自动降级切换
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

### 5 分钟启动

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
# 编辑 .env，填入必要配置（至少 API_KEY）

# 4. 启动服务（Windows）
.\scripts\dev.ps1

# 或 Linux/Mac
chmod +x scripts/dev.sh
./scripts/dev.sh

# 5. 访问文档
# Swagger UI: http://localhost:8000/docs
# 管理界面: http://localhost:8000/admin
```

### 第一个 API 调用

```bash
# 创建下载任务
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "include_audio": true,
    "include_transcript": true
  }'
```

### Docker 部署

```bash
# 1. 复制生产配置
cp .env.example .env

# 2. 构建并启动
docker-compose -f docker/docker-compose.prod.yml up -d

# 3. 查看日志
docker-compose -f docker/docker-compose.prod.yml logs -f youtube-api
```

## 文档导航

### 📘 快速入门

- 📖 [完整快速开始指南](docs/quick-start.md) - 详细的环境配置和启动步骤
- 🚀 [生产环境部署](docs/deployment.md) - Docker 部署、反向代理、监控配置
- 🔧 [常见问题](docs/quick-start.md#常见问题) - 服务启动失败、下载失败等问题排查

### 📌 API 使用

- 🔌 [API 参考文档](docs/api-reference.md) - 完整的 API 接口文档
- 📤 [人工上传指南](docs/guides/manual-upload.md) - 手动上传音频/视频文件
- 🎬 [视频元数据查询](docs/guides/video-metadata.md) - 快速获取视频信息
- 🔔 [Webhook 集成](docs/guides/webhook-integration.md) - 任务完成回调通知

### ⚙️ 配置说明

- 📋 [配置总览](docs/configuration/overview.md) - 所有配置项说明
- 🎧 [下载器配置](docs/configuration/downloaders.md) - CDP/ytdlp/TikHub 下载器配置
- 🔐 [熔断器配置](docs/configuration/circuit-breakers.md) - IP 熔断器和下载器熔断器
- 🔧 [高级配置](docs/configuration/advanced.md) - Cookie/PO Token/YouTube Data API

### 🏗️ 架构设计

- 📐 [架构概览](docs/architecture/overview.md) - 系统整体架构设计
- ⚡ [下载器架构](docs/architecture/downloader-architecture.md) - 下载器管理机制详解

### 📊 运维管理

- 📈 [监控与日志](docs/operations/monitoring.md) - 日志系统和企业微信通知
- 🔨 [故障排查](docs/operations/troubleshooting.md) - 常见问题解决方案
- 🚄 [性能优化](docs/operations/performance-tuning.md) - 下载速度和系统性能调优

## API 概览

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/api/v1/tasks` | 创建下载任务 | 需要 |
| GET | `/api/v1/tasks` | 列出任务 | 需要 |
| GET | `/api/v1/tasks/{task_id}` | 查询任务详情 | 需要 |
| DELETE | `/api/v1/tasks/{task_id}` | 取消任务 | 需要 |
| GET | `/api/v1/files/{file_id}` | 下载文件 | 公开 |
| POST | `/api/v1/manual-upload` | 人工上传音频 | 需要 |
| GET | `/api/v1/video-resources` | 列出视频资源 | 需要 |
| GET | `/api/v1/videos/{video_id}/info` | 查询视频元数据 | 需要 |
| GET | `/health` | 健康检查 | 公开 |

**完整 API 文档**：[API 参考文档](docs/api-reference.md)

## 项目结构

```
youtube-audio-api/
├── docker/                      # Docker 配置
├── docs/                        # 文档
│   ├── quick-start.md          # 快速开始
│   ├── api-reference.md        # API 文档
│   ├── deployment.md           # 部署指南
│   ├── configuration/          # 配置文档
│   ├── guides/                  # 使用指南
│   ├── architecture/           # 架构文档
│   └── operations/              # 运维文档
├── src/                         # 源代码
│   ├── api/                     # API 路由
│   ├── core/                    # 核心业务逻辑
│   ├── downloaders/             # 下载器实现
│   ├── services/                # 业务服务
│   └── db/                      # 数据库
├── data/                        # 运行时数据
└── tests/                       # 测试
```

## 下载器说明

系统支持三种下载器，可自动降级：

| 下载器 | 说明 | 优点 | 成本 |
|--------|------|------|------|
| **CDP** | Chrome DevTools Protocol | 真实浏览器指纹、支持音频+字幕、降低 403 风险 | 免费 |
| **yt-dlp** | 本地 yt-dlp 库 | 免费、功能强大 | 免费 |
| **TikHub** | TikHub API 服务 | 稳定、不受限流影响 | 0.002$/次 |

**默认优先级**：CDP > yt-dlp > TikHub（音频和字幕下载均以 CDP 为最高优先级）

### 下载链路依赖

```
CDP 下载器
  |-- Chrome (remote debugging) : 提供真实浏览器 cookies/headers
  |-- yt-dlp                    : 使用 cookies 提取音频流 URL 并下载
  |-- pot-provider              : 生成 GVS PO Token，解决 YouTube 风控实验
  |      (brainicism/bgutil-ytdlp-pot-provider, 随 docker-compose 自动启动)
  |-- deno                      : 解决 yt-dlp n challenge (URL 签名解密)

yt-dlp 下载器
  |-- yt-dlp                    : 独立下载，依赖 cookies + PO Token
  |-- pot-provider              : 同上
  |-- deno                      : 同上
```

> YouTube 正在逐步对部分视频强制要求 GVS PO Token（A/B 实验），
> 未提供 PO Token 时所有音频格式会被跳过。pot-provider 默认随服务启动，
> 通过 `CDP_ENABLE_POT_TOKEN=true` 启用。

**详细配置**：[下载器配置](docs/configuration/downloaders.md)

## 参与贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License

---

**相关文档**：
- 📘 [快速开始指南](docs/quick-start.md)
- 📌 [API 参考文档](docs/api-reference.md)
- ⚙️ [配置总览](docs/configuration/overview.md)
- 🚀 [生产环境部署](docs/deployment.md)

<!-- fork-gate-probe: 测试 fork PR 防护路由,验证后即删 -->
