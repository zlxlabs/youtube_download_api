# 视频元数据查询指南

本文档介绍如何快速获取 YouTube 视频元数据，无需下载文件。

## 目录

- [功能特性](#功能特性)
- [配置 YouTube Data API（可选）](#配置-youtube-data-api可选)
- [API 使用](#api-使用)
- [使用场景](#使用场景)
- [与下载任务的区别](#与下载任务的区别)
- [常见问题](#常见问题)

---

## 功能特性

### 核心优势

- ✅ **快速响应**：< 1 秒返回（相比下载任务的 30-120 秒）
- ✅ **数据库缓存**：元数据永久缓存，重复查询无需 API 调用
- ✅ **智能降级**：优先使用 YouTube Data API，自动降级到 ytdlp/tikhub
- ✅ **无需下载**：仅获取元数据，不占用存储空间

### 工作流程

```
请求视频元数据
  │
  ├─> 1. 检查数据库缓存
  │   ├─ 命中 → 直接返回（5ms）
  │   └─ 未命中 → 继续
  │
  ├─> 2. 尝试 YouTube Data API
  │   ├─ 成功 → 写入数据库 → 返回
  │   └─ 失败 → 降级
  │
  ├─> 3. 尝试 yt-dlp（免费）
  │   ├─ 成功 → 写入数据库 → 返回
  │   └─ 失败 → 降级
  │
  └─> 4. 尝试 TikHub（付费）
      ├─ 成功 → 写入数据库 → 返回
      └─ 失败 → 返回错误
```

---

## 配置 YouTube Data API（可选）

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

## API 使用

### 查询视频元数据

**接口**：`GET /api/v1/videos/{video_id}/info`

**路径参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `video_id` | string | 是 | YouTube 视频 ID |

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/videos/dQw4w9WgXcQ/info"
```

**Python 示例**：
```python
import requests

url = "http://localhost:8000/api/v1/videos/dQw4w9WgXcQ/info"
headers = {"X-API-Key": "your-api-key"}

response = requests.get(url, headers=headers)
print(response.json())
```

### 响应示例

```json
{
  "video_id": "dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "duration": 213,
    "description": "Official music video by Rick Astley...",
    "upload_date": "20091025",
    "view_count": 1500000000,
    "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
  },
  "cached": false,
  "metadata_source": "youtube_data_api",
  "fetched_at": "2026-01-28T12:00:00Z"
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `video_id` | string | YouTube 视频 ID |
| `video_info` | object | 视频元数据对象 |
| `cached` | boolean | `true` = 从数据库缓存读取，`false` = 实时获取 |
| `metadata_source` | string | 元数据来源：`cached` / `youtube_data_api` / `ytdlp` / `tikhub` |
| `fetched_at` | datetime | 元数据获取/更新时间 |

### video_info 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 视频标题 |
| `author` | string | 视频作者 |
| `channel_id` | string | 频道 ID |
| `duration` | integer | 视频时长（秒） |
| `description` | string | 视频描述 |
| `upload_date` | string | 上传日期（YYYYMMDD） |
| `view_count` | integer | 观看次数 |
| `thumbnail` | string | 缩略图 URL |

---

## 使用场景

### 场景1：批量视频信息采集

快速获取多个视频的元数据，用于信息采集和分析。

```bash
#!/bin/bash

VIDEO_IDS=("dQw4w9WgXcQ" "abc123xyz" "def456uvw")
API_KEY="your-api-key"
API_URL="http://localhost:8000/api/v1/videos"

for video_id in "${VIDEO_IDS[@]}"; do
  echo "Fetching info for $video_id..."
  curl -H "X-API-Key: $API_KEY" \
    "$API_URL/$video_id/info" | jq '.video_info.title'
done
```

### 场景2：预检查视频可用性

下载前先检查视频是否存在，避免不必要的下载任务。

```bash
#!/bin/bash

VIDEO_ID="dQw4w9WgXcQ"
API_KEY="your-api-key"
API_URL="http://localhost:8000/api/v1/videos"

# 检查视频是否存在
if curl -s -H "X-API-Key: $API_KEY" \
  "$API_URL/$VIDEO_ID/info" | jq -e '.video_info.title'; then
  echo "Video exists, proceed to download"
  # 创建下载任务
else
  echo "Video not found"
  exit 1
fi
```

### 场景3：获取视频时长

获取视频时长（秒），用于预估下载时间。

```bash
#!/bin/bash

VIDEO_ID="dQw4w9WgXcQ"
API_KEY="your-api-key"
API_URL="http://localhost:8000/api/v1/videos"

# 获取视频时长
DURATION=$(curl -s -H "X-API-Key: $API_KEY" \
  "$API_URL/$VIDEO_ID/info" | jq '.video_info.duration')

echo "Video duration: $DURATION seconds"

# 预估下载时间（假设 1MB/s）
ESTIMATED_SIZE=$((DURATION / 2 * 1000000))  # 假设 128kbps
ESTIMATED_TIME=$((ESTIMATED_SIZE / 1000000))

echo "Estimated download time: ~$ESTIMATED_TIME seconds"
```

### 场景4：视频筛选和分类

根据视频信息进行筛选和分类。

```python
import requests

API_KEY = "your-api-key"
API_URL = "http://localhost:8000/api/v1/videos"

VIDEO_IDS = ["dQw4w9WgXcQ", "abc123", "def456"]

# 获取所有视频信息
videos_info = []
for video_id in VIDEO_IDS:
    response = requests.get(
        f"{API_URL}/{video_id}/info",
        headers={"X-API-Key": API_KEY}
    ).json()
    videos_info.append(response["video_info"])

# 筛选时长小于 300 秒的视频
short_videos = [v for v in videos_info if v["duration"] < 300]
print(f"Short videos: {[v['title'] for v in short_videos]}")

# 筛选观看次数超过 100 万的视频
popular_videos = [v for v in videos_info if v["view_count"] > 1000000]
print(f"Popular videos: {[v['title'] for v in popular_videos]}")
```

---

## 与下载任务的区别

| 特性 | 元数据查询 `/videos/{id}/info` | 下载任务 `/tasks` |
|------|-------------------------------|------------------|
| **用途** | 查询视频信息 | 下载音频/字幕 |
| **响应时间** | < 1 秒 | 30-120 秒 |
| **返回内容** | 仅元数据 | 元数据 + 文件下载链接 |
| **存储占用** | 无 | 有（音频/字幕文件） |
| **适用场景** | 信息查询、批量采集 | 实际下载文件 |
| **成本** | 首次免费，重复缓存 | 下载器成本（免费/付费） |

### 使用建议

- **仅需视频信息**：使用元数据查询 API
- **需要音频/字幕文件**：创建下载任务
- **批量信息采集**：使用元数据查询 API（速度快）
- **实际下载播放**：创建下载任务

---

## 常见问题

### 1. 查询返回 "Video not found"

**问题**：API 返回视频不存在错误

**可能原因**：
- 视频 ID 错误
- 视频已删除
- 视频为私有视频
- 视频有地区限制

**解决方案**：
```bash
# 验证视频 ID
# 从 YouTube URL 中提取：https://www.youtube.com/watch?v=dQw4w9WgXcQ
# 视频 ID 为：dQw4w9WgXcQ
```

### 2. 查询速度较慢

**问题**：首次查询响应时间超过 1 秒

**原因**：
- 未配置 YouTube Data API，使用 ytdlp 较慢
- 网络延迟

**解决方案**：
```bash
# 配置 YouTube Data API
YOUTUBE_DATA_API_KEY=AIzaSy...

# 后续查询会使用数据库缓存（快速）
```

### 3. 返回的元数据不准确

**问题**：元数据与实际视频不符

**可能原因**：
- 数据库缓存过期（但理论上是永久缓存）
- YouTube 元数据已更新

**解决方案**：
```bash
# 当前版本不支持强制刷新缓存
# 如需最新元数据，请联系管理员清除数据库缓存
```

---

## 相关文档

- [API 参考文档](../api-reference.md)
- [快速开始指南](../quick-start.md)
- [人工上传指南](./manual-upload.md)
