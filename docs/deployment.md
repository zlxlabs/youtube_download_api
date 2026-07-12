# 部署指南

本指南提供生产环境部署的详细说明，包括 Docker 配置、环境变量管理、性能调优和监控配置。

## 目录

- [部署方式概览](#部署方式概览)
- [Docker 生产环境部署](#docker-生产环境部署)
- [环境变量管理](#环境变量管理)
- [性能调优](#性能调优)
- [安全配置](#安全配置)
- [监控和日志](#监控和日志)
- [备份和恢复](#备份和恢复)
- [故障排查](#故障排查)

---

## 部署方式概览

### 推荐方案：Docker Compose

| 方案 | 优点 | 缺点 | 推荐场景 |
|------|------|------|----------|
| **Docker Compose** | 易于部署、资源隔离、便于升级 | 需要容器环境 | **推荐：生产环境** |
| 手动部署 | 完全控制 | 复杂、易出错 | 不推荐 |
| Kubernetes | 高可用、自动扩缩容 | 复杂度高 | 大规模部署 |

### 部署架构

```
┌─────────────────────────────────────────┐
│         Nginx / Cloudflare (可选)       │
│              反向代理 + HTTPS             │
└────────────┬────────────────────────────┘
             │
┌────────────▼────────────────────────────┐
│      Docker Compose                      │
│                                          │
│  ┌──────────────────────────────────┐  │
│  │  youtube-api (主服务)             │  │
│  │  - FastAPI 应用                  │  │
│  │  - 端口: 8000                     │  │
│  └──────────────────────────────────┘  │
│                                          │
│  ┌──────────────────────────────────┐  │
│  │  pot-provider (PO Token 服务)    │  │
│  │  - 可选                          │  │
│  │  - 端口: 4416                     │  │
│  └──────────────────────────────────┘  │
│                                          │
│  ┌──────────────────────────────────┐  │
│  │  postgres (数据库，可选)          │  │
│  │  - 当前使用 SQLite                │  │
│  │  - 未来可升级到 PostgreSQL       │  │
│  └──────────────────────────────────┘  │
│                                          │
│  ┌──────────────────────────────────┐  │
│  │  数据卷                          │  │
│  │  - ./data (SQLite + 文件)        │  │
│  │  - ./logs (日志)                 │  │
│  └──────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

---

## Docker 生产环境部署

### 1. 准备工作

#### 1.1 克隆仓库

```bash
git clone <your-repo-url>
cd youtube-audio-api
```

#### 1.2 创建配置文件

```bash
cp .env.example .env
```

#### 1.3 编辑 `.env` 文件

**生产环境配置**：
```bash
# ====== 必需配置 ======
API_KEY=your-strong-secret-api-key-here

# ====== 服务配置 ======
HOST=0.0.0.0
PORT=8000
DEBUG=false

# ====== 存储配置 ======
DATA_DIR=/app/data
FILE_RETENTION_DAYS=60

# ====== 时区 ======
TZ=Asia/Shanghai

# ====== 外部访问地址 ======
BASE_URL=https://your-domain.com

# ====== TikHub API（推荐配置）=====
TIKHUB_API_KEY=your-tikhub-api-key

# ====== PO Token 服务 ======
POT_SERVER_URL=http://pot-provider:4416

# ====== 下载器配置（推荐配置）=====
CDP_ENABLED=false
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub

# ====== 熔断器配置 ======
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT=1800

# ====== IP 熔断器配置 ======
IP_BAN_MIN_WAIT_BEFORE_RETRY=3600
IP_BAN_MAX_RETRY_INTERVAL=1800

# ====== 间隔策略（根据实际情况调整）=====
TRANSCRIPT_INTERVAL_MIN=20
TRANSCRIPT_INTERVAL_MAX=40
AUDIO_INTERVAL_MIN=60
AUDIO_INTERVAL_MAX=600

# ====== 企微通知（可选）=====
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
WECOM_MODERATION_ENABLED=false

# ====== Cookie 管理（可选）=====
COOKIE_FILE=/app/data/cookies.txt
```

### 2. 构建镜像

#### 2.1 使用 Docker Compose 构建

```bash
docker-compose -f docker/docker-compose.prod.yml build
```

#### 2.2 手动构建镜像（可选）

```bash
docker build -t youtube-api:latest .
```

**构建参数**：
```bash
# 指定 Python 版本
docker build --build-arg PYTHON_VERSION=3.11 -t youtube-api:latest .

# 多平台构建（ARM64）
docker buildx build --platform linux/amd64,linux/arm64 -t youtube-api:latest .
```

### 3. 启动服务

#### 3.1 使用 Docker Compose 启动

```bash
# 后台启动
docker-compose -f docker/docker-compose.prod.yml up -d

# 查看启动日志
docker-compose -f docker/docker-compose.prod.yml logs -f

# 查看服务状态
docker-compose -f docker/docker-compose.prod.yml ps
```

**服务状态检查**：
```bash
# 健康检查
curl http://localhost:8000/health

# 应返回
{"status":"healthy","version":"1.0.0","timestamp":"2026-01-28T12:00:00Z"}
```

#### 3.2 启动单个服务

```bash
# 仅启动 API 服务
docker-compose -f docker/docker-compose.prod.yml up -d youtube-api

# 启动 POT Provider
docker-compose -f docker/docker-compose.prod.yml up -d pot-provider
```

### 4. 配置反向代理（推荐）

#### 4.1 Nginx 配置示例

```nginx
upstream youtube_api {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name your-domain.com;

    # 重定向到 HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    # SSL 证书
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # SSL 配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # 客户端最大上传大小
    client_max_body_size 500M;

    # 代理配置
    location / {
        proxy_pass http://youtube_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 超时配置
        proxy_connect_timeout 600;
        proxy_send_timeout 600;
        proxy_read_timeout 600;
    }

    # WebSocket 支持（如果需要）
    location /ws {
        proxy_pass http://youtube_api;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

#### 4.2 Cloudflare 配置（推荐）

1. 添加域名到 Cloudflare
2. 配置 DNS：`your-domain.com` → `your-server-ip`
3. 开启 SSL/TLS：Full 模式
4. 配置 Page Rules（可选）：
   - 缓存静态文件
   - 启用 Auto Minify
   - 启用 Brotli 压缩

### 5. Docker Compose 配置说明

#### 5.1 docker-compose.prod.yml

```yaml
version: '3.8'

services:
  youtube-api:
    build: .
    container_name: youtube-api
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - HOST=0.0.0.0
      - PORT=8000
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    networks:
      - youtube-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  pot-provider:
    image: brainicism/bgutil-ytdlp-pot-provider:latest
    container_name: pot-provider
    restart: unless-stopped
    ports:
      - "4416:4416"
    networks:
      - youtube-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:4416/ping"]
      interval: 60s
      timeout: 10s
      retries: 3

networks:
  youtube-network:
    driver: bridge
```

**配置说明**：
- `restart: unless-stopped` - 自动重启（除非手动停止）
- `volumes` - 持久化数据卷
- `healthcheck` - 健康检查
- `networks` - 独立网络，隔离服务

### 6. 滚动更新

#### 6.1 无停机更新

```bash
# 拉取最新代码
git pull

# 重新构建镜像
docker-compose -f docker/docker-compose.prod.yml build

# 滚动更新（平滑过渡）
docker-compose -f docker/docker-compose.prod.yml up -d --no-deps --build youtube-api

# 查看更新日志
docker-compose -f docker/docker-compose.prod.yml logs -f youtube-api
```

#### 6.2 备份数据

```bash
# 备份数据目录
tar -czf backup_$(date +%Y%m%d_%H%M%S).tar.gz ./data

# 备份 .env
cp .env .env.backup
```

---

## 环境变量管理

### 1. 环境变量文件

#### 1.1 .env.example（模板）

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
DOWNLOADER_PRIORITY=ytdlp,tikhub

# ====== 熔断器配置 ======
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT=1800

# ====== IP 熔断器配置 ======
IP_BAN_MIN_WAIT_BEFORE_RETRY=3600
IP_BAN_MAX_RETRY_INTERVAL=1800

# ====== 间隔策略 ======
TRANSCRIPT_INTERVAL_MIN=20
TRANSCRIPT_INTERVAL_MAX=40
AUDIO_INTERVAL_MIN=60
AUDIO_INTERVAL_MAX=600

# ====== 企微通知 ======
WECOM_WEBHOOK_URL=
WECOM_MODERATION_ENABLED=false
WECOM_MODERATION_URLS=
WECOM_MODERATION_STRATEGY=pinyin_reverse

# ====== Cookie 管理 ======
COOKIE_FILE=./cookies.txt

# ====== 人工上传配置 ======
MANUAL_UPLOAD_ENABLED=true
MANUAL_UPLOAD_MAX_SIZE_MB=500
MANUAL_UPLOAD_ALLOWED_VIDEO_FORMATS=.mp4,.webm,.mkv,.avi,.mov
MANUAL_UPLOAD_ALLOWED_AUDIO_FORMATS=.m4a,.mp3,.aac,.opus,.wav,.flac,.ogg
```

### 2. 敏感信息管理

#### 2.1 使用 Docker Secrets（推荐）

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  youtube-api:
    secrets:
      - api_key
      - tikhub_api_key
    environment:
      API_KEY_FILE: /run/secrets/api_key
      TIKHUB_API_KEY_FILE: /run/secrets/tikhub_api_key

secrets:
  api_key:
    file: ./secrets/api_key.txt
  tikhub_api_key:
    file: ./secrets/tikhub_api_key.txt
```

#### 2.2 使用环境变量管理工具

```bash
# 使用 direnv（推荐）
echo "export API_KEY='your-api-key'" > .envrc
direnv allow

# 或使用 python-dotenv（开发环境）
pip install python-dotenv
```

---

## 性能调优

### 1. 下载速度优化

#### 1.1 启用 CDP 下载器（推荐）

```bash
# .env
CDP_ENABLED=true
CDP_URLS=http://192.168.1.100:9222,http://192.168.1.101:9222
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1  # 降低阈值，让小文件也享受分片加速
CDP_MULTIPART_CHUNKS=6
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

**效果**：下载速度提升 5-10 倍

详细配置：[CDP 下载器配置](./configuration/downloaders.md)

#### 1.2 优化间隔策略

根据实际需求调整下载间隔：

```bash
# 激进策略（高并发）
TRANSCRIPT_INTERVAL_MIN=10
TRANSCRIPT_INTERVAL_MAX=20
AUDIO_INTERVAL_MIN=30
AUDIO_INTERVAL_MAX=120

# 保守策略（低风控）
TRANSCRIPT_INTERVAL_MIN=30
TRANSCRIPT_INTERVAL_MAX=60
AUDIO_INTERVAL_MIN=120
AUDIO_INTERVAL_MAX=600
```

### 2. 并发控制

当前版本为单线程处理，预留并发控制配置：

```bash
# 未来功能
DOWNLOAD_CONCURRENCY=1
```

### 3. 缓存策略

#### 3.1 元数据缓存（自动）

元数据永久缓存在数据库中，重复查询无需网络请求。

#### 3.2 文件缓存

文件保留 60 天后自动清理：

```bash
FILE_RETENTION_DAYS=60
```

### 4. 资源限制

#### 4.1 Docker 资源限制

```yaml
# docker-compose.prod.yml
services:
  youtube-api:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

---

## 安全配置

### 1. API Key 鉴权

```bash
# 使用强随机字符串
API_KEY=$(openssl rand -hex 32)
```

### 2. HTTPS 配置

#### 2.1 使用 Let's Encrypt（免费）

```bash
# 安装 certbot
apt-get install certbot

# 获取证书
certbot certonly --standalone -d your-domain.com

# 自动续期
certbot renew --dry-run
```

#### 2.2 使用 Cloudflare SSL

1. Cloudflare 默认提供免费 SSL 证书
2. 选择 SSL/TLS 模式为 Full

### 3. 防火墙配置

```bash
# 仅开放必要端口
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw enable
```

### 4. 敏感信息保护

```bash
# 不要提交以下文件到 Git
echo ".env" >> .gitignore
echo "secrets/" >> .gitignore
echo "data/" >> .gitignore
```

---

## 监控和日志

### 1. 日志管理

#### 1.1 日志级别

```bash
# 生产环境
DEBUG=false

# 日志级别
# - DEBUG: 详细的调试信息
# - INFO: 一般信息
# - WARNING: 警告信息
# - ERROR: 错误信息
```

#### 1.2 日志轮转

```bash
# 使用 logrotate
cat > /etc/logrotate.d/youtube-api << EOF
/path/to/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    create 0644 www-data www-data
}
EOF
```

#### 1.3 企业微信通知

```bash
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

详细配置：[监控与日志](./operations/monitoring.md)

### 2. 健康检查

```bash
# 健康检查端点
curl http://localhost:8000/health
```

**响应**：
```json
{
  "status":"healthy",
  "version":"1.0.0",
  "timestamp":"2026-01-28T12:00:00Z"
}
```

### 3. 监控指标

#### 3.1 基础监控

- CPU 使用率
- 内存使用率
- 磁盘使用率
- 网络流量

#### 3.2 业务监控

- 任务成功率
- 平均下载时间
- 下载失败率
- 熔断器状态

---

## 备份和恢复

### 1. 数据备份

#### 1.1 备份数据库

```bash
# 备份 SQLite 数据库
cp data/db.sqlite backup/db_$(date +%Y%m%d_%H%M%S).sqlite

# 或使用 SQLite 命令
sqlite3 data/db.sqlite ".backup backup/db_$(date +%Y%m%d_%H%M%S).sqlite"
```

#### 1.2 备份文件

```bash
# 备份所有数据
tar -czf backup_$(date +%Y%m%d_%H%M%S).tar.gz data/

# 仅备份音频文件
tar -czf audio_$(date +%Y%m%d_%H%M%S).tar.gz data/files/audio/
```

#### 1.3 自动备份脚本

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="./backups"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# 备份数据库
cp data/db.sqlite $BACKUP_DIR/db_$DATE.sqlite

# 备份配置
cp .env $BACKUP_DIR/.env.backup.$DATE

# 删除 30 天前的备份
find $BACKUP_DIR -type f -mtime +30 -delete

echo "Backup completed: $DATE"
```

```bash
# 添加到 crontab（每天凌晨 2 点备份）
crontab -e
0 2 * * * /path/to/backup.sh >> /var/log/youtube-backup.log 2>&1
```

### 2. 数据恢复

```bash
# 恢复数据库
cp backup/db_20260128_020000.sqlite data/db.sqlite

# 恢复配置
cp backup/.env.backup.20260128_020000 .env

# 重启服务
docker-compose -f docker/docker-compose.prod.yml restart
```

---

## 故障排查

### 1. 服务无法启动

#### 问题：容器启动失败

```bash
# 查看日志
docker-compose -f docker/docker-compose.prod.yml logs youtube-api

# 检查配置
docker-compose -f docker/docker-compose.prod.yml config

# 重新构建
docker-compose -f docker/docker-compose.prod.yml build --no-cache youtube-api
```

### 2. 下载失败

#### 问题：频繁出现 403 错误

**解决方案**：
1. 启用 CDP 下载器
2. 配置有效的 Cookie
3. 降低下载频率
4. 检查 IP 熔断器状态

详细排查：[故障排查](./operations/troubleshooting.md)

### 3. 磁盘空间不足

#### 问题：文件占用过多

```bash
# 检查磁盘使用
du -sh data/

# 手动清理旧文件
find data/files/ -type f -mtime +60 -delete

# 调整保留天数
FILE_RETENTION_DAYS=30
```

### 4. 性能问题

#### 问题：下载速度慢

**解决方案**：
1. 启用分片下载
2. 检查网络带宽
3. 优化间隔策略

详细调优：[性能优化](./operations/performance-tuning.md)

---

## 相关文档

- [快速开始指南](./quick-start.md)
- [配置总览](./configuration/overview.md)
- [CDP 下载器配置](./configuration/downloaders.md)
- [监控与日志](./operations/monitoring.md)
- [故障排查](./operations/troubleshooting.md)
- [性能优化](./operations/performance-tuning.md)
