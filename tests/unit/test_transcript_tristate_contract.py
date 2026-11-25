"""字幕三态契约、单次提取与 429 失败态回归。"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.core.ip_ban_models import IPBanLevel
from src.core.worker import DownloadWorker
from src.db.models import ErrorCode, Task, TaskPriority, TaskStatus
from src.downloaders.cdp.models import SubtitleInfo
from src.downloaders.cdp.subtitle_fetcher import fetch_subtitle, sort_subtitle_tracks
from src.downloaders.exceptions import DownloaderError
from src.downloaders.models import DownloaderResult, VideoMetadata


def _meta() -> VideoMetadata:
    return VideoMetadata(video_id="vid123", title="T")


def _task(audio: bool, transcript: bool) -> Task:
    return Task(
        id="task-1",
        video_id="vid123",
        video_url="https://www.youtube.com/watch?v=vid123",
        status=TaskStatus.PENDING,
        include_audio=audio,
        include_transcript=transcript,
        priority=TaskPriority.NORMAL,
        created_at=datetime.now(timezone.utc),
        retry_count=0,
    )


def _sub(lang: str, url: str, auto: bool = True) -> SubtitleInfo:
    return SubtitleInfo(lang=lang, url=url, ext="json3", is_auto=auto)


def _httpx(status: int, content: bytes = b"") -> AsyncMock:
    response = MagicMock(status_code=status, content=content)
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    return client


def _worker(settings: Settings) -> DownloadWorker:
    db = AsyncMock()
    db.increment_retry_count = AsyncMock(return_value=1)
    task_service = MagicMock()
    task_service.task_queue = MagicMock()
    task_service.task_queue.put = AsyncMock()
    file_service = MagicMock()
    file_service.get_all_files_for_video = AsyncMock(
        return_value={"audio": None, "transcript": None}
    )
    file_service.create_file_record = AsyncMock(return_value=MagicMock(id="file-id"))
    return DownloadWorker(
        db=db,
        settings=settings,
        task_service=task_service,
        file_service=file_service,
        callback_service=MagicMock(),
        notify_service=AsyncMock(),
        downloader_manager=MagicMock(),
        metrics_collector=None,
    )


def test_downloader_result_default_has_transcript_is_none() -> None:
    result = DownloaderResult(success=True, downloader="cdp", video_metadata=_meta())
    assert result.has_transcript is None


def test_missing_language_prefers_non_tlang_original_track() -> None:
    tracks = [
        _sub("zh-Hans", "https://youtube.com/api/timedtext?lang=en&tlang=zh-Hans"),
        _sub("en", "https://youtube.com/api/timedtext?lang=en"),
        _sub("en-orig", "https://youtube.com/api/timedtext?lang=en-orig"),
    ]
    sort_subtitle_tracks(tracks, original_language=None)
    assert "tlang=" not in tracks[0].url
    assert tracks[0].lang in {"en", "en-orig"}


def test_original_language_ranks_orig_suffix_first() -> None:
    tracks = [
        _sub("zh-Hans", "https://youtube.com/api/timedtext?lang=en&tlang=zh-Hans"),
        _sub("en-orig", "https://youtube.com/api/timedtext?lang=en-orig"),
    ]
    sort_subtitle_tracks(tracks, original_language="en")
    assert tracks[0].lang == "en-orig"


@pytest.mark.asyncio
async def test_fetch_subtitle_http_429_maps_to_rate_limited(tmp_path: Path) -> None:
    subtitle = _sub("zh-Hans", "https://example.test/timedtext?lang=zh-Hans")
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n")
    client = _httpx(429)
    with (
        patch("src.downloaders.cdp.subtitle_fetcher.httpx.AsyncClient", return_value=client),
        patch("yt_dlp.YoutubeDL") as ydl,
        pytest.raises(DownloaderError) as exc,
    ):
        await fetch_subtitle(subtitle, "vid123", cookie_file, tmp_path)
    assert exc.value.error_code == ErrorCode.RATE_LIMITED
    assert exc.value.http_status_code == 429
    ydl.assert_not_called()
    client.get.assert_awaited_once_with(subtitle.url)


@pytest.mark.asyncio
async def test_fetch_subtitle_empty_url_fails_without_ytdlp(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n")
    with patch("yt_dlp.YoutubeDL") as ydl, pytest.raises(DownloaderError) as exc:
        await fetch_subtitle(_sub("en", ""), "vid123", cookie_file, tmp_path)
    assert exc.value.error_code == ErrorCode.DOWNLOAD_FAILED
    ydl.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_subtitle_writes_video_id_lang_suffix(tmp_path: Path) -> None:
    subtitle = SubtitleInfo(
        lang="en", url="https://example.test/timedtext?lang=en", ext="vtt"
    )
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n")
    with patch(
        "src.downloaders.cdp.subtitle_fetcher.httpx.AsyncClient",
        return_value=_httpx(200, b"WEBVTT\n"),
    ):
        path = await fetch_subtitle(subtitle, "vid123", cookie_file, tmp_path)
    assert path.name == "vid123.en.vtt"
    assert path.read_bytes() == b"WEBVTT\n"


@pytest.mark.asyncio
async def test_audio_only_does_not_overwrite_native_transcript(
    test_settings: Settings, tmp_path: Path
) -> None:
    worker = _worker(test_settings)
    audio_file = tmp_path / "a.m4a"
    audio_file.write_text("audio")
    worker.downloader_manager.download_with_fallback = AsyncMock(
        return_value=DownloaderResult(
            success=True,
            downloader="cdp",
            video_metadata=_meta(),
            audio_path=audio_file,
            has_transcript=None,
        )
    )
    with patch("pathlib.Path.exists", return_value=True):
        await worker._execute_download_with_manager(
            task=_task(True, False),
            existing_audio=None,
            existing_transcript=None,
            need_audio=True,
            need_transcript=False,
        )
    assert worker.db.update_video_resource.call_args.kwargs["has_native_transcript"] is None


@pytest.mark.asyncio
async def test_subtitle_429_e2e_no_fallback_no_false_cache_no_ban_downgrade(
    test_settings: Settings,
) -> None:
    test_settings.ip_ban_min_wait_before_retry = 60
    test_settings.ip_ban_max_retry_interval = 60
    worker = _worker(test_settings)
    await worker.ip_ban_breaker.trigger_full_ban(reason="seed")
    worker.ip_ban_breaker.banned_at -= timedelta(seconds=120)
    task = _task(False, True)
    worker.task_service.get_next_task = AsyncMock(return_value=task)
    worker.db.get_task = AsyncMock(return_value=task)
    worker.downloader_manager.download_with_fallback = AsyncMock(
        side_effect=DownloaderError(
            message="Too many requests (HTTP 429)",
            error_code=ErrorCode.RATE_LIMITED,
            downloader="cdp",
            http_status_code=429,
            operation="transcript",
        )
    )
    worker._running = True
    with patch("src.core.worker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await worker._process_next_task()
    assert worker.downloader_manager.download_with_fallback.call_count == 1
    worker.db.update_video_resource.assert_not_called()
    assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.FULLY_BANNED
    for call in mock_sleep.call_args_list:
        delay = call.args[0] if call.args else 0
        assert delay < 600


@pytest.mark.asyncio
async def test_rate_limited_retry_does_not_block_on_sleep(test_settings: Settings) -> None:
    worker = _worker(test_settings)
    error = DownloaderError(
        message="Too many requests (HTTP 429)",
        error_code=ErrorCode.RATE_LIMITED,
        downloader="cdp",
        http_status_code=429,
        operation="transcript",
    )
    loop = asyncio.get_running_loop()
    with (
        patch("src.core.worker.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch.object(loop, "call_later") as mock_later,
    ):
        await worker._handle_download_error(_task(False, True), error)
    mock_sleep.assert_not_called()
    worker.task_service.task_queue.put.assert_not_called()
    mock_later.assert_called_once()
    assert mock_later.call_args[0][0] >= 600


@pytest.mark.asyncio
async def test_download_subtitle_logs_marker_and_reraises(
    test_settings: Settings, tmp_path: Path
) -> None:
    from src.downloaders.cdp.audio_downloader import AudioDownloader

    downloader = AudioDownloader(test_settings, downloader_name="cdp")
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n")
    with (
        patch(
            "src.downloaders.cdp.audio_downloader.fetch_subtitle",
            side_effect=DownloaderError(
                message="Too many requests (HTTP 429)",
                error_code=ErrorCode.RATE_LIMITED,
                downloader="cdp",
                http_status_code=429,
                operation="transcript",
            ),
        ),
        patch("src.downloaders.cdp.audio_downloader.logger.error") as mock_log,
        pytest.raises(DownloaderError) as exc,
    ):
        await downloader.download_subtitle(
            subtitle=_sub("en", "https://example.test/x"),
            video_id="vid123",
            cookie_file=cookie_file,
            output_dir=tmp_path,
        )
    assert exc.value.error_code == ErrorCode.RATE_LIMITED
    assert "SUBTITLE-DOWNLOAD-FAILED" in mock_log.call_args[0][0]
