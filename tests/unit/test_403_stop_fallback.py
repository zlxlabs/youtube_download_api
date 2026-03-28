"""
测试 403 错误时停止降级逻辑。

当任意下载器报 403 错误时，证明本地 IP 有问题，
此时不应该继续尝试其他下载器，因为所有渠道都会检测目的地 IP。
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderError
from src.downloaders.manager import DownloaderManager
from src.downloaders.ytdlp_downloader import YtdlpDownloader


@pytest.fixture
def mock_settings():
    """创建模拟的配置对象。"""
    settings = MagicMock(spec=Settings)
    settings.downloader_priority = "ytdlp,tikhub"
    settings.circuit_breaker_enabled = False  # 禁用熔断器以简化测试
    settings.tikhub_api_key = "test-api-key"  # 启用 TikHub
    # 添加必要的属性以避免初始化错误
    settings.audio_quality = 128
    settings.data_dir = "data"
    settings.pot_server_url = None
    settings.cookie_file = None
    settings.http_proxy = None
    settings.https_proxy = None
    return settings


@pytest.mark.asyncio
async def test_403_error_stops_fallback(tmp_path):
    """
    测试：当第一个下载器报 403 错误时，不再尝试其他下载器。
    """
    # 创建两个模拟的下载器
    mock_downloader1 = MagicMock()
    mock_downloader1.name = "ytdlp"
    mock_downloader1.supports_resource_download = True
    mock_downloader1.download_resources = AsyncMock(
        side_effect=DownloaderError(
            message="HTTP 403 Forbidden",
            error_code=ErrorCode.RATE_LIMITED,
            downloader="ytdlp",
            http_status_code=403,
            stop_fallback=True,  # 关键：设置停止降级标志
        )
    )
    mock_downloader1.should_retry = MagicMock(return_value=False)
    mock_downloader1.should_trigger_circuit_breaker = MagicMock(return_value=False)

    mock_downloader2 = MagicMock()
    mock_downloader2.name = "tikhub"
    mock_downloader2.supports_resource_download = True
    mock_downloader2.download_resources = AsyncMock()
    mock_downloader2.should_retry = MagicMock(return_value=False)
    mock_downloader2.should_trigger_circuit_breaker = MagicMock(return_value=False)

    # 创建下载器管理器
    mock_settings = MagicMock(spec=Settings)
    mock_settings.circuit_breaker_enabled = False
    mock_settings.audio_download_priority = "ytdlp,tikhub"
    mock_settings.transcript_only_priority = "ytdlp,tikhub"
    manager = DownloaderManager(mock_settings)

    # 手动设置下载器列表
    manager.downloaders = [mock_downloader1, mock_downloader2]

    # Mock get_metadata 以跳过元数据获取
    manager.get_metadata = AsyncMock(return_value=None)

    # 执行下载（应该在第一个下载器失败后就停止）
    with pytest.raises(AllDownloadersFailed) as exc_info:
        await manager.download_with_fallback(
            video_url="https://www.youtube.com/watch?v=test",
            video_id="test",
            output_dir=tmp_path,
            include_audio=True,
            include_transcript=True,
        )

    # 验证：只尝试了第一个下载器（ytdlp）
    assert mock_downloader1.download_resources.call_count == 1, "应该调用了第一个下载器"

    # 验证：没有尝试第二个下载器（tikhub）
    assert mock_downloader2.download_resources.call_count == 0, "不应该调用第二个下载器"

    # 验证：错误信息中包含第一个下载器的错误
    error = exc_info.value
    assert "ytdlp" in str(error), "错误信息应该包含第一个下载器名称"


@pytest.mark.asyncio
async def test_other_errors_continue_fallback(tmp_path):
    """
    测试：当遇到非 403 错误时，应该继续尝试其他下载器。
    """
    # 创建两个模拟的下载器
    mock_downloader1 = MagicMock()
    mock_downloader1.name = "ytdlp"
    mock_downloader1.supports_resource_download = True
    mock_downloader1.download_resources = AsyncMock(
        side_effect=DownloaderError(
            message="Network timeout",
            error_code=ErrorCode.NETWORK_ERROR,
            downloader="ytdlp",
            http_status_code=None,
            stop_fallback=False,  # 不停止降级
        )
    )
    mock_downloader1.should_retry = MagicMock(return_value=False)
    mock_downloader1.should_trigger_circuit_breaker = MagicMock(return_value=False)

    mock_downloader2 = MagicMock()
    mock_downloader2.name = "tikhub"
    mock_downloader2.supports_resource_download = True
    mock_downloader2.download_resources = AsyncMock(
        side_effect=DownloaderError(
            message="Another error",
            error_code=ErrorCode.DOWNLOAD_FAILED,
            downloader="tikhub",
            http_status_code=None,
            stop_fallback=False,
        )
    )
    mock_downloader2.should_retry = MagicMock(return_value=False)
    mock_downloader2.should_trigger_circuit_breaker = MagicMock(return_value=False)

    # 创建下载器管理器
    mock_settings = MagicMock(spec=Settings)
    mock_settings.circuit_breaker_enabled = False
    mock_settings.audio_download_priority = "ytdlp,tikhub"
    mock_settings.transcript_only_priority = "ytdlp,tikhub"
    manager = DownloaderManager(mock_settings)

    # 手动设置下载器列表
    manager.downloaders = [mock_downloader1, mock_downloader2]

    # Mock get_metadata 以跳过元数据获取
    manager.get_metadata = AsyncMock(return_value=None)

    # 执行下载（应该尝试所有下载器）
    with pytest.raises(AllDownloadersFailed):
        await manager.download_with_fallback(
            video_url="https://www.youtube.com/watch?v=test",
            video_id="test",
            output_dir=tmp_path,
            include_audio=True,
            include_transcript=True,
        )

    # 验证：尝试了第一个下载器
    assert mock_downloader1.download_resources.call_count == 1, "应该调用了第一个下载器"

    # 验证：也尝试了第二个下载器（降级）
    assert mock_downloader2.download_resources.call_count == 1, "应该调用了第二个下载器（降级）"


def test_403_error_flag_in_exception():
    """
    测试：验证 DownloaderError 可以正确设置 stop_fallback 标志。
    """
    # 测试创建带 stop_fallback 标志的异常
    error = DownloaderError(
        message="HTTP 403 Forbidden",
        error_code=ErrorCode.RATE_LIMITED,
        downloader="ytdlp",
        http_status_code=403,
        stop_fallback=True,
    )

    # 验证属性正确设置
    assert error.http_status_code == 403, "应该保留 HTTP 状态码"
    assert error.stop_fallback is True, "403 错误应该设置 stop_fallback=True"
    assert error.message == "HTTP 403 Forbidden"
    assert error.error_code == ErrorCode.RATE_LIMITED

    # 测试默认 stop_fallback=False
    error2 = DownloaderError(
        message="Network timeout",
        error_code=ErrorCode.NETWORK_ERROR,
    )
    assert error2.stop_fallback is False, "默认情况下 stop_fallback 应该为 False"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
