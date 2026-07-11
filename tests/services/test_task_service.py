"""
TaskService 单元测试：前置校验（precheck）与活跃任务去重覆盖校验。

覆盖两个需求：
- 需求 A：create_task 在真正创建任务前，先通过元数据探测拦截已知不可下载的视频
  （直播/预约首播/视频不可用/私享/地区限制），并且严格 fail-open（探测超时或异常都不阻塞任务创建）。
- 需求 B：活跃任务去重只有在活跃任务的能力覆盖新请求时才复用，否则照常创建新任务。

元数据获取统一通过 mock 的 DownloaderManager.get_metadata（AsyncMock）完成，不触碰真实网络。
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.api.schemas import CreateTaskRequest
from src.config import Settings
from src.db.database import Database
from src.db.models import ErrorCode, Task, TaskStatus
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderError
from src.services.file_service import FileService
from src.services.task_service import TaskService, VideoNotDownloadableError


TEST_VIDEO_ID = "dQw4w9WgXcQ"
TEST_VIDEO_URL = f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}"


def _video_url(video_id: str) -> str:
    """构造一个格式合法的 YouTube 视频 URL（用于测试）。"""
    return f"https://www.youtube.com/watch?v={video_id}"


@pytest.fixture
def mock_downloader_manager() -> MagicMock:
    """
    带 get_metadata 的 DownloaderManager mock。

    默认返回一个普通（非直播）视频的元数据，测试中按需覆盖 return_value / side_effect。
    """
    manager = MagicMock()
    manager.get_metadata = AsyncMock(return_value={"title": "Normal video"})
    return manager


@pytest_asyncio.fixture
async def task_service(
    test_db: Database,
    test_settings: Settings,
    file_service: FileService,
    mock_downloader_manager: MagicMock,
) -> TaskService:
    """带 precheck 能力的 TaskService（precheck_enabled 使用 test_settings 默认值 True）。"""
    return TaskService(
        test_db, test_settings, file_service, downloader_manager=mock_downloader_manager
    )


# ==================== 需求 A：前置拦截不可下载视频 ====================


class TestPrecheckRejectsUndownloadable:
    """明确判定为不可下载时，create_task 应拒绝并抛出 VideoNotDownloadableError。"""

    @pytest.mark.asyncio
    async def test_live_broadcast_rejected(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """直播中的视频应被拒绝，error_code=VIDEO_LIVE_STREAM。"""
        mock_downloader_manager.get_metadata.return_value = {
            "title": "Live now",
            "live_broadcast_content": "live",
        }
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with pytest.raises(VideoNotDownloadableError) as exc_info:
            await task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_LIVE_STREAM
        assert exc_info.value.video_id == TEST_VIDEO_ID

    @pytest.mark.asyncio
    async def test_upcoming_broadcast_rejected(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """预约首播的视频应被拒绝，error_code=VIDEO_LIVE_STREAM。"""
        mock_downloader_manager.get_metadata.return_value = {
            "title": "Upcoming premiere",
            "live_broadcast_content": "upcoming",
        }
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with pytest.raises(VideoNotDownloadableError) as exc_info:
            await task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_LIVE_STREAM
        assert exc_info.value.video_id == TEST_VIDEO_ID

    @pytest.mark.asyncio
    async def test_downloader_error_video_unavailable_rejected(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """元数据获取抛出 DownloaderError(VIDEO_UNAVAILABLE) 应被拒绝并透传 error_code。"""
        mock_downloader_manager.get_metadata.side_effect = DownloaderError(
            message="Video not found", error_code=ErrorCode.VIDEO_UNAVAILABLE
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with pytest.raises(VideoNotDownloadableError) as exc_info:
            await task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_UNAVAILABLE
        assert exc_info.value.video_id == TEST_VIDEO_ID

    @pytest.mark.asyncio
    async def test_downloader_error_video_private_rejected(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """私享视频应被拒绝，error_code=VIDEO_PRIVATE。"""
        mock_downloader_manager.get_metadata.side_effect = DownloaderError(
            message="Video is private", error_code=ErrorCode.VIDEO_PRIVATE
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with pytest.raises(VideoNotDownloadableError) as exc_info:
            await task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_PRIVATE

    @pytest.mark.asyncio
    async def test_downloader_error_region_blocked_rejected(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """地区限制视频应被拒绝，error_code=VIDEO_REGION_BLOCKED。"""
        mock_downloader_manager.get_metadata.side_effect = DownloaderError(
            message="Blocked in your region", error_code=ErrorCode.VIDEO_REGION_BLOCKED
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with pytest.raises(VideoNotDownloadableError) as exc_info:
            await task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_REGION_BLOCKED

    @pytest.mark.asyncio
    async def test_precheck_requests_content_errors_to_be_raised(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """
        precheck 必须显式请求 get_metadata 在内容级终态错误上抛出（raise_content_errors=True）。

        背景：DownloaderManager.get_metadata 默认会吞掉所有下载器的 DownloaderError 并
        返回 None，precheck 的 except DownloaderError 分支在生产环境因此永远走不到。
        这里 mock 的是 get_metadata 本身，无法体现"是否真的会抛"——但可以锁死调用参数，
        防止未来有人误删这个 kwarg 导致 422 拦截在生产环境再次静默失效
        （真实吞错/抛错行为由 tests/unit/test_metadata_content_errors.py 覆盖）。
        """
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        await task_service.create_task(request)

        mock_downloader_manager.get_metadata.assert_awaited_once_with(
            video_url=TEST_VIDEO_URL, video_id=TEST_VIDEO_ID, raise_content_errors=True
        )


class TestPrecheckCachedLiveStatusRefresh:
    """
    Codex 第6轮问题1(P1)：缓存的 live/upcoming 状态不应让已结束的直播永远被 422 拒绝。

    DownloaderManager.get_metadata() 把元数据（含 live_broadcast_content）永久落库
    缓存。precheck 第一次读到的缓存若显示 live/upcoming，不能直接拒绝——直播可能早已
    结束、缓存只是没刷新。正确行为是再强制刷新一次拿新鲜数据，只有新鲜数据仍是
    live/upcoming 才 422；刷新失败/超时则 fail-open。
    """

    @pytest.mark.asyncio
    async def test_cached_upcoming_refreshed_to_ended_creates_task(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """缓存显示 upcoming，强制刷新后直播已结束（无 live_broadcast_content）：应正常建任务。"""
        calls: list[dict] = []

        async def _get_metadata(**kwargs):
            calls.append(kwargs)
            if kwargs.get("force_refresh"):
                return {"title": "Now a VOD", "live_broadcast_content": None}
            return {"title": "Upcoming premiere", "live_broadcast_content": "upcoming"}

        mock_downloader_manager.get_metadata.side_effect = _get_metadata
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await task_service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING
        # 第一次走缓存（未传 force_refresh），第二次强制刷新确认。
        assert len(calls) == 2
        assert calls[0].get("force_refresh", False) is False
        assert calls[1]["force_refresh"] is True
        assert calls[1]["raise_content_errors"] is True

    @pytest.mark.asyncio
    async def test_cached_upcoming_refreshed_still_upcoming_rejected(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """缓存显示 upcoming，强制刷新后依旧是 upcoming：应 422 拒绝。"""

        async def _get_metadata(**kwargs):
            return {"title": "Still upcoming", "live_broadcast_content": "upcoming"}

        mock_downloader_manager.get_metadata.side_effect = _get_metadata
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with pytest.raises(VideoNotDownloadableError) as exc_info:
            await task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_LIVE_STREAM

    @pytest.mark.asyncio
    async def test_cached_live_refresh_raises_fails_open(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """缓存显示 live，强制刷新时抛出异常（网络错误等）：应 fail-open，正常建任务。"""

        async def _get_metadata(**kwargs):
            if kwargs.get("force_refresh"):
                raise RuntimeError("network boom")
            return {"title": "Live now (cached)", "live_broadcast_content": "live"}

        mock_downloader_manager.get_metadata.side_effect = _get_metadata
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await task_service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_cached_live_refresh_times_out_fails_open(
        self,
        test_db: Database,
        test_settings: Settings,
        file_service: FileService,
        mock_downloader_manager: MagicMock,
    ) -> None:
        """缓存显示 live，强制刷新超时（挂起超过 precheck_timeout）：应 fail-open，正常建任务。"""

        async def _get_metadata(**kwargs):
            if kwargs.get("force_refresh"):
                await asyncio.sleep(10)
            return {"title": "Live now (cached)", "live_broadcast_content": "live"}

        mock_downloader_manager.get_metadata.side_effect = _get_metadata
        test_settings.precheck_timeout = 0.05

        service = TaskService(
            test_db, test_settings, file_service, downloader_manager=mock_downloader_manager
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_non_live_status_does_not_trigger_second_call(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """非 live/upcoming 状态：不应触发第二次刷新调用，只调一次 get_metadata。"""
        mock_downloader_manager.get_metadata.return_value = {
            "title": "Normal video",
            "live_broadcast_content": "none",
        }
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await task_service.create_task(request)

        assert response.task_id is not None
        mock_downloader_manager.get_metadata.assert_awaited_once_with(
            video_url=TEST_VIDEO_URL, video_id=TEST_VIDEO_ID, raise_content_errors=True
        )


class TestPrecheckFailOpen:
    """
    Fail-open：前置检查本身的任何故障都不能降低服务可用性，一律放行照常建任务。
    """

    @pytest.mark.asyncio
    async def test_metadata_fetch_timeout_fails_open(
        self,
        test_db: Database,
        test_settings: Settings,
        file_service: FileService,
        mock_downloader_manager: MagicMock,
    ) -> None:
        """元数据获取超过 precheck_timeout 应 fail-open，任务正常创建。"""

        async def _hang(*args, **kwargs):
            await asyncio.sleep(10)

        mock_downloader_manager.get_metadata.side_effect = _hang
        test_settings.precheck_timeout = 0.05

        service = TaskService(
            test_db, test_settings, file_service, downloader_manager=mock_downloader_manager
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_metadata_fetch_unexpected_exception_fails_open(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """元数据获取抛出非 DownloaderError 的意外异常应 fail-open，任务正常创建。"""
        mock_downloader_manager.get_metadata.side_effect = RuntimeError("boom")
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await task_service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_all_downloaders_failed_fails_open(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """AllDownloadersFailed（error_code 默认 DOWNLOAD_FAILED，不在拒绝名单内）应 fail-open。"""
        mock_downloader_manager.get_metadata.side_effect = AllDownloadersFailed(
            errors=["ytdlp: network error", "tikhub: quota exceeded"]
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await task_service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_metadata_none_fails_open(
        self, task_service: TaskService, mock_downloader_manager: MagicMock
    ) -> None:
        """所有下载器都拿不到元数据（返回 None）应 fail-open，任务正常创建。"""
        mock_downloader_manager.get_metadata.return_value = None
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await task_service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING


class TestPrecheckSkipped:
    """precheck_enabled=False 或未注入 downloader_manager 时，完全跳过前置检查。"""

    @pytest.mark.asyncio
    async def test_precheck_disabled_skips_metadata_call(
        self,
        test_db: Database,
        test_settings: Settings,
        file_service: FileService,
        mock_downloader_manager: MagicMock,
    ) -> None:
        """precheck_enabled=False 时，get_metadata 完全不应被调用。"""
        test_settings.precheck_enabled = False
        service = TaskService(
            test_db, test_settings, file_service, downloader_manager=mock_downloader_manager
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await service.create_task(request)

        assert response.task_id is not None
        mock_downloader_manager.get_metadata.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_downloader_manager_skips_precheck(
        self, test_db: Database, test_settings: Settings, file_service: FileService
    ) -> None:
        """未注入 downloader_manager（默认 None）时，precheck 应静默跳过，不报错。"""
        service = TaskService(test_db, test_settings, file_service)
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response = await service.create_task(request)

        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_cache_hit_skips_metadata_call(
        self,
        test_db: Database,
        test_settings: Settings,
        file_service: FileService,
        mock_downloader_manager: MagicMock,
    ) -> None:
        """
        文件级缓存命中路径不应触发前置检查（不能给缓存命中路径增加任何延迟）。
        """
        from src.db.models import FileRecord, FileType

        video_id = "cachehit0001"
        await test_db.get_or_create_video_resource(video_id)

        audio_dir = test_settings.audio_dir
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / "cached-audio.m4a"
        audio_path.write_text("mock audio")

        audio_record = FileRecord(
            id="cached-audio-file",
            video_id=video_id,
            file_type=FileType.AUDIO,
            filename="cached-audio.m4a",
            filepath=str(audio_path.relative_to(test_settings.data_dir)),
            size=100,
            format="m4a",
            created_at=datetime.now(timezone.utc),
            last_accessed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )
        await test_db.create_file(audio_record)

        service = TaskService(
            test_db, test_settings, file_service, downloader_manager=mock_downloader_manager
        )
        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=True,
            include_transcript=False,
        )

        response = await service.create_task(request)

        assert response.cache_hit is True
        mock_downloader_manager.get_metadata.assert_not_awaited()


# ==================== 需求 B：活跃任务去重的需求覆盖校验 ====================


class TestActiveTaskDedupCoverage:
    """
    只有当活跃任务的能力覆盖新请求时才复用，否则照常创建新任务。

    使用禁用 precheck 的 TaskService，避免前置检查逻辑干扰去重逻辑本身的验证。
    """

    @pytest_asyncio.fixture
    async def service(
        self, test_db: Database, test_settings: Settings, file_service: FileService
    ) -> TaskService:
        """去重测试不关心 precheck，直接禁用以隔离变量。"""
        test_settings.precheck_enabled = False
        return TaskService(test_db, test_settings, file_service)

    @pytest.mark.asyncio
    async def test_audio_only_active_task_does_not_cover_transcript_request(
        self, test_db: Database, service: TaskService
    ) -> None:
        """
        活跃任务只请求了音频，新请求需要字幕：不应复用（否则永远等不到字幕产出），
        应该照常创建一个新任务。
        """
        video_id = "deduptest001"
        await test_db.get_or_create_video_resource(video_id)

        existing_task = Task(
            id="existing-audio-only-task",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=False,
        )
        await test_db.create_task(existing_task)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=False,
            include_transcript=True,
        )
        response = await service.create_task(request)

        assert response.task_id is not None
        assert response.task_id != existing_task.id
        assert response.message != "Task already in progress"

    @pytest.mark.asyncio
    async def test_full_active_task_covers_audio_only_request(
        self, test_db: Database, service: TaskService
    ) -> None:
        """活跃任务同时覆盖音频+字幕，新请求只要音频：应直接复用该活跃任务。"""
        video_id = "deduptest002"
        await test_db.get_or_create_video_resource(video_id)

        existing_task = Task(
            id="existing-full-task",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=True,
        )
        await test_db.create_task(existing_task)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=True,
            include_transcript=False,
        )
        response = await service.create_task(request)

        assert response.task_id == existing_task.id
        assert response.message == "Task already in progress"

    @pytest.mark.asyncio
    async def test_identical_request_reuses_active_task(
        self, test_db: Database, service: TaskService
    ) -> None:
        """请求需求与活跃任务完全一致：应复用。"""
        video_id = "deduptest003"
        await test_db.get_or_create_video_resource(video_id)

        existing_task = Task(
            id="existing-identical-task",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=True,
        )
        await test_db.create_task(existing_task)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=True,
            include_transcript=True,
        )
        response = await service.create_task(request)

        assert response.task_id == existing_task.id
        assert response.message == "Task already in progress"

    @pytest.mark.asyncio
    async def test_transcript_only_active_task_does_not_cover_audio_request(
        self, test_db: Database, service: TaskService
    ) -> None:
        """活跃任务只请求了字幕，新请求需要音频：不应复用，应创建新任务。"""
        video_id = "deduptest004"
        await test_db.get_or_create_video_resource(video_id)

        existing_task = Task(
            id="existing-transcript-only-task",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=False,
            include_transcript=True,
        )
        await test_db.create_task(existing_task)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=True,
            include_transcript=False,
        )
        response = await service.create_task(request)

        assert response.task_id is not None
        assert response.task_id != existing_task.id
        assert response.message != "Task already in progress"

    @pytest.mark.asyncio
    async def test_older_active_task_covering_request_is_reused_even_if_not_latest(
        self, test_db: Database, service: TaskService
    ) -> None:
        """
        同一视频同时存在旧的 audio-only 活跃任务和更新的 transcript-only 活跃任务时，
        只检查"最新一条"活跃任务会漏掉更早的、恰好覆盖新请求的 audio 任务——
        新来的 audio 请求会被误判为不覆盖（因为最新一条是 transcript-only），
        进而重复创建一个新的 audio 任务。应遍历全部活跃任务，命中覆盖的那条
        （即使不是最新的）就应该被复用，不创建新任务。
        """
        video_id = "deduptest005"
        await test_db.get_or_create_video_resource(video_id)

        older_audio_task = Task(
            id="older-audio-task",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=False,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        await test_db.create_task(older_audio_task)

        newer_transcript_task = Task(
            id="newer-transcript-task",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=False,
            include_transcript=True,
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        await test_db.create_task(newer_transcript_task)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=True,
            include_transcript=False,
        )
        response = await service.create_task(request)

        assert response.task_id == older_audio_task.id
        assert response.message == "Task already in progress"

        # 不应该额外创建新任务：数据库里仍然只有这两条活跃任务
        active_tasks = await test_db.get_active_tasks_by_video(video_id)
        assert len(active_tasks) == 2

    @pytest.mark.asyncio
    async def test_cached_audio_plus_transcript_only_active_task_covers_remaining_need(
        self, test_db: Database, test_settings: Settings, file_service: FileService, service: TaskService
    ) -> None:
        """
        覆盖判断应对照"剩余需求"而非原始请求：音频已经文件级缓存命中
        （不再需要任何任务提供），只有 transcript-only 的活跃任务在跑，
        新请求同时要音频+字幕。

        实际缺口只有 transcript，活跃任务的 include_transcript=True 已经能
        覆盖这个缺口——不应该因为该任务 include_audio=False 就误判"不覆盖"，
        从而重复创建一个新任务。
        """
        from src.db.models import FileRecord, FileType

        video_id = "deduptest006"
        await test_db.get_or_create_video_resource(video_id)

        # 音频已文件级缓存命中
        audio_dir = test_settings.audio_dir
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / "cached-audio.m4a"
        audio_path.write_text("mock audio")
        audio_record = FileRecord(
            id="cached-audio-file-006",
            video_id=video_id,
            file_type=FileType.AUDIO,
            filename="cached-audio.m4a",
            filepath=str(audio_path.relative_to(test_settings.data_dir)),
            size=100,
            format="m4a",
            created_at=datetime.now(timezone.utc),
            last_accessed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )
        await test_db.create_file(audio_record)

        # 仅有一条 transcript-only 的活跃任务
        transcript_only_task = Task(
            id="transcript-only-task-006",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=False,
            include_transcript=True,
        )
        await test_db.create_task(transcript_only_task)

        # 新请求：音频（已缓存）+ 字幕（剩余需求，活跃任务可覆盖）
        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=True,
            include_transcript=True,
        )
        response = await service.create_task(request)

        assert response.task_id == transcript_only_task.id
        assert response.message == "Task already in progress"

        # 不应该额外创建新任务
        active_tasks = await test_db.get_active_tasks_by_video(video_id)
        assert len(active_tasks) == 1

    @pytest.mark.asyncio
    async def test_cached_audio_plus_uncovered_remaining_need_still_creates_task(
        self, test_db: Database, test_settings: Settings, file_service: FileService, service: TaskService
    ) -> None:
        """
        对照场景：音频已缓存，但活跃任务是 audio-only（不含字幕），
        新请求需要音频+字幕。剩余需求（字幕）未被任何活跃任务覆盖，
        必须照常创建新任务，不能被误判为"已覆盖"。
        """
        from src.db.models import FileRecord, FileType

        video_id = "deduptest007"
        await test_db.get_or_create_video_resource(video_id)

        audio_dir = test_settings.audio_dir
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / "cached-audio.m4a"
        audio_path.write_text("mock audio")
        audio_record = FileRecord(
            id="cached-audio-file-007",
            video_id=video_id,
            file_type=FileType.AUDIO,
            filename="cached-audio.m4a",
            filepath=str(audio_path.relative_to(test_settings.data_dir)),
            size=100,
            format="m4a",
            created_at=datetime.now(timezone.utc),
            last_accessed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )
        await test_db.create_file(audio_record)

        # 活跃任务只覆盖音频（对剩余需求——字幕——没有帮助）
        audio_only_task = Task(
            id="audio-only-task-007",
            video_id=video_id,
            video_url=_video_url(video_id),
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=False,
        )
        await test_db.create_task(audio_only_task)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=True,
            include_transcript=True,
        )
        response = await service.create_task(request)

        assert response.task_id is not None
        assert response.task_id != audio_only_task.id
        assert response.message != "Task already in progress"


# ==================== has_native_transcript 语义回归：未知(None) vs 确认无字幕(False) ====================


class TestTranscriptFallbackHasNativeTranscriptSemantics:
    """
    create_task 顶部的音频兜底判断只应在 video_resource.has_native_transcript
    严格等于 False（== 已被 worker 真实探测确认无字幕）时触发；has_native_transcript
    为 None（尚未探测）绝不能触发兜底，否则 precheck 元数据落库时误写的 False
    （已修复的 P2 bug）一旦复现，会让原本只是"还没探测过"的视频被错误地当成
    "确认无字幕"处理，把用户请求的字幕悄悄替换成音频。

    使用禁用 precheck 的 TaskService，隔离前置检查逻辑，只验证这段读取判断本身。
    """

    @pytest_asyncio.fixture
    async def service(
        self, test_db: Database, test_settings: Settings, file_service: FileService
    ) -> TaskService:
        """禁用 precheck，避免前置检查逻辑干扰音频兜底判断本身的验证。"""
        test_settings.precheck_enabled = False
        return TaskService(test_db, test_settings, file_service)

    async def _create_cached_audio(
        self, test_db: Database, test_settings: Settings, video_id: str
    ) -> None:
        """在给定 video_id 下预置一份文件级缓存的音频（供音频兜底逻辑复用）。"""
        from src.db.models import FileRecord, FileType

        audio_dir = test_settings.audio_dir
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"{video_id}-audio.m4a"
        audio_path.write_text("mock audio")
        audio_record = FileRecord(
            id=f"cached-audio-{video_id}",
            video_id=video_id,
            file_type=FileType.AUDIO,
            filename=audio_path.name,
            filepath=str(audio_path.relative_to(test_settings.data_dir)),
            size=100,
            format="m4a",
            created_at=datetime.now(timezone.utc),
            last_accessed_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )
        await test_db.create_file(audio_record)

    @pytest.mark.asyncio
    async def test_unknown_transcript_status_does_not_trigger_audio_fallback(
        self, test_db: Database, test_settings: Settings, service: TaskService
    ) -> None:
        """
        测试 C：has_native_transcript=None（未探测）+ 音频已缓存 + 请求字幕
        -> 不应触发音频兜底，应正常创建下载任务，等待 worker 真实探测字幕。
        """
        video_id = "transcriptc01"
        # get_or_create_video_resource 默认创建 has_native_transcript=None 的记录
        await test_db.get_or_create_video_resource(video_id)
        await self._create_cached_audio(test_db, test_settings, video_id)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=False,
            include_transcript=True,
        )
        response = await service.create_task(request)

        # 不是缓存兜底响应，而是正常创建了一个待处理的下载任务
        assert response.cache_hit is False
        assert response.task_id is not None
        assert response.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_confirmed_no_transcript_triggers_audio_fallback(
        self, test_db: Database, test_settings: Settings, service: TaskService
    ) -> None:
        """
        测试 D（回归，锁定既有行为不被破坏）：has_native_transcript=False
        （worker 真实探测确认无字幕）+ 音频已缓存 + 请求字幕
        -> 应触发音频兜底，直接返回缓存的音频，不创建新任务。
        """
        video_id = "transcriptd01"
        await test_db.get_or_create_video_resource(video_id)
        await test_db.update_video_resource(video_id, has_native_transcript=False)
        await self._create_cached_audio(test_db, test_settings, video_id)

        request = CreateTaskRequest(
            video_url=_video_url(video_id),
            include_audio=False,
            include_transcript=True,
        )
        response = await service.create_task(request)

        assert response.cache_hit is True
        assert response.task_id is None
        assert response.result is not None
        assert response.result.audio_fallback is True


# ==================== 同视频并发建任务的 TOCTOU 窗口(Codex 第6轮问题2) ====================


class TestConcurrentSameVideoRequestsDeduped:
    """
    create_task 里"活跃任务检查 -> 缓存检查 -> precheck -> 建任务"整段临界区
    此前没有互斥保护：并发 POST 同一个未缓存视频时，两个请求都能通过活跃任务检查，
    然后各自 await 元数据探测（最长 precheck_timeout），再各自插入任务，造成重复任务。

    TaskService 现在按 video_id 维护一把进程内 asyncio.Lock，把整段临界区串行化。
    """

    @pytest.mark.asyncio
    async def test_concurrent_same_video_creates_only_one_task(
        self,
        test_db: Database,
        test_settings: Settings,
        file_service: FileService,
        mock_downloader_manager: MagicMock,
    ) -> None:
        """
        并发两个同视频请求，precheck 用慢速 mock 拉开窗口：只应创建一条任务，
        第二个请求应在活跃任务检查处复用第一个请求刚建的任务。
        """
        precheck_call_count = 0

        async def _slow_metadata(**kwargs):
            nonlocal precheck_call_count
            precheck_call_count += 1
            await asyncio.sleep(0.05)
            return {"title": "Normal video", "live_broadcast_content": "none"}

        mock_downloader_manager.get_metadata.side_effect = _slow_metadata

        service = TaskService(
            test_db, test_settings, file_service, downloader_manager=mock_downloader_manager
        )
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        response_a, response_b = await asyncio.gather(
            service.create_task(request), service.create_task(request)
        )

        active_tasks = await test_db.get_active_tasks_by_video(TEST_VIDEO_ID)
        assert len(active_tasks) == 1

        task_ids = {response_a.task_id, response_b.task_id}
        assert len(task_ids) == 1
        messages = {response_a.message, response_b.message}
        assert "Task already in progress" in messages
        # 加锁串行化后，第二个请求在活跃任务检查处就命中了第一个请求刚建的任务，
        # 不会再走一次 precheck——get_metadata 全程只应被真正探测调用一次。
        assert precheck_call_count == 1

    @pytest.mark.asyncio
    async def test_different_videos_use_independent_locks(
        self, test_db: Database, test_settings: Settings, file_service: FileService
    ) -> None:
        """
        不同视频不应共享同一把锁、互相阻塞：验证锁按 video_id 隔离
        （持有视频 A 的锁期间，视频 B 的锁应能立即被获取）。
        """
        test_settings.precheck_enabled = False
        service = TaskService(test_db, test_settings, file_service)

        lock_a = service._get_video_lock("videoaaaaaa")
        lock_b = service._get_video_lock("videobbbbbb")
        assert lock_a is not lock_b

        async with lock_a:
            acquired_b = await asyncio.wait_for(lock_b.acquire(), timeout=0.1)
            assert acquired_b is True
            lock_b.release()

    @pytest.mark.asyncio
    async def test_video_lock_dict_stays_bounded(
        self, test_db: Database, test_settings: Settings, file_service: FileService
    ) -> None:
        """锁 dict 不应随请求数量无限增长：超过阈值后应清理已释放的锁。"""
        test_settings.precheck_enabled = False
        service = TaskService(test_db, test_settings, file_service)
        service._VIDEO_LOCKS_MAX = 5

        for i in range(20):
            video_id = f"boundedlk{i:03d}"
            request = CreateTaskRequest(video_url=_video_url(video_id))
            response = await service.create_task(request)
            assert response.task_id is not None

        assert len(service._video_locks) <= service._VIDEO_LOCKS_MAX
