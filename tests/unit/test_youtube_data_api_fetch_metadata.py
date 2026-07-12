"""
测试 YouTube Data API v3 下载路径的 fetch_metadata()。

背景（外部 code review 第 8 轮，Codex P1 指控 3）：videos.list 返回空 items 时，
fetch_metadata() 在 try 块内主动抛出 DownloaderError(VIDEO_UNAVAILABLE)——但这个
异常紧接着被同一方法里排在后面的 `except Exception as e:` 分支重新捕获（因为
DownloaderError 也是 Exception 的子类，且当时没有专门拦截 DownloaderError 的
分支），根据消息文本判断是否为网络错误后，把它改写成了 ErrorCode.DOWNLOAD_FAILED，
丢失了"视频明确不存在"的语义。

这里直接 mock 最底层的 googleapiclient.discovery.build（而不是 mock
fetch_metadata 本身或 _get_youtube_service），驱动真实的 fetch_metadata()
代码路径验证修复。
"""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import httplib2
import pytest
from googleapiclient.errors import HttpError

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.exceptions import DownloaderError
from src.downloaders.youtube_data_api_downloader import YoutubeDataApiDownloader


TEST_VIDEO_ID = "dQw4w9WgXcQ"
TEST_VIDEO_URL = f"https://www.youtube.com/watch?v={TEST_VIDEO_ID}"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_key="test-key", wecom_webhook_url="", youtube_data_api_key="yt-test-key"
    )


@pytest.fixture
def downloader(settings: Settings) -> YoutubeDataApiDownloader:
    return YoutubeDataApiDownloader(settings)


def _mock_youtube_service(execute_result=None, execute_side_effect=None) -> MagicMock:
    """
    构造 googleapiclient.discovery.build(...) 返回值的 mock，还原真实调用链：
    youtube.videos().list(part=..., id=...).execute()
    """
    service = MagicMock()
    execute_mock = service.videos.return_value.list.return_value.execute
    if execute_side_effect is not None:
        execute_mock.side_effect = execute_side_effect
    else:
        execute_mock.return_value = execute_result
    return service


def _http_error(status: int, reason: str, message: str = "error") -> HttpError:
    """构造带可解析 error_details 的 HttpError（googleapiclient 要求 error.message 存在
    才会填充 error_details，见 HttpError._get_reason 实现）。"""
    resp = httplib2.Response({"status": status})
    body = {
        "error": {
            "code": status,
            "message": message,
            "errors": [{"reason": reason, "message": message}],
        }
    }
    return HttpError(resp, json.dumps(body).encode())


