# CDP 下载器 M4A 转换实现

## 背景

CDP 下载器当前下载 YouTube 音频时，最终文件格式为 **webm**（通常是 Opus 编码），而项目标准要求统一输出 **M4A** 格式（128kbps AAC）。

这导致：
- API 响应不一致（有时是 webm，有时是 m4a）
- 客户端需要处理多种格式
- 不符合 README 承诺的 "M4A 格式，128kbps 高质量音频"

## 解决方案

在 CDP 下载器的 `AudioDownloader` 模块中，下载完成后自动检测文件格式，如果是 webm 则调用 `TranscodeService` 转换为 m4a。

### 修改文件

1. **src/downloaders/cdp/audio_downloader.py**
   - 导入 `TranscodeService`
   - 初始化转码服务实例
   - 新增 `_convert_to_m4a_if_needed()` 方法
   - 在三个下载成功返回点调用转换逻辑

2. **src/db/models.py**
   - 新增错误码：`CDP_TRANSCODE_FAILED`

3. **tests/test_cdp_transcode.py**
   - 新增单元测试覆盖转码逻辑

## 实现细节

### 转换逻辑

```python
async def _convert_to_m4a_if_needed(
    self,
    file_path: Path,
    output_dir: Path,
) -> Path:
    """
    如果文件不是 m4a 格式，转换为 m4a。

    检查文件扩展名 → webm → 调用 TranscodeService
                   → m4a → 直接返回（无需转换）

    转换成功后删除原始 webm 文件。
    """
```

### 调用点

在 `download_audio()` 方法的三个成功返回点：

1. **curl_cffi 分片下载成功后**
   ```python
   if success:
       final_path = await self._convert_to_m4a_if_needed(target_path, output_dir)
       return final_path
   ```

2. **curl_cffi 单线程下载成功后**
   ```python
   if success:
       final_path = await self._convert_to_m4a_if_needed(target_path, output_dir)
       return final_path
   ```

3. **yt-dlp 兜底下载成功后**
   ```python
   if ytdlp_path and ytdlp_path.exists():
       final_path = await self._convert_to_m4a_if_needed(ytdlp_path, output_dir)
       return final_path
   ```

## 转码策略

利用 `TranscodeService` 的智能转码：

1. **检测音频编码**：
   - 如果已经是 AAC → 直接封装（remux），速度快
   - 如果是 Opus/Vorbis → 重新编码为 AAC

2. **质量参数**：
   - 目标比特率：128kbps（从 `settings.audio_quality` 读取）
   - 编码器：AAC
   - 格式：M4A 容器

3. **性能影响**：
   - WebM (Opus) → M4A (AAC) 转换时间：~2-5秒（10MB 文件）
   - 相对于总下载时间（30-120秒）：占比 2-8%
   - 影响可接受

## 错误处理

转码失败时抛出 `DownloaderError`：

```python
raise DownloaderError(
    message=f"Failed to convert {file_path.suffix} to m4a: {str(e)}",
    error_code=ErrorCode.CDP_TRANSCODE_FAILED,
    downloader=self.downloader_name,
)
```

这会触发下载器降级策略（CDP → ytdlp → tikhub）。

## 测试覆盖

单元测试场景：

1. ✅ M4A 文件无需转换
2. ✅ WebM 文件成功转换为 M4A
3. ✅ 转换成功后删除原始 WebM
4. ✅ 转码失败抛出异常
5. ✅ `download_audio` 集成测试

## 影响范围

### 受益模块

- **API 响应一致性**：所有下载器统一返回 M4A
- **客户端简化**：无需处理多格式
- **存储优化**：AAC 编码效率高于 Opus（同等质量下）

### 无影响模块

- **ytdlp 下载器**：已经输出 M4A
- **tikhub 下载器**：已经输出 M4A
- **人工上传**：已经有转码逻辑

## 配置项

无需新增配置，复用现有配置：

- `AUDIO_QUALITY`：音频比特率（默认 128kbps）
- CDP 下载器已启用时自动生效

## 上线建议

1. **测试验证**：
   ```bash
   pytest tests/test_cdp_transcode.py -v
   ```

2. **集成测试**：
   - 下载一个视频，检查最终文件是否是 M4A
   - 检查日志中的转码信息

3. **性能监控**：
   - 观察转码时间占比
   - 如有性能问题，考虑异步转码

4. **回滚计划**：
   - 如转码频繁失败，可临时禁用 CDP 下载器
   - 降级到 ytdlp/tikhub

## 未来优化

1. **异步转码**：下载完成立即返回 webm，后台转码
2. **缓存转码结果**：避免重复转码同一文件
3. **支持更多格式**：MP3、FLAC 等
4. **自适应比特率**：根据原始文件自动选择

## 参考

- [TranscodeService 文档](../src/services/transcode_service.py)
- [CDP 下载器设计](cdp_downloader_design.md)
- [项目 README](../README.md#功能特性)
