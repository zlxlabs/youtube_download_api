"""
测试 ytdlp 下载路径的 fetch_metadata() 内容级错误分类与直播状态映射。

背景（外部 code review 第 8 轮，Codex P1）：DownloaderManager.get_metadata(
raise_content_errors=True) 依赖具体下载器的 fetch_metadata() 在遇到内容级终态
错误（视频不存在/私有/地区限制/年龄限制/直播）时抛出 DownloaderError，并通过
live_broadcast_content 字段暴露直播状态。但修复前：

1. core.downloader.get_video_info() 对 yt-dlp 抛出的所有异常统一吞成
   DOWNLOAD_FAILED，从不使用 _map_ytdlp_error 分类器（该分类器只在真实下载路径
   download()/extract_transcript_only() 里被使用）。
2. YtdlpDownloader.fetch_metadata() 又把 get_video_info() 的所有异常（无论
   DOWNLOAD_FAILED 还是具体分类）一律吞掉返回 None。
3. get_video_info() 从不读取 yt-dlp 返回信息里的 live_status 字段，
   live_broadcast_content 永远缺失。

三层叠加导致：默认 metadata_priority=ytdlp,tikhub 配置下，precheck 的 422
拦截和直播探测在生产环境完全拿不到终态信号。

这里直接 mock 最底层的 yt_dlp.YoutubeDL（而不是 mock get_video_info 或
fetch_metadata 本身），驱动真实的 get_video_info() 和 YtdlpDownloader.
fetch_metadata() 代码路径，验证修复后的分类/映射行为，同时用一个"网络错误
仍返回 None"的用例锁死非内容级错误的原有行为没有被顺手改掉。
"""

from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.exceptions import DownloaderError
from src.downloaders.ytdlp_downloader import YtdlpDownloader


TEST_VIDEO_ID = "dQw4w9WgXcQ"
TEST_VIDEO_URL = f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}"


def _patch_extract_info(*, return_value: dict | None = None, side_effect: Exception | None = None):
    """
    构造对 src.core.downloader.yt_dlp.YoutubeDL 的 patch 上下文。

    模拟 `with yt_dlp.YoutubeDL(opts) as ydl: ydl.extract_info(...)` 的调用链：
    YoutubeDL(...) 返回一个支持上下文管理器协议的 mock，其 extract_info()
    要么返回指定的 info 字典，要么抛出指定异常。
    """
    mock_ydl_instance = MagicMock()
    if side_effect is not None:
        mock_ydl_instance.extract_info.side_effect = side_effect
    else:
        mock_ydl_instance.extract_info.return_value = return_value

    mock_ydl_cls = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance
    mock_ydl_cls.return_value.__exit__.return_value = False

    return patch("src.core.downloader.yt_dlp.YoutubeDL", mock_ydl_cls)


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key="test-key", wecom_webhook_url="")


@pytest.fixture
def downloader(settings: Settings) -> YtdlpDownloader:
    return YtdlpDownloader(settings)


class TestGetVideoInfoErrorClassification:
    """core.downloader.get_video_info() 应复用 _map_ytdlp_error 分类内容级错误。"""

    @pytest.mark.asyncio
    async def test_private_video_maps_to_video_private(self, settings: Settings) -> None:
        from src.core.downloader import DownloadError, get_video_info

        error = yt_dlp.utils.DownloadError(
            "ERROR: [youtube] xxx: Private video. Sign in if you've been granted access"
        )
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloadError) as exc_info:
                await get_video_info(TEST_VIDEO_URL, settings)

        assert exc_info.value.error_code == ErrorCode.VIDEO_PRIVATE

    @pytest.mark.asyncio
    async def test_video_unavailable_maps_to_video_unavailable(
        self, settings: Settings
    ) -> None:
        from src.core.downloader import DownloadError, get_video_info

        error = yt_dlp.utils.DownloadError("ERROR: [youtube] xxx: Video unavailable")
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloadError) as exc_info:
                await get_video_info(TEST_VIDEO_URL, settings)

        assert exc_info.value.error_code == ErrorCode.VIDEO_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_country_uploader_restriction_maps_to_region_blocked(
        self, settings: Settings
    ) -> None:
        """
        外部 review 第15轮问题1(P1)：yt-dlp 典型地区限制文案 "The uploader has
        not made this video available in your country" 本身包含 "available"
        字样，之前会先命中泛化的 "not available" 判断被误判为 VIDEO_UNAVAILABLE
        （全局终态，precheck 直接 422 拒绝），与 REGION_BLOCKED 的 fail-open
        语义矛盾——本地探测受限不代表 TikHub 等远端出口也下载不了同一视频。
        """
        from src.core.downloader import DownloadError, get_video_info

        error = yt_dlp.utils.DownloadError(
            "ERROR: [youtube] xxx: The uploader has not made this video "
            "available in your country"
        )
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloadError) as exc_info:
                await get_video_info(TEST_VIDEO_URL, settings)

        assert exc_info.value.error_code == ErrorCode.VIDEO_REGION_BLOCKED

    @pytest.mark.asyncio
    async def test_country_not_available_variant_maps_to_region_blocked(
        self, settings: Settings
    ) -> None:
        """同一地区限制场景的另一种常见 yt-dlp 文案变体，同样要分类为地区限制。"""
        from src.core.downloader import DownloadError, get_video_info

        error = yt_dlp.utils.DownloadError(
            "ERROR: [youtube] xxx: This video is not available in your country"
        )
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloadError) as exc_info:
                await get_video_info(TEST_VIDEO_URL, settings)

        assert exc_info.value.error_code == ErrorCode.VIDEO_REGION_BLOCKED

    @pytest.mark.asyncio
    async def test_network_error_keeps_network_error_code(self, settings: Settings) -> None:
        """非内容级错误（网络问题）应维持原有分类，不应被误判为内容级错误。"""
        from src.core.downloader import DownloadError, get_video_info

        error = yt_dlp.utils.DownloadError("ERROR: unable to download: connection timeout")
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloadError) as exc_info:
                await get_video_info(TEST_VIDEO_URL, settings)

        assert exc_info.value.error_code == ErrorCode.NETWORK_ERROR

    @pytest.mark.asyncio
    async def test_non_ytdlp_exception_still_download_failed(self, settings: Settings) -> None:
        """非 yt_dlp.utils.DownloadError 的意外异常维持原行为：统一 DOWNLOAD_FAILED。"""
        from src.core.downloader import DownloadError, get_video_info

        with _patch_extract_info(side_effect=RuntimeError("boom")):
            with pytest.raises(DownloadError) as exc_info:
                await get_video_info(TEST_VIDEO_URL, settings)

        assert exc_info.value.error_code == ErrorCode.DOWNLOAD_FAILED


