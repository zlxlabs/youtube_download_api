# CDP 下载器快速启动指南

> 基于真实浏览器的 YouTube 音频下载器，降低 403 风控风险

## 一、前置准备

### 1. 安装依赖

```bash
# 安装 Python 依赖
uv sync

# 安装 Playwright 浏览器（可选，仅本地测试需要）
# 注意：CDP 下载器使用外部 Chrome，不需要 Playwright 浏览器
uv run python -m playwright install chromium
```

### 2. 启动 Chrome（带 CDP）

CDP 下载器需要连接到外部运行的 Chrome 实例。

#### Windows

```powershell
# 方法 1：命令行启动
"C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir=C:\temp\chrome-cdp `
  --no-first-run `
  --no-default-browser-check

# 方法 2：PowerShell 后台启动
Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  -ArgumentList "--remote-debugging-port=9222","--user-data-dir=C:\temp\chrome-cdp"
```

#### Mac

```bash
# 方法 1：命令行启动
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check &

# 方法 2：创建启动脚本
cat > ~/start_chrome_cdp.sh << 'EOF'
#!/bin/bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp &
EOF
chmod +x ~/start_chrome_cdp.sh
~/start_chrome_cdp.sh
```

#### Linux

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check &
```

### 3. 验证 CDP 可用

```bash
# 访问 CDP 状态页面
curl http://localhost:9222/json/version

# 应该返回类似：
# {
#   "Browser": "Chrome/120.0.6099.109",
#   "Protocol-Version": "1.3",
#   "User-Agent": "Mozilla/5.0...",
#   "V8-Version": "12.0.267.8",
#   "WebKit-Version": "537.36",
#   "webSocketDebuggerUrl": "ws://localhost:9222/devtools/browser/..."
# }
```

## 二、配置

### 1. 创建配置文件

```bash
# 复制示例配置
cp .env.example .env

# 编辑配置
# Windows: notepad .env
# Mac/Linux: nano .env
```

### 2. 启用 CDP 下载器

在 `.env` 文件中添加/修改以下配置：

```bash
# 启用 CDP 下载器
CDP_ENABLED=true

# CDP 端点（本地）
CDP_URLS=http://127.0.0.1:9222

# 或者远程 Chrome（如 Mac Mini）
# CDP_URLS=http://192.168.1.100:9222

# 音频下载优先级（CDP 优先）
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

### 3. 其他可选配置

```bash
# 连接超时（秒）
CDP_TIMEOUT=30

# 使用 curl_cffi TLS 指纹模拟（推荐）
CDP_USE_CURL_CFFI=true

# 启用 poToken（可选，双重保障）
CDP_ENABLE_POT_TOKEN=false

# 熔断器阈值
CDP_CIRCUIT_FAILURE_THRESHOLD=3
CDP_CIRCUIT_TIMEOUT=1800
```

## 三、运行测试

### 方法 1：手动测试脚本

```bash
# 运行测试
python tests/test_cdp_manual.py

# 指定测试视频
TEST_VIDEO_URL="https://www.youtube.com/watch?v=0kARDVL2nZg" python tests/test_cdp_manual.py
```

### 方法 2：启动完整服务

```bash
# Windows
.\scripts\dev.ps1

# Mac/Linux
./scripts/dev.sh
```

然后通过 API 测试：

```bash
# 创建下载任务
curl -X POST http://localhost:8011/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=0kARDVL2nZg",
    "include_audio": true,
    "include_transcript": false
  }'

# 查看任务状态
curl http://localhost:8011/api/v1/tasks/{task_id} \
  -H "X-API-Key: your-api-key"
```

## 四、故障排查

### 问题 1：CDP 连接失败

**症状**：`CDP_CONNECTION_FAILED`

**解决方案**：

1. 确认 Chrome 正在运行：
   ```bash
   curl http://localhost:9222/json/version
   ```

2. 检查端口是否被占用：
   ```bash
   # Windows
   netstat -ano | findstr :9222

   # Mac/Linux
   lsof -i :9222
   ```

3. 检查防火墙设置（如果使用远程 Chrome）

### 问题 2：Playwright 未安装

**症状**：`Playwright not installed`

**解决方案**：

```bash
# 安装 Playwright
pip install playwright

# 或使用 uv
uv add playwright
```

### 问题 3：下载 403 错误

**症状**：`CDP_DOWNLOAD_403`

