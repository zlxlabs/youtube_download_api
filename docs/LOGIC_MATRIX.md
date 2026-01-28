# Audio/Transcript 返回逻辑矩阵

## 1. 基本组合（无缓存情况）

| include_audio | include_transcript | 行为 | 返回结果 | 备注 |
|---------------|-------------------|------|----------|------|
| `true` | `true` | 下载音频 + 字幕 | `audio` + `transcript` | 标准完整模式 |
| `true` | `false` | 仅下载音频 | `audio` | 仅音频模式 |
| `false` | `true` | 仅下载字幕 | `transcript` | 仅字幕模式 |
| `false` | `false` | ❌ 拒绝请求 | 400 错误 | 至少选一个 |

---

## 2. Audio Fallback（音频降级）

**触发条件**：`include_audio=false` + `include_transcript=true` + 视频无字幕

| 场景 | 原始请求 | 实际行为 | 返回结果 | 标识字段 |
|------|---------|---------|----------|----------|
| 字幕不存在 | 仅字幕 | 自动下载音频 | `audio` (无 `transcript`) | `result.audio_fallback=true` |

**代码位置**：`worker.py` line 389-408

```python
if not need_audio and need_transcript and not result.has_transcript:
    logger.info("No transcript available, falling back to audio download")
    audio_fallback = True
    # 重新下载音频
    result = await self.downloader_manager.download_with_fallback(
        include_audio=True,
        include_transcript=False,
    )
```

---

## 3. Transcript Fallback（字幕降级）

**触发条件**：`include_audio=true` + `include_transcript=false` + 音频下载失败

| 场景 | 原始请求 | 失败类型 | 降级行为 | 返回结果 | 标识字段 |
|------|---------|---------|---------|----------|----------|
| 音频失败，有字幕 | 仅音频 | 音频下载失败 | 尝试下载字幕 | `transcript` (无 `audio`) | 任务 `completed`，但仅字幕 |
| 音频失败，无字幕 | 仅音频 | 音频+字幕都失败 | 任务标记失败 | 无文件 | 任务 `failed` |

**代码位置**：`worker.py` line 519-546

```python
# 字幕降级逻辑
if (
    task.include_audio
    and not task.include_transcript
    and isinstance(error, (DownloaderError, AllDownloadersFailed))
    and not getattr(task, "_transcript_fallback_attempted", False)
):
    transcript_fallback_success = await self._try_transcript_fallback(task)
```

---

## 4. 缓存复用逻辑

### 4.1 全部命中缓存

| 请求组合 | 缓存状态 | 行为 | 返回 |
|---------|---------|------|------|
| `audio=true`, `transcript=true` | 音频+字幕都存在 | 直接返回缓存 | `cache_hit=true`, `task_id=null` |
| `audio=true`, `transcript=false` | 音频存在 | 直接返回缓存 | `cache_hit=true`, `task_id=null` |
| `false`, `true` | 字幕存在 | 直接返回缓存 | `cache_hit=true`, `task_id=null` |

**代码位置**：`worker.py` line 330-338

```python
if not need_audio and not need_transcript:
    logger.info("All resources already exist, nothing to download")
    return {
        "audio_file_id": existing_audio.id if existing_audio else None,
        "transcript_file_id": existing_transcript.id if existing_transcript else None,
        "reused_audio": existing_audio is not None,
        "reused_transcript": existing_transcript is not None,
    }
```

### 4.2 部分命中缓存

| 请求组合 | 缓存状态 | 行为 | result 标识 |
|---------|---------|------|-------------|
| `audio=true`, `transcript=true` | 仅音频存在 | 下载字幕 | `reused_audio=true`, `reused_transcript=false` |
| `audio=true`, `transcript=true` | 仅字幕存在 | 下载音频 | `reused_audio=false`, `reused_transcript=true` |
| `audio=true`, `transcript=false` | 字幕存在（音频不存在） | 仅下载音频，忽略已有字幕 | `reused_audio=false`, `reused_transcript=false` |
| `false`, `true` | 音频存在（字幕不存在） | 仅下载字幕，忽略已有音频 | `reused_audio=false`, `reused_transcript=false` |

---

## 5. Partial Success（部分成功）

**触发条件**：请求音频+字幕，但其中一个失败

| 场景 | 请求组合 | 失败项 | 行为 | 任务状态 | result 标识 |
|------|---------|-------|------|---------|-------------|
| 音频失败，字幕成功 | `audio=true`, `transcript=true` | 音频 | 保存字幕，标记部分成功 | `completed` | `partial_success=true`, `failure_details.audio.success=false` |
| 音频成功，字幕失败 | `audio=true`, `transcript=true` | 字幕 | 保存音频，标记部分成功 | `completed` | `partial_success=true`, `failure_details.transcript.success=false` |

**说明**：部分成功的任务状态为 `completed`，不是 `failed`。成功的资源可以被后续请求复用。

---

## 6. 完整状态流转图

```
用户请求
  │
  ├─ 验证请求参数
  │   └─ include_audio=false & include_transcript=false → 400 错误
  │
  ├─ 检查缓存
  │   ├─ 全部命中 → 直接返回 (cache_hit=true, task_id=null)
  │   └─ 部分/未命中 → 创建任务
  │
  ├─ 下载资源
  │   │
  │   ├─ [音频+字幕模式] (audio=true, transcript=true)
  │   │   ├─ 全部成功 → completed
  │   │   ├─ 音频失败 → partial_success (仅字幕)
  │   │   └─ 字幕失败 → partial_success (仅音频)
  │   │
  │   ├─ [仅音频模式] (audio=true, transcript=false)
  │   │   ├─ 成功 → completed
  │   │   └─ 失败 → 尝试字幕降级
  │   │       ├─ 字幕降级成功 → completed (仅字幕)
  │   │       └─ 字幕降级失败 → failed
  │   │
  │   └─ [仅字幕模式] (audio=false, transcript=true)
  │       ├─ 字幕存在 → completed (仅字幕)
  │       └─ 字幕不存在 → Audio Fallback
  │           ├─ 音频下载成功 → completed (仅音频, audio_fallback=true)
  │           └─ 音频下载失败 → failed
  │
  └─ 返回结果
```

