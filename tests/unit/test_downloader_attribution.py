"""
测试 worker 完成/失败路径的下载器归属与失败详情持久化闭环。

背景：生产取证发现 tasks 表没有"哪个下载器完成的"这一列，要回答
"CDP 下载器成功率是多少"这种问题只能翻日志。本组测试锁定：
1. 完成路径：音频/字幕分别由哪个下载器产出，写入 audio_downloader /
   transcript_downloader 列（缓存复用场景保持 NULL，不写 'cache' 占位值）。
2. 失败路径：把降级链每个下载器的尝试结果序列化为 JSON 写入 failure_details 列。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.config import Settings
from src.core.worker import DownloadWorker
from src.db.database import Database
from src.db.models import ErrorCode, FileRecord, FileType, Task, TaskStatus
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderAttempt, DownloaderError
from src.downloaders.models import DownloaderResult, VideoMetadata
from src.services.callback_service import CallbackService
from src.services.file_service import FileService
from src.services.notify import NotificationService
from src.services.task_service import TaskService


def _make_metadata(video_id: str, title: str = "Test Video") -> VideoMetadata:
    return VideoMetadata(
        video_id=video_id, title=title, author="Test Author", duration=60, channel_id="UC123456"
    )


# ==================== Completion Path: Downloader Attribution ====================


class TestDownloaderAttributionOnCompletion:
    """完成路径应把音频/字幕各自的下载器归属写入数据库。"""

    @pytest_asyncio.fixture
    async def worker_deps(
        self, test_db: Database, test_settings: Settings, file_service: FileService
    ):
        task_service = TaskService(test_db, test_settings, file_service)
        callback_service = CallbackService(test_db, base_url="http://localhost:8000")
        notify_service = NotificationService(test_settings)
        return {
            "db": test_db,
            "settings": test_settings,
            "task_service": task_service,
            "file_service": file_service,
            "callback_service": callback_service,
            "notify_service": notify_service,
        }

    @pytest.fixture
    def mock_manager(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def _patch_manager(self, mock_manager: AsyncMock):
        with patch("src.core.worker.DownloaderManager", return_value=mock_manager):
            yield

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_manager")
    async def test_audio_and_transcript_from_same_call_share_downloader(
        self, worker_deps: dict, mock_manager: AsyncMock, temp_dir: Path, test_db: Database
    ):
        """单次 download_with_fallback 同时产出音频+字幕时，两者归属同一个下载器。"""
        worker = DownloadWorker(**worker_deps)
        video_id = "attrboth12345"
        await test_db.get_or_create_video_resource(video_id)

        audio_file = temp_dir / "both.m4a"
        transcript_file = temp_dir / "both.en.srt"
        audio_file.write_text("audio")
        transcript_file.write_text("subs")

        mock_manager.download_with_fallback = AsyncMock(
            return_value=DownloaderResult(
                success=True,
                downloader="ytdlp",
                video_metadata=_make_metadata(video_id),
                audio_path=audio_file,
                transcript_path=transcript_file,
                has_transcript=True,
            )
        )

        task = Task(
            id="attr-task-both",
            video_id=video_id,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=True,
        )
        await test_db.create_task(task)

        result = await worker._execute_task(task)
        assert result["audio_downloader"] == "ytdlp"
        assert result["transcript_downloader"] == "ytdlp"

        await worker._update_task_completed_with_retry(task.id, result)

        saved = await test_db.get_task(task.id)
        assert saved is not None
        assert saved.audio_downloader == "ytdlp"
        assert saved.transcript_downloader == "ytdlp"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_manager")
    async def test_reused_resources_keep_attribution_null(
        self,
        worker_deps: dict,
        mock_manager: AsyncMock,
        temp_dir: Path,
        test_db: Database,
        test_settings: Settings,
    ):
        """
        缓存复用场景（reused_audio=True）不应写入下载器归属，
        reused 标志已经表达了来源，不应该写类似 'cache' 的占位值。
        """
        worker = DownloadWorker(**worker_deps)
        video_id = "attrreuse123"
        await test_db.get_or_create_video_resource(video_id)

        audio_dir = test_settings.audio_dir
        audio_dir.mkdir(parents=True, exist_ok=True)
        existing_audio_path = audio_dir / "existing.m4a"
        existing_audio_path.write_text("existing audio")

        await test_db.create_file(
            FileRecord(
                id="existing-audio-attr",
                video_id=video_id,
                file_type=FileType.AUDIO,
                filename="existing.m4a",
                filepath=str(existing_audio_path.relative_to(test_settings.data_dir)),
                size=100,
                format="m4a",
            )
        )

        transcript_file = temp_dir / "reuse.en.srt"
        transcript_file.write_text("subs")
        mock_manager.download_with_fallback = AsyncMock(
            return_value=DownloaderResult(
                success=True,
                downloader="tikhub",
                video_metadata=_make_metadata(video_id),
                audio_path=None,
                transcript_path=transcript_file,
                has_transcript=True,
            )
        )

        task = Task(
            id="attr-task-reuse",
            video_id=video_id,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=True,
        )
        await test_db.create_task(task)

        result = await worker._execute_task(task)
        assert result["reused_audio"] is True
        assert result["audio_downloader"] is None
        assert result["transcript_downloader"] == "tikhub"

        await worker._update_task_completed_with_retry(task.id, result)

        saved = await test_db.get_task(task.id)
        assert saved is not None
        assert saved.audio_downloader is None
        assert saved.transcript_downloader == "tikhub"

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_patch_manager")
    async def test_audio_fallback_attributes_only_audio(
        self, worker_deps: dict, mock_manager: AsyncMock, temp_dir: Path, test_db: Database
    ):
        """
        字幕不存在时降级下载音频：音频归属第二次调用的下载器，
        字幕本来就没有下载到，归属应保持 None。
        """
        worker = DownloadWorker(**worker_deps)
        video_id = "attrfallback1"
        await test_db.get_or_create_video_resource(video_id)

        audio_file = temp_dir / "fallback.m4a"
        audio_file.write_text("audio")

        mock_manager.download_with_fallback = AsyncMock(
            side_effect=[
                DownloaderResult(
                    success=True,
                    downloader="tikhub",
                    video_metadata=_make_metadata(video_id),
                    audio_path=None,
                    transcript_path=None,
                    has_transcript=False,
                ),
                DownloaderResult(
                    success=True,
                    downloader="cdp",
                    video_metadata=_make_metadata(video_id),
                    audio_path=audio_file,
                    transcript_path=None,
                    has_transcript=False,
                ),
            ]
        )

        task = Task(
            id="attr-task-fallback",
            video_id=video_id,
            video_url=f"https://www.youtube.com/watch?v={video_id}",
            status=TaskStatus.PENDING,
            include_audio=False,
            include_transcript=True,
        )
        await test_db.create_task(task)

        result = await worker._execute_task(task)
        assert result["audio_downloader"] == "cdp"
        assert result["transcript_downloader"] is None


# ==================== Transcript Fallback Completion Path ====================


class TestTranscriptFallbackAttribution:
    """_try_transcript_fallback 完成路径的归属写入。"""

    def _make_worker(
        self, db: Database, settings: Settings, file_service: FileService
    ) -> DownloadWorker:
        task_service = MagicMock()
        task_service.task_queue = MagicMock()
        task_service.task_queue.put = AsyncMock()
        worker = DownloadWorker(
            db=db,
            settings=settings,
            task_service=task_service,
            file_service=file_service,
            callback_service=MagicMock(),
            notify_service=AsyncMock(),
            metrics_collector=None,
        )
        worker.downloader_manager = AsyncMock()
        return worker

    @pytest.mark.asyncio
    async def test_newly_downloaded_transcript_fallback_records_downloader(
        self,
        test_db: Database,
        test_settings: Settings,
        file_service: FileService,
        temp_dir: Path,
    ):
        task = Task(
            id="tf-task-1",
            video_id="tf_video_1",
            video_url="https://www.youtube.com/watch?v=tf_video_1",
            status=TaskStatus.DOWNLOADING,
            include_audio=True,
            include_transcript=False,
        )
        await test_db.create_task(task)
        await test_db.get_or_create_video_resource("tf_video_1")

        worker = self._make_worker(test_db, test_settings, file_service)

        transcript_file = temp_dir / "tf.en.srt"
        transcript_file.write_text("subs")
        worker.downloader_manager.download_with_fallback = AsyncMock(
            return_value=DownloaderResult(
                success=True,
                downloader="ytdlp",
                video_metadata=_make_metadata("tf_video_1"),
                audio_path=None,
                transcript_path=transcript_file,
                has_transcript=True,
            )
        )

        success = await worker._try_transcript_fallback(task)
        assert success is True

        saved = await test_db.get_task("tf-task-1")
        assert saved is not None
        assert saved.transcript_downloader == "ytdlp"
        assert saved.audio_downloader is None

    @pytest.mark.asyncio
    async def test_cached_transcript_fallback_keeps_attribution_null(
        self,
        test_db: Database,
        test_settings: Settings,
        file_service: FileService,
        temp_dir: Path,
    ):
        """已有字幕缓存直接复用时，不应写入下载器归属。"""
        task = Task(
            id="tf-task-2",
            video_id="tf_video_2",
            video_url="https://www.youtube.com/watch?v=tf_video_2",
            status=TaskStatus.DOWNLOADING,
            include_audio=True,
            include_transcript=False,
        )
        await test_db.create_task(task)
        await test_db.get_or_create_video_resource("tf_video_2")

        cached_path = temp_dir / "cached.en.srt"
        cached_path.write_text("cached subs")
        await test_db.create_file(
            FileRecord(
                id="cached-transcript-1",
                video_id="tf_video_2",
                file_type=FileType.TRANSCRIPT,
                filename="cached.en.srt",
                filepath="cached.en.srt",
                language="en",
            )
        )

        worker = self._make_worker(test_db, test_settings, file_service)

        success = await worker._try_transcript_fallback(task)
        assert success is True

        saved = await test_db.get_task("tf-task-2")
        assert saved is not None
        assert saved.transcript_downloader is None
        assert saved.reused_transcript is True


# ==================== Failure Path: failure_details Persistence ====================


class TestFailureDetailsPersistence:
    """失败路径应把降级链每个下载器的尝试结果序列化写入 failure_details。"""

    def _make_worker(self, db: Database) -> DownloadWorker:
        task_service = MagicMock()
        task_service.task_queue = MagicMock()
        task_service.task_queue.put = AsyncMock()
        return DownloadWorker(
            db=db,
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
            notify_service=AsyncMock(),
            metrics_collector=None,
        )

    @pytest.mark.asyncio
    async def test_all_downloaders_failed_writes_structured_json(self, test_db: Database):
        task = Task(
            id="fail-task-1",
            video_id="fail_video_1",
            video_url="https://www.youtube.com/watch?v=fail_video_1",
            status=TaskStatus.DOWNLOADING,
            include_audio=True,
            include_transcript=True,
            # 已达重试上限，确保直接进入失败路径而不是被重试挡住
            retry_count=1,
        )
        await test_db.create_task(task)

        worker = self._make_worker(test_db)

        error = AllDownloadersFailed(
            errors=["cdp: no cookies exported", "ytdlp: nsig challenge failed"],
            error_code=ErrorCode.DOWNLOAD_FAILED,
            attempts=[
                DownloaderAttempt(
                    downloader="cdp", error_code="CDP_NO_COOKIES", message="no cookies exported"
                ),
                DownloaderAttempt(
                    downloader="ytdlp",
                    error_code="CDP_NSIG_FAILED",
                    message="nsig challenge failed",
                ),
            ],
        )

        await worker._handle_download_error(task, error)

        saved = await test_db.get_task("fail-task-1")
        assert saved is not None
        assert saved.status == TaskStatus.FAILED
        assert saved.failure_details is not None

        parsed = json.loads(saved.failure_details)
        assert len(parsed) == 2
        assert parsed[0] == {
            "downloader": "cdp",
            "error_code": "CDP_NO_COOKIES",
            "message": "no cookies exported",
        }
        assert parsed[1]["downloader"] == "ytdlp"
        assert parsed[1]["error_code"] == "CDP_NSIG_FAILED"

    @pytest.mark.asyncio
    async def test_retryable_error_does_not_write_failure_details(self, test_db: Database):
        """
        可重试错误（如任务级超时，retry_count 未达上限）应走重试分支而非直接失败，
        用它验证 failure_details 只在真正终态失败时才写入。
        退避 sleep 会等待数分钟，测试中 mock 掉以避免真实等待。
        """
        task = Task(
            id="fail-task-2",
            video_id="fail_video_2",
            video_url="https://www.youtube.com/watch?v=fail_video_2",
            status=TaskStatus.DOWNLOADING,
            include_audio=True,
            include_transcript=True,
        )
        await test_db.create_task(task)

        worker = self._make_worker(test_db)

        error = DownloaderError(
            message="Task timed out after 300s",
            error_code=ErrorCode.TASK_TIMEOUT,
        )

        with patch("src.core.worker.asyncio.sleep", new=AsyncMock()):
            await worker._handle_download_error(task, error)

        saved = await test_db.get_task("fail-task-2")
        assert saved is not None
        # TASK_TIMEOUT 是可重试错误，retry_count(0) < max_retries(1)，
        # 应走重试分支而非直接失败——用它来验证失败详情只在真正失败时才写入。
        assert saved.status == TaskStatus.PENDING
        assert saved.failure_details is None

    @pytest.mark.asyncio
    async def test_plain_downloader_error_non_retryable_writes_single_entry(
        self, test_db: Database
    ):
        """不可重试的普通错误应直接失败，failure_details 退化为单条记录。"""
        task = Task(
            id="fail-task-3",
            video_id="fail_video_3",
            video_url="https://www.youtube.com/watch?v=fail_video_3",
            status=TaskStatus.DOWNLOADING,
            include_audio=True,
            include_transcript=True,
        )
        await test_db.create_task(task)

        worker = self._make_worker(test_db)

        error = DownloaderError(
            message="Video is private",
            error_code=ErrorCode.VIDEO_PRIVATE,
            downloader="ytdlp",
        )

        await worker._handle_download_error(task, error)

        saved = await test_db.get_task("fail-task-3")
        assert saved is not None
        assert saved.status == TaskStatus.FAILED
        assert saved.failure_details is not None

        parsed = json.loads(saved.failure_details)
        assert parsed == [
            {"downloader": "ytdlp", "error_code": "VIDEO_PRIVATE", "message": "Video is private"}
        ]

    @pytest.mark.asyncio
    async def test_failure_message_truncated_to_200_chars(self, test_db: Database):
        task = Task(
            id="fail-task-4",
            video_id="fail_video_4",
            video_url="https://www.youtube.com/watch?v=fail_video_4",
            status=TaskStatus.DOWNLOADING,
            include_audio=True,
            include_transcript=True,
        )
        await test_db.create_task(task)

        worker = self._make_worker(test_db)

        long_message = "x" * 500
        error = AllDownloadersFailed(
            errors=[f"cdp: {long_message}"],
            error_code=ErrorCode.VIDEO_UNAVAILABLE,
            attempts=[
                DownloaderAttempt(
                    downloader="cdp", error_code="CDP_DOWNLOAD_FAILED", message=long_message
                )
            ],
        )

        await worker._handle_download_error(task, error)

        saved = await test_db.get_task("fail-task-4")
        assert saved is not None
        parsed = json.loads(saved.failure_details)
        assert len(parsed[0]["message"]) == 200
