"""
测试 AllDownloadersFailed 携带的结构化下载器尝试记录（DownloaderAttempt）。

背景：worker 失败路径需要把"降级链里每个下载器的尝试结果（下载器名/
error_code/message）"序列化写入 tasks.failure_details 列，用于失败归因
统计。此前 AllDownloadersFailed 只保留拼接好的字符串列表（errors），
丢失了结构化的 error_code 信息，因此新增 attempts 字段承载结构化数据。
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderAttempt, DownloaderError
from src.downloaders.manager import DownloaderManager


def _make_manager(mock_settings: MagicMock) -> DownloaderManager:
    """构造禁用熔断器的 DownloaderManager，避免真实初始化下载器。"""
    mock_settings.circuit_breaker_enabled = False
    mock_settings.audio_download_priority = "ytdlp,tikhub"
    mock_settings.transcript_only_priority = "ytdlp,tikhub"
    manager = DownloaderManager(mock_settings)
    manager.get_metadata = AsyncMock(return_value=None)
    return manager


class TestAllDownloadersFailedAttempts:
    """AllDownloadersFailed.attempts 结构化记录测试。"""

    def test_default_attempts_is_empty_list(self):
        """未显式传入 attempts 时（旧调用点）应默认为空列表，保持向后兼容。"""
        error = AllDownloadersFailed(errors=["ytdlp: boom"])
        assert error.attempts == []

    def test_attempts_can_be_passed_explicitly(self):
        error = AllDownloadersFailed(
            errors=["ytdlp: boom"],
            attempts=[
                DownloaderAttempt(
                    downloader="ytdlp", error_code="NETWORK_ERROR", message="boom"
                )
            ],
        )
        assert len(error.attempts) == 1
        assert error.attempts[0].downloader == "ytdlp"
        assert error.attempts[0].error_code == "NETWORK_ERROR"

    @pytest.mark.asyncio
    async def test_download_with_fallback_collects_attempts_for_all_downloaders(
        self, tmp_path: Path
    ):
        """所有下载器都失败时，attempts 应包含每个下载器的结构化尝试记录。"""
        mock_downloader1 = MagicMock()
        mock_downloader1.name = "ytdlp"
        mock_downloader1.supports_resource_download = True
        mock_downloader1.download_resources = AsyncMock(
            side_effect=DownloaderError(
                message="network timeout",
                error_code=ErrorCode.NETWORK_ERROR,
                downloader="ytdlp",
                stop_fallback=False,
            )
        )
        mock_downloader1.should_retry = MagicMock(return_value=False)
        mock_downloader1.should_trigger_circuit_breaker = MagicMock(return_value=False)

        mock_downloader2 = MagicMock()
        mock_downloader2.name = "tikhub"
        mock_downloader2.supports_resource_download = True
        mock_downloader2.download_resources = AsyncMock(
            side_effect=DownloaderError(
                message="no cookies exported",
                error_code=ErrorCode.CDP_NO_COOKIES,
                downloader="tikhub",
                stop_fallback=False,
            )
        )
        mock_downloader2.should_retry = MagicMock(return_value=False)
        mock_downloader2.should_trigger_circuit_breaker = MagicMock(return_value=False)

        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        manager.downloaders = [mock_downloader1, mock_downloader2]

        with pytest.raises(AllDownloadersFailed) as exc_info:
            await manager.download_with_fallback(
                video_url="https://www.youtube.com/watch?v=test",
                video_id="test",
                output_dir=tmp_path,
                include_audio=True,
                include_transcript=True,
            )

        attempts = exc_info.value.attempts
        assert len(attempts) == 2
        assert attempts[0].downloader == "ytdlp"
        assert attempts[0].error_code == "NETWORK_ERROR"
        assert attempts[0].message == "network timeout"
        assert attempts[1].downloader == "tikhub"
        assert attempts[1].error_code == "CDP_NO_COOKIES"
        assert attempts[1].message == "no cookies exported"

    @pytest.mark.asyncio
    async def test_stop_fallback_attempts_only_include_tried_downloaders(
        self, tmp_path: Path
    ):
        """403 停止降级场景：attempts 只应包含实际尝试过的下载器（第一个）。"""
        mock_downloader1 = MagicMock()
        mock_downloader1.name = "ytdlp"
        mock_downloader1.supports_resource_download = True
        mock_downloader1.download_resources = AsyncMock(
            side_effect=DownloaderError(
                message="HTTP 403 Forbidden",
                error_code=ErrorCode.RATE_LIMITED,
                downloader="ytdlp",
                http_status_code=403,
                stop_fallback=True,
            )
        )
        mock_downloader1.should_retry = MagicMock(return_value=False)
        mock_downloader1.should_trigger_circuit_breaker = MagicMock(return_value=False)

        mock_downloader2 = MagicMock()
        mock_downloader2.name = "tikhub"
        mock_downloader2.supports_resource_download = True
        mock_downloader2.download_resources = AsyncMock()
        mock_downloader2.should_retry = MagicMock(return_value=False)

        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        manager.downloaders = [mock_downloader1, mock_downloader2]

        with pytest.raises(AllDownloadersFailed) as exc_info:
            await manager.download_with_fallback(
                video_url="https://www.youtube.com/watch?v=test",
                video_id="test",
                output_dir=tmp_path,
                include_audio=True,
                include_transcript=True,
            )

        attempts = exc_info.value.attempts
        assert len(attempts) == 1
        assert attempts[0].downloader == "ytdlp"
        assert attempts[0].error_code == "RATE_LIMITED"
        assert mock_downloader2.download_resources.call_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_attempt_recorded(self, tmp_path: Path):
        """熔断器开启跳过下载器时，attempts 应记录该下载器且带专用错误标识。"""
        mock_downloader1 = MagicMock()
        mock_downloader1.name = "ytdlp"
        mock_downloader1.supports_resource_download = True
        mock_downloader1.download_resources = AsyncMock()

        mock_downloader2 = MagicMock()
        mock_downloader2.name = "tikhub"
        mock_downloader2.supports_resource_download = True
        mock_downloader2.download_resources = AsyncMock(
            side_effect=DownloaderError(
                message="download failed",
                error_code=ErrorCode.DOWNLOAD_FAILED,
                downloader="tikhub",
            )
        )
        mock_downloader2.should_retry = MagicMock(return_value=False)
        mock_downloader2.should_trigger_circuit_breaker = MagicMock(return_value=False)

        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        manager.downloaders = [mock_downloader1, mock_downloader2]

        # 手动为 ytdlp 注入一个已打开的熔断器（enabled=False 时 manager 不会
        # 自动初始化 circuit_breakers，这里模拟"已经熔断"的场景）。
        from src.downloaders.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(name="ytdlp", failure_threshold=1, timeout=1800)
        breaker.force_open("simulated global failure")
        manager.circuit_breakers = {"ytdlp": breaker}

        with pytest.raises(AllDownloadersFailed) as exc_info:
            await manager.download_with_fallback(
                video_url="https://www.youtube.com/watch?v=test",
                video_id="test",
                output_dir=tmp_path,
                include_audio=True,
                include_transcript=True,
            )

        attempts = exc_info.value.attempts
        assert len(attempts) == 2
        assert attempts[0].downloader == "ytdlp"
        assert attempts[0].error_code == "CIRCUIT_BREAKER_OPEN"
        assert mock_downloader1.download_resources.call_count == 0
        assert attempts[1].downloader == "tikhub"
        assert attempts[1].error_code == "DOWNLOAD_FAILED"
