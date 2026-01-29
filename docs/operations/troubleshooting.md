# 故障排查

本文档提供常见问题的解决方案和故障排查指南。

## 目录

- [服务启动问题](#服务启动问题)
- [下载失败问题](#下载失败问题)
- [性能问题](#性能问题)
- [CDP 相关问题](#cdp-相关问题)
- [网络和代理问题](#网络和代理问题)

---

## 服务启动问题

### 问题：端口被占用

**错误信息**：
```
OSError: [Errno 48] Address already in use
```

**解决方案**：

1. 更改端口：
```bash
# .env
PORT=8011
```

2. 查找并停止占用进程：

```bash
# Linux/Mac
lsof -i :8000
kill -9 <PID>

# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```

---

### 问题：依赖安装失败

**错误信息**：
```
uv sync failed: Could not resolve dependencies
```

**解决方案**：

1. 配置国内镜像（如果在中国）：
```bash
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
uv sync
```

2. 清理缓存并重试：
```bash
uv cache clean
uv sync
```

---

### 问题：服务无法启动（Docker）

**错误信息**：
```
Container exited with code 1
```

**解决方案**：

1. 查看日志：
```bash
docker-compose -f docker/docker-compose.prod.yml logs youtube-api
```

2. 检查配置：
```bash
docker-compose -f docker/docker-compose.prod.yml config
```

3. 重新构建：
```bash
docker-compose -f docker/docker-compose.prod.yml build --no-cache youtube-api
```

---

## 下载失败问题

### 问题：频繁出现 403 错误

**错误信息**：
```
HTTP 403 Forbidden
```

**可能原因**：
- YouTube 检测到自动化请求
- 本地 IP 被限制
- 下载频率过高

**解决方案**：

1. **启用 CDP 下载器**（推荐）：
```bash
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

2. **配置有效的 Cookie**：
```bash
COOKIE_FILE=./cookies.txt
```

3. **降低下载频率**：
```bash
AUDIO_INTERVAL_MIN=120
AUDIO_INTERVAL_MAX=300
```

4. **检查 IP 熔断器状态**：
```bash
# 查看日志中的 IP 熔断器状态
grep "IPBanBreaker" data/logs/app.log
```

---

### 问题：视频不存在

**错误信息**：
```
VIDEO_UNAVAILABLE: Video not found or removed
```

**解决方案**：

1. 验证视频 URL 是否正确
2. 确认视频未被删除或设为私有
3. 检查是否有地区限制

---

### 问题：下载速度慢

**可能原因**：
- 未启用分片下载
- 网络带宽限制
- YouTube 限速

**解决方案**：

1. **启用分片下载**（CDP 下载器）：
```bash
CDP_ENABLED=true
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1
CDP_MULTIPART_CHUNKS=6
```

2. **检查网络带宽**：
```bash
# 测试网速
curl -o /dev/null http://speedtest.tele2.net/100MB.zip
```

详细优化：[性能优化](./performance-tuning.md)

---

## CDP 相关问题

### 问题：CDP 无法连接到 Chrome

**错误信息**：
```
CDP connection failed: Connection refused
```

**解决方案**：

1. **检查 Chrome 是否运行**：
```bash
curl http://localhost:9222/json/version
```

2. **启动 Chrome**（如果未运行）：
```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp &

# Linux
google-chrome --remote-debugging-port=9222 &

# Windows
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

3. **检查防火墙**：
```bash
# macOS
sudo pfctl -d

# Linux
sudo ufw allow 9222/tcp

# Windows
netsh advfirewall firewall add rule name="Chrome CDP" dir=in action=allow protocol=TCP localport=9222
```

---

### 问题：熔断器频繁触发

**可能原因**：
- CDP 连接不稳定
- Chrome 崩溃
- 网络问题

**解决方案**：

1. 调整熔断器参数：
```bash
CDP_CIRCUIT_FAILURE_THRESHOLD=5  # 提高阈值
CDP_CIRCUIT_TIMEOUT=3600  # 延长恢复时间
```

2. 检查 Chrome 日志：
```
访问 chrome://inspect 查看崩溃日志
```

3. 检查网络连接：
```bash
ping 127.0.0.1
```

---

## 网络和代理问题

### 问题：无法访问 YouTube

**错误信息**：
```
NETWORK_ERROR: Connection timeout
```

**解决方案**：

1. **配置代理**（开发环境）：
```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
```

2. **测试代理连接**：
```bash
curl -x http://127.0.0.1:7890 https://www.youtube.com
```

3. **检查代理服务是否运行**：
```bash
# 常见代理服务
# - Clash: http://127.0.0.1:7890
# - Shadowsocks: http://127.0.0.1:1080
# - V2Ray: http://127.0.0.1:10809
```

---

### 问题：企业微信通知发送失败

**错误信息**：
```
Failed to send WeCom notification
```

**解决方案**：

1. **验证 Webhook URL**：
```bash
curl -X POST $WECOM_WEBHOOK_URL \
  -H "Content-Type: application/json" \
  -d '{"msgtype":"text","text":{"content":"测试消息"}}'
```

2. **检查 Webhook 是否过期**：
- 企业微信 Webhook 有效期为 90 天
- 过期后需要重新生成

3. **检查敏感词审核**：
```bash
# 如果启用了敏感词审核，检查是否被拦截
WECOM_MODERATION_ENABLED=true
```

---

## 日志分析

### 查看应用日志

```bash
# 实时查看日志
tail -f data/logs/app.log

# 查看 ERROR 级别日志
grep "ERROR" data/logs/app.log

# 查看特定任务的日志
grep "550e8400-e29b-41d4-a716-446655440000" data/logs/app.log
```

### 查看 CDP 日志

```bash
tail -f data/logs/cdp.log
```

### 查看 TikHub 日志

```bash
tail -f data/logs/tikhub.log
```

---

## 获取帮助

如果以上解决方案无法解决问题，请：

1. 收集日志文件：
```bash
tar -czf logs_$(date +%Y%m%d_%H%M%S).tar.gz data/logs/
```

2. 记录错误信息和复现步骤

3. 提交 Issue 到 GitHub

---

## 相关文档

- [配置总览](../configuration/overview.md)
- [监控与日志](./monitoring.md)
- [性能优化](./performance-tuning.md)
- [部署指南](../deployment.md)
