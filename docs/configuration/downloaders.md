# 下载器配置

本文档详细介绍系统支持的下载器及其配置方式。

## 目录

- [下载器概览](#下载器概览)
- [CDP 下载器](#cdp-下载器)
- [yt-dlp 下载器](#yt-dlp-下载器)
- [TikHub 下载器](#tikhub-下载器)
- [下载器优先级](#下载器优先级)
- [熔断器配置](#熔断器配置)
- [配置示例](#配置示例)

---

## 下载器概览

### 支持的下载器

| 下载器 | 说明 | 优点 | 缺点 | 成本 | 适用场景 |
|--------|------|------|------|------|----------|
| **CDP** | Chrome DevTools Protocol | 真实浏览器指纹、降低 403 风险 | 需要外部 Chrome | 免费 | 高频下载、风控敏感环境 |
| **yt-dlp** | 本地 yt-dlp 库 | 免费、功能强大 | 可能遇到 YouTube 限流 | 免费 | 个人项目、低频下载 |
| **TikHub** | TikHub API 服务 | 稳定、不受限流影响 | 需要 API key | 0.002$/次 | 商业项目、高稳定性要求 |

### 工作原理

```
请求下载
  │
  ├─> 1. 尝试 CDP（如果启用且优先）
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

- **403 错误优化**：当任意下载器报 HTTP 403 错误时，说明本地网络 IP 有问题，此时系统会立即停止尝试其他下载器
- **其他错误降级**：对于网络超时、连接失败等临时性错误，系统会继续尝试下一个下载器

---

## CDP 下载器

### 核心优势

- **真实浏览器指纹**：使用外部 Chrome 获取真实 Cookies 和 Headers
- **TLS 指纹模拟**：curl_cffi 模拟浏览器 TLS 握手
- **降低 403 风险**：预期降低 403 错误率 60-80%
- **多实例故障转移**：支持多个 Chrome 实例，自动切换
- **熔断器保护**：智能熔断机制，避免持续性故障
- **分片多线程下载**：可选的分片并发下载（推荐大文件）

### 快速开始

#### 1. 启动外部 Chrome

**macOS**：
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check &
```

**Linux**：
```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp \
  --no-first-run \
  --no-default-browser-check &
```

**Windows**：
```powershell
"C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir=%TEMP%\chrome-cdp `
  --no-first-run `
  --no-default-browser-check
```

**验证 CDP 可用**：
```bash
curl http://localhost:9222/json/version
```

#### 2. 配置环境变量

```bash
# .env
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222

# 启用分片下载（强烈推荐）
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1  # 推荐：1MB（让小文件也享受 5-10 倍提速）
CDP_MULTIPART_CHUNKS=6

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
| `CDP_ENABLE_POT_TOKEN` | `false` | 启用 poToken 支持（可选） |
| `CDP_ENABLE_MULTIPART` | `false` | 启用分片多线程下载 |
| `CDP_TRANSCODE_TO_M4A` | `false` | 转码为 m4a 格式（关闭时保留原始格式如 webm，节省转码时间） |

#### 分片下载配置

| 参数 | 默认值 | 推荐值 | 说明 |
|------|--------|--------|------|
| `CDP_MULTIPART_CHUNKS` | `6` | `6` | 分片数量（推荐 4-8） |
| `CDP_MULTIPART_MIN_SIZE` | `10` | `1` ⭐ | 最小文件阈值（MB）。**强烈推荐设为 1**，让 3-8MB 音频享受分片加速（5-10 倍提速） |

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
| `CDP_NOTIFY_COOLDOWN` | `3600` | 通知冷却时间（秒，1小时） |

#### 人类行为模拟配置

模拟真实人类浏览行为，降低 YouTube 风控概率。后台任务异步执行，不阻塞下载流程。

**⚠️ 重要**：人类行为模拟要求 `DOWNLOAD_CONCURRENCY=1`（单并发），否则多任务会互相干扰。

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_HUMAN_BEHAVIOR_ENABLED` | `true` | 启用人类行为模拟（生产环境推荐） |
| `CDP_QUICK_MODE` | `false` | 快速模式：跳过人类行为模拟（仅测试用） |

**视频播放时长控制**（智能防止持续播放）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_MIN_PLAY_DURATION` | `30` | 最小播放时长（秒），避免短视频播放过短 |
| `CDP_MAX_PLAY_DURATION` | `600` | 最大播放时长（秒，10分钟），限制长视频播放 |
| `CDP_PLAY_RATIO_MIN` | `0.2` | 最小播放比例（20%），基于视频总时长 |
| `CDP_PLAY_RATIO_MAX` | `0.4` | 最大播放比例（40%），基于视频总时长 |

**工作原理**：
- 获取视频总时长，计算播放时长 = min(max(视频时长 × 随机比例, 最小时长), 最大时长)
- 例：10分钟视频 → 播放 2-4 分钟（20%-40%）
- 例：30秒短视频 → 播放 30 秒（应用最小时长）
- 例：2小时长视频 → 播放 10 分钟（应用最大时长）
- 播放完成后，如果是最后一个 Page，自动暂停视频，避免持续播放

**默认行为模拟**（无视频时长时降级使用）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_WATCH_DURATION_MIN` | `20` | 最小观看时长（秒），直播或时长获取失败时使用 |
| `CDP_WATCH_DURATION_MAX` | `40` | 最大观看时长（秒），直播或时长获取失败时使用 |
| `CDP_PAGE_ALIVE_MIN` | `30` | 暂停后最小存活时长（秒） |
| `CDP_PAGE_ALIVE_MAX` | `60` | 暂停后最大存活时长（秒） |

**行为概率**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CDP_SCROLL_PROBABILITY` | `0.8` | 滚动页面概率（0.8 = 80%） |
| `CDP_PAUSE_PROBABILITY` | `0.2` | 暂停/恢复视频概率（0.2 = 20%，观看期间） |

### 配置示例

#### 示例 1：基础配置（本地开发）

```bash
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222
CDP_USE_CURL_CFFI=true
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

#### 示例 2：生产环境（多实例 + 分片下载）推荐配置

```bash
CDP_ENABLED=true
CDP_URLS=http://192.168.1.100:9222,http://192.168.1.101:9222
CDP_FAILOVER_STRATEGY=sequential
CDP_USE_CURL_CFFI=true
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1  # 推荐：降低阈值，让小文件也享受分片加速
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
CDP_TRANSCODE_TO_M4A=false  # 保留原始格式（webm），节省转码时间

# 分片配置（推荐）
CDP_MULTIPART_CHUNKS=6
CDP_MULTIPART_MIN_SIZE=1

# 熔断器
CDP_CIRCUIT_FAILURE_THRESHOLD=3
CDP_CIRCUIT_TIMEOUT=1800
CDP_CIRCUIT_HALF_OPEN_SUCCESS=2

# 健康检查
CDP_HEALTH_CHECK_INTERVAL=300
CDP_CONNECTION_RETRY=3
CDP_NOTIFY_COOLDOWN=3600

# 人类行为模拟（推荐启用，降低风控）
CDP_HUMAN_BEHAVIOR_ENABLED=true
CDP_QUICK_MODE=false
# 视频播放时长控制
CDP_MIN_PLAY_DURATION=30
CDP_MAX_PLAY_DURATION=600
CDP_PLAY_RATIO_MIN=0.2
CDP_PLAY_RATIO_MAX=0.4
# 默认行为模拟
CDP_WATCH_DURATION_MIN=20
CDP_WATCH_DURATION_MAX=40
CDP_PAGE_ALIVE_MIN=30
CDP_PAGE_ALIVE_MAX=60
# 行为概率
CDP_SCROLL_PROBABILITY=0.8
CDP_PAUSE_PROBABILITY=0.2

# 下载器优先级
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
# 重要：人类行为模拟要求单并发
DOWNLOAD_CONCURRENCY=1
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

### 性能优化

#### 关键认知：为什么大视频反而下载更快？

在实际测试中发现了一个反直觉的现象：

| 文件大小 | 下载方式 | 耗时 | 速度 |
|---------|---------|------|------|
| **27.42 MB** | 分片下载（6 chunks） | 14 秒 | ~2 MB/s |
| **< 10 MB** | 单线程下载 | 176 秒 | ~0.06 MB/s |

**核心原因：YouTube 的反爬策略**

YouTube 对不同连接模式采用不同的限速策略：

```
单连接下载（单线程）
  → YouTube 识别为"批量下载器/爬虫"
  → 严格限速：100-200 KB/s
  → 小文件也很慢

多连接下载（分片）
  → YouTube 识别为"正常视频播放器"（DASH/HLS）
  → 放宽限制：1-2 MB/s
  → 大文件反而快
```

**推荐配置：降低分片阈值**

**问题**：默认配置 `CDP_MULTIPART_MIN_SIZE=10`（10MB）导致 3-8MB 的音频文件使用单线程下载，触发严格限速。

**解决方案**：

```bash
# .env
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1  # 从 10MB 降至 1MB（推荐）
CDP_MULTIPART_CHUNKS=6    # 保持 6 个分片
```

**效果对比**：

| 场景 | 优化前（10MB 阈值） | 优化后（1MB 阈值） | 提升 |
|------|------------------|------------------|------|
| 3-8MB 音频 | 单线程，60-180 秒 | 分片下载，8-15 秒 | **5-10 倍** |
| 10-30MB 音频 | 分片下载，15-30 秒 | 分片下载，15-30 秒 | 无变化 |
| 极小文件（< 1MB） | 单线程，5-10 秒 | 单线程，5-10 秒 | 无变化 |

### 故障排查

#### 连接失败

```bash
# 1. 检查 Chrome 是否运行
curl http://localhost:9222/json/version

# 2. 检查防火墙
# macOS
sudo pfctl -d

# Linux
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

---

## yt-dlp 下载器

### 简介

yt-dlp 是一个强大的 YouTube 下载工具，支持多种视频网站。

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `COOKIE_FILE` | 无 | Cookie 文件路径（可选） |
| `AUDIO_QUALITY` | `128` | 音频比特率 (kbps) |

### 配置示例

```bash
# 仅使用 yt-dlp（免费）
AUDIO_DOWNLOAD_PRIORITY=ytdlp

# 使用 Cookie 提高成功率
COOKIE_FILE=./cookies.txt
```

### Cookie 配置

推荐使用浏览器扩展导出 Cookies：

1. **使用浏览器扩展**
   - 推荐扩展：[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldkbehn)
   - 访问 YouTube 并登录
   - 导出 `youtube.com` 的 cookies 到文件

2. **使用无痕模式（推荐）**
   - 打开新的无痕窗口并登录 YouTube
   - 在同一标签页访问 `https://www.youtube.com/robots.txt`
   - 导出 cookies，然后关闭无痕窗口

**注意事项**：
- 使用账户的 cookies 可能导致账户被临时或永久封禁
- 请谨慎使用下载频率和数量
- 仅在必要时使用，或使用临时账户

详细配置：[高级配置 - Cookie 管理](./advanced.md#cookie-管理)

---

## TikHub 下载器

### 简介

TikHub 是一个提供 YouTube 下载 API 的服务，稳定且不受限流影响。

### 配置参数

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `TIKHUB_API_KEY` | 是 | 无 | TikHub API 密钥 |
| `TIKHUB_CACHE_TTL_HOURS` | 否 | `3` | TikHub 缓存时间（小时） |

### 获取 API Key

1. 访问 [TikHub 官网](https://tikhub.io/)
2. 注册账号并登录
3. 获取 API Key
4. 配额：10,000 units/天

### 成本计算

- **单次下载**：~$0.002
- **月成本（每天30个视频）**：~$2-3

### 配置示例

```bash
# 仅使用 TikHub（最稳定）
TIKHUB_API_KEY=your-api-key-here
AUDIO_DOWNLOAD_PRIORITY=tikhub

# yt-dlp + TikHub 双重保障（推荐）
TIKHUB_API_KEY=your-api-key-here
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub
```

---

## 下载器优先级

### 配置方式

使用逗号分隔的列表指定下载器优先级：

```bash
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

### 场景化配置

#### 场景 1：成本优先（推荐个人项目）

```bash
# 仅使用 ytdlp
AUDIO_DOWNLOAD_PRIORITY=ytdlp
TIKHUB_API_KEY=

# 或 yt-dlp + TikHub 双重保障
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub
TIKHUB_API_KEY=your-api-key-here
```

**成本分析**（每天30个视频）：
```
- 元数据：24个 ytdlp 成功（$0） + 6个 tikhub（$0.012）
- 字幕任务：3个 × $0.002 = $0.006
- 音频任务：大部分 ytdlp 成功（$0）
- 总成本：~$0.02/天 = $0.6/月
```

#### 场景 2：稳定性优先（推荐商业项目）

```bash
# 全部优先 TikHub（最稳定）
TIKHUB_API_KEY=your-api-key-here
AUDIO_DOWNLOAD_PRIORITY=tikhub,ytdlp
```

**成本分析**（每天30个视频）：
```
- 所有操作优先 TikHub
- 总成本：~$2-3/月
- 优势：最高成功率，最少用户等待
```

#### 场景 3：完全免费

```bash
# 仅使用 ytdlp
AUDIO_DOWNLOAD_PRIORITY=ytdlp
TIKHUB_API_KEY=
```

#### 场景 4：CDP 优先（推荐高频下载）

```bash
CDP_ENABLED=true
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

**优势**：
- 真实浏览器指纹，降低 403 风险
- 分片下载，速度提升 5-10 倍
- 免费使用

---

## 熔断器配置

### 下载器熔断器

系统采用熔断器模式保护下载器，避免持续性故障影响服务。

#### 工作原理

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

#### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CIRCUIT_BREAKER_ENABLED` | `true` | 启用熔断器 |
| `CIRCUIT_BREAKER_THRESHOLD` | `5` | 熔断器失败阈值 |
| `CIRCUIT_BREAKER_TIMEOUT` | `1800` | 熔断器超时（秒，30分钟） |
| `CIRCUIT_BREAKER_HALF_OPEN_CALLS` | `3` | 半开状态最大调用次数 |

#### 实际效果

```
场景：yt-dlp 遇到 YouTube 限流

时刻 10:00 - yt-dlp 连续失败 5 次
           → 熔断器开启

时刻 10:00-10:30 - 所有任务直接使用 TikHub
                 → 跳过 yt-dlp，节省时间

时刻 10:30 - 熔断器恢复，重新尝试 yt-dlp
```

详细配置：[熔断器配置](./circuit-breakers.md)

---

## 配置示例

### 示例 1：仅使用 yt-dlp（免费）

```bash
AUDIO_DOWNLOAD_PRIORITY=ytdlp
TIKHUB_API_KEY=
```

### 示例 2：yt-dlp + TikHub 双重保障（推荐）

```bash
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub
TIKHUB_API_KEY=your-api-key-here
TIKHUB_CACHE_TTL_HOURS=3
```

### 示例 3：仅使用 TikHub（最稳定）

```bash
AUDIO_DOWNLOAD_PRIORITY=tikhub
TIKHUB_API_KEY=your-api-key-here
```

### 示例 4：CDP 优先（推荐高频下载）

```bash
CDP_ENABLED=true
CDP_URLS=http://127.0.0.1:9222
CDP_ENABLE_MULTIPART=true
CDP_MULTIPART_MIN_SIZE=1
CDP_MULTIPART_CHUNKS=6
AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub
```

### 示例 5：自定义熔断器参数

```bash
# 更激进的熔断策略（适合频繁限流的环境）
CIRCUIT_BREAKER_THRESHOLD=3
CIRCUIT_BREAKER_TIMEOUT=900

# 更保守的熔断策略（适合偶尔限流的环境）
CIRCUIT_BREAKER_THRESHOLD=10
CIRCUIT_BREAKER_TIMEOUT=3600
```

---

## 相关文档

- [配置总览](./overview.md)
- [熔断器配置](./circuit-breakers.md)
- [高级配置](./advanced.md)
- [CDP 下载器设计文档](../architecture/downloader-architecture.md)
- [性能优化](../operations/performance-tuning.md)
