"""
集成测试：真实 DownloaderManager + 真实 YtdlpDownloader + mock 最底层 yt-dlp 库。

背景（外部 code review 第 8 轮，Codex P1）：此前的测试分层各自 mock 掉了紧邻自己
的下一层，从未验证过完整链路：

- tests/unit/test_metadata_content_errors.py：真实 DownloaderManager + mock 的
  下载器对象（fetch_metadata 直接 side_effect 出 DownloaderError）。验证的是
  manager 内部对"下载器抛出内容级错误"这件事的处理逻辑，但没有验证具体下载器
  是否真的会抛出这个错误。
- tests/services/test_task_service.py：mock 整个 DownloaderManager.get_metadata。
  验证的是 TaskService precheck 对"manager 抛出/返回"这件事的处理逻辑。
- tests/unit/test_ytdlp_fetch_metadata.py（本轮新增）：mock 最底层 yt_dlp.
  YoutubeDL，验证 YtdlpDownloader.fetch_metadata() 自身的分类逻辑。

这三层单独看都通过，但默认 metadata_priority=ytdlp,tikhub 配置下三层拼接起来
是否真的贯通，此前从未有测试验证过——这正是 Codex 指出的问题本身。这里只 mock
最底层的 yt_dlp.YoutubeDL，其余（DownloaderManager、YtdlpDownloader、
TaskService）全部使用真实对象，驱动完整链路。
"""

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
import yt_dlp

from src.api.schemas import CreateTaskRequest
from src.config import Settings
from src.db.database import Database
from src.db.models import ErrorCode
from src.downloaders.exceptions import DownloaderError
from src.downloaders.manager import DownloaderManager
from src.services.file_service import FileService
from src.services.task_service import TaskService, VideoNotDownloadableError


TEST_VIDEO_ID = "dQw4w9WgXcQ"
TEST_VIDEO_URL = f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}"


def _patch_extract_info(*, return_value: dict | None = None, side_effect: Exception | None = None):
    """mock 最底层的 yt_dlp.YoutubeDL().extract_info()，其余全部走真实代码路径。"""
    mock_ydl_instance = MagicMock()
    if side_effect is not None:
        mock_ydl_instance.extract_info.side_effect = side_effect
    else:
        mock_ydl_instance.extract_info.return_value = return_value

    mock_ydl_cls = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance
    mock_ydl_cls.return_value.__exit__.return_value = False

    return patch("src.core.downloader.yt_dlp.YoutubeDL", mock_ydl_cls)


def _configure_ytdlp_only(settings: Settings) -> None:
    """
    将所有下载器优先级配置收窄为只含 ytdlp，避免真实初始化 tikhub/youtube_data_api/
    cdp（这些下载器需要外部凭据，且不是本测试关心的对象）。同时关闭熔断器，避免
    跨用例的状态残留影响判断。
    """
    settings.circuit_breaker_enabled = False
    settings.metadata_priority = "ytdlp"
    settings.audio_download_priority = "ytdlp"
    settings.transcript_only_priority = "ytdlp"
    settings.downloader_priority = "ytdlp"


