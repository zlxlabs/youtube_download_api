# TikHub 下载器重试机制分析

## 当前设计

### 1. TikHub 下载器自身

**❌ 没有内部重试逻辑**

TikHub 下载器的 `download()` 方法**不会自动重试**。一次 API 调用失败就立即返回错误。

```python
# src/downloaders/tikhub_downloader.py:81-224
async def download(...):
    try:
        # 1. 获取视频信息
        video_data = await self._fetch_video_info(...)

        # 2. 下载音频（如果需要）
        if include_audio:
            audio_path = await self._download_audio_chunked(...)

        # 3. 下载字幕（如果需要）
        if include_transcript:
            transcript_path = await self._download_subtitle(...)

        return DownloaderResult(...)

    except Exception as e:
        # 直接抛出，不重试
        raise DownloaderError(...)
```

### 2. 下载器管理器 (DownloaderManager)

下载器管理器通过 `should_retry()` 方法来决定策略：

#### 策略判断逻辑 (manager.py:301-308)

```python
except DownloaderError as e:
    # 判断是否应该重试当前下载器（而非降级）
    if downloader.should_retry(e):
        logger.info(f"Error is retryable, not falling back to next downloader")
        raise  # 重新抛出异常，触发更高层重试

    # 否则，继续尝试下一个下载器
    logger.info(f"Falling back to next downloader...")
    continue
```

### 3. TikHub 的 should_retry() 策略

**设计原则**：TikHub 是付费 API，应该优先降级而不是重试

```python
# src/downloaders/tikhub_downloader.py:817-855
def should_retry(self, error: Exception) -> bool:
    """
    判断错误是否应该重试当前下载器。

    TikHub 是付费 API，大部分错误不应该重试而应该降级。
    """
    if isinstance(error, DownloaderError):
        error_code = error.error_code

        # 唯一例外：真正的网络超时（而不是 HTTP 错误）
        if error_code == ErrorCode.NETWORK_ERROR:
            # 检查是否是 HTTP 错误
            error_msg = str(error.message).lower()
            if any(keyword in error_msg for keyword in ["http", "redirect", "status"]):
                return False  # HTTP 错误 → 降级

            # 真正的网络超时可以重试一次
            return True  # 网络超时 → 重试

        # 其他错误降级
        return False

    # 默认：降级
    return False
```

## 问题分析：ConnectError 会如何处理？

### 用户遇到的错误

```
ConnectError: ConnectError('')
```

### 处理流程

1. **捕获异常** (tikhub_downloader.py:213-224)
   ```python
   except Exception as e:
       error_msg = str(e) if str(e) else repr(e)
       raise DownloaderError(
           message=f"{type(e).__name__}: {error_msg}",
           error_code=ErrorCode.DOWNLOAD_FAILED,  # ← 注意这里！
           downloader=self.name,
       )
   ```

2. **错误码是 `DOWNLOAD_FAILED`**，不是 `NETWORK_ERROR`

3. **should_retry() 返回 False**
   ```python
   if error_code == ErrorCode.NETWORK_ERROR:  # ← 不满足
       ...
       return True

   return False  # ← 会走到这里
   ```

4. **Manager 执行降级**
   ```
   Falling back to next downloader...
   ```

## 问题所在

**ConnectError 被错误地归类为 `DOWNLOAD_FAILED` 而不是 `NETWORK_ERROR`！**

这导致：
- ✅ 降级机制正常工作（切换到 ytdlp）
- ❌ 但这不是最优策略（ConnectError 可能是临时性的，应该重试）

## 建议修复

### 方案 1: 修复错误码映射（推荐）

```python
# src/downloaders/tikhub_downloader.py:213-224
except httpx.ConnectError as e:
    # ConnectError 应该映射为 NETWORK_ERROR
    logger.error(f"[tikhub] Connection error: {e}", exc_info=True)
    raise DownloaderError(
        message=f"Connection failed: {e}",
        error_code=ErrorCode.NETWORK_ERROR,  # ← 修改这里
        downloader=self.name,
    ) from e

except Exception as e:
    # 其他未知错误
    error_msg = str(e) if str(e) else repr(e)
    logger.error(f"[tikhub] Unexpected error: {type(e).__name__}: {error_msg}", exc_info=True)
    raise DownloaderError(
        message=f"{type(e).__name__}: {error_msg}",
        error_code=ErrorCode.DOWNLOAD_FAILED,
        downloader=self.name,
    ) from e
```

### 方案 2: 添加 httpx 内置重试（可选）

```python
# src/downloaders/tikhub_downloader.py:51-56
self.client = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, read=300.0),
    follow_redirects=True,
    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
    transport=httpx.AsyncHTTPTransport(retries=1),  # ← 添加这行
)
```

**优点**：在 HTTP 层面自动重试连接失败
**缺点**：会增加 API 调用次数（付费）

## 当前行为总结

| 错误类型 | 错误码 | should_retry() | Manager 行为 | 说明 |
|---------|--------|----------------|-------------|------|
| HTTP 401/429 | DOWNLOAD_FAILED | False | 降级到 ytdlp | ✅ 正确 |
| ConnectError | DOWNLOAD_FAILED | False | 降级到 ytdlp | ⚠️ 可优化 |
| TimeoutException | NETWORK_ERROR | True | 重试 TikHub | ✅ 正确 |
| 其他异常 | DOWNLOAD_FAILED | False | 降级到 ytdlp | ✅ 正确 |

## 重试层级

系统有**三层重试机制**：

### 第 1 层：httpx 客户端（当前未启用）
- 位置：HTTP 传输层
- 触发条件：连接失败
- 配置：`httpx.AsyncHTTPTransport(retries=N)`
- 状态：**未启用**

### 第 2 层：下载器管理器 (should_retry)
- 位置：下载器切换逻辑
- 触发条件：`should_retry() 返回 True`
- 行为：重新抛出异常，由更高层重试
- 状态：**已启用**，但 ConnectError 不会触发

### 第 3 层：任务队列
- 位置：任务重试机制
- 触发条件：任务失败且错误可重试
- 行为：将任务重新加入队列
- 状态：**已启用**（在 worker.py 中）

## 结论

**当前 TikHub 下载器没有自动重试网络连接错误**。

遇到 `ConnectError` 时：
1. ❌ TikHub 自身不重试
2. ❌ 错误码被错误映射为 `DOWNLOAD_FAILED`
3. ✅ 管理器执行降级，切换到 ytdlp
4. ✅ 如果所有下载器都失败，任务队列会重试整个任务

**建议**：
- 修复 ConnectError 的错误码映射（方案 1）
- 这样临时性网络问题会触发重试，而不是直接降级
- 降级应该留给真正需要的情况（如 API 配额用尽）
