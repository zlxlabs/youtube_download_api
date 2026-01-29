# 高级配置

本文档介绍系统的高级配置选项，包括 Cookie 管理、PO Token 配置、YouTube Data API 配置等。

## 目录

- [Cookie 管理](#cookie-管理)
- [PO Token 配置](#po-token-配置)
- [YouTube Data API 配置](#youtube-data-api-配置)
- [敏感词审核](#敏感词审核)
- [其他高级选项](#其他高级选项)

---

## Cookie 管理

### Cookie 获取方式

推荐使用浏览器扩展导出 Cookies：

1. **使用浏览器扩展**
   - 推荐扩展：[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldkbehn)
   - 访问 YouTube 并登录
   - 导出 `youtube.com` 的 cookies 到文件

2. **使用无痕模式（推荐）**
   - 打开新的无痕窗口并登录 YouTube
   - 在同一标签页访问 `https://www.youtube.com/robots.txt`
   - 导出 cookies，然后关闭无痕窗口
   - 这样可以避免 cookies 被浏览器轮换

**注意事项**：
- 使用账户的 cookies 可能导致账户被临时或永久封禁
- 请谨慎使用下载频率和数量
- 仅在必要时使用，或使用临时账户

### 配置 Cookie

```bash
# .env
COOKIE_FILE=./cookies.txt
```

### Cookie API

#### 获取 Cookie 信息

**接口**：`GET /api/v1/settings/cookie`

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/settings/cookie"
```

**响应示例**：
```json
{
  "cookie_file": "./cookies.txt",
  "exists": true,
  "line_count": 15,
  "last_modified": "2026-01-28T12:00:00Z"
}
```

#### 更新 Cookie

**接口**：`PUT /api/v1/settings/cookie`

**请求参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `content` | string | 是 | - | Cookie 文件内容（Netscape 格式） |
| `create_backup` | boolean | 否 | `true` | 是否创建备份 |

**请求示例**：
```bash
curl -X PUT http://localhost:8000/api/v1/settings/cookie \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "content": "# Netscape HTTP Cookie File\n...\n",
    "create_backup": true
  }'
```

#### 验证 Cookie 格式

**接口**：`POST /api/v1/settings/cookie/validate`

**请求示例**：
```bash
curl -X POST http://localhost:8000/api/v1/settings/cookie/validate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "content": "# Netscape HTTP Cookie File\n...\n"
  }'
```

**响应示例**：
```json
{
  "valid": true,
  "errors": [],
  "warnings": ["缺少 Netscape HTTP Cookie File header"],
  "line_count": 15
}
```

### Cookie 协同机制（CDP + ytdlp）

系统实现了 CDP 和 ytdlp 下载器之间的 Cookie 自动同步。

#### 工作原理

```
CDP 下载器
  ↓
导出最新 Cookie → data/latest_cookies.txt（5分钟有效）
  ↓
ytdlp 下载器智能选择：
  1. 优先：latest_cookies.txt（CDP 共享 Cookie）
  2. 降级：COOKIE_FILE（静态 Cookie）
  3. 兜底：不使用 Cookie
```

#### 核心优势

- **提高降级成功率**：ytdlp 降级时成功率提升 +20%（70% → 90%）
- **保持 Cookie 及时性**：每次 CDP 任务都刷新登录态
- **零性能损耗**：Cookie 同步仅需 < 1ms
- **向后兼容**：未启用 CDP 时行为完全一致

#### 日志示例

**CDP 同步 Cookie**：
```
[cdp] Synced fresh cookies to shared location: data/latest_cookies.txt
  event: cdp_cookie_synced
  cookie_age_seconds: 0
```

**ytdlp 使用 CDP Cookie**：
```
[ytdlp] Using fresh CDP cookies (age: 45.2s)
  event: ytdlp_cookie_selected
  cookie_source: cdp_shared
```

---

## PO Token 配置

YouTube 使用 PO (Proof of Origin) Token 来验证请求来源。

### 工作原理

```
┌─────────────┐    请求 PO Token    ┌─────────────────┐
│   yt-dlp    │ ──────────────────> │  bgutil HTTP    │
│  (下载器)   │ <────────────────── │  (POT Provider) │
└─────────────┘    返回 Token       └─────────────────┘
                                             │
                                             v
                                     ┌───────────────┐
                                     │   YouTube     │
                                     │   BotGuard    │
                                     └───────────────┘
```

### 配置方式

#### 方式一：仅 PO Token Provider（当前默认）

```bash
# .env
POT_SERVER_URL=http://pot-provider:4416
```

#### 方式二：Cookies + PO Token（推荐）

```bash
COOKIE_FILE=./cookies.txt
POT_SERVER_URL=http://pot-provider:4416
```

### 配置逻辑

| 场景 | Cookies | PO Token 请求 | 成功率 |
|------|---------|---------------|--------|
| 有 Cookies | 是 | 按需自动 | 高 |
| 无 Cookies | 否 | 强制请求 | 中 |

### 本地开发 POT Provider

开发环境需要单独启动 POT Provider 服务：

```bash
# 使用 Docker 启动 bgutil POT Provider
docker run -d -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider

# 验证服务
curl http://localhost:4416/ping
```

---

## YouTube Data API 配置

配置官方 API 可获得更快的响应速度和更高的稳定性。

### 获取 API Key

1. 访问 [Google Cloud Console](https://console.cloud.google.com/apis/credentials)
2. 创建项目并启用 YouTube Data API v3
3. 创建 API Key
4. 配额：10,000 units/天（videos.list = 1 unit）

### 配置 API Key

```bash
# .env
YOUTUBE_DATA_API_KEY=AIzaSy...
```

### 未配置时

未配置 YouTube Data API 时，系统会自动降级到 ytdlp（免费但可能较慢）。

---

## 敏感词审核

企业微信通知支持敏感词审核功能。

### 配置示例

```bash
# 启用敏感词审核
WECOM_MODERATION_ENABLED=true

# 敏感词库 URL（支持多个，逗号分隔）
WECOM_MODERATION_URLS=https://example.com/words1.txt,https://example.com/words2.txt

# 审核策略：pinyin_reverse（拼音混淆）
WECOM_MODERATION_STRATEGY=pinyin_reverse
```

### 审核策略说明

| 策略 | 说明 |
|------|------|
| `block` | 检测到敏感词时拒绝发送，发送告警消息 |
| `replace` | 将敏感词替换为 `[敏感词]` |
| `pinyin_reverse` | 将敏感词转换为拼音混淆形式（默认） |

### 敏感词文件格式

每行一个敏感词，支持注释（以 `#` 开头）：

```text
# 这是注释
敏感词1
敏感词2
```

---

## 其他高级选项

### DRY_RUN

干跑模式（跳过下载，用于测试）。

```bash
# 启用干跑模式
DRY_RUN=true
```

**说明**：
- 跳过实际下载
- 仅模拟任务流程
- 用于测试和调试

---

## 相关文档

- [配置总览](./overview.md)
- [下载器配置](./downloaders.md)
- [部署指南](../deployment.md)
