# TikHub 音频下载卡住问题记录

## 问题描述
TikHub 返回的音频 URL 在浏览器里可播放，但项目里会卡在：

```
[tikhub] Sending GET request...
```

## 原因分析
原始 `_download_audio_simple` 使用：

```
response = await client.get(...)
response.content
```

该写法需要 **完整下载结束** 才会返回，所以：

- 中间不会输出进度日志。
- 在当前环境下 googlevideo 单连接吞吐很低。
- URL 会发生 302 跳转与 TLS 重新协商，进一步降低速度。

结果就是看起来“卡住”，实际上在慢速下载。

## 解决方案
改为 Range 分段下载：

- 使用 `Range: bytes=start-end` 分段请求（1MB）。
- 增加浏览器样式 `User-Agent`。
- 每 10% 输出一次进度日志。
- 避免单条长连接长时间无响应。

实现位置：`src/downloaders/tikhub_downloader.py`
- 入口改为 `_download_audio_chunked`，不再走 `_download_audio_simple`。
- 新增辅助：`_build_media_headers`、`_parse_content_range_total`。

## 验证结果（使用日志中的 URL）
使用日志里的 googlevideo URL（itag=140，约 36.4MB）：

- `_download_audio_chunked` 完整下载成功。
- 文件大小：38156221 bytes。
- 进度日志从 10% 到 100% 正常输出。
- 输出示例：`data/tmp/lbESr58-7DQ.m4a`。

## 备注
- 如果吞吐仍偏低，可以调大 `range_size`（如 2-4MB）或加入分段重试。
- 调试时保持 `read` 超时适中（当前 120s），避免单段请求挂死。
