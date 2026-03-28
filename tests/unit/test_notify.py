"""
Tests for NotificationService.

Covers notification sending, message formatting for completion/failure,
disabled service behavior, and WeCom API error handling.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings
from src.db.models import ErrorCode, Task, TaskStatus, VideoInfo, VideoResource
from src.services.notify import NotificationService


def _make_settings(**overrides) -> Settings:
    """Create Settings with test defaults and optional overrides."""
    defaults = {
        "api_key": "test-api-key",
        "wecom_webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        "debug": True,
        "data_dir": Path(tempfile.mkdtemp()),
        "base_url": "https://api.example.com",
        "tz": "UTC",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _make_task(**overrides) -> Task:
    """Create a Task with test defaults."""
    defaults = {
        "id": "test-task-001",
        "video_id": "dQw4w9WgXcQ",
        "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "status": TaskStatus.COMPLETED,
        "created_at": datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        "started_at": datetime(2025, 1, 1, 10, 1, 0, tzinfo=timezone.utc),
        "completed_at": datetime(2025, 1, 1, 10, 5, 0, tzinfo=timezone.utc),
        "audio_file_id": "file-audio-001",
        "transcript_file_id": "file-transcript-001",
    }
    defaults.update(overrides)
    return Task(**defaults)


def _make_video_resource() -> VideoResource:
    """Create a VideoResource with test data."""
    return VideoResource(
        video_id="dQw4w9WgXcQ",
        video_info=VideoInfo(
            title="Test Video Title",
            author="Test Author",
            duration=180,
            description="A test video description",
        ),
    )


@pytest.fixture
def mock_notifier():
    """Create a mock WeComNotifier."""
    notifier = MagicMock()
    notifier.send_markdown = MagicMock(return_value=MagicMock(is_success=lambda: True))
    return notifier


@pytest.fixture
def mock_db():
    """Create mock database that returns video resource."""
    from unittest.mock import AsyncMock

    db = AsyncMock()
    db.get_video_resource = AsyncMock(return_value=_make_video_resource())
    db.get_file = AsyncMock(return_value=MagicMock(
        format="m4a",
        filename="test.m4a",
        language=None,
    ))
    return db


@pytest.fixture
def notification_service(mock_notifier, mock_db):
    """Create NotificationService with mock notifier and db."""
    settings = _make_settings()

    with patch("src.services.notify.WECOM_AVAILABLE", True):
        service = NotificationService(settings=settings, db=mock_db)
        # Replace the notifier with our mock
        service.notifier = mock_notifier
        service.enabled = True
    return service


class TestNotifyCompleted:
    """Tests for task completion notifications."""

    @pytest.mark.asyncio
    async def test_sends_markdown_on_completion(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Should send markdown notification for completed task."""
        task = _make_task(status=TaskStatus.COMPLETED)

        await notification_service.notify_completed(task)

        mock_notifier.send_markdown.assert_called_once()
        call_kwargs = mock_notifier.send_markdown.call_args
        content = call_kwargs.kwargs.get("content") or call_kwargs[1].get("content", "")
        assert "Download Completed" in content
        assert "Test Video Title" in content

    @pytest.mark.asyncio
    async def test_completion_includes_video_info(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Completion notification should include video metadata."""
        task = _make_task(status=TaskStatus.COMPLETED)

        await notification_service.notify_completed(task)

        content = mock_notifier.send_markdown.call_args.kwargs.get("content", "")
        assert "Test Author" in content
        assert "Test Video Title" in content

    @pytest.mark.asyncio
    async def test_completion_includes_download_urls(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Completion notification should include file download URLs."""
        task = _make_task(
            status=TaskStatus.COMPLETED,
            audio_file_id="file-audio-001",
            transcript_file_id="file-transcript-001",
        )

        await notification_service.notify_completed(task)

        content = mock_notifier.send_markdown.call_args.kwargs.get("content", "")
        assert "file-audio-001" in content

    @pytest.mark.asyncio
    async def test_completion_with_downloader_info(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Completion notification should include downloader name when provided."""
        task = _make_task(status=TaskStatus.COMPLETED)

        await notification_service.notify_completed(task, downloader="ytdlp")

        content = mock_notifier.send_markdown.call_args.kwargs.get("content", "")
        assert "YTDLP" in content

    @pytest.mark.asyncio
    async def test_completion_with_transcript_fallback(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Completion notification should note transcript fallback."""
        task = _make_task(status=TaskStatus.COMPLETED)

        await notification_service.notify_completed(task, transcript_fallback=True)

        content = mock_notifier.send_markdown.call_args.kwargs.get("content", "")
        assert "transcript only" in content.lower() or "Audio download failed" in content


class TestNotifyFailed:
    """Tests for task failure notifications."""

    @pytest.mark.asyncio
    async def test_sends_markdown_on_failure(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Should send markdown notification for failed task."""
        task = _make_task(
            status=TaskStatus.FAILED,
            error_code=ErrorCode.DOWNLOAD_FAILED,
            error_message="Network timeout",
        )

        await notification_service.notify_failed(task, error="Network timeout")

        mock_notifier.send_markdown.assert_called_once()
        content = mock_notifier.send_markdown.call_args.kwargs.get("content", "")
        assert "DOWNLOAD_FAILED" in content
        assert "Network timeout" in content

    @pytest.mark.asyncio
    async def test_failure_mentions_all_for_system_errors(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """System errors should trigger mention_all=True."""
        task = _make_task(
            status=TaskStatus.FAILED,
            error_code=ErrorCode.INTERNAL_ERROR,
        )

        await notification_service.notify_failed(task, error="Internal error")

        call_kwargs = mock_notifier.send_markdown.call_args.kwargs
        assert call_kwargs.get("mention_all") is True

    @pytest.mark.asyncio
    async def test_failure_no_mention_for_expected_video_errors(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Expected video errors (private, unavailable, etc.) should not mention all."""
        task = _make_task(
            status=TaskStatus.FAILED,
            error_code=ErrorCode.VIDEO_PRIVATE,
        )

        await notification_service.notify_failed(task, error="Video is private")

        call_kwargs = mock_notifier.send_markdown.call_args.kwargs
        assert call_kwargs.get("mention_all") is False

    @pytest.mark.asyncio
    async def test_failure_includes_failed_downloaders(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Failure notification should list failed downloaders when provided."""
        task = _make_task(
            status=TaskStatus.FAILED,
            error_code=ErrorCode.DOWNLOAD_FAILED,
        )

        await notification_service.notify_failed(
            task, error="All failed", failed_downloaders=["ytdlp", "cdp"]
        )

        content = mock_notifier.send_markdown.call_args.kwargs.get("content", "")
        assert "YTDLP" in content
        assert "CDP" in content

    @pytest.mark.asyncio
    async def test_failure_skipped_title_for_video_errors(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """Expected video errors should use 'Download Skipped' title."""
        task = _make_task(
            status=TaskStatus.FAILED,
            error_code=ErrorCode.VIDEO_UNAVAILABLE,
        )

        await notification_service.notify_failed(task, error="Video not found")

        content = mock_notifier.send_markdown.call_args.kwargs.get("content", "")
        assert "Download Skipped" in content


class TestNotificationDisabled:
    """Tests for disabled notification service."""

    @pytest.mark.asyncio
    async def test_disabled_when_no_webhook_url(self):
        """Service should be disabled when webhook URL is empty."""
        settings = _make_settings(wecom_webhook_url="")

        with patch("src.services.notify.WECOM_AVAILABLE", True):
            service = NotificationService(settings=settings)

        assert service.enabled is False

    @pytest.mark.asyncio
    async def test_disabled_when_wecom_not_available(self):
        """Service should be disabled when wecom-notifier is not installed."""
        settings = _make_settings()

        with patch("src.services.notify.WECOM_AVAILABLE", False):
            service = NotificationService(settings=settings)

        assert service.enabled is False

    @pytest.mark.asyncio
    async def test_disabled_service_skips_completion(self, mock_db):
        """Disabled service should silently skip notify_completed."""
        settings = _make_settings(wecom_webhook_url="")

        with patch("src.services.notify.WECOM_AVAILABLE", True):
            service = NotificationService(settings=settings, db=mock_db)

        task = _make_task()
        # Should not raise any exception
        await service.notify_completed(task)

    @pytest.mark.asyncio
    async def test_disabled_service_skips_failure(self, mock_db):
        """Disabled service should silently skip notify_failed."""
        settings = _make_settings(wecom_webhook_url="")

        with patch("src.services.notify.WECOM_AVAILABLE", True):
            service = NotificationService(settings=settings, db=mock_db)

        task = _make_task(status=TaskStatus.FAILED, error_code=ErrorCode.DOWNLOAD_FAILED)
        # Should not raise any exception
        await service.notify_failed(task, error="test error")

    @pytest.mark.asyncio
    async def test_disabled_service_skips_startup(self):
        """Disabled service should silently skip notify_startup."""
        settings = _make_settings(wecom_webhook_url="")

        with patch("src.services.notify.WECOM_AVAILABLE", True):
            service = NotificationService(settings=settings)

        await service.notify_startup(version="1.0.0")


class TestWeComApiErrors:
    """Tests for WeCom API error handling."""

    @pytest.mark.asyncio
    async def test_api_error_does_not_raise(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """WeCom API errors should be caught and logged, not raised."""
        mock_notifier.send_markdown.side_effect = Exception("WeCom API timeout")
        task = _make_task(status=TaskStatus.COMPLETED)

        # Should not raise
        await notification_service.notify_completed(task)

    @pytest.mark.asyncio
    async def test_failure_api_error_does_not_raise(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """WeCom API errors during failure notification should be caught."""
        mock_notifier.send_markdown.side_effect = ConnectionError("Connection refused")
        task = _make_task(
            status=TaskStatus.FAILED,
            error_code=ErrorCode.DOWNLOAD_FAILED,
        )

        # Should not raise
        await notification_service.notify_failed(task, error="test")

    @pytest.mark.asyncio
    async def test_startup_api_error_does_not_raise(
        self, notification_service: NotificationService, mock_notifier: MagicMock
    ):
        """WeCom API errors during startup notification should be caught."""
        mock_notifier.send_markdown.side_effect = Exception("API error")

        # Should not raise
        await notification_service.notify_startup(version="1.0.0")


class TestHelperMethods:
    """Tests for internal helper methods."""

    def test_format_uptime_seconds(self):
        """Should format seconds-only uptime."""
        settings = _make_settings(wecom_webhook_url="")
        with patch("src.services.notify.WECOM_AVAILABLE", False):
            service = NotificationService(settings=settings)

        assert service._format_uptime(45) == "45 秒"

    def test_format_uptime_complex(self):
        """Should format complex uptime with days, hours, minutes."""
        settings = _make_settings(wecom_webhook_url="")
        with patch("src.services.notify.WECOM_AVAILABLE", False):
            service = NotificationService(settings=settings)

        # 1 day, 2 hours, 30 minutes, 15 seconds
        seconds = 86400 + 7200 + 1800 + 15
        result = service._format_uptime(seconds)
        assert "1 天" in result
        assert "2 小时" in result
        assert "30 分钟" in result
        assert "15 秒" in result

    def test_format_uptime_zero(self):
        """Should format zero seconds."""
        settings = _make_settings(wecom_webhook_url="")
        with patch("src.services.notify.WECOM_AVAILABLE", False):
            service = NotificationService(settings=settings)

        assert service._format_uptime(0) == "0 秒"

    def test_format_local_time_none(self):
        """Should return N/A for None datetime."""
        settings = _make_settings(wecom_webhook_url="")
        with patch("src.services.notify.WECOM_AVAILABLE", False):
            service = NotificationService(settings=settings)

        assert service._format_local_time(None) == "N/A"

    def test_format_local_time_utc(self):
        """Should format UTC datetime correctly."""
        settings = _make_settings(wecom_webhook_url="", tz="UTC")
        with patch("src.services.notify.WECOM_AVAILABLE", False):
            service = NotificationService(settings=settings)

        dt = datetime(2025, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = service._format_local_time(dt)
        assert "2025-06-15" in result
        assert "10:30:00" in result
