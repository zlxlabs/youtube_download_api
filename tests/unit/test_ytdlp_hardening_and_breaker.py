"""Regression tests for yt-dlp defaults and selective circuit-breaker accounting."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config import Settings
from src.core.downloader import YtDlpLogger
from src.db.models import ErrorCode
from src.downloaders.cdp.audio_downloader import AudioDownloader
from src.downloaders.cdp.downloader import CDPDownloader
from src.downloaders.circuit_breaker import CircuitBreaker, CircuitState
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderError
from src.downloaders.manager import DownloaderManager


class _CDPDownloaderStub:
    name = "cdp"
    supports_resource_download = True

    def should_trigger_circuit_breaker(self, error: Exception) -> bool:
        return CDPDownloader.should_trigger_circuit_breaker(self, error)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        api_key="test-key",
        cdp_enable_pot_token=False,
        audio_download_priority="cdp",
        transcript_only_priority="cdp",
        metadata_priority="cdp",
    )


@pytest.mark.asyncio
async def test_audio_extraction_options_use_yt_dlp_defaults_and_log_warnings(monkeypatch):
    downloader = AudioDownloader(_settings())
    captured = {}
    info = {
        "url": "https://example.test/audio?itag=140&n=token&sig=signature&expire=9999999999",
        "protocol": "https",
        "format_id": "140",
        "ext": "m4a",
        "title": "sample",
    }

    async def run_in_thread(func, *args):
        captured["opts"] = args[1]
        return info

    monkeypatch.setattr("src.downloaders.cdp.audio_downloader.asyncio.to_thread", run_in_thread)

    result = await downloader.extract_audio_url(
        "https://www.youtube.com/watch?v=sample", "sample", Path("cookies.txt"), "task"
    )

    opts = captured["opts"]
    youtube_args = opts["extractor_args"].get("youtube", {})
    assert "player_js_version" not in youtube_args
    assert "player_client" not in youtube_args
    assert "po_token" not in youtube_args
    assert opts["no_warnings"] is False
    assert isinstance(opts["logger"], YtDlpLogger)
    with patch("src.core.downloader.logger.warning") as warning:
        opts["logger"].warning("n challenge solving failed")
    warning.assert_called_once()
    assert result.itag == 140


@pytest.mark.asyncio
async def test_requested_format_unavailable_is_not_retried():
    downloader = AudioDownloader(_settings())
    downloader._extract_and_parse_video_info = AsyncMock(
        side_effect=RuntimeError("Requested format is not available")
    )

    with pytest.raises(DownloaderError) as caught:
        await downloader.extract_video_info(
            "https://www.youtube.com/watch?v=sample",
            "sample",
            Path("cookies.txt"),
            "task",
        )

    assert caught.value.error_code == ErrorCode.CDP_YTDLP_FAILED
    downloader._extract_and_parse_video_info.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code",
    [ErrorCode.VIDEO_PRIVATE, ErrorCode.VIDEO_UNAVAILABLE, ErrorCode.DOWNLOAD_FAILED],
)
async def test_manager_does_not_open_breaker_for_repeated_video_errors(error_code):
    manager = DownloaderManager(_settings())
    downloader = _CDPDownloaderStub()
    breaker = CircuitBreaker("cdp", failure_threshold=5)
    manager.downloaders = [downloader]
    manager.circuit_breakers = {"cdp": breaker}
    manager.get_metadata = AsyncMock(return_value=None)
    manager._download_with_downloader = AsyncMock(
        side_effect=DownloaderError("video-specific failure", error_code=error_code)
    )

    for _ in range(10):
        with pytest.raises(AllDownloadersFailed):
            await manager.download_with_fallback(
                "https://www.youtube.com/watch?v=sample",
                "sample",
                Path("/tmp"),
                include_audio=True,
                include_transcript=False,
            )

    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


@pytest.mark.asyncio
async def test_manager_records_systemic_cdp_failure():
    manager = DownloaderManager(_settings())
    downloader = _CDPDownloaderStub()
    breaker = CircuitBreaker("cdp", failure_threshold=5)
    manager.downloaders = [downloader]
    manager.circuit_breakers = {"cdp": breaker}
    manager.get_metadata = AsyncMock(return_value=None)
    manager._download_with_downloader = AsyncMock(
        side_effect=DownloaderError(
            "browser connection timed out", error_code=ErrorCode.CDP_CONNECTION_TIMEOUT
        )
    )

    with pytest.raises(AllDownloadersFailed):
        await manager.download_with_fallback(
            "https://www.youtube.com/watch?v=sample",
            "sample",
            Path("/tmp"),
            include_audio=True,
            include_transcript=False,
        )

    assert breaker.failure_count == 1
    assert breaker.state is CircuitState.CLOSED


def test_default_resource_priorities_exclude_ytdlp():
    settings = Settings(_env_file=None, api_key="test-key")

    assert settings.audio_download_priority == "cdp,tikhub"
    assert settings.transcript_only_priority == "cdp,tikhub"
    assert "ytdlp" not in settings.audio_download_priority.split(",")
    assert "ytdlp" not in settings.transcript_only_priority.split(",")
