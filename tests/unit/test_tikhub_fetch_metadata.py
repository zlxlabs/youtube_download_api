"""
测试 TikHub 下载路径的 fetch_metadata()。

背景（外部 code review 第 8 轮，Codex P1）：Codex 指出 TikHubDownloader.
fetch_metadata() catch 所有异常返回 None，内容级终态错误（视频不存在/私有等）
永远无法向上传播给 DownloaderManager.get_metadata(raise_content_errors=True)。

调查结论：TikHub API 的 code != 200 错误响应只带一句自由文本 message，没有
文档化/可复现的错误分类体系（不像 audios/subtitles 子对象那样有稳定的 errorId
取值域）。仓库里没有任何历史代码、测试夹具或文档记录过 TikHub 对"私有/不存在/
地区限制"视频的具体错误信号长什么样。贸然按字符串猜测容易把瞬时错误（限流/
鉴权失败）误判为内容级终态错误，导致 precheck 永久拒绝一个其实可下载的视频，
风险高于收益。因此这里维持原有行为不变（catch 所有异常返回 None），只新增两类
覆盖：
1. 回归锁定：确认"能判定的程度做不到就维持现状"这个决策没有被后续修改悄悄推翻。
2. live_broadcast_content 显式置 None（"未知"，非"确认不是直播"）的新增字段。

mock 的是最底层的 httpx.AsyncClient.get（TikHubDownloader.client.get），而不是
_fetch_video_info 或 fetch_metadata 本身。
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from src.config import Settings
from src.downloaders.tikhub_downloader import TikHubDownloader


TEST_VIDEO_ID = "dQw4w9WgXcQ"
TEST_VIDEO_URL = f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}"


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key="test-key", wecom_webhook_url="", tikhub_api_key="tikhub-test-key")


@pytest.fixture
def downloader(settings: Settings) -> TikHubDownloader:
    return TikHubDownloader(settings)


class TestTikHubFetchMetadataSuccess:
    """成功路径：字段解析 + live_broadcast_content 显式置 None。"""

    @pytest.mark.asyncio
    async def test_success_parses_fields_and_live_broadcast_content_is_none(
        self, downloader: TikHubDownloader
    ) -> None:
        response = httpx.Response(
            200,
            json={
                "code": 200,
                "data": {
                    "title": "Test Video",
                    "channel": {"name": "Test Author", "id": "UC123"},
                    "lengthSeconds": 120,
                },
            },
            request=httpx.Request("GET", TEST_VIDEO_URL),
        )
        downloader.client.get = AsyncMock(return_value=response)  # type: ignore[method-assign]

        result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is not None
        assert result["title"] == "Test Video"
        assert result["author"] == "Test Author"
        assert result["duration"] == 120
        # TikHub 元数据响应不含直播状态字段：显式 None 表示"未知"，
        # 让 DownloaderManager 的强制刷新兜底逻辑决定是否换下载器确认。
        assert result["live_broadcast_content"] is None


class TestTikHubFetchMetadataErrorsPreserveExistingBehavior:
    """
    错误路径回归锁定：TikHub 无法可靠判定内容级错误，维持"全部吞掉返回 None"。

    与 ytdlp/youtube_data_api 不同——这里不新增任何 DownloaderError 抛出路径，
    这些用例的目的是确认该决策没有被后续修改意外推翻。
    """

    @pytest.mark.asyncio
    async def test_api_level_error_returns_none(self, downloader: TikHubDownloader) -> None:
        """TikHub API 返回 code != 200（如视频不存在）：仍返回 None，不抛出。"""
        response = httpx.Response(
            200,
            json={"code": 400, "message": "Video not found or removed"},
            request=httpx.Request("GET", TEST_VIDEO_URL),
        )
        downloader.client.get = AsyncMock(return_value=response)  # type: ignore[method-assign]

        result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is None

    @pytest.mark.asyncio
    async def test_http_403_returns_none(self, downloader: TikHubDownloader) -> None:
        """HTTP 层错误（如鉴权/限流触发的 403）：仍返回 None，不抛出。"""
        response = httpx.Response(
            403, text="Forbidden", request=httpx.Request("GET", TEST_VIDEO_URL)
        )
        downloader.client.get = AsyncMock(return_value=response)  # type: ignore[method-assign]

        result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is None

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self, downloader: TikHubDownloader) -> None:
        downloader.client.get = AsyncMock(  # type: ignore[method-assign]
            side_effect=httpx.ConnectError("Network unreachable")
        )

        result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is None


class TestTikHubFetchMetadataAvailability:
    @pytest.mark.asyncio
    async def test_unavailable_without_api_key_returns_none(self) -> None:
        settings = Settings(api_key="test-key", wecom_webhook_url="", tikhub_api_key=None)
        downloader = TikHubDownloader(settings)

        result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is None