class TestGetVideoInfoLiveStatusMapping:
    """get_video_info() 应将 yt-dlp 的 live_status 映射到 live_broadcast_content。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "live_status,expected",
        [
            ("is_live", "live"),
            ("is_upcoming", "upcoming"),
            ("not_live", "none"),
            ("was_live", "none"),
            ("post_live", "none"),
            (None, None),
        ],
    )
    async def test_live_status_mapping(
        self, settings: Settings, live_status: str | None, expected: str | None
    ) -> None:
        from src.core.downloader import get_video_info

        info = {"title": "T", "uploader": "A", "duration": 10, "live_status": live_status}
        with _patch_extract_info(return_value=info):
            video_info = await get_video_info(TEST_VIDEO_URL, settings)

        assert video_info.live_broadcast_content == expected

    @pytest.mark.asyncio
    async def test_missing_live_status_key_maps_to_none(self, settings: Settings) -> None:
        """live_status 字段完全缺失（不是 None 值，而是 key 都不存在）时同样映射为 None。"""
        from src.core.downloader import get_video_info

        info = {"title": "T", "uploader": "A", "duration": 10}
        with _patch_extract_info(return_value=info):
            video_info = await get_video_info(TEST_VIDEO_URL, settings)

        assert video_info.live_broadcast_content is None


class TestYtdlpDownloaderFetchMetadata:
    """YtdlpDownloader.fetch_metadata() 端到端：mock yt_dlp.YoutubeDL，驱动真实调用链。"""

    @pytest.mark.asyncio
    async def test_private_video_raises_downloader_error(
        self, downloader: YtdlpDownloader
    ) -> None:
        error = yt_dlp.utils.DownloadError("ERROR: [youtube] xxx: Private video.")
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloaderError) as exc_info:
                await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert exc_info.value.error_code == ErrorCode.VIDEO_PRIVATE
        assert exc_info.value.downloader == "ytdlp"

    @pytest.mark.asyncio
    async def test_video_unavailable_raises_downloader_error(
        self, downloader: YtdlpDownloader
    ) -> None:
        error = yt_dlp.utils.DownloadError("ERROR: [youtube] xxx: Video unavailable")
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloaderError) as exc_info:
                await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert exc_info.value.error_code == ErrorCode.VIDEO_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_live_stream_raises_downloader_error(
        self, downloader: YtdlpDownloader
    ) -> None:
        error = yt_dlp.utils.DownloadError("ERROR: [youtube] xxx: This live event will begin in 2 hours, premieres in 2 hours")
        with _patch_extract_info(side_effect=error):
            with pytest.raises(DownloaderError) as exc_info:
                await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert exc_info.value.error_code == ErrorCode.VIDEO_LIVE_STREAM

    @pytest.mark.asyncio
    async def test_network_error_still_returns_none(self, downloader: YtdlpDownloader) -> None:
        """非内容级错误（网络问题）必须维持原有行为：吞掉异常返回 None，不抛出。"""
        error = yt_dlp.utils.DownloadError("ERROR: unable to download: connection timeout")
        with _patch_extract_info(side_effect=error):
            result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_exception_still_returns_none(
        self, downloader: YtdlpDownloader
    ) -> None:
        """非 yt-dlp 异常（如意外的 Python 错误）维持原行为：吞掉返回 None。"""
        with _patch_extract_info(side_effect=RuntimeError("boom")):
            result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is None

    @pytest.mark.asyncio
    async def test_success_includes_live_broadcast_content(
        self, downloader: YtdlpDownloader
    ) -> None:
        info = {
            "title": "Live now",
            "uploader": "Author",
            "duration": None,
            "live_status": "is_live",
        }
        with _patch_extract_info(return_value=info):
            result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is not None
        assert result["live_broadcast_content"] == "live"
        assert result["title"] == "Live now"

    @pytest.mark.asyncio
    async def test_success_normal_video_broadcast_content_none(
        self, downloader: YtdlpDownloader
    ) -> None:
        info = {
            "title": "Normal video",
            "uploader": "Author",
            "duration": 100,
            "live_status": "not_live",
        }
        with _patch_extract_info(return_value=info):
            result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is not None
        assert result["live_broadcast_content"] == "none"
