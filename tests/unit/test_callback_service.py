"""
Tests for CallbackService.

Covers webhook callback delivery, error handling, retry logic,
and missing URL scenarios.
"""

import hashlib
import hmac
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.db.models import (
    CallbackStatus,
    ErrorCode,
    FileRecord,
    FileType,
    Task,
    TaskStatus,
    VideoInfo,
    VideoResource,
)
from src.services.callback_service import CallbackService, verify_callback_signature


def _make_task(**overrides) -> Task:
    """Create a Task with test defaults and optional overrides."""
    defaults = {
        "id": "test-task-001",
        "video_id": "dQw4w9WgXcQ",
        "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "status": TaskStatus.COMPLETED,
        "callback_url": "https://example.com/webhook",
        "callback_secret": "test-secret-key",
        "audio_file_id": "file-audio-001",
        "transcript_file_id": "file-transcript-001",
    }
    defaults.update(overrides)
    return Task(**defaults)


def _make_file_record(file_id: str, file_type: FileType, **overrides) -> FileRecord:
    """Create a FileRecord with test defaults."""
    defaults = {
        "id": file_id,
        "video_id": "dQw4w9WgXcQ",
        "file_type": file_type,
        "filename": f"test.{'m4a' if file_type == FileType.AUDIO else 'json'}",
        "filepath": f"files/{file_id}",
        "size": 1024,
        "format": "m4a" if file_type == FileType.AUDIO else "json",
        "expires_at": datetime(2025, 12, 31, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return FileRecord(**defaults)


@pytest.fixture
def mock_db() -> AsyncMock:
    """Create mock database with common return values."""
    db = AsyncMock()
    db.get_file = AsyncMock(side_effect=lambda fid: {
        "file-audio-001": _make_file_record("file-audio-001", FileType.AUDIO),
        "file-transcript-001": _make_file_record(
            "file-transcript-001", FileType.TRANSCRIPT, language="en"
        ),
    }.get(fid))
    db.get_video_resource = AsyncMock(return_value=VideoResource(
        video_id="dQw4w9WgXcQ",
        video_info=VideoInfo(
            title="Test Video",
            author="Test Author",
            duration=120,
        ),
    ))
    db.update_callback_status = AsyncMock()
    return db


@pytest.fixture
def callback_service(mock_db: AsyncMock) -> CallbackService:
    """Create CallbackService with mock dependencies."""
    return CallbackService(
        db=mock_db,
        file_service=None,
        base_url="https://api.example.com",
    )


class TestSendCallbackSuccess:
    """Tests for successful callback delivery."""

    @pytest.mark.asyncio
    async def test_successful_delivery(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """Callback should succeed when remote returns 200."""
        task = _make_task()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await callback_service.send_callback(task)

        assert result is True
        mock_db.update_callback_status.assert_awaited_once_with(
            task.id, CallbackStatus.SUCCESS, 1
        )

    @pytest.mark.asyncio
    async def test_payload_includes_video_info(
        self, callback_service: CallbackService
    ):
        """Completed task callback should include video info and file URLs."""
        task = _make_task(status=TaskStatus.COMPLETED)

        payload = await callback_service._build_payload(task)

        assert payload.task_id == task.id
        assert payload.status == TaskStatus.COMPLETED
        assert payload.video_info is not None
        assert payload.video_info.title == "Test Video"
        assert payload.files is not None
        assert "file-audio-001" in payload.files.audio.url
        assert payload.files.transcript is not None

    @pytest.mark.asyncio
    async def test_payload_failed_task_includes_error(
        self, callback_service: CallbackService
    ):
        """Failed task callback should include error info."""
        task = _make_task(
            status=TaskStatus.FAILED,
            error_code=ErrorCode.DOWNLOAD_FAILED,
            error_message="Network timeout",
            retry_count=2,
        )

        payload = await callback_service._build_payload(task)

        assert payload.status == TaskStatus.FAILED
        assert payload.error is not None
        assert payload.error.code == ErrorCode.DOWNLOAD_FAILED
        assert payload.error.message == "Network timeout"
        assert payload.error.retry_count == 2


class TestCallbackHttpErrors:
    """Tests for HTTP error responses (4xx, 5xx)."""

    @pytest.mark.asyncio
    async def test_4xx_error_no_retry(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """4xx client errors should not trigger retry."""
        task = _make_task()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {}
        mock_response.request = MagicMock()
        http_error = httpx.HTTPStatusError(
            "Forbidden", request=mock_response.request, response=mock_response
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=http_error)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await callback_service.send_callback(task)

        assert result is False
        # 4xx should only attempt once (no retry)
        mock_db.update_callback_status.assert_awaited_once_with(
            task.id, CallbackStatus.FAILED, 1
        )

    @pytest.mark.asyncio
    async def test_5xx_error_retries(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """5xx server errors should trigger retries."""
        task = _make_task()

        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.headers = {}
        mock_response.request = MagicMock()
        http_error = httpx.HTTPStatusError(
            "Bad Gateway", request=mock_response.request, response=mock_response
        )

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=http_error)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await callback_service.send_callback(task)

        assert result is False
        # Should have attempted MAX_RETRIES times
        mock_db.update_callback_status.assert_awaited_once_with(
            task.id, CallbackStatus.FAILED, CallbackService.MAX_RETRIES
        )


class TestCallbackTimeout:
    """Tests for callback timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout_retries_then_fails(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """Timeout errors should trigger retries and eventually fail."""
        task = _make_task()

        timeout_error = httpx.TimeoutException("Connection timed out")

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=timeout_error)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await callback_service.send_callback(task)

        assert result is False
        mock_db.update_callback_status.assert_awaited_once_with(
            task.id, CallbackStatus.FAILED, CallbackService.MAX_RETRIES
        )

    @pytest.mark.asyncio
    async def test_timeout_then_success(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """Should succeed if retry after timeout succeeds."""
        task = _make_task()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        timeout_error = httpx.TimeoutException("Connection timed out")

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            # First call times out, second succeeds
            mock_client.post = AsyncMock(
                side_effect=[timeout_error, mock_response]
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await callback_service.send_callback(task)

        assert result is True
        mock_db.update_callback_status.assert_awaited_once_with(
            task.id, CallbackStatus.SUCCESS, 2
        )


class TestCallbackRetryLogic:
    """Tests for retry behavior with different error types."""

    @pytest.mark.asyncio
    async def test_connect_error_retries(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """Connection errors should trigger retries."""
        task = _make_task()

        connect_error = httpx.ConnectError("Connection refused")

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=connect_error)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await callback_service.send_callback(task)

        assert result is False
        # Verify sleep was called between retries (MAX_RETRIES - 1 times)
        assert mock_sleep.await_count == CallbackService.MAX_RETRIES - 1

    @pytest.mark.asyncio
    async def test_unknown_error_retries(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """Unknown exceptions should also trigger retries."""
        task = _make_task()

        with patch("httpx.AsyncClient") as mock_client_cls, \
             patch("asyncio.sleep", new_callable=AsyncMock):
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(side_effect=RuntimeError("unexpected"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await callback_service.send_callback(task)

        assert result is False
        mock_db.update_callback_status.assert_awaited_once_with(
            task.id, CallbackStatus.FAILED, CallbackService.MAX_RETRIES
        )


class TestMissingCallbackUrl:
    """Tests for missing or empty webhook URL."""

    @pytest.mark.asyncio
    async def test_no_callback_url_returns_true(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """Tasks without callback_url should return True immediately."""
        task = _make_task(callback_url=None)

        result = await callback_service.send_callback(task)

        assert result is True
        # Should not attempt any HTTP calls or DB updates
        mock_db.update_callback_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_callback_url_returns_true(
        self, callback_service: CallbackService, mock_db: AsyncMock
    ):
        """Tasks with empty callback_url should return True immediately."""
        task = _make_task(callback_url="")

        result = await callback_service.send_callback(task)

        assert result is True
        mock_db.update_callback_status.assert_not_awaited()


class TestHmacSignature:
    """Tests for HMAC signature generation and verification."""

    def test_generate_signature(self, callback_service: CallbackService):
        """Generated signature should match expected HMAC-SHA256."""
        body = b'{"task_id":"test"}'
        secret = "my-secret"

        sig = callback_service._generate_signature(body, secret)

        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        assert sig == f"sha256={expected}"

    def test_verify_callback_signature_valid(self):
        """verify_callback_signature should return True for valid signature."""
        body = b'{"task_id":"test"}'
        secret = "my-secret"
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        signature = f"sha256={expected}"

        assert verify_callback_signature(body, signature, secret) is True

    def test_verify_callback_signature_invalid(self):
        """verify_callback_signature should return False for invalid signature."""
        body = b'{"task_id":"test"}'
        secret = "my-secret"

        assert verify_callback_signature(body, "sha256=invalid", secret) is False


class TestBuildPayload:
    """Tests for payload construction."""

    @pytest.mark.asyncio
    async def test_completed_task_payload_has_file_urls(
        self, callback_service: CallbackService
    ):
        """Completed task payload should contain correct file URLs."""
        task = _make_task(status=TaskStatus.COMPLETED)

        payload = await callback_service._build_payload(task)

        assert payload.files is not None
        assert payload.files.audio.url == "https://api.example.com/api/v1/files/file-audio-001"
        assert payload.files.transcript.url == "https://api.example.com/api/v1/files/file-transcript-001"

    @pytest.mark.asyncio
    async def test_completed_task_no_transcript(
        self, callback_service: CallbackService
    ):
        """Completed task without transcript should have transcript=None in files."""
        task = _make_task(status=TaskStatus.COMPLETED, transcript_file_id=None)

        payload = await callback_service._build_payload(task)

        assert payload.files is not None
        assert payload.files.audio is not None
        assert payload.files.transcript is None

    @pytest.mark.asyncio
    async def test_pending_task_no_files_or_error(
        self, callback_service: CallbackService
    ):
        """Non-completed, non-failed task should have no files or error."""
        task = _make_task(
            status=TaskStatus.PENDING,
            audio_file_id=None,
            transcript_file_id=None,
        )

        payload = await callback_service._build_payload(task)

        assert payload.files is None
        assert payload.error is None
