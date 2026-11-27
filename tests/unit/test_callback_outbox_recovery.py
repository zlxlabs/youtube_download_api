"""SQLite-backed callback outbox recovery tests."""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
import pytest

from src.db.models import CallbackStatus, Task, TaskStatus, VideoResource
from src.services.callback_service import CallbackService


async def _seed_task(
    db, task_id: str, status: TaskStatus, completed_at: datetime
) -> None:
    await db.create_video_resource(VideoResource(video_id=f"video-{task_id}"))
    await db.create_task(
        Task(
            id=task_id,
            video_id=f"video-{task_id}",
            video_url="https://example.test/video",
            status=status,
            callback_url="https://example.test/hook",
            callback_secret="secret",
            callback_status=CallbackStatus.PENDING,
            created_at=completed_at,
            completed_at=completed_at,
        )
    )


@pytest.mark.asyncio
async def test_recovers_recent_completed_and_failed_callbacks(test_db, monkeypatch):
    now = datetime.now(timezone.utc)
    await _seed_task(test_db, "completed", TaskStatus.COMPLETED, now)
    await _seed_task(test_db, "failed", TaskStatus.FAILED, now)
    requests = []

    class Client:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, content, headers):
            requests.append((url, content, headers))
            response = type("Response", (), {"raise_for_status": lambda self: None})()
            return response

    monkeypatch.setattr("src.services.callback_service.httpx.AsyncClient", Client)
    service = CallbackService(test_db)
    assert await service.recover_pending_callbacks() == 2
    assert {request[2]["X-Task-Id"] for request in requests} == {"completed", "failed"}
    for _, body, headers in requests:
        assert isinstance(body, bytes)
        parsed = json.loads(body)
        assert parsed["task_id"] == headers["X-Task-Id"]
        assert headers["X-Signature"] == "sha256=" + hmac.new(
            b"secret", body, hashlib.sha256
        ).hexdigest()
        status = await test_db.get_task(headers["X-Task-Id"])
        assert status.callback_status == CallbackStatus.SUCCESS


@pytest.mark.asyncio
async def test_skips_outbox_tasks_older_than_window(test_db):
    await _seed_task(
        test_db,
        "stale",
        TaskStatus.COMPLETED,
        datetime.now(timezone.utc) - timedelta(days=10),
    )
    assert await CallbackService(test_db).recover_pending_callbacks(max_age_days=7) == 0


@pytest.mark.asyncio
async def test_send_callback_deduplicates_in_flight_task(test_db, monkeypatch):
    service = CallbackService(test_db)
    task = Task(
        id="in-flight", video_id="video-in-flight", video_url="https://example.test",
        status=TaskStatus.COMPLETED, callback_url="https://example.test/hook",
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def blocked_send(_task):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(service, "_send_callback", blocked_send)
    first = asyncio.create_task(service.send_callback(task))
    await entered.wait()
    assert await service.send_callback(task) is False
    release.set()
    assert await first is True
    assert calls == 1


@pytest.mark.asyncio
async def test_recovery_isolates_per_task_exception(test_db, monkeypatch):
    now = datetime.now(timezone.utc)
    await _seed_task(test_db, "broken", TaskStatus.COMPLETED, now)
    await _seed_task(test_db, "healthy", TaskStatus.FAILED, now)
    service = CallbackService(test_db)
    calls = []

    async def send(task):
        calls.append(task.id)
        if task.id == "broken":
            raise RuntimeError("broken payload")
        await test_db.update_callback_status(task.id, CallbackStatus.SUCCESS, 1)
        return True

    monkeypatch.setattr(service, "send_callback", send)
    assert await service.recover_pending_callbacks() == 2
    assert calls == ["broken", "healthy"]