---

## 7. Response 字段说明

### 7.1 标识字段

| 字段 | 类型 | 说明 | 出现场景 |
|------|------|------|----------|
| `cache_hit` | bool | 是否缓存命中 | 所有场景，命中时为 `true` |
| `task_id` | str/null | 任务ID | 缓存命中时为 `null` |
| `result.has_transcript` | bool | 视频是否有字幕 | 所有场景 |
| `result.audio_fallback` | bool | 是否触发音频降级 | 仅字幕模式但无字幕时为 `true` |
| `result.reused_audio` | bool | 音频是否来自缓存 | 音频存在时 |
| `result.reused_transcript` | bool | 字幕是否来自缓存 | 字幕存在时 |
| `result.partial_success` | bool | 是否部分成功 | 音频+字幕模式，其中一个失败时为 `true` |
| `result.failure_details` | dict | 详细成功/失败信息 | 部分成功时包含详细信息 |

### 7.2 典型响应示例

#### 场景1：完整模式 + 全部成功
```json
{
  "task_id": "xxx",
  "status": "completed",
  "cache_hit": false,
  "files": {
    "audio": { "url": "..." },
    "transcript": { "url": "..." }
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": false
  }
}
```

#### 场景2：仅字幕模式 + 无字幕 → Audio Fallback
```json
{
  "task_id": "xxx",
  "status": "completed",
  "cache_hit": false,
  "request": {
    "include_audio": false,
    "include_transcript": true
  },
  "files": {
    "audio": { "url": "..." },
    "transcript": null
  },
  "result": {
    "has_transcript": false,
    "audio_fallback": true,  // ✓ 标记音频降级
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": false
  }
}
```

#### 场景3：仅音频模式 + 音频失败 → Transcript Fallback 成功
```json
{
  "task_id": "xxx",
  "status": "completed",
  "cache_hit": false,
  "request": {
    "include_audio": true,
    "include_transcript": false
  },
  "files": {
    "audio": null,
    "transcript": { "url": "..." }  // ✓ 降级到字幕
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": false
  },
  "message": "Audio download failed, completed with transcript only"
}
```

#### 场景4：完整模式 + 部分成功
```json
{
  "task_id": "xxx",
  "status": "completed",
  "cache_hit": false,
  "files": {
    "audio": null,
    "transcript": { "url": "..." }
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": false,
    "reused_transcript": false,
    "partial_success": true,  // ✓ 部分成功标记
    "failure_details": {
      "audio": {
        "success": false,
        "error": {
          "code": "RATE_LIMITED",
          "message": "Rate limited by YouTube"
        }
      },
      "transcript": {
        "success": true
      }
    }
  }
}
```

#### 场景5：缓存命中
```json
{
  "task_id": null,  // ✓ 缓存命中无任务ID
  "status": "completed",
  "cache_hit": true,  // ✓ 标记缓存命中
  "files": {
    "audio": { "url": "..." },
    "transcript": { "url": "..." }
  },
  "result": {
    "has_transcript": true,
    "audio_fallback": false,
    "reused_audio": true,  // ✓ 标记复用
    "reused_transcript": true,  // ✓ 标记复用
    "partial_success": false
  },
  "message": "Resources retrieved from cache"
}
```

---

## 8. 关键代码位置索引

| 功能 | 文件 | 行号 | 说明 |
|------|------|------|------|
| 参数验证 | `schemas.py` | 59-64 | 至少一个必须为 `true` |
| 缓存检查 | `worker.py` | 322-338 | 检查已有文件，决定下载内容 |
| Audio Fallback | `worker.py` | 389-408 | 字幕不存在时下载音频 |
| Transcript Fallback | `worker.py` | 519-546 | 音频失败时尝试字幕 |
| 字幕降级实现 | `worker.py` | 595-735 | `_try_transcript_fallback()` |
| 部分成功处理 | `database.py` | - | `update_task_completed()` |

---

## 9. 注意事项

### 9.1 隐式行为

1. **字幕保存优化**（worker.py line 448-467）
   - 无论用户是否请求字幕，只要下载器返回了字幕就会保存
   - 这样可以提高后续缓存命中率

2. **降级逻辑的单向性**
   - Audio Fallback：字幕 → 音频（自动触发）
   - Transcript Fallback：音频 → 字幕（需手动触发，仅在音频失败时）
   - 没有双向自动降级，避免逻辑混乱

3. **部分成功的任务状态**
   - 部分成功的任务标记为 `completed`，不是 `failed`
   - 成功的资源可以被后续请求复用
   - 客户端需检查 `result.partial_success` 和 `failure_details`

### 9.2 最佳实践

1. **客户端判断逻辑**
   ```python
   if response.cache_hit:
       # 缓存命中，立即使用
       return use_files(response.files)

   if response.status == "completed":
       if response.result.partial_success:
           # 部分成功，检查哪些资源可用
           if response.files.audio:
               use_audio()
           if response.files.transcript:
               use_transcript()
       else:
           # 完全成功
           use_all_files()
   ```

2. **错误处理**
   - 检查 `result.audio_fallback` 判断是否是降级结果
   - 检查 `result.partial_success` 判断是否部分成功
   - 检查 `result.failure_details` 获取详细失败原因
