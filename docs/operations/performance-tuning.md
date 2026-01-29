# 性能优化

本文档介绍如何优化系统性能，提高下载速度和稳定性。

## 目录

- [下载速度优化](#下载速度优化)
- [并发优化](#并发优化)
- [缓存优化](#缓存优化)
- [资源优化](#资源优化)

---

## 下载速度优化

### 启用 CDP 下载器（推荐）

CDP 下载器通过真实浏览器指纹和分片下载，可提升 5-10 倍下载速度。

**配置**：
```bash
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1
CDP_MULTIPART_CHUNKS=6
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

**效果对比**：

| 场景 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| 3-8MB 音频 | 单线程，60-180 秒 | 分片下载，8-15 秒 | **5-10 倍** |
| 10-30MB 音频 | 分片下载，15-30 秒 | 分片下载，15-30 秒 | 无变化 |

详细配置：[CDP 下载器配置](../configuration/downloaders.md)

---

### 分片下载优化

**关键认知**：YouTube 对不同连接模式采用不同的限速策略

```
单连接下载（单线程）
  → YouTube 识别为"批量下载器/爬虫"
  → 严格限速：100-200 KB/s

多连接下载（分片）
  → YouTube 识别为"正常视频播放器"
  → 放宽限制：1-2 MB/s
```

**推荐配置**：
```bash
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1  # 从 10MB 降至 1MB
CDP_MULTIPART_CHUNKS=6    # 推荐 4-8 个分片
```

---

### 优化间隔策略

根据实际需求调整下载间隔，平衡速度和风控风险。

**激进策略（高并发）**：
```bash
TRANSCRIPT_INTERVAL_MIN=10
TRANSCRIPT_INTERVAL_MAX=20
AUDIO_INTERVAL_MIN=30
AUDIO_INTERVAL_MAX=120
```

**保守策略（低风控）**：
```bash
TRANSCRIPT_INTERVAL_MIN=30
TRANSCRIPT_INTERVAL_MAX=60
AUDIO_INTERVAL_MIN=120
AUDIO_INTERVAL_MAX=600
```

---

## 并发优化

### 当前限制

当前版本为单线程处理，预留并发控制配置：

```bash
# 未来功能
DOWNLOAD_CONCURRENCY=1
```

---

## 缓存优化

### 元数据缓存

元数据永久缓存在数据库中，重复查询无需网络请求。

**性能提升**：
- 首次查询：1-2 秒
- 缓存命中：5-10 毫秒（提升 200-400 倍）

### 文件缓存

文件级缓存策略，同一视频的资源可跨任务复用。

**性能提升**：
- 首次下载：30-120 秒
- 缓存命中：< 1 秒（即时返回）

---

## 资源优化

### Docker 资源限制

合理配置 Docker 容器资源限制，避免资源争用。

**配置示例**：
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

### 磁盘空间优化

定期清理旧文件，避免磁盘空间不足。

**配置**：
```bash
FILE_RETENTION_DAYS=60
```

**手动清理**：
```bash
# 清理 60 天前的文件
find data/files/ -type f -mtime +60 -delete
```

---

## 相关文档

- [CDP 下载器配置](../configuration/downloaders.md)
- [配置总览](../configuration/overview.md)
- [监控与日志](./monitoring.md)
