from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.downloader import DownloadError
from src.core.worker import DownloadWorker
from src.db.models import ErrorCode, Task, TaskPriority, TaskStatus
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderError
from src.downloaders.manager import DownloaderManager
from src.downloaders.models import DownloaderResult, VideoMetadata
from src.downloaders.ytdlp_downloader import YtdlpDownloader


@pytest.mark.asyncio
async def test_ytdlp_mixed_no_transcript_raises_with_audio_error(tmp_path: Path) -> None:
    ytdlp = YtdlpDownloader.__new__(YtdlpDownloader)
    ytdlp.settings = MagicMock()
    ytdlp._downloader = MagicMock()
    audio_error = DownloadError(ErrorCode.NETWORK_ERROR, "audio blocked")
    ytdlp._downloader.download = AsyncMock(side_effect=audio_error)
    ytdlp._downloader.extract_transcript_only = AsyncMock(
        return_value=MagicMock(
            video_info=MagicMock(title="title"), transcript_path=None, has_transcript=False
        )
    )
    ytdlp._convert_video_info = MagicMock(return_value=VideoMetadata(video_id="video"))

    with pytest.raises(DownloaderError) as exc:
        await ytdlp.download_resources("url", "video", tmp_path, True, True)

    assert exc.value.error_code == ErrorCode.NETWORK_ERROR
    assert exc.value.transcript_confirmed_absent is True
    assert exc.value.operation == "mixed"


@pytest.mark.asyncio
async def test_manager_rejects_empty_mixed_result_and_falls_back(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.circuit_breaker_enabled = False
    settings.audio_download_priority = "ytdlp,tikhub"
    settings.transcript_only_priority = "ytdlp,tikhub"
    manager = DownloaderManager(settings)
    first, second = MagicMock(), MagicMock()
    for name, item in (("ytdlp", first), ("tikhub", second)):
        item.name = name
        item.supports_resource_download = True
        item.should_retry.return_value = False
        item.should_trigger_circuit_breaker.return_value = False
        item.download_resources = AsyncMock()
    first.download_resources.return_value = DownloaderResult(
        success=True, downloader="ytdlp", video_metadata=VideoMetadata(video_id="v")
    )
    second.download_resources.return_value = DownloaderResult(
        success=True, downloader="tikhub", video_metadata=VideoMetadata(video_id="v"),
        audio_path=tmp_path / "audio.m4a",
    )
    manager.downloaders = [first, second]
    manager.get_metadata = AsyncMock(return_value=None)

    result = await manager.download_with_fallback("url", "v", tmp_path, True, True)

    assert first.download_resources.await_count == 1
    assert second.download_resources.await_count == 1
    assert result.audio_path == tmp_path / "audio.m4a"


@pytest.mark.asyncio
async def test_empty_result_from_all_downloaders_fails(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.circuit_breaker_enabled = False
    settings.audio_download_priority = "ytdlp,tikhub"
    settings.transcript_only_priority = "ytdlp,tikhub"
    manager = DownloaderManager(settings)
    downloaders = [MagicMock(), MagicMock()]
    for item in downloaders:
        item.name = "fake"
        item.supports_resource_download = True
        item.should_retry.return_value = False
        item.should_trigger_circuit_breaker.return_value = False
        item.download_resources = AsyncMock(
            return_value=DownloaderResult(
                success=True, downloader="fake", video_metadata=VideoMetadata(video_id="v")
            )
        )
    manager.downloaders = downloaders
    manager.get_metadata = AsyncMock(return_value=None)

    with pytest.raises(AllDownloadersFailed):
        await manager.download_with_fallback("url", "v", tmp_path, True, True)

    assert all(item.download_resources.await_count == 1 for item in downloaders)


@pytest.mark.asyncio
async def test_empty_mixed_result_from_403_stops_fallback(tmp_path: Path) -> None:
    settings = MagicMock()
    settings.circuit_breaker_enabled = False
    settings.audio_download_priority = "ytdlp,tikhub"
    settings.transcript_only_priority = "ytdlp,tikhub"
    manager = DownloaderManager(settings)
    first, second = MagicMock(), MagicMock()
    for name, item in (("ytdlp", first), ("tikhub", second)):
        item.name = name
        item.supports_resource_download = True
        item.should_retry.return_value = False
        item.should_trigger_circuit_breaker.return_value = False
        item.download_resources = AsyncMock()
    first.download_resources.side_effect = DownloaderError(
        "audio 403; transcript absent", error_code=ErrorCode.RATE_LIMITED,
        downloader="ytdlp", http_status_code=403, stop_fallback=True,
        transcript_confirmed_absent=True,
    )
    manager.downloaders = [first, second]
    manager.get_metadata = AsyncMock(return_value=None)

    with pytest.raises(AllDownloadersFailed):
        await manager.download_with_fallback("url", "v", tmp_path, True, True)

    assert first.download_resources.await_count == 1
    assert second.download_resources.await_count == 0


@pytest.mark.asyncio
async def test_worker_rejects_audio_task_with_no_file_ids(test_settings, test_db) -> None:
    task_service = MagicMock()
    task_service.get_next_task = AsyncMock()
    task_service.task_queue = MagicMock()
    task_service.task_queue.put = AsyncMock()
    worker = DownloadWorker(
        db=test_db, settings=test_settings, task_service=task_service,
        file_service=MagicMock(), callback_service=MagicMock(),
        notify_service=AsyncMock(), downloader_manager=MagicMock(), metrics_collector=None,
    )
    worker.db.update_task_completed = AsyncMock(wraps=test_db.update_task_completed)
    task = Task(
        id="t", video_id="v", video_url="url", status=TaskStatus.PENDING,
        include_audio=True, include_transcript=True, priority=TaskPriority.NORMAL,
        retry_count=1,
    )
    await test_db.create_task(task)
    worker._running = True
    worker.task_service.get_next_task = AsyncMock(return_value=task)
    worker._check_ip_ban_and_decide = AsyncMock(return_value=None)
    worker._execute_task = AsyncMock(return_value={
        "audio_file_id": None, "transcript_file_id": None,
        "reused_audio": False, "reused_transcript": False,
    })
    worker._handle_download_error = AsyncMock(wraps=worker._handle_download_error)
    worker._get_adaptive_wait_time = MagicMock(return_value=0)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.core.worker.asyncio.sleep", AsyncMock())
        await worker._process_next_task()
    worker._handle_download_error.assert_awaited_once()
    assert (await test_db.get_task(task.id)).status == TaskStatus.FAILED
    worker.db.update_task_completed.assert_not_awaited()
