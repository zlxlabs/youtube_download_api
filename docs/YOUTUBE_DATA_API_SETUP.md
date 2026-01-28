# YouTube Data API v3 集成说明

## 功能概述

YouTube Data API v3 下载器已集成到系统中，作为官方稳定的元数据获取方式。

### 特性

- ✅ **仅用于元数据获取**：获取视频标题、作者、时长、描述、缩略图等基础信息
- ✅ **官方 API，稳定性高**：不受 YouTube 爬虫限流影响
- ✅ **速度快**：通常 < 1 秒完成元数据获取
- ✅ **与现有架构无缝集成**：加入优先级队列，支持自动降级
- ✅ **复用缓存机制**：与其他下载器共享数据库元数据缓存

### 限制

- ❌ **不支持资源下载**：仅提供元数据，不提供音频/字幕下载链接
- ⚠️ **配额限制**：每日 10,000 units，`videos.list` 消耗 1 unit
- 🔑 **需要 API Key**：需要在 Google Cloud Console 申请

---

## 申请 YouTube Data API Key

### 步骤 1：创建 Google Cloud 项目

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 登录您的 Google 账号
3. 点击顶部导航栏的项目下拉菜单
4. 点击 **"新建项目"**
5. 输入项目名称（如 `youtube-audio-api`）
6. 点击 **"创建"**

### 步骤 2：启用 YouTube Data API v3

1. 在左侧菜单中选择 **"API 和服务"** → **"库"**
2. 搜索 **"YouTube Data API v3"**
3. 点击搜索结果中的 **"YouTube Data API v3"**
4. 点击 **"启用"** 按钮

### 步骤 3：创建 API 凭据

1. 在左侧菜单中选择 **"API 和服务"** → **"凭据"**
2. 点击顶部的 **"创建凭据"** → **"API 密钥"**
3. 复制生成的 API 密钥
4. （推荐）点击 **"限制密钥"**：
   - **应用限制**：选择 "IP 地址"，添加您的服务器 IP
   - **API 限制**：选择 "限制密钥"，勾选 "YouTube Data API v3"
5. 点击 **"保存"**

### 步骤 4：配置 API Key

在项目的 `.env` 文件中添加：

```bash
YOUTUBE_DATA_API_KEY=AIzaSy...（您的 API 密钥）
```

---

## 配置示例

### 场景 1：元数据优先使用官方 API（推荐）

```bash
# 元数据优先级：YouTube Data API → yt-dlp → TikHub
METADATA_PRIORITY=youtube_data_api,ytdlp,tikhub

# 仅字幕：TikHub 更稳定
TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp

# 音频下载：优先免费
AUDIO_DOWNLOAD_PRIORITY=ytdlp,tikhub
```

**优势**：
- 元数据获取最稳定（官方 API）
- 配额消耗可控（仅元数据）
- 资源下载仍使用免费方案

**成本**：
- 元数据：免费（配额内）
- 资源下载：主要依赖 ytdlp（免费）

---

### 场景 2：纯免费方案（不使用 YouTube Data API）

```bash
# 不配置 API Key
YOUTUBE_DATA_API_KEY=

# 元数据优先级：仅使用 ytdlp
METADATA_PRIORITY=ytdlp
```

**优势**：
- 完全免费
- 无配额限制

**劣势**：
- 可能遇到 YouTube 限流
- 稳定性稍差

---

### 场景 3：最大稳定性（商业项目）

```bash
# 所有操作优先官方/付费 API
METADATA_PRIORITY=youtube_data_api,tikhub,ytdlp
TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp
AUDIO_DOWNLOAD_PRIORITY=tikhub,ytdlp
```

**优势**：
- 最高稳定性
- 最少用户等待时间

**成本**：
- 每天 30 个视频约 $0.06/天

---

## 配额管理

### 配额计算

YouTube Data API v3 默认配额：**10,000 units/天**

| 操作 | 配额消耗 | 每日上限 |
|------|----------|----------|
| `videos.list` | 1 unit | 10,000 次 |

### 配额监控

