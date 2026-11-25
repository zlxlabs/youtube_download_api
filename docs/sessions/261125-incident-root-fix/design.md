# #20 字幕 429 误判与三态契约（卡 1/2）

生产事故：任务 `ff4f9e55-3ff6-4a85-b93b-49bca3ea43cc`（`p8lvQQebvpg`）首次提取 177 条字幕，下载 `zh-Hans` 遇 HTTP 429。CDP 吞错返回 `success=True, has_transcript=False`，Worker 误回退音频并写 `has_native_transcript=False`。

## 两卡拆分

- 卡 1（本卡）：三态契约、单次提取、429 失败态、负缓存捷径、RATE_LIMITED 不阻塞 worker。
- 卡 2：`player_js_version` / POT / 删除 `YtdlpDownloader` 与 cookies 同步。卡 1 合并后再派。
- PR/commit 只写 `Refs #20`。

## 锁定决策

- 用已有可空布尔列表达三态，不新增状态机列。
- `False` 仅「完整探测且列表为空」；未探测/失败（含 429）为 `None`。
- 仅字幕 429：不音频回退、不写 False、不 `sleep(600s)`、不把失败当探测成功降级 IP 熔断。
- 直接用首次 `SubtitleInfo.url`；url 空则上抛，不回退二次 `ydl.download()`。
- `info.language` 缺失时，`tlang=` 机翻轨不得排在原语言轨前。
- 已否决：429 多语言轮询；独立 `transcript_status` 列。

## 契约

`DownloaderResult.has_transcript: Optional[bool] = None`。Worker 成功路径只传 `first_result.has_transcript if need_transcript else None`。库列由 `database.update_video_resource` 在 `None` 时跳过。

新模块 `subtitle_fetcher.py` 承接直下与选轨；`download_subtitle` 失败打 `SUBTITLE-DOWNLOAD-FAILED`。`task_service` 删除历史 False 负缓存捷径。RATE_LIMITED 仍入队，休眠改由自适应间隔承担。
