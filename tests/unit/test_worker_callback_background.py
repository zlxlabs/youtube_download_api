"""
Tests for background webhook callback delivery in the worker.

Regression tests: callbacks were previously awaited inline, blocking the
worker main loop for up to ~30s (timeout x retries) per task. They are now
fire-and-forget with strong references and exception swallowing.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.worker import DownloadWorker
from src.db.models import Task, TaskPriority, TaskStatus


def _make_task(task_id: str = "task-1") -> Task:
    return Task(
        id=task_id,
        video_id=f"video-{task_id}",
        video_url=f"https://youtube.com/watch?v=video-{task_id}",
        status=TaskStatus.COMPLETED,
        include_audio=True,
        include_transcript=True,
        priority=TaskPriority.NORMAL,
        created_at=datetime.now(timezone.utc),
    )


def _make_worker() -> DownloadWorker:
    task_service = MagicMock()
    task_service.task_queue = MagicMock()
    task_service.task_queue.put = AsyncMock()

    return DownloadWorker(
        db=AsyncMock(),
        settings=MagicMock(
            task_timeout=300,
            transcript_interval_min=20,
            transcript_interval_max=40,
            audio_interval_min=60,
            audio_interval_max=600,
        ),
        task_service=task_service,
        file_service=MagicMock(),
        callback_service=MagicMock(),
        notify_service=MagicMock(),
        metrics_collector=None,
    )


@pytest.mark.asyncio
async def test_callback_runs_in_background_and_is_tracked():
    """回调应在后台任务中执行，并通过强引用集合追踪直到完成。"""
    worker = _make_worker()
    worker.callback_service.send_callback = AsyncMock()
    task = _make_task()

    worker._send_callback_background(task)
    assert len(worker._callback_tasks) == 1

    await asyncio.gather(*worker._callback_tasks)
    await asyncio.sleep(0)  # 让 done_callback 执行

    worker.callback_service.send_callback.assert_awaited_once_with(task)
    assert len(worker._callback_tasks) == 0


@pytest.mark.asyncio
async def test_callback_exception_is_swallowed():
    """回调失败不应抛出未处理异常影响 worker 主循环。"""
    worker = _make_worker()
    worker.callback_service.send_callback = AsyncMock(
        side_effect=RuntimeError("webhook down")
    )
    task = _make_task()

    worker._send_callback_background(task)
    results = await asyncio.gather(*worker._callback_tasks, return_exceptions=True)

    # _safe_send_callback 内部已捕获异常，gather 不应收到异常
    assert all(not isinstance(r, Exception) for r in results)


@pytest.mark.asyncio
async def test_stop_waits_for_pending_callbacks():
    """关停时应等待未完成的回调发送完毕。"""
    worker = _make_worker()
    worker.downloader_manager.cancel_all = MagicMock()

    delivered = asyncio.Event()

    async def slow_callback(task):
        await asyncio.sleep(0.05)
        delivered.set()

    worker.callback_service.send_callback = slow_callback
    worker._send_callback_background(_make_task())

    await worker.stop()
    assert delivered.is_set()
