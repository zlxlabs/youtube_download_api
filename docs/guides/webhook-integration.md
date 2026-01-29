# Webhook 集成指南

本文档介绍如何使用 Webhook 回调功能，在任务完成或失败时自动接收通知。

## 目录

- [功能概述](#功能概述)
- [配置 Webhook](#配置-webhook)
- [回调数据格式](#回调数据格式)
- [签名验证](#签名验证)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 功能概述

### Webhook 工作原理

```
创建任务（提供 callback_url）
  ↓
任务执行（下载中）
  ↓
任务完成/失败
  ↓
系统 POST 请求到 callback_url
  ↓
您的服务器接收并处理回调
```

### 回调触发时机

- 任务完成（成功或部分成功）
- 任务失败（已重试）
- 任务取消

### 通知方式

系统提供两种通知方式：
1. **Webhook 回调**：主动推送通知到您的服务器（推荐）
2. **轮询查询**：客户端定期查询任务状态

---

## 配置 Webhook

### 在创建任务时指定回调 URL

**接口**：`POST /api/v1/tasks`

**请求参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `callback_url` | string | 否 | Webhook 回调 URL |
| `callback_secret` | string | 否 | HMAC 签名密钥（8-256字符） |

**请求示例**：
```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "include_audio": true,
    "include_transcript": true,
    "callback_url": "https://your-server.com/webhook",
    "callback_secret": "your-hmac-secret"
  }'
```

### callback_url 要求

- 必须是有效的 HTTPS URL（生产环境）
- 必须接受 POST 请求
- 必须在 10 秒内返回 200 状态码
- 建议使用公网可访问的地址

### callback_secret 要求

- 长度：8-256 字符
- 用途：HMAC 签名密钥，验证回调真实性
- 建议使用强随机字符串

**生成密钥**：
```bash
# 使用 openssl 生成
openssl rand -hex 32

# 使用 python 生成
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 回调数据格式

### HTTP 请求头

```http
POST {callback_url}
Content-Type: application/json
X-Signature: sha256=xxxxxxxx
X-Task-Id: 550e8400-e29b-41d4-a716-446655440000
X-Timestamp: 1702357425
```

**请求头说明**：

| 请求头 | 说明 |
|--------|------|
| `Content-Type` | 固定为 `application/json` |
| `X-Signature` | HMAC-SHA256 签名（如果配置了 callback_secret） |
| `X-Task-Id` | 任务 ID |
| `X-Timestamp` | 回调时间戳（Unix 时间戳） |

### 回调数据结构

#### 任务完成（成功）

```json
{
  "event": "task.completed",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "video_id": "dQw4w9WgXcQ",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "priority": "normal",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "duration": 213,
    "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
  },
  "files": {
    "audio": {
      "url": "/api/v1/files/abc123.m4a",
      "size": 3456789,
      "format": "m4a",
      "bitrate": 128
    },
    "transcript": {
      "url": "/api/v1/files/def456.srt",
      "size": 12345,
      "format": "srt",
      "language": "en"
    }
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": false
  },
  "cache_hit": false,
  "request": {
    "include_audio": true,
    "include_transcript": true
  },
  "error": null,
  "created_at": "2025-12-12T10:00:00Z",
  "started_at": "2025-12-12T10:00:05Z",
  "completed_at": "2025-12-12T10:01:30Z",
  "expires_at": "2025-02-10T10:01:30Z"
}
```

#### 任务完成（部分成功）

```json
{
  "event": "task.completed",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "video_id": "dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley"
  },
  "files": {
    "audio": null,
    "transcript": {
      "url": "/api/v1/files/def456.srt",
      "size": 12345,
      "format": "srt",
      "language": "en"
    }
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": true,
    "failure_details": {
      "audio": {
        "success": false,
        "error": {
          "code": "RATE_LIMITED",
          "message": "Rate limited by YouTube",
          "retry_count": 1
        }
      },
      "transcript": {
        "success": true
      }
    }
  },
  "cache_hit": false,
  "error": null
}
```

#### 任务失败

```json
{
  "event": "task.failed",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "video_id": "dQw4w9WgXcQ",
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "video_info": null,
  "files": {
    "audio": null,
    "transcript": null
  },
  "error": {
    "code": "VIDEO_UNAVAILABLE",
    "message": "Video not found or removed",
    "details": {
      "video_id": "dQw4w9WgXcQ"
    }
  },
  "created_at": "2025-12-12T10:00:00Z",
  "failed_at": "2025-12-12T10:00:10Z",
  "retry_count": 1
}
```

#### 任务取消

```json
{
  "event": "task.cancelled",
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "cancelled",
  "video_id": "dQw4w9WgXcQ",
  "message": "Task cancelled by user",
  "cancelled_at": "2025-12-12T10:00:15Z"
}
```

### 回调数据字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `event` | string | 事件类型：`task.completed`, `task.failed`, `task.cancelled` |
| `task_id` | string | 任务 ID |
| `status` | string | 任务状态 |
| `video_id` | string | 视频 ID |
| `video_url` | string | 视频 URL |
| `video_info` | object | 视频元数据（可能为 null） |
| `files` | object | 文件信息 |
| `result` | object | 任务结果详情 |
| `error` | object | 错误信息（失败时） |
| `cache_hit` | boolean | 是否为缓存命中 |
| `created_at` | datetime | 创建时间 |
| `completed_at` | datetime | 完成时间 |
| `failed_at` | datetime | 失败时间 |
| `cancelled_at` | datetime | 取消时间 |

---

## 签名验证

### 为什么需要签名验证？

签名验证用于确保：
1. 回调确实来自 YouTube Audio API
2. 回调数据未被篡改
3. 防止伪造回调攻击

### 签名算法

系统使用 HMAC-SHA256 算法生成签名：

```python
import hmac
import hashlib

signature = hmac.new(
    secret.encode(),
    body.encode(),
    hashlib.sha256
).hexdigest()
```

签名格式：`sha256=<hexdigest>`

### 验证签名

#### Python 示例

```python
import hmac
import hashlib
from fastapi import FastAPI, Request, HTTPException

app = FastAPI()

def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """验证 HMAC 签名"""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)

@app.post("/webhook")
async def webhook_handler(request: Request):
    # 获取签名
    signature = request.headers.get("X-Signature")

    # 如果没有配置 callback_secret，则跳过签名验证
    if signature:
        secret = "your-hmac-secret"
        body = await request.body()

        # 验证签名
        if not verify_signature(body, signature, secret):
            raise HTTPException(401, "Invalid signature")

    # 处理回调数据
    data = await request.json()

    event = data.get("event")
    task_id = data.get("task_id")
    status = data.get("status")

    print(f"Received webhook: {event} for task {task_id}, status: {status}")

    # 根据事件类型处理
    if event == "task.completed":
        # 处理完成事件
        files = data.get("files", {})
        if files.get("audio"):
            audio_url = files["audio"]["url"]
            print(f"Audio file ready: {audio_url}")

    elif event == "task.failed":
        # 处理失败事件
        error = data.get("error")
        print(f"Task failed: {error}")

    # 返回 200 状态码
    return {"status": "received"}
```

#### Node.js 示例

```javascript
const crypto = require('crypto');
const express = require('express');

const app = express();
app.use(express.json());

function verifySignature(body, signature, secret) {
    const expected = crypto
        .createHmac('sha256', secret)
        .update(body)
        .digest('hex');
    return `sha256=${expected}` === signature;
}

app.post('/webhook', (req, res) => {
    const signature = req.headers['x-signature'];

    // 如果没有配置 callback_secret，则跳过签名验证
    if (signature) {
        const secret = 'your-hmac-secret';
        const body = JSON.stringify(req.body);

        // 验证签名
        if (!verifySignature(body, signature, secret)) {
            return res.status(401).json({ error: 'Invalid signature' });
        }
    }

    // 处理回调数据
    const { event, task_id, status, files, error } = req.body;

    console.log(`Received webhook: ${event} for task ${task_id}, status: ${status}`);

    // 根据事件类型处理
    if (event === 'task.completed') {
        if (files && files.audio) {
            console.log(`Audio file ready: ${files.audio.url}`);
        }
    } else if (event === 'task.failed') {
        console.log(`Task failed: ${error.message}`);
    }

    // 返回 200 状态码
    res.json({ status: 'received' });
});

app.listen(3000, () => {
    console.log('Webhook server listening on port 3000');
});
```

#### Go 示例

```go
package main

import (
    "crypto/hmac"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
)

func verifySignature(body []byte, signature, secret string) bool {
    expected := hmac.New(sha256.New, []byte(secret))
    expected.Write(body)
    expectedHex := hex.EncodeToString(expected.Sum(nil))
    return "sha256=" + expectedHex == signature
}

func webhookHandler(w http.ResponseWriter, r *http.Request) {
    signature := r.Header.Get("X-Signature")

    // 读取请求体
    body, err := io.ReadAll(r.Body)
    if err != nil {
        http.Error(w, "Failed to read body", 400)
        return
    }

    // 如果有签名，验证签名
    if signature != "" {
        secret := "your-hmac-secret"
        if !verifySignature(body, signature, secret) {
            http.Error(w, "Invalid signature", 401)
            return
        }
    }

    // 解析 JSON
    var data map[string]interface{}
    if err := json.Unmarshal(body, &data); err != nil {
        http.Error(w, "Invalid JSON", 400)
        return
    }

    // 处理回调数据
    event := data["event"].(string)
    taskID := data["task_id"].(string)
    status := data["status"].(string)

    fmt.Printf("Received webhook: %s for task %s, status: %s\n", event, taskID, status)

    // 返回 200 状态码
    w.Header().Set("Content-Type", "application/json")
    w.WriteHeader(200)
    json.NewEncoder(w).Encode(map[string]string{"status": "received"})
}

func main() {
    http.HandleFunc("/webhook", webhookHandler)
    fmt.Println("Webhook server listening on port 3000")
    http.ListenAndServe(":3000", nil)
}
```

---

## 最佳实践

### 1. 快速响应

- Webhook 处理应尽可能快速（< 10 秒）
- 建议将耗时操作放入后台任务队列

```python
@app.post("/webhook")
async def webhook_handler(request: Request):
    data = await request.json()

    # 快速验证并接收回调
    task_id = data.get("task_id")

    # 将耗时操作放入后台任务
    process_task_async.delay(task_id)

    # 立即返回
    return {"status": "received"}
```

### 2. 幂等性处理

Webhook 可能重复发送，确保处理逻辑是幂等的。

```python
@app.post("/webhook")
async def webhook_handler(request: Request):
    data = await request.json()
    task_id = data.get("task_id")

    # 检查是否已处理
    if is_task_processed(task_id):
        return {"status": "already_processed"}

    # 处理任务
    process_task(task_id)

    # 标记为已处理
    mark_task_processed(task_id)

    return {"status": "received"}
```

### 3. 重试机制

- 如果您的服务器返回非 200 状态码，系统会重试
- 重试间隔：30 秒
- 最大重试次数：3 次

### 4. 安全建议

- **生产环境必须使用 HTTPS**
- **强烈建议配置 callback_secret**
- 验证签名后再处理回调数据
- 定期轮换 callback_secret

---

## 常见问题

### 1. 没有收到 Webhook 回调

**可能原因**：
- callback_url 配置错误
- 服务器未公网可访问
- 服务器返回非 200 状态码
- 防火墙拦截

**排查步骤**：
```bash
# 1. 测试 callback_url 是否可访问
curl -X POST https://your-server.com/webhook \
  -H "Content-Type: application/json" \
  -d '{"test": true}'

# 2. 检查服务器日志
# 查看是否有请求记录

# 3. 检查防火墙
# 确保端口 443 或 80 开放
```

### 2. 签名验证失败

**可能原因**：
- callback_secret 配置错误
- 回调数据被篡改
- 时间戳验证失败

**排查步骤**：
```python
# 打印调试信息
print(f"Received signature: {signature}")
print(f"Expected signature: sha256={expected}")

# 确保 callback_secret 一致
secret = "your-hmac-secret"  # 确认与创建任务时一致
```

### 3. 重复收到回调

**原因**：
- 系统重试机制
- 网络延迟导致重复

**解决方案**：
- 实现幂等性处理
- 使用 task_id 去重

---

## 相关文档

- [API 参考文档](../api-reference.md)
- [快速开始指南](../quick-start.md)
