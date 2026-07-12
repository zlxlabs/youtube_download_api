"""
测试 DownloaderManager.get_metadata 的 raise_content_errors 参数。

背景（外部 code review 发现的问题）：TaskService._precheck_video_downloadable
依赖捕获 DownloaderManager.get_metadata() 抛出的 DownloaderError 来拒绝
VIDEO_UNAVAILABLE/VIDEO_PRIVATE/VIDEO_REGION_BLOCKED 等已知不可下载的视频。
但 get_metadata() 内部的 _fetch_metadata_with_retry 会吞掉所有下载器抛出的
DownloaderError，全部失败时只返回 None——precheck 的 except DownloaderError
分支在生产环境永远不会被触发，422 拦截对这些内容级错误完全失效。旧测试直接
mock 整个 DownloaderManager.get_metadata 并令其 raise，掩盖了这个行为差异。

这里用"真实 DownloaderManager + mock 下载器"复现并验证修复：get_metadata
新增 raise_content_errors 参数，为 True 时内容级终态错误会被立即向上抛出；
默认（False）时行为与此前完全一致（吞掉异常，返回 None），保证其余调用点零影响。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.exceptions import DownloaderError
from src.downloaders.manager import CONTENT_LEVEL_ERROR_CODES, DownloaderManager


def _make_manager(mock_settings: MagicMock) -> DownloaderManager:
    """构造禁用熔断器的 DownloaderManager，避免真实初始化下载器（同 test_downloader_attempts.py 的模式）。"""
    mock_settings.circuit_breaker_enabled = False
    return DownloaderManager(mock_settings)


def _mock_downloader(name: str, error_code: ErrorCode, message: str) -> MagicMock:
    """构造一个 fetch_metadata 会抛出指定 DownloaderError 的 mock 下载器。"""
    downloader = MagicMock()
    downloader.name = name
    downloader.fetch_metadata = AsyncMock(
        side_effect=DownloaderError(message=message, error_code=error_code, downloader=name)
    )
    # 内容级错误天然不可重试（换下载器/重试都不会改变视频的客观状态）
    downloader.should_retry = MagicMock(return_value=False)
    return downloader


class TestContentLevelErrorCodesAlignment:
    """CONTENT_LEVEL_ERROR_CODES 应与 models.py 的 ErrorCode 枚举实际对齐。"""

    def test_contains_exactly_four_content_level_codes(self) -> None:
        assert CONTENT_LEVEL_ERROR_CODES == {
            ErrorCode.VIDEO_UNAVAILABLE,
            ErrorCode.VIDEO_PRIVATE,
            ErrorCode.VIDEO_LIVE_STREAM,
            ErrorCode.VIDEO_AGE_RESTRICTED,
        }

    def test_region_blocked_excluded(self) -> None:
        """
        外部 review 第13轮问题2(P2)：VIDEO_REGION_BLOCKED 是下载器/出口位置相关的
        错误（本地部署被地区封锁不代表远端下载器如 TikHub 也下载不了同一视频），
        不是视频客观状态，因此不属于全局终态错误，不应终止元数据降级链。
        """
        assert ErrorCode.VIDEO_REGION_BLOCKED not in CONTENT_LEVEL_ERROR_CODES


class TestGetMetadataRaiseContentErrors:
    """get_metadata(raise_content_errors=True) 应在内容级终态错误上向上抛出。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error_code", sorted(CONTENT_LEVEL_ERROR_CODES, key=lambda c: c.value))
    async def test_raises_on_content_level_error(self, error_code: ErrorCode) -> None:
        """下载器抛出内容级终态错误时，raise_content_errors=True 应让异常向上传播。"""
        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        manager.downloaders = [_mock_downloader("ytdlp", error_code, "video not downloadable")]

        with pytest.raises(DownloaderError) as exc_info:
            await manager.get_metadata(
                "https://www.youtube.com/watch?v=test",
                "test",
                raise_content_errors=True,
            )

        assert exc_info.value.error_code == error_code

    @pytest.mark.asyncio
    @pytest.mark.parametrize("error_code", sorted(CONTENT_LEVEL_ERROR_CODES, key=lambda c: c.value))
    async def test_default_swallows_content_level_error_and_returns_none(
        self, error_code: ErrorCode
    ) -> None:
        """默认行为（不传 raise_content_errors）与修复前完全一致：吞掉异常，返回 None。"""
        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        manager.downloaders = [_mock_downloader("ytdlp", error_code, "video not downloadable")]

        result = await manager.get_metadata("https://www.youtube.com/watch?v=test", "test")

        assert result is None

    @pytest.mark.asyncio
    async def test_raises_immediately_without_trying_next_downloader(self) -> None:
        """
        内容级错误与下载器实现无关：raise_content_errors=True 时应立即抛出，
        不再尝试降级链里的下一个下载器（不必等整条链跑完）。
        """
        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        first = _mock_downloader("ytdlp", ErrorCode.VIDEO_PRIVATE, "private video")
        second = MagicMock()
        second.name = "tikhub"
        second.fetch_metadata = AsyncMock(return_value={"title": "should not be reached"})
        manager.downloaders = [first, second]

        with pytest.raises(DownloaderError):
            await manager.get_metadata(
                "https://www.youtube.com/watch?v=test",
                "test",
                priority="ytdlp,tikhub",
                raise_content_errors=True,
            )

        second.fetch_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_content_level_error_still_swallowed_even_with_flag(self) -> None:
        """非内容级错误（如网络错误）即使传了 raise_content_errors=True 也不应抛出，仍正常降级。"""
        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        failing = _mock_downloader("ytdlp", ErrorCode.NETWORK_ERROR, "network error")
        succeeding = MagicMock()
        succeeding.name = "tikhub"
        succeeding.fetch_metadata = AsyncMock(return_value={"title": "fallback success"})
        manager.downloaders = [failing, succeeding]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=test",
            "test",
            priority="ytdlp,tikhub",
            raise_content_errors=True,
        )

        assert result == {"title": "fallback success"}

    @pytest.mark.asyncio
    async def test_region_blocked_swallowed_even_with_flag_falls_back_to_next_downloader(
        self,
    ) -> None:
        """
        外部 review 第13轮问题2(P2) 回归：VIDEO_REGION_BLOCKED 不再是内容级终态
        错误，即使 raise_content_errors=True 也不应上抛，应像普通失败一样被吞掉、
        照常降级到链上下一个下载器（地区限制是出口位置相关的错误，本地探测到
        不代表其他下载器/远端服务也下载不了）。
        """
        mock_settings = MagicMock(spec=Settings)
        manager = _make_manager(mock_settings)
        blocked = _mock_downloader(
            "ytdlp", ErrorCode.VIDEO_REGION_BLOCKED, "blocked in your region"
        )
        succeeding = MagicMock()
        succeeding.name = "tikhub"
        succeeding.fetch_metadata = AsyncMock(return_value={"title": "fallback success"})
        manager.downloaders = [blocked, succeeding]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=test",
            "test",
            priority="ytdlp,tikhub",
            raise_content_errors=True,
        )

        assert result == {"title": "fallback success"}
