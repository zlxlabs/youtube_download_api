# 人工上传指南

本文档介绍如何使用人工上传功能，将已有的音频或视频文件上传到系统中。

## 目录

- [功能说明](#功能说明)
- [管理界面](#管理界面)
- [支持格式](#支持格式)
- [API 使用](#api-使用)
- [配置选项](#配置选项)
- [常见问题](#常见问题)

---

## 功能说明

人工上传功能适用于：

- 已自行下载音频/视频，直接上传并生成可下载音频文件
- 将本地音频文件导入系统进行管理
- 批量处理已有的音频/视频资源

### 核心特性

- 支持上传音频或视频文件，统一输出为 `m4a`
- 如果原始音频为 **AAC**（常见于 mp4），将优先 **直接封装（remux）**，速度更快
- 上传后自动写入 `video_resources`，并可在管理界面查看/删除
- 自动从 YouTube 获取视频元数据（可选手动提供）

### 处理流程

```
上传文件
  │
  ├─> 音频文件（m4a, mp3, aac, opus, wav, flac, ogg）
  │   ├─ AAC 格式 → 直接封装（快速）
  │   └─ 其他格式 → 转码为 m4a
  │
  └─> 视频文件（mp4, webm, mkv, avi, mov）
      └─ 提取音频 → 转码为 m4a

  ↓
写入 video_resources
  │
  ├─ video_id: 从 URL 解析或生成
  ├─ title: 从元数据获取或手动提供
  ├─ author: 从元数据获取或手动提供
  └─ file_id: 生成唯一 ID

  ↓
返回下载链接
```

---

## 管理界面

访问：`http://localhost:8000/admin`

### 功能模块

管理界面包含以下功能模块：

- **任务管理** - 查看所有下载任务，支持搜索和状态筛选
- **视频资源** - 管理所有视频资源，查看详情和删除
- **人工上传** - 手动上传音频/视频文件
- **Cookie 管理** - 查看和更新 YouTube Cookie 文件
- **系统配置** - 查看系统配置和状态

---

## 支持格式

### 音频格式

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| M4A | `.m4a` | 直接封装（如果为 AAC）或转码 |
| MP3 | `.mp3` | 转码为 m4a |
| AAC | `.aac` | 直接封装（快速） |
| Opus | `.opus` | 转码为 m4a |
| WAV | `.wav` | 转码为 m4a |
| FLAC | `.flac` | 转码为 m4a |
| OGG | `.ogg` | 转码为 m4a |

### 视频格式

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| MP4 | `.mp4` | 提取音频 |
| WebM | `.webm` | 提取音频 |
| MKV | `.mkv` | 提取音频 |
| AVI | `.avi` | 提取音频 |
| MOV | `.mov` | 提取音频 |

---

## API 使用

### 上传文件

**接口**：`POST /api/v1/manual-upload`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | File | 是 | 音频或视频文件 |
| `video_url` | string | 否 | YouTube 视频 URL（用于获取元数据） |
| `title` | string | 否 | 自定义标题（优先级高于自动获取） |
| `author` | string | 否 | 自定义作者（优先级高于自动获取） |

**请求示例 - 基础上传**：
```bash
curl -X POST http://localhost:8000/api/v1/manual-upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@/path/to/audio.mp3"
```

**请求示例 - 完整参数**：
```bash
curl -X POST http://localhost:8000/api/v1/manual-upload \
  -H "X-API-Key: your-api-key" \
  -F "video_url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  -F "file=@/path/to/video.mp4" \
  -F "title=Custom Title" \
  -F "author=Channel Name"
```

**Python 示例**：
```python
import requests

url = "http://localhost:8000/api/v1/manual-upload"
headers = {"X-API-Key": "your-api-key"}

files = {"file": open("/path/to/audio.mp3", "rb")}
data = {
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "title": "Custom Title",
    "author": "Channel Name"
}

response = requests.post(url, headers=headers, files=files, data=data)
print(response.json())
```

**响应示例 - 成功**：
```json
{
  "video_id": "dQw4w9WgXcQ",
  "file_id": "abc123-4567-89ef-0123-456789abcdef",
  "title": "Custom Title",
  "author": "Channel Name",
  "size": 3456789,
  "format": "m4a",
  "original_format": "mp4",
  "created_at": "2026-01-28T12:00:00Z"
}
```

**响应字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `video_id` | string | 视频 ID（从 URL 解析或生成） |
| `file_id` | string | 文件唯一 ID |
| `title` | string | 视频标题 |
| `author` | string | 视频作者 |
| `size` | integer | 文件大小（字节） |
| `format` | string | 输出格式（固定为 m4a） |
| `original_format` | string | 原始文件格式 |
| `created_at` | datetime | 创建时间 |

### 列出上传

获取人工上传列表，支持分页。

**接口**：`GET /api/v1/manual-uploads`

**查询参数**：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `limit` | integer | 否 | `20` | 每页数量 |
| `offset` | integer | 否 | `0` | 偏移量 |

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads?limit=20&offset=0"
```

**响应示例**：
```json
{
  "total": 50,
  "uploads": [
    {
      "video_id": "dQw4w9WgXcQ",
      "file_id": "abc123",
      "title": "Rick Astley - Never Gonna Give You Up",
      "author": "Rick Astley",
      "size": 3456789,
      "format": "m4a",
      "created_at": "2026-01-28T12:00:00Z"
    }
  ]
}
```

### 查看上传详情

获取单个上传的详细信息。

**接口**：`GET /api/v1/manual-uploads/{video_id}`

**请求示例**：
```bash
curl -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads/dQw4w9WgXcQ"
```

**响应示例**：
```json
{
  "video_id": "dQw4w9WgXcQ",
  "file_id": "abc123",
  "title": "Rick Astley - Never Gonna Give You Up",
  "author": "Rick Astley",
  "size": 3456789,
  "format": "m4a",
  "original_format": "mp4",
  "duration": 213,
  "created_at": "2026-01-28T12:00:00Z",
  "file_url": "/api/v1/files/abc123.m4a"
}
```

### 删除上传

删除一个人工上传及其关联文件。

**接口**：`DELETE /api/v1/manual-uploads/{video_id}`

**请求示例**：
```bash
curl -X DELETE -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/v1/manual-uploads/dQw4w9WgXcQ"
```

**响应示例**：
```json
{
  "video_id": "dQw4w9WgXcQ",
  "message": "Upload deleted successfully"
}
```

---

## 配置选项

### 启用/禁用功能

```bash
# .env

# 启用人工上传
MANUAL_UPLOAD_ENABLED=true

# 禁用人工上传
MANUAL_UPLOAD_ENABLED=false
```

### 文件大小限制

```bash
# 单文件最大大小（MB）
MANUAL_UPLOAD_MAX_SIZE_MB=500
```

### 允许的格式

```bash
# 允许的视频格式（逗号分隔）
MANUAL_UPLOAD_ALLOWED_VIDEO_FORMATS=.mp4,.webm,.mkv,.avi,.mov

# 允许的音频格式（逗号分隔）
MANUAL_UPLOAD_ALLOWED_AUDIO_FORMATS=.m4a,.mp3,.aac,.opus,.wav,.flac,.ogg
```

---

## 常见问题

### 1. 上传失败：文件格式不支持

**问题**：上传时返回 "Unsupported file format"

**解决方案**：
```bash
# 检查文件扩展名是否在允许列表中
MANUAL_UPLOAD_ALLOWED_VIDEO_FORMATS=.mp4,.webm,.mkv,.avi,.mov
MANUAL_UPLOAD_ALLOWED_AUDIO_FORMATS=.m4a,.mp3,.aac,.opus,.wav,.flac,.ogg

# 确保文件扩展名正确（大小写敏感）
# 例如：.MP4 → .mp4
```

### 2. 上传失败：文件过大

**问题**：上传时返回 "File too large"

**解决方案**：
```bash
# 调整文件大小限制
MANUAL_UPLOAD_MAX_SIZE_MB=1000
```

### 3. 转码时间过长

**问题**：大文件转码时间过长

**解决方案**：
- 上传 AAC 格式（m4a）可以跳过转码，直接封装
- 上传较小的文件

### 4. 无法获取视频元数据

**问题**：上传后显示标题为 "Unknown Title"

**原因**：
- 未提供 `video_url`
- 视频元数据获取失败

**解决方案**：
```bash
# 提供 video_url 参数
curl -X POST http://localhost:8000/api/v1/manual-upload \
  -H "X-API-Key: your-api-key" \
  -F "file=@audio.mp3" \
  -F "video_url=https://www.youtube.com/watch?v=xxx" \
  -F "title=Custom Title" \
  -F "author=Custom Author"
```

---

## 相关文档

- [API 参考文档](../api-reference.md)
- [快速开始指南](../quick-start.md)
- [视频元数据查询](./video-metadata.md)
