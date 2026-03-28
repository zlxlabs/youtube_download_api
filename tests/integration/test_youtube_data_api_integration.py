"""
YouTube Data API v3 下载器集成测试。

注意：需要配置有效的 YOUTUBE_DATA_API_KEY 才能运行。
"""

import pytest

pytestmark = pytest.mark.requires_external

from src.config import get_settings
from src.downloaders.models import DownloaderType
from src.downloaders.youtube_data_api_downloader import YoutubeDataApiDownloader


@pytest.fixture
def settings():
    """创建测试用的 Settings 实例（从 .env 加载）。"""
    return get_settings()


@pytest.fixture
def downloader(settings):
    """创建 YouTube Data API 下载器实例。"""
    return YoutubeDataApiDownloader(settings)


def test_downloader_basic_properties(downloader):
    """测试下载器基本属性。"""
    assert downloader.name == "youtube_data_api"
    assert downloader.downloader_type == DownloaderType.YOUTUBE_DATA_API


def test_downloader_availability(downloader, settings):
    """测试下载器可用性检查。"""
    # 如果配置了 API Key，应该可用
    if settings.youtube_data_api_key:
        assert downloader.is_available is True
    else:
        pytest.skip("YOUTUBE_DATA_API_KEY not configured")


@pytest.mark.asyncio
async def test_fetch_metadata_success(downloader):
    """测试获取元数据（需要有效的 API Key）。"""
    if not downloader.is_available:
        pytest.skip("YOUTUBE_DATA_API_KEY not configured")

    # 使用一个公开的测试视频
    video_id = "dQw4w9WgXcQ"  # Rick Astley - Never Gonna Give You Up
    video_url = f"https://www.youtube.com/watch?v={video_id}"

    metadata = await downloader.fetch_metadata(video_url, video_id)

    # 验证元数据
    assert metadata is not None
    assert metadata.get("title") is not None
    assert metadata.get("author") is not None
    assert metadata.get("duration") is not None
    assert metadata.get("duration") > 0
    assert metadata.get("view_count") is not None
    assert metadata.get("thumbnail") is not None

    print(f"Metadata fetched: {metadata.get('title')}")


@pytest.mark.asyncio
async def test_download_resources_not_supported(downloader):
    """测试下载资源功能（应该抛出 NotImplementedError）。"""
    from pathlib import Path

    video_id = "dQw4w9WgXcQ"
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    output_dir = Path("/tmp/test")

    with pytest.raises(NotImplementedError) as exc_info:
        await downloader.download_resources(
            video_url=video_url,
            video_id=video_id,
            output_dir=output_dir,
        )

    assert "does not support resource downloading" in str(exc_info.value)


def test_duration_parser(downloader):
    """测试 ISO 8601 时长解析。"""
    # 测试各种格式
    assert downloader._parse_duration("PT1H2M3S") == 3723  # 1h 2m 3s
    assert downloader._parse_duration("PT15M30S") == 930  # 15m 30s
    assert downloader._parse_duration("PT3M") == 180  # 3m
    assert downloader._parse_duration("PT45S") == 45  # 45s
    assert downloader._parse_duration("PT1H") == 3600  # 1h
    assert downloader._parse_duration("PT0S") == 0  # 0s


def test_should_retry(downloader):
    """测试重试策略。"""
    from src.downloaders.exceptions import DownloaderError
    from src.db.models import ErrorCode

    # 网络错误 → 应该重试
    network_error = DownloaderError(
        message="SSL connection error",
        error_code=ErrorCode.NETWORK_ERROR,
        downloader="youtube_data_api",
    )
    assert downloader.should_retry(network_error) is True

    # API 限流错误 → 不应该重试（应该降级）
    rate_limit_error = DownloaderError(
        message="Rate limited",
        error_code=ErrorCode.RATE_LIMITED,
        downloader="youtube_data_api",
    )
    assert downloader.should_retry(rate_limit_error) is False

    # 视频不存在 → 不应该重试（应该降级）
    not_found_error = DownloaderError(
        message="Video not found",
        error_code=ErrorCode.VIDEO_UNAVAILABLE,
        downloader="youtube_data_api",
    )
    assert downloader.should_retry(not_found_error) is False


def test_should_trigger_circuit_breaker(downloader):
    """测试熔断器触发策略。"""
    from src.downloaders.exceptions import DownloaderError
    from src.db.models import ErrorCode

    # 配额超限 → 触发熔断器
    error_rate_limited = DownloaderError(
        message="Quota exceeded",
        error_code=ErrorCode.RATE_LIMITED,
        downloader="youtube_data_api",
    )
    assert downloader.should_trigger_circuit_breaker(error_rate_limited) is True

    # 视频不存在 → 不触发熔断器
    error_not_found = DownloaderError(
        message="Video not found",
        error_code=ErrorCode.VIDEO_UNAVAILABLE,
        downloader="youtube_data_api",
    )
    assert downloader.should_trigger_circuit_breaker(error_not_found) is False


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v", "-s"])