1. 访问 [Google Cloud Console - API 配额页面](https://console.cloud.google.com/apis/api/youtube.googleapis.com/quotas)
2. 查看当日已使用配额
3. 如需增加配额，点击 **"请求配额增加"**

### 配额保护策略

系统已实现自动配额保护：

1. **自动降级**：YouTube Data API 配额超限时，自动降级到 ytdlp
2. **熔断器保护**：连续失败时自动熔断，避免持续消耗配额
3. **数据库缓存**：元数据永久缓存，避免重复 API 调用

---

## 测试验证

### 验证 API Key 是否有效

```bash
# 1. 确保已配置 API Key
grep YOUTUBE_DATA_API_KEY .env

# 2. 启动服务
uv run uvicorn src.main:app --host 127.0.0.1 --port 8011

# 3. 创建测试任务（观察日志）
curl -X POST http://localhost:8011/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "include_audio": false,
    "include_transcript": true
  }'
```

### 预期日志

如果配置正确，日志中应出现：

```
[INFO] DownloaderManager initialized with 3 downloader(s): ['youtube_data_api', 'ytdlp', 'tikhub']
[INFO]   ✓ youtube_data_api enabled (API key configured)
[INFO] [youtube_data_api] Fetching metadata for dQw4w9WgXcQ
[INFO] [youtube_data_api] ✓ Metadata fetched: dQw4w9WgXcQ (title: Rick Astley - Never Gonna Give You Up)
```

### 常见错误

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `accessNotConfigured` | YouTube Data API 未启用 | 在 Google Cloud Console 启用 API |
| `quotaExceeded` | 配额已用尽 | 等待配额重置（每日 0:00 UTC）或申请增加配额 |
| `keyInvalid` | API Key 无效 | 检查 API Key 是否正确复制 |
| `ipDenied` | IP 地址被限制 | 检查 API Key 的 IP 限制配置 |

---

## 与现有架构的集成

### 工作流程

```
请求下载任务
  ↓
获取元数据
  ├─ 检查数据库缓存 → 命中则直接返回（5ms）
  ├─ 未命中 → 按优先级调用下载器
  │   ├─ youtube_data_api（1秒，官方）
  │   ├─ ytdlp（1-2秒，免费）
  │   └─ tikhub（0.5秒，$0.002）
  └─ 写入数据库（永久缓存）
  ↓
下载资源（音频/字幕）
  ├─ ytdlp（免费）
  └─ tikhub（$0.002）
```

### 关键特性

1. **元数据缓存复用**：所有下载器共享数据库缓存，避免重复调用
2. **并发锁保护**：同一视频的并发请求使用锁机制，防止重复 API 调用
3. **自动降级**：任意下载器失败时自动切换下一个
4. **熔断器保护**：连续失败时自动熔断，保护系统稳定性

---

## 常见问题

### Q1: YouTube Data API 会影响下载速度吗？

**A**: 不会。元数据获取和资源下载是分离的：
- 元数据获取（< 1 秒）：可使用 YouTube Data API
- 资源下载（30-60 秒）：仍使用 ytdlp/tikhub

### Q2: 配额用完了怎么办？

**A**: 系统会自动降级到 ytdlp/tikhub，不会影响服务可用性。

### Q3: 是否需要同时配置 TikHub API？

**A**: 不是必须的。推荐配置方案：
- **个人项目**：YouTube Data API + ytdlp（免费）
- **商业项目**：YouTube Data API + ytdlp + TikHub（稳定）

### Q4: 如何监控 API 使用情况？

**A**:
1. 访问 [Google Cloud Console - API 指标](https://console.cloud.google.com/apis/dashboard)
2. 查看 YouTube Data API v3 的调用次数和错误率
3. 系统日志也会记录每次 API 调用

---

## 参考资料

- [YouTube Data API v3 官方文档](https://developers.google.com/youtube/v3/docs)
- [Google Cloud Console](https://console.cloud.google.com/)
- [API 配额和限制](https://developers.google.com/youtube/v3/getting-started#quota)
- [videos.list API 参考](https://developers.google.com/youtube/v3/docs/videos/list)
