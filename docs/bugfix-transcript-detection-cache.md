# 修复：字幕探测结果缓存丢失问题

## 问题描述

### 场景复现

1. 客户端请求只下载字幕（`include_audio=false`, `include_transcript=true`）
2. 系统探测发现视频没有字幕（`has_transcript=false`）
3. 触发音频降级逻辑，尝试下载音频代替
4. 音频下载失败，任务标记为失败
5. **问题：** 字幕探测结果（`has_native_transcript=false`）未保存到数据库
6. 客户端再次提交同样的任务时，系统重新探测字幕，浪费 API 调用

### 影响评估

| 维度 | 影响 |
|------|------|
| **API 成本** | 每次重复探测消耗 API 配额（TikHub: $0.02/次） |
| **性能** | 重复网络请求，增加用户等待时间 |
| **缓存失效** | 智能资源复用机制部分失效 |

## 根本原因

**代码路径：** `src/core/worker.py` -> `_execute_download_with_manager` 方法

**问题分析：**

```python
# 第 374-380 行：首次下载，探测字幕可用性
result = await self.downloader_manager.download_with_fallback(...)

# 第 388-419 行：音频降级逻辑
if not need_audio and need_transcript and not result.has_transcript:
    result = await self.downloader_manager.download_with_fallback(...)  # 可能失败

# 第 436-440 行：保存探测结果
await self._update_video_resource(
    task.video_id,
    video_info,
    result.has_transcript,  # 如果音频降级失败，这行代码不会执行
)
```

**异常处理链：**

```
_execute_download_with_manager (408行音频下载失败)
  ↓ 抛出 DownloaderError
_execute_task (345行调用)
  ↓ 异常向上传播
_process_single_task (242行调用)
  ↓ except 捕获异常 (272行)
_handle_download_error (273行处理)
  ↓ 任务标记为失败
⚠️ has_native_transcript 从未保存到数据库
```

## 修复方案

### 实现思路

在异常处理中保存首次探测结果，确保即使下载失败也能缓存字幕可用性信息。

### 代码改动

**文件：** `src/core/worker.py`

**改动点：**

1. **追踪首次探测结果**

```python
# 声明变量追踪首次探测结果
first_result: Optional[DownloaderResult] = None

try:
    # 首次下载
    result = await self.downloader_manager.download_with_fallback(...)
    first_result = result  # 保存首次结果

    # 音频降级逻辑
    if audio_fallback:
        result = await self.downloader_manager.download_with_fallback(...)
        # 注意：此时 result 被覆盖，但 first_result 保留了字幕探测结果
```

2. **异常处理中保存探测结果**

```python
except (DownloaderError, AllDownloadersFailed) as e:
    # 即使下载失败，也尝试保存首次探测到的字幕可用性信息
    if first_result and first_result.video_metadata:
        try:
            video_info = convert_to_video_info(first_result.video_metadata)
            await self._update_video_resource(
                task.video_id,
                video_info,
                has_native_transcript=first_result.has_transcript
            )
            logger.info(
                f"Task {task.id}: Saved transcript detection result "
                f"(has_transcript={first_result.has_transcript}) despite download failure"
            )
        except Exception as save_error:
            # 保存失败不应影响原有错误处理
            logger.warning(f"Failed to save transcript detection result: {save_error}")

    # 重新抛出原始异常，让上层处理
    raise
```

### 改动优势

| 优势 | 说明 |
|------|------|
| ✅ **零成本** | 不增加额外 API 调用 |
| ✅ **最小改动** | 仅修改 1 个方法，约 40 行代码 |
| ✅ **向后兼容** | 不改变现有流程，不影响正常功能 |
| ✅ **低风险** | 保存失败不影响原有错误处理 |
| ✅ **完整覆盖** | 覆盖所有失败场景 |

## 测试验证

### 测试文件

**文件：** `tests/test_transcript_detection_cache.py`

### 测试用例

| 测试用例 | 场景 | 验证点 |
|---------|------|--------|
| `test_save_transcript_detection_on_audio_fallback_failure` | 音频降级失败 | 探测结果被保存 ✅ |
| `test_save_transcript_detection_on_first_download_failure` | 首次下载就失败 | 不会崩溃 ✅ |
| `test_normal_flow_still_works` | 正常下载成功 | 正常流程不受影响 ✅ |
| `test_audio_fallback_with_existing_audio` | 音频降级复用缓存 | 逻辑正确 ✅ |

### 测试结果

```bash
$ uv run pytest tests/test_transcript_detection_cache.py -v

============================== test session starts ==============================
collected 4 items

test_save_transcript_detection_on_audio_fallback_failure PASSED [ 25%]
test_save_transcript_detection_on_first_download_failure PASSED [ 50%]
test_normal_flow_still_works PASSED [ 75%]
test_audio_fallback_with_existing_audio PASSED [100%]

============================== 4 passed in 1.06s ===============================
```

## 效果评估

### 修复前

```
第一次请求（只要字幕）
  ↓ 探测：无字幕
  ↓ 音频降级失败
  ↓ has_native_transcript 未保存 ❌

第二次请求（同一视频）
  ↓ 重新探测：无字幕 💸 API 调用
  ↓ 音频降级失败
  ↓ has_native_transcript 未保存 ❌

第 N 次请求...
  ↓ 重复探测 💸💸💸
```

### 修复后

```
第一次请求（只要字幕）
  ↓ 探测：无字幕
  ↓ 音频降级失败
  ↓ has_native_transcript=false 保存 ✅

第二次请求（同一视频）
  ↓ 数据库查询：has_native_transcript=false（缓存命中）
  ↓ 直接跳过字幕探测 💰 节省成本
  ↓ 根据用户请求决定后续操作
```

### 成本节省

假设场景：100 个视频，每个被请求 5 次，其中 50% 无字幕且音频下载失败

- **修复前：** 100 × 5 × 50% = 250 次重复探测
- **修复后：** 100 × 1 × 50% = 50 次探测（首次）
- **节省：** 200 次 API 调用 = $4.00（TikHub）

## 相关文件

- `src/core/worker.py` - 核心修复
- `tests/test_transcript_detection_cache.py` - 测试用例
- `docs/bugfix-transcript-detection-cache.md` - 本文档

## 提交信息

```
fix(worker): 修复音频降级失败时字幕探测结果未保存的问题

问题：
- 用户请求字幕，探测到无字幕后触发音频降级
- 音频下载失败时，字幕探测结果（has_native_transcript）未保存
- 下次请求同一视频时，重复探测浪费 API 调用

修复：
- 在异常处理中保存首次探测结果
- 确保即使下载失败也能缓存字幕可用性信息
- 添加完整测试覆盖

影响：
- 节省重复 API 调用成本（TikHub: $0.02/次）
- 提升缓存命中率，改善用户体验
```

---

**修复日期：** 2026-01-31
**修复人员：** Claude Sonnet 4.5
**测试状态：** ✅ 全部通过
