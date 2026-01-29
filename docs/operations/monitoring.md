# 监控与日志

本文档介绍系统的监控配置、日志管理和企业微信通知。

## 目录

- [日志系统](#日志系统)
- [企业微信通知](#企业微信通知)
- [健康检查](#健康检查)
- [监控指标](#监控指标)

---

## 日志系统

### 日志级别

| 级别 | 说明 | 用途 |
|------|------|------|
| DEBUG | 详细的调试信息 | 开发环境 |
| INFO | 一般信息 | 正常运行状态 |
| WARNING | 警告信息 | 潜在问题 |
| ERROR | 错误信息 | 错误和异常 |
| CRITICAL | 严重错误 | 系统故障 |

### 配置日志级别

```bash
# .env

# 开发环境（详细日志）
DEBUG=true

# 生产环境（仅 INFO 及以上）
DEBUG=false
```

### 日志文件位置

```
data/
├── logs/
│   ├── app.log              # 应用日志
│   ├── error.log            # 错误日志
│   ├── cdp.log              # CDP 下载器日志
│   └── tikhub.log           # TikHub 下载器日志
```

### 日志格式

```
[2026-01-28 12:00:00] [INFO] [task_service] Created task: 550e8400-e29b-41d4-a716-446655440000
[2026-01-28 12:00:05] [INFO] [worker] Started task: 550e8400-e29b-41d4-a716-446655440000
[2026-01-28 12:01:30] [INFO] [worker] Completed task: 550e8400-e29b-41d4-a716-446655440000
```

### 日志轮转

使用 logrotate 进行日志轮转：

```bash
# /etc/logrotate.d/youtube-api
/path/to/logs/*.log {
    daily
    rotate 7
    compress
    missingok
    notifempty
    create 0644 www-data www-data
}
```

---

## 企业微信通知

### 配置 Webhook URL

```bash
# .env
WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

### 获取 Webhook URL

1. 进入企业微信群
2. 群设置 → 群机器人 → 添加机器人
3. 复制 Webhook 地址

### 通知内容

系统会发送以下通知：

| 通知类型 | 触发条件 | 接收人 |
|---------|---------|--------|
| 任务完成 | 任务成功完成 | 配置的群 |
| 任务失败 | 任务失败（已重试） | 配置的群 |
| CDP 连接失败 | CDP 无法连接到 Chrome | 配置的群 |
| 熔断器触发 | 下载器熔断 | @ 所有人 |
| 熔断器恢复 | 熔断器恢复正常 | 配置的群 |

### 敏感词审核

系统支持敏感词审核功能，自动处理消息中的敏感内容。

**配置**：
```bash
# 启用敏感词审核
WECOM_MODERATION_ENABLED=true

# 敏感词库 URL（支持多个，逗号分隔）
WECOM_MODERATION_URLS=https://example.com/words1.txt,https://example.com/words2.txt

# 审核策略
WECOM_MODERATION_STRATEGY=pinyin_reverse
```

**审核策略**：

| 策略 | 说明 |
|------|------|
| `block` | 检测到敏感词时拒绝发送，发送告警消息 |
| `replace` | 将敏感词替换为 `[敏感词]` |
| `pinyin_reverse` | 将敏感词转换为拼音混淆形式（默认） |

---

## 健康检查

### 健康检查端点

**接口**：`GET /health`

**请求示例**：
```bash
curl http://localhost:8000/health
```

**响应示例**：
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2026-01-28T12:00:00Z"
}
```

### 健康检查配置（Docker）

```yaml
# docker-compose.prod.yml
services:
  youtube-api:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

---

## 监控指标

### 基础监控

| 指标 | 说明 | 建议阈值 |
|------|------|----------|
| CPU 使用率 | CPU 占用 | < 80% |
| 内存使用率 | 内存占用 | < 80% |
| 磁盘使用率 | 磁盘占用 | < 80% |
| 网络流量 | 上传/下载流量 | 监控异常 |

### 业务监控

| 指标 | 说明 | 建议阈值 |
|------|------|----------|
| 任务成功率 | 成功任务数 / 总任务数 | > 95% |
| 平均下载时间 | 任务完成平均时间 | 监控异常增长 |
| 下载失败率 | 失败任务数 / 总任务数 | < 5% |
| 熔断器状态 | 各下载器熔断状态 | 监控频繁熔断 |

---

## 相关文档

- [配置总览](../configuration/overview.md)
- [故障排查](./troubleshooting.md)
- [部署指南](../deployment.md)