**可能原因**：

1. **本地 IP 被封禁**：YouTube 检测到自动化下载
2. **Cookie 过期**：Chrome 未登录或登录已过期

**解决方案**：

1. 在 Chrome 中登录 YouTube 账号
2. 访问 `https://www.youtube.com/robots.txt` 刷新 session
3. 等待 IP 熔断器恢复（默认 30 分钟）
4. 降级到 ytdlp 或 tikhub

### 问题 4：下载速度慢

**可能原因**：httpx 下载性能问题

**解决方案**：

1. 启用 curl_cffi（默认已启用）：
   ```bash
   CDP_USE_CURL_CFFI=true
   ```

2. 检查网络连接
3. 使用代理（如果在国内）

## 五、多实例故障转移

### 配置多个 CDP 实例

```bash
# 启动多个 Chrome 实例（不同端口）
# 实例 1
chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp-1 &

# 实例 2
chrome --remote-debugging-port=9223 --user-data-dir=/tmp/chrome-cdp-2 &

# 配置多实例
CDP_URLS=http://127.0.0.1:9222,http://127.0.0.1:9223

# 故障转移策略
CDP_FAILOVER_STRATEGY=sequential  # 或 random
```

### 工作原理

1. 按顺序（或随机）尝试连接各个实例
2. 跳过熔断器打开的实例
3. 连接成功后复用连接
4. 所有实例失败时降级到 ytdlp

## 六、监控与告警

### 查看熔断器状态

```bash
# 查看日志
tail -f data/logs/app.log | grep -i "circuit breaker"

# 应该看到类似输出：
# [cdp] Circuit breaker CLOSED
# [cdp] Circuit breaker OPEN (failures: 3)
# [cdp] Circuit breaker entering HALF_OPEN state
```

### 企微通知

CDP 下载器会在以下情况发送企微通知：

1. **连接失败**：无法连接到 Chrome
2. **熔断器打开**：连续失败达到阈值
3. **熔断器恢复**：从 OPEN 状态恢复

通知频率限制：同一错误 1 小时内只通知一次。

## 七、性能优化建议

### 1. Cookie 管理

- **推荐**：在 Chrome 中保持 YouTube 登录状态
- **目的**：每次下载自动刷新 session，降低 403 风险

### 2. 并发控制

当前 CDP 下载器设计为单并发（`DOWNLOAD_CONCURRENCY=1`），原因：

- 避免触发 YouTube 风控
- 共享 Browser 连接，减少资源占用

如需提高并发，建议部署多个服务实例而非增加单实例并发。

### 3. 间隔策略

CDP 下载器会遵循系统的任务间隔配置：

```bash
# 音频任务间隔（推荐保守配置）
AUDIO_INTERVAL_MIN=60
AUDIO_INTERVAL_MAX=600
```

## 八、常见使用场景

### 场景 1：本地开发测试

```bash
# 1. 启动本地 Chrome
chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp &

# 2. 配置
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222

# 3. 运行测试
python tests/test_cdp_manual.py
```

### 场景 2：生产环境（Mac Mini 作为 CDP Server）

```bash
# Mac Mini 上启动 Chrome
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp &

# 服务器配置（假设 Mac Mini IP: 192.168.1.100）
CDP_ENABLED=true
CDP_URLS=http://192.168.1.100:9222
```

### 场景 3：高可用配置（主备 CDP）

```bash
# 主 CDP（Mac Mini 1）
CDP_URLS=http://192.168.1.100:9222,http://192.168.1.101:9222

# 故障转移策略
CDP_FAILOVER_STRATEGY=sequential
```

## 九、安全注意事项

1. **Cookie 隐私**：CDP 会导出 YouTube cookies，包含登录信息
2. **临时文件清理**：cookies 临时文件会在下载完成后自动删除
3. **网络隔离**：生产环境建议使用内网 IP 访问 CDP
4. **账号风险**：频繁下载可能导致账号被 YouTube 临时封禁

## 十、总结

CDP 下载器通过以下技术降低 403 风控：

✅ **真实浏览器 Cookies**（每次刷新）
✅ **CDP 捕获的真实 Headers**
✅ **curl_cffi TLS 指纹模拟**
✅ **可选 poToken 支持**

预期效果：**降低 403 错误率 60-80%**

如有问题，请查看日志或提交 Issue。
