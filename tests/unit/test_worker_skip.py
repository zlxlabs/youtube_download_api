"""
Tests for Worker IP ban task skip mechanism.

Verifies that the worker correctly skips incompatible tasks during IP ban
and re-queues them instead of blocking.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.ip_ban_models import ExecutionDecision, IPBanLevel
from src.core.worker import DownloadWorker
from src.db.models import Task, TaskPriority, TaskStatus


def _make_task(
    task_id: str = "task-1",
    include_audio: bool = True,
    include_transcript: bool = True,
    priority: TaskPriority = TaskPriority.NORMAL,
) -> Task:
    """Create a test task."""
    return Task(
        id=task_id,
        video_id=f"video-{task_id}",
        video_url=f"https://youtube.com/watch?v=video-{task_id}",
        status=TaskStatus.PENDING,
        include_audio=include_audio,
        include_transcript=include_transcript,
        priority=priority,
        created_at=datetime.now(timezone.utc),
    )


def _make_worker() -> DownloadWorker:
    """Create a worker with mocked dependencies."""
    task_service = MagicMock()
    # task_queue.put is async, so it needs AsyncMock
    task_service.task_queue = MagicMock()
    task_service.task_queue.put = AsyncMock()

    worker = DownloadWorker(
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
    return worker


class TestGetExecutableTask:
    """Tests for _get_executable_task method."""

    @pytest.mark.asyncio
    async def test_normal_state_returns_first_task(self):
        """When IP ban is NORMAL, should return the first task from queue."""
        worker = _make_worker()
        task = _make_task("audio-1", include_audio=True)

        worker.task_service.get_next_task = AsyncMock(return_value=task)
        worker._check_ip_ban_and_decide = AsyncMock(return_value=None)

        result_task, result_decision = await worker._get_executable_task()

        assert result_task is task
        assert result_decision is None

    @pytest.mark.asyncio
    async def test_audio_banned_skips_audio_task_returns_transcript(self):
        """
        When AUDIO_BANNED, audio tasks should be skipped.
        If a transcript-only task is next, it should be returned.
        """
        worker = _make_worker()
        audio_task = _make_task("audio-1", include_audio=True, include_transcript=False)
        transcript_task = _make_task("transcript-1", include_audio=False, include_transcript=True)

        # First call returns audio task (should be skipped),
        # second call returns transcript task (should be returned)
        worker.task_service.get_next_task = AsyncMock(
            side_effect=[audio_task, transcript_task]
        )

        delay_decision = ExecutionDecision(
            action="delay",
            reason="Audio ban active",
            delay_seconds=3600,
        )
        # Audio task -> delay, transcript task -> None (execute)
        worker._check_ip_ban_and_decide = AsyncMock(
            side_effect=[delay_decision, None]
        )

        result_task, result_decision = await worker._get_executable_task()

        assert result_task is transcript_task
        assert result_decision is None

        # Audio task should be re-queued
        worker.task_service.task_queue.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_tasks_blocked_sleeps_and_returns_none(self):
        """
        When all tasks in queue are blocked by IP ban,
        should sleep and return None. All tasks should be re-queued.
        """
        worker = _make_worker()
        audio_task1 = _make_task("audio-1", include_audio=True)
        audio_task2 = _make_task("audio-2", include_audio=True)

        worker.task_service.get_next_task = AsyncMock(
            side_effect=[audio_task1, audio_task2, None]
        )

        delay_decision = ExecutionDecision(
            action="delay",
            reason="Audio ban active",
            delay_seconds=3600,
        )
        worker._check_ip_ban_and_decide = AsyncMock(return_value=delay_decision)
        worker.ip_ban_breaker.get_remaining_time = MagicMock(return_value=1800)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result_task, result_decision = await worker._get_executable_task()

        assert result_task is None
        assert result_decision is None
        # Should sleep for min(remaining, 60) = 60
        mock_sleep.assert_called_once_with(60)
        # Both tasks should be re-queued
        assert worker.task_service.task_queue.put.call_count == 2

    @pytest.mark.asyncio
    async def test_empty_queue_returns_none(self):
        """When queue is empty, should return None without sleeping."""
        worker = _make_worker()
        worker.task_service.get_next_task = AsyncMock(return_value=None)

        result_task, result_decision = await worker._get_executable_task()

        assert result_task is None
        assert result_decision is None

    @pytest.mark.asyncio
    async def test_probe_task_returned(self):
        """When a task is allowed as a recovery probe, should return it."""
        worker = _make_worker()
        task = _make_task("probe-1", include_audio=True)

        worker.task_service.get_next_task = AsyncMock(return_value=task)

        probe_decision = ExecutionDecision(
            action="execute",
            reason="Recovery probe allowed",
            is_probe=True,
        )
        worker._check_ip_ban_and_decide = AsyncMock(return_value=probe_decision)

        result_task, result_decision = await worker._get_executable_task()

        assert result_task is task
        assert result_decision is probe_decision
        assert result_decision.is_probe is True

    @pytest.mark.asyncio
    async def test_deferred_tasks_requeued_on_success(self):
        """
        Deferred tasks should be re-queued even when a compatible task is found.
        """
        worker = _make_worker()
        audio_task = _make_task("audio-1", include_audio=True, include_transcript=False)
        transcript_task = _make_task("transcript-1", include_audio=False, include_transcript=True)

        worker.task_service.get_next_task = AsyncMock(
            side_effect=[audio_task, transcript_task]
        )

        delay_decision = ExecutionDecision(
            action="delay", reason="Audio ban", delay_seconds=3600
        )
        worker._check_ip_ban_and_decide = AsyncMock(
            side_effect=[delay_decision, None]
        )

        result_task, _ = await worker._get_executable_task()

        assert result_task is transcript_task
        # Audio task should be re-queued
        worker.task_service.task_queue.put.assert_called_once()
