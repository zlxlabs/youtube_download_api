"""
API endpoint tests for the YouTube Audio API.

Uses FastAPI TestClient with mocked services to verify:
- Task CRUD operations (create, list, detail, cancel)
- Authentication (valid/missing/invalid API key)
- Error responses (404, 422, etc.)
- Health check endpoint
- File download endpoint
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.api.schemas import (
    CancelTaskResponse,
    CreateTaskResponse,
    TaskListResponse,
    TaskResponse,
)
from src.db.models import ErrorCode, FileRecord, FileType, TaskPriority, TaskStatus
from src.services.task_service import VideoNotDownloadableError


# -- Constants --

TEST_API_KEY = "test-api-key-12345"
TEST_TASK_ID = "550e8400-e29b-41d4-a716-446655440000"
TEST_VIDEO_ID = "dQw4w9WgXcQ"
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
TEST_FILE_ID = "aabbccdd-1122-3344-5566-778899aabbcc"


# -- Helpers --


def _make_task_response(
    task_id: str = TEST_TASK_ID,
    video_id: str = TEST_VIDEO_ID,
    task_status: TaskStatus = TaskStatus.PENDING,
    message: Optional[str] = None,
) -> TaskResponse:
    """
    Build a TaskResponse object for use in mock return values.

    Args:
        task_id: Task ID
        video_id: YouTube video ID
        task_status: Task status enum
        message: Optional message string

    Returns:
        TaskResponse instance
    """
    return TaskResponse(
        task_id=task_id,
        status=task_status,
        video_id=video_id,
        video_url=TEST_VIDEO_URL,
        priority=TaskPriority.NORMAL,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        message=message,
    )


def _auth_headers(api_key: str = TEST_API_KEY) -> dict[str, str]:
    """
    Build HTTP headers with API key for authenticated requests.

    Args:
        api_key: API key value

    Returns:
        dict with X-API-Key header
    """
    return {"X-API-Key": api_key}


# -- Fixtures --


@pytest.fixture
def mock_task_service() -> AsyncMock:
    """
    Create a mocked TaskService with default return values.

    All async methods return sensible defaults that can be overridden
    per-test via ``mock_task_service.<method>.return_value = ...``.
    """
    svc = AsyncMock()

    # Default: create_task returns a pending task
    svc.create_task.return_value = _make_task_response(
        message="Task created successfully"
    )

    # Default: list_tasks returns an empty list
    svc.list_tasks.return_value = TaskListResponse(
        tasks=[], total=0, limit=20, offset=0
    )

    # Default: get_task returns a task
    svc.get_task.return_value = _make_task_response()

    # Default: cancel_task returns a cancelled task
    svc.cancel_task.return_value = _make_task_response(
        task_status=TaskStatus.CANCELLED,
        message="Task cancelled successfully",
    )

    return svc


@pytest.fixture
def mock_file_service() -> AsyncMock:
    """
    Create a mocked FileService.

    Default get_file returns None (file not found).
    """
    svc = AsyncMock()
    svc.get_file.return_value = None
    svc.check_disk_space.return_value = True
    return svc


@pytest.fixture
def client(
    mock_task_service: AsyncMock,
    mock_file_service: AsyncMock,
) -> TestClient:
    """
    Create a FastAPI TestClient with mocked services and settings.

    Uses FastAPI dependency_overrides to inject test Settings into the
    auth dependency chain. Module-level service globals are set directly.
    """
    from src.config import Settings, get_settings

    test_settings = Settings(
        api_key=TEST_API_KEY,
        wecom_webhook_url="",
        debug=True,
        pot_server_url="http://localhost:4416",
        data_dir=Path(tempfile.mkdtemp()),
        file_retention_days=1,
        task_interval_min=5,
        task_interval_max=10,
        dry_run=True,
    )

    # Import routes module and inject mock services via module globals
    from src.api import routes as routes_mod

    routes_mod._task_service = mock_task_service
    routes_mod._file_service = mock_file_service

    # Build a minimal FastAPI app with only the routes we need to test
    from fastapi import FastAPI
    from src.api.schemas import ComponentStatus, HealthResponse, QueueStatus

    app = FastAPI()
    app.include_router(routes_mod.router)

    # Override the get_settings dependency so verify_api_key sees our test key
    app.dependency_overrides[get_settings] = lambda: test_settings

    # Register a minimal health endpoint (avoids full lifespan init)
    @app.get("/health", response_model=HealthResponse)
    async def health_check() -> HealthResponse:
        return HealthResponse(
            status="healthy",
            version="1.0.0-test",
            components=ComponentStatus(),
            queue=QueueStatus(),
            uptime=42,
        )

    yield TestClient(app)

    # Cleanup
    app.dependency_overrides.clear()
    routes_mod._task_service = None
    routes_mod._file_service = None


# ==================== Authentication Tests ====================


class TestAuthentication:
    """Verify API key authentication on protected endpoints."""

    def test_missing_api_key_returns_401(self, client: TestClient) -> None:
        """Request without X-API-Key header should return 401."""
        response = client.get("/api/v1/tasks")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Missing API key" in response.json()["detail"]

    def test_invalid_api_key_returns_403(self, client: TestClient) -> None:
        """Request with wrong API key should return 403."""
        response = client.get(
            "/api/v1/tasks",
            headers=_auth_headers("wrong-key"),
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "Invalid API key" in response.json()["detail"]

    def test_valid_api_key_succeeds(self, client: TestClient) -> None:
        """Request with correct API key should be accepted."""
        response = client.get(
            "/api/v1/tasks",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_200_OK

    def test_create_task_requires_auth(self, client: TestClient) -> None:
        """POST /tasks without auth should return 401."""
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": TEST_VIDEO_URL},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_get_task_requires_auth(self, client: TestClient) -> None:
        """GET /tasks/{id} without auth should return 401."""
        response = client.get(f"/api/v1/tasks/{TEST_TASK_ID}")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_cancel_task_requires_auth(self, client: TestClient) -> None:
        """DELETE /tasks/{id} without auth should return 401."""
        response = client.delete(f"/api/v1/tasks/{TEST_TASK_ID}")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_file_download_no_auth_required(
        self, client: TestClient, mock_file_service: AsyncMock
    ) -> None:
        """GET /files/{id} is public (no auth needed), returns 404 if not found."""
        response = client.get(f"/api/v1/files/{TEST_FILE_ID}")
        # Should not be 401/403; file endpoint is unauthenticated
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ==================== Task Creation Tests ====================


class TestCreateTask:
    """Tests for POST /api/v1/tasks."""

    def test_create_task_success(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Creating a valid task returns 201 with task details."""
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": TEST_VIDEO_URL},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["task_id"] == TEST_TASK_ID
        assert body["video_id"] == TEST_VIDEO_ID
        assert body["status"] == TaskStatus.PENDING.value

        # Verify the service was called with parsed request
        mock_task_service.create_task.assert_awaited_once()

    def test_create_task_with_priority(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Task creation respects optional priority field."""
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": TEST_VIDEO_URL, "priority": "urgent"},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_201_CREATED
        # Verify the request object passed to service has urgent priority
        call_args = mock_task_service.create_task.call_args
        request_obj = call_args[0][0]
        assert request_obj.priority == TaskPriority.URGENT

    def test_create_task_invalid_url_returns_422(
        self, client: TestClient
    ) -> None:
        """Invalid YouTube URL should be rejected with 422."""
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": "not-a-valid-url"},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_create_task_missing_url_returns_422(
        self, client: TestClient
    ) -> None:
        """Missing required video_url field should return 422."""
        response = client.post(
            "/api/v1/tasks",
            json={},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_create_task_empty_body_returns_422(
        self, client: TestClient
    ) -> None:
        """Empty request body should return 422."""
        response = client.post(
            "/api/v1/tasks",
            headers=_auth_headers(),
            content="",
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_create_task_both_flags_false_returns_422(
        self, client: TestClient
    ) -> None:
        """Setting both include_audio and include_transcript to False should fail validation."""
        response = client.post(
            "/api/v1/tasks",
            json={
                "video_url": TEST_VIDEO_URL,
                "include_audio": False,
                "include_transcript": False,
            },
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_create_task_service_value_error_returns_400(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """ValueError from service should map to 400 Bad Request."""
        mock_task_service.create_task.side_effect = ValueError("bad input")
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": TEST_VIDEO_URL},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "bad input" in response.json()["detail"]

    def test_create_task_video_not_downloadable_returns_422(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """
        视频被前置检查判定为不可下载（如直播中）时，应返回 422，
        body 中包含 error_code / message / video_id 三个字段。
        """
        mock_task_service.create_task.side_effect = VideoNotDownloadableError(
            video_id=TEST_VIDEO_ID,
            error_code=ErrorCode.VIDEO_LIVE_STREAM,
            message="Video is a live broadcast (status: live), not available for download",
        )
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": TEST_VIDEO_URL},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        body = response.json()["detail"]
        assert body["error_code"] == ErrorCode.VIDEO_LIVE_STREAM.value
        assert body["video_id"] == TEST_VIDEO_ID
        assert "live" in body["message"].lower()

    def test_create_task_video_unavailable_returns_422(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """视频不可用（已删除等）同样应返回 422，error_code 对应透传。"""
        mock_task_service.create_task.side_effect = VideoNotDownloadableError(
            video_id=TEST_VIDEO_ID,
            error_code=ErrorCode.VIDEO_UNAVAILABLE,
            message="Video not found",
        )
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": TEST_VIDEO_URL},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        body = response.json()["detail"]
        assert body["error_code"] == ErrorCode.VIDEO_UNAVAILABLE.value
        assert body["video_id"] == TEST_VIDEO_ID

    def test_create_task_422_openapi_schema_matches_actual_response_shape(
        self, client: TestClient
    ) -> None:
        """
        422 响应的 OpenAPI 声明必须与实际响应体一致。

        实现里通过 ``raise HTTPException(422, detail={...})`` 抛出，实际响应体是
        ``{"detail": {error_code, message, video_id}}``（FastAPI HTTPException 的
        detail 包裹惯例，上面两个用例已验证），而不是平铺的
        ``{error_code, message, video_id}``。因此 OpenAPI responses 声明必须引用
        一个带 detail 字段的包裹模型（VideoNotDownloadableErrorResponse），
        否则据此生成的客户端代码会按错误的契约反序列化。
        """
        schema = client.app.openapi()  # type: ignore[union-attr]
        responses = schema["paths"]["/api/v1/tasks"]["post"]["responses"]
        ref = responses["422"]["content"]["application/json"]["schema"]["$ref"]
        model_name = ref.rsplit("/", 1)[-1]

        assert model_name == "VideoNotDownloadableErrorResponse"

        wrapper_schema = schema["components"]["schemas"][model_name]
        assert "detail" in wrapper_schema["properties"]

        detail_ref = wrapper_schema["properties"]["detail"]["$ref"]
        detail_model_name = detail_ref.rsplit("/", 1)[-1]
        assert detail_model_name == "VideoNotDownloadableResponse"

        detail_schema = schema["components"]["schemas"][detail_model_name]
        assert set(detail_schema["properties"]) == {"error_code", "message", "video_id"}

    def test_create_task_unexpected_error_returns_500(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Unhandled exception from service should return 500."""
        mock_task_service.create_task.side_effect = RuntimeError("boom")
        response = client.post(
            "/api/v1/tasks",
            json={"video_url": TEST_VIDEO_URL},
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Internal server error" in response.json()["detail"]

    def test_create_task_with_callback_url(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Task creation can include optional callback_url."""
        response = client.post(
            "/api/v1/tasks",
            json={
                "video_url": TEST_VIDEO_URL,
                "callback_url": "https://example.com/webhook",
                "callback_secret": "mysecret123456",
            },
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_201_CREATED


# ==================== Task List Tests ====================


class TestListTasks:
    """Tests for GET /api/v1/tasks."""

    def test_list_tasks_empty(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Empty task list returns proper structure."""
        response = client.get("/api/v1/tasks", headers=_auth_headers())
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["tasks"] == []
        assert body["total"] == 0
        assert body["limit"] == 20
        assert body["offset"] == 0

    def test_list_tasks_with_results(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Task list with items includes all expected fields."""
        task = _make_task_response()
        mock_task_service.list_tasks.return_value = TaskListResponse(
            tasks=[task], total=1, limit=20, offset=0
        )
        response = client.get("/api/v1/tasks", headers=_auth_headers())
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["total"] == 1
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["task_id"] == TEST_TASK_ID

    def test_list_tasks_with_status_filter(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Status filter query parameter is forwarded to service."""
        response = client.get(
            "/api/v1/tasks?status=completed",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_200_OK
        call_kwargs = mock_task_service.list_tasks.call_args[1]
        assert call_kwargs["status"] == TaskStatus.COMPLETED

    def test_list_tasks_with_pagination(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Limit and offset query params are forwarded to service."""
        response = client.get(
            "/api/v1/tasks?limit=5&offset=10",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_200_OK
        call_kwargs = mock_task_service.list_tasks.call_args[1]
        assert call_kwargs["limit"] == 5
        assert call_kwargs["offset"] == 10

    def test_list_tasks_with_search(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Search query parameter is forwarded to service."""
        response = client.get(
            "/api/v1/tasks?search=rick",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_200_OK
        call_kwargs = mock_task_service.list_tasks.call_args[1]
        assert call_kwargs["search"] == "rick"

    def test_list_tasks_invalid_limit_returns_422(
        self, client: TestClient
    ) -> None:
        """Limit exceeding max (100) should return 422."""
        response = client.get(
            "/api/v1/tasks?limit=200",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_list_tasks_negative_offset_returns_422(
        self, client: TestClient
    ) -> None:
        """Negative offset should return 422."""
        response = client.get(
            "/api/v1/tasks?offset=-1",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ==================== Task Detail Tests ====================


class TestGetTask:
    """Tests for GET /api/v1/tasks/{task_id}."""

    def test_get_task_success(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Existing task returns full details."""
        response = client.get(
            f"/api/v1/tasks/{TEST_TASK_ID}",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["task_id"] == TEST_TASK_ID
        assert body["video_id"] == TEST_VIDEO_ID

    def test_get_task_not_found_returns_404(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Non-existent task ID returns 404."""
        mock_task_service.get_task.return_value = None
        response = client.get(
            "/api/v1/tasks/non-existent-id",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Task not found" in response.json()["detail"]

    def test_get_task_database_error_returns_503(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Database errors during task retrieval return 503."""
        import aiosqlite

        mock_task_service.get_task.side_effect = aiosqlite.Error("db down")
        response = client.get(
            f"/api/v1/tasks/{TEST_TASK_ID}",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "Database error" in response.json()["detail"]


# ==================== Task Cancellation Tests ====================


class TestCancelTask:
    """Tests for DELETE /api/v1/tasks/{task_id}."""

    def test_cancel_task_success(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Successful cancellation returns task_id, status, and message."""
        response = client.delete(
            f"/api/v1/tasks/{TEST_TASK_ID}",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["task_id"] == TEST_TASK_ID
        assert body["status"] == TaskStatus.CANCELLED.value
        assert "cancelled" in body["message"].lower()

    def test_cancel_task_not_found_returns_404(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Cancelling a non-existent task returns 404."""
        mock_task_service.cancel_task.return_value = None
        response = client.delete(
            "/api/v1/tasks/non-existent-id",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Task not found" in response.json()["detail"]

    def test_cancel_task_not_cancellable_returns_400(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Attempting to cancel a non-cancellable task returns 400."""
        mock_task_service.cancel_task.side_effect = ValueError(
            "Task is not in a cancellable state"
        )
        response = client.delete(
            f"/api/v1/tasks/{TEST_TASK_ID}",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "cancellable" in response.json()["detail"].lower()

    def test_cancel_task_database_error_returns_503(
        self, client: TestClient, mock_task_service: AsyncMock
    ) -> None:
        """Database errors during cancellation return 503."""
        import aiosqlite

        mock_task_service.cancel_task.side_effect = aiosqlite.Error("db down")
        response = client.delete(
            f"/api/v1/tasks/{TEST_TASK_ID}",
            headers=_auth_headers(),
        )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# ==================== File Download Tests ====================


class TestFileDownload:
    """Tests for GET /api/v1/files/{file_id}."""

    def test_file_not_found_returns_404(
        self, client: TestClient, mock_file_service: AsyncMock
    ) -> None:
        """Non-existent file ID returns 404."""
        mock_file_service.get_file.return_value = None
        response = client.get(f"/api/v1/files/{TEST_FILE_ID}")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "File not found" in response.json()["detail"]

    def test_file_download_success(
        self, client: TestClient, mock_file_service: AsyncMock
    ) -> None:
        """Valid file ID returns file content with correct headers."""
        # Create a real temporary file for FileResponse to read
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
            tmp.write(b"fake audio content")
            tmp_path = Path(tmp.name)

        try:
            file_record = FileRecord(
                id=TEST_FILE_ID,
                video_id=TEST_VIDEO_ID,
                file_type=FileType.AUDIO,
                filename="test_audio.m4a",
                filepath=str(tmp_path),
                size=18,
                format="m4a",
            )
            mock_file_service.get_file.return_value = (file_record, tmp_path)

            response = client.get(f"/api/v1/files/{TEST_FILE_ID}.m4a")
            assert response.status_code == status.HTTP_200_OK
            assert response.content == b"fake audio content"
            assert "audio/mp4" in response.headers.get("content-type", "")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_file_download_with_extension(
        self, client: TestClient, mock_file_service: AsyncMock
    ) -> None:
        """File ID with extension is correctly parsed."""
        mock_file_service.get_file.return_value = None
        # UUID (36 chars) + ".m4a"
        response = client.get(f"/api/v1/files/{TEST_FILE_ID}.m4a")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        # Verify get_file was called with the UUID portion only
        mock_file_service.get_file.assert_awaited_once_with(TEST_FILE_ID)

    def test_file_download_with_custom_filename(
        self, client: TestClient, mock_file_service: AsyncMock
    ) -> None:
        """Custom filename query parameter is reflected in response headers."""
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as tmp:
            tmp.write(b"audio data")
            tmp_path = Path(tmp.name)

        try:
            file_record = FileRecord(
                id=TEST_FILE_ID,
                video_id=TEST_VIDEO_ID,
                file_type=FileType.AUDIO,
                filename="original.m4a",
                filepath=str(tmp_path),
                size=10,
                format="m4a",
            )
            mock_file_service.get_file.return_value = (file_record, tmp_path)

            response = client.get(
                f"/api/v1/files/{TEST_FILE_ID}.m4a?filename=my_custom_name.m4a"
            )
            assert response.status_code == status.HTTP_200_OK
            content_disp = response.headers.get("content-disposition", "")
            assert "my_custom_name.m4a" in content_disp
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_file_download_database_error_returns_503(
        self, client: TestClient, mock_file_service: AsyncMock
    ) -> None:
        """Database errors during file retrieval return 503."""
        import aiosqlite

        mock_file_service.get_file.side_effect = aiosqlite.Error("db down")
        response = client.get(f"/api/v1/files/{TEST_FILE_ID}")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


# ==================== Health Check Tests ====================


class TestHealthCheck:
    """Tests for GET /health."""

    def test_health_check_returns_200(self, client: TestClient) -> None:
        """Health endpoint returns 200 with expected structure."""
        response = client.get("/health")
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["status"] == "healthy"
        assert "version" in body
        assert "components" in body
        assert "queue" in body
        assert "uptime" in body

    def test_health_check_no_auth_required(self, client: TestClient) -> None:
        """Health endpoint does not require authentication."""
        # No X-API-Key header, should still succeed
        response = client.get("/health")
        assert response.status_code == status.HTTP_200_OK

    def test_health_check_components_present(self, client: TestClient) -> None:
        """Health response includes all expected component fields."""
        response = client.get("/health")
        body = response.json()
        components = body["components"]
        assert "database" in components
        assert "pot_provider" in components
        assert "disk_space" in components

    def test_health_check_queue_stats(self, client: TestClient) -> None:
        """Health response includes queue statistics."""
        response = client.get("/health")
        body = response.json()
        queue = body["queue"]
        assert "pending" in queue
        assert "downloading" in queue