class TestRealManagerRealYtdlpDownloaderContentErrorPropagation:
    """真 DownloaderManager + 真 YtdlpDownloader + mock yt-dlp：get_metadata 端到端。"""

    @pytest.mark.asyncio
    async def test_private_video_raises_with_raise_content_errors_true(
        self, test_settings: Settings
    ) -> None:
        _configure_ytdlp_only(test_settings)
        manager = DownloaderManager(test_settings)
        error = yt_dlp.utils.DownloadError(
            "ERROR: [youtube] xxx: Private video. Sign in if you've been granted access"
        )

        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloaderError) as exc_info:
                await manager.get_metadata(
                    TEST_VIDEO_URL, TEST_VIDEO_ID, raise_content_errors=True
                )

        assert exc_info.value.error_code == ErrorCode.VIDEO_PRIVATE

    @pytest.mark.asyncio
    async def test_private_video_default_raise_content_errors_returns_none(
        self, test_settings: Settings
    ) -> None:
        """默认 raise_content_errors=False：行为与修复前完全一致，吞掉异常返回 None。"""
        _configure_ytdlp_only(test_settings)
        manager = DownloaderManager(test_settings)
        error = yt_dlp.utils.DownloadError(
            "ERROR: [youtube] xxx: Private video. Sign in if you've been granted access"
        )

        with _patch_extract_info(side_effect=error):
            result = await manager.get_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is None

    @pytest.mark.asyncio
    async def test_live_video_metadata_marks_live_broadcast_content(
        self, test_settings: Settings
    ) -> None:
        _configure_ytdlp_only(test_settings)
        manager = DownloaderManager(test_settings)
        info = {"title": "Live now", "uploader": "A", "duration": None, "live_status": "is_live"}

        with _patch_extract_info(return_value=info):
            result = await manager.get_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is not None
        assert result["live_broadcast_content"] == "live"


class TestPrecheckEndToEndWithRealDownloaderChain:
    """
    precheck 422 链路端到端：真 TaskService + 真 DownloaderManager + 真
    YtdlpDownloader，只 mock 最底层的 yt-dlp。复用 test_task_service.py 里已验证过
    的 TaskService/precheck 基建（test_db/test_settings/file_service fixtures，
    VideoNotDownloadableError 断言方式），区别只在于 downloader_manager 换成真实
    对象而不是 MagicMock。
    """

    @pytest_asyncio.fixture
    async def real_task_service(
        self, test_db: Database, test_settings: Settings, file_service: FileService
    ) -> TaskService:
        _configure_ytdlp_only(test_settings)
        manager = DownloaderManager(test_settings)
        return TaskService(
            test_db, test_settings, file_service, downloader_manager=manager
        )

    @pytest.mark.asyncio
    async def test_live_video_rejected_via_real_downloader_chain(
        self, real_task_service: TaskService
    ) -> None:
        info = {"title": "Live now", "uploader": "A", "duration": None, "live_status": "is_live"}
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with _patch_extract_info(return_value=info):
            with pytest.raises(VideoNotDownloadableError) as exc_info:
                await real_task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_LIVE_STREAM
        assert exc_info.value.video_id == TEST_VIDEO_ID

    @pytest.mark.asyncio
    async def test_private_video_rejected_via_real_downloader_chain(
        self, real_task_service: TaskService
    ) -> None:
        """
        私享视频（内容级终态错误，而非直播字段）同样应端到端触发 422——覆盖
        Codex 指控 1（fetch_metadata 吞掉内容级错误）与指控 2（live 字段缺失）
        之外的第三条路径：raise_content_errors 信号本身。
        """
        error = yt_dlp.utils.DownloadError("ERROR: [youtube] xxx: Private video.")
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with _patch_extract_info(side_effect=error):
            with pytest.raises(VideoNotDownloadableError) as exc_info:
                await real_task_service.create_task(request)

        assert exc_info.value.error_code == ErrorCode.VIDEO_PRIVATE

    @pytest.mark.asyncio
    async def test_normal_video_not_rejected_via_real_downloader_chain(
        self, real_task_service: TaskService
    ) -> None:
        """回归护栏：正常点播视频不应被误拒（防止 live 映射逻辑过度拦截）。"""
        info = {
            "title": "Normal video",
            "uploader": "A",
            "duration": 100,
            "live_status": "not_live",
        }
        request = CreateTaskRequest(video_url=TEST_VIDEO_URL)

        with _patch_extract_info(return_value=info):
            # dry_run=True（test_settings 默认）会让后续实际下载走 mock 分支，
            # 这里只关心 precheck 不抛 VideoNotDownloadableError。
            response = await real_task_service.create_task(request)

        assert response is not None