class TestFetchMetadataEmptyItemsRaisesVideoUnavailable:
    """核心回归：空 items 必须保持 VIDEO_UNAVAILABLE，不能被改写成 DOWNLOAD_FAILED。"""

    @pytest.mark.asyncio
    async def test_empty_items_raises_video_unavailable_not_download_failed(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        service = _mock_youtube_service(execute_result={"items": []})
        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            with pytest.raises(DownloaderError) as exc_info:
                await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert exc_info.value.error_code == ErrorCode.VIDEO_UNAVAILABLE
        assert exc_info.value.error_code != ErrorCode.DOWNLOAD_FAILED


class TestFetchMetadataSuccess:
    """成功路径：字段解析 + live_broadcast_content 透传（回归）。"""

    @pytest.mark.asyncio
    async def test_success_fills_live_broadcast_content(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        service = _mock_youtube_service(
            execute_result={
                "items": [
                    {
                        "snippet": {
                            "title": "T",
                            "channelTitle": "A",
                            "channelId": "UC1",
                            "description": "d",
                            "publishedAt": "2026-01-01T00:00:00Z",
                            "thumbnails": {"high": {"url": "http://x/thumb.jpg"}},
                            "liveBroadcastContent": "live",
                        },
                        "contentDetails": {"duration": "PT1M40S"},
                        "statistics": {"viewCount": "100"},
                    }
                ]
            }
        )
        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is not None
        assert result["live_broadcast_content"] == "live"
        assert result["duration"] == 100
        assert result["title"] == "T"

    @pytest.mark.asyncio
    async def test_success_default_broadcast_content_none_when_not_live(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        service = _mock_youtube_service(
            execute_result={
                "items": [
                    {
                        "snippet": {
                            "title": "T",
                            "channelTitle": "A",
                            "liveBroadcastContent": "none",
                        },
                        "contentDetails": {"duration": "PT10S"},
                        "statistics": {},
                    }
                ]
            }
        )
        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            result = await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert result is not None
        assert result["live_broadcast_content"] == "none"


class TestFetchMetadataHttpErrorClassificationUnaffected:
    """HttpError 分类回归：新增的 `except DownloaderError: raise` 分支不能影响这部分逻辑。"""

    @pytest.mark.asyncio
    async def test_quota_exceeded_maps_to_rate_limited(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        http_error = _http_error(403, "quotaExceeded", "Quota exceeded")
        service = _mock_youtube_service(execute_side_effect=http_error)

        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            with pytest.raises(DownloaderError) as exc_info:
                await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert exc_info.value.error_code == ErrorCode.RATE_LIMITED

    @pytest.mark.asyncio
    async def test_404_maps_to_video_unavailable(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        http_error = _http_error(404, "notFound", "Not found")
        service = _mock_youtube_service(execute_side_effect=http_error)

        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            with pytest.raises(DownloaderError) as exc_info:
                await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert exc_info.value.error_code == ErrorCode.VIDEO_UNAVAILABLE


class TestFetchMetadataNetworkErrorClassification:
    """非 HttpError 的意外异常（如底层网络库抛出的 OSError）应维持 NETWORK_ERROR 分类。"""

    @pytest.mark.asyncio
    async def test_generic_network_exception_maps_to_network_error(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        service = _mock_youtube_service(execute_side_effect=OSError("Connection timeout"))
        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            with pytest.raises(DownloaderError) as exc_info:
                await downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)

        assert exc_info.value.error_code == ErrorCode.NETWORK_ERROR


class TestFetchMetadataDoesNotBlockEventLoop:
    """
    回归（外部 code review 第 13 轮，Codex P1 指控 1）：fetch_metadata 内部直接调用
    同步的 request.execute()（google-api-python-client 是同步库），会整段阻塞
    事件循环本身——asyncio.wait_for 只能取消"等待"，取消不了一个正在运行、
    尚未让出控制权的同步调用，所以 precheck 的超时形同虚设，而且不止 precheck
    请求被卡，同一进程里其他所有并发请求都会被这一次慢 API 调用拖住。

    验证方式：把 execute() mock 成一个耗时的同步函数（time.sleep 模拟慢 API），
    与 fetch_metadata 并发跑一个很短的 asyncio.sleep 任务。若 execute() 仍在
    协程里同步阻塞，事件循环在 execute() 返回前无法调度任何其他任务，短 sleep
    会被拖到 execute() 结束之后才完成；若已改用 asyncio.to_thread 把阻塞调用
    甩到线程池，短 sleep 应该几乎立刻按预期完成。
    """

    @pytest.mark.asyncio
    async def test_slow_execute_does_not_block_concurrent_coroutine(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        slow_seconds = 0.3

        def slow_execute():
            # 模拟慢 API：同步阻塞线程（这正是 google-api-python-client 的真实行为）
            time.sleep(slow_seconds)
            return {
                "items": [
                    {
                        "snippet": {"title": "T", "channelTitle": "A"},
                        "contentDetails": {"duration": "PT1S"},
                        "statistics": {},
                    }
                ]
            }

        service = _mock_youtube_service(execute_side_effect=slow_execute)

        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            start = time.monotonic()
            fetch_task = asyncio.create_task(
                downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID)
            )

            # 与 fetch_task 并发跑一个很短的 sleep：若 execute() 未被丢进线程池，
            # asyncio.create_task 调度的 fetch_task 会先被事件循环取到并同步跑完
            # 整个 0.3s 阻塞调用，才轮到这里的 sleep(0.01) 开始计时。
            await asyncio.sleep(0.01)
            concurrent_sleep_elapsed = time.monotonic() - start

            result = await fetch_task

        assert result is not None
        # 事件循环未被阻塞：并发的短 sleep 应该在远小于 slow_seconds 的时间内完成，
        # 不会被 execute() 的同步阻塞拖住。
        assert concurrent_sleep_elapsed < slow_seconds / 2

    @pytest.mark.asyncio
    async def test_slow_execute_can_be_interrupted_by_wait_for_timeout(
        self, downloader: YoutubeDataApiDownloader
    ) -> None:
        """precheck 场景的真实约束：asyncio.wait_for 短超时必须能真正让调用方
        及时拿到 TimeoutError 返回，而不是被同步阻塞拖到 execute() 自然结束。"""

        def slow_execute():
            time.sleep(0.3)
            return {"items": []}

        service = _mock_youtube_service(execute_side_effect=slow_execute)

        with patch(
            "src.downloaders.youtube_data_api_downloader.build", return_value=service
        ):
            start = time.monotonic()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    downloader.fetch_metadata(TEST_VIDEO_URL, TEST_VIDEO_ID),
                    timeout=0.05,
                )
            elapsed = time.monotonic() - start

        # 超时应该在接近 0.05s 时生效，而不是等到 slow_execute 的 0.3s 结束
        assert elapsed < 0.2
