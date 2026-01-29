# 快速开始指南

本指南帮助您在 5-10 分钟内启动 YouTube Audio API 服务。

## 目录

- [环境要求](#环境要求)
- [安装 uv](#安装-uv)
- [克隆项目](#克隆项目)
- [配置环境变量](#配置环境变量)
- [本地开发](#本地开发)
- [Docker 部署](#docker-部署)
- [第一个 API 调用](#第一个-api-调用)
- [常见问题](#常见问题)
- [下一步](#下一步)

---

## 环境要求

### 本地开发
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** - Python 包管理器（推荐）
- **代理服务**（开发环境需要，用于访问 YouTube）

### Docker 部署
- **Docker 20.10+**
- **Docker Compose 2.0+**

---

## 安装 uv

uv 是一个快速的 Python 包管理器，比 pip 快 10-100 倍。

### Windows
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Linux/Mac
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

验证安装：
```bash
uv --version
```

---

## 克隆项目

```bash
git clone <your-repo-url>
cd youtube-audio-api
```

---

## 配置环境变量

### 1. 复制配置文件模板

```bash
cp .env.example .env
```

### 2. 编辑 `.env` 文件

**最小配置（必需）**：
```bash
# API 鉴权密钥（必需）
API_KEY=your-secret-api-key-here
```

**可选配置**：
```bash
# 开发模式（推荐）
DEBUG=true

# 服务端口（默认 8000）
PORT=8000

# 代理设置（开发环境必需）
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890

# 企业微信通知（可选）
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx

# TikHub API Key（可选，用于降级）
TIKHUB_API_KEY=your-tikhub-api-key
```

### 3. 环境变量说明

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `API_KEY` | **是** | - | API 鉴权密钥，用于保护 API 接口 |
| `DEBUG` | 否 | `false` | 调试模式，启用详细日志 |
| `PORT` | 否 | `8000` | 服务监听端口 |
| `HTTP_PROXY` | 否 | - | HTTP 代理地址（开发环境推荐） |
| `HTTPS_PROXY` | 否 | - | HTTPS 代理地址（开发环境推荐） |

**完整环境变量列表**：[配置总览](./configuration/overview.md)

---

## 本地开发

### Windows

```bash
# 使用开发脚本（推荐）
.\scripts\dev.ps1

# 或手动运行
uv sync
uv run uvicorn src.main:app --host 127.0.0.1 --port 8011 --reload
```

### Linux/Mac

```bash
# 添加执行权限
chmod +x scripts/dev.sh

# 运行开发脚本
./scripts/dev.sh
```

### 验证服务启动

访问以下地址确认服务正常运行：

- **Swagger UI**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/health
- **管理界面**: http://localhost:8000/admin

---

## Docker 部署

### 快速启动（开发环境）

```bash
# 启动开发环境（包含热重载）
docker-compose -f docker-compose.dev.yml up -d

# 查看日志
docker-compose -f docker-compose.dev.yml logs -f
```

### 生产环境部署

```bash
# 1. 复制并编辑生产配置
cp .env.example .env
# 编辑 .env 文件，填入必要配置

# 2. 构建镜像（可选，如果使用预构建镜像可跳过）
docker build -t youtube-api:latest .

# 3. 启动服务
docker-compose -f docker/docker-compose.prod.yml up -d

# 4. 查看日志
docker-compose -f docker/docker-compose.prod.yml logs -f youtube-api

# 5. 检查服务状态
docker-compose -f docker/docker-compose.prod.yml ps
```

### 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 应返回
{"status":"healthy"}
```

---

## 第一个 API 调用

### 1. 创建下载任务

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "include_audio": true,
    "include_transcript": true
  }'
```

**响应示例**：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "video_id": "dQw4w9WgXcQ",
  "position": 0,
  "estimated_wait": 30
}
```

### 2. 查询任务状态

```bash
curl http://localhost:8000/api/v1/tasks/550e8400-e29b-41d4-a716-446655440000 \
  -H "X-API-Key: your-api-key"
```

**响应示例**（完成状态）：
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
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
  }
}
```

### 3. 下载文件

```bash
# 下载音频
curl -o audio.m4a http://localhost:8000/api/v1/files/abc123.m4a

# 下载字幕
curl -o transcript.srt http://localhost:8000/api/v1/files/def456.srt
```

---

## 常见问题

### 1. 服务启动失败

**问题**：`uv run uvicorn` 命令失败

**解决方案**：
```bash
# 检查 Python 版本
python --version  # 需要 3.11+

# 检查 uv 安装
uv --version

# 重新安装依赖
uv sync
```

### 2. 下载任务失败（403 错误）

**问题**：任务返回 `HTTP 403 Forbidden`

**原因**：YouTube 检测到自动化请求

**解决方案**：

1. **配置代理**（开发环境）：
```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
```

2. **降低下载频率**：
```bash
AUDIO_INTERVAL_MIN=60
AUDIO_INTERVAL_MAX=300
```

3. **启用 CDP 下载器**（推荐）：[CDP 下载器配置](./configuration/downloaders.md)

### 3. 端口被占用

**问题**：`OSError: [Errno 48] Address already in use`

**解决方案**：
```bash
# 更改端口
PORT=8011

# 或查找并停止占用进程
# Linux/Mac
lsof -i :8000
kill -9 <PID>

# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

### 4. 依赖安装失败

**问题**：`uv sync` 下载依赖超时

**解决方案**：
```bash
# 配置国内镜像（如果在中国）
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
uv sync
```

### 5. Docker 镜像构建失败

**问题**：`docker build` 网络超时

**解决方案**：
```bash
# 配置 Docker 镜像加速（如果在中国）
# 编辑 Docker Desktop 设置，添加镜像源
```

---

## 下一步

现在您已经成功启动了服务，可以继续探索以下功能：

### 深入了解

- 📖 [API 参考文档](./api-reference.md) - 完整的 API 接口文档
- ⚙️ [配置总览](./configuration/overview.md) - 详细的配置说明
- 🚀 [生产环境部署](./deployment.md) - Docker 生产环境部署指南

### 高级功能

- 🎧 [CDP 下载器配置](./configuration/downloaders.md) - 使用真实浏览器指纹降低风控
- 🔄 [多下载器降级](./configuration/downloaders.md) - ytdlp + TikHub 双重保障
- 🔐 [熔断器配置](./configuration/circuit-breakers.md) - IP 熔断器保护机制

### 使用指南

- 📤 [人工上传功能](./guides/manual-upload.md) - 手动上传音频/视频文件
- 🎬 [视频元数据查询](./guides/video-metadata.md) - 快速获取视频信息
- 🔔 [Webhook 集成](./guides/webhook-integration.md) - 任务完成回调通知

### 架构设计

- 🏗️ [架构概览](./architecture/overview.md) - 系统架构设计
- ⚡ [下载器架构](./architecture/downloader-architecture.md) - 下载器管理机制

### 运维监控

- 📊 [监控与日志](./operations/monitoring.md) - 日志系统和企业微信通知
- 🔧 [故障排查](./operations/troubleshooting.md) - 常见问题解决方案
- 🚄 [性能优化](./operations/performance-tuning.md) - 下载性能调优

---

## 相关文档

- [部署指南](./deployment.md) - 生产环境部署详解
- [API 文档](./api-reference.md) - 完整的 API 接口参考
- [配置文档](./configuration/overview.md) - 所有配置项说明
