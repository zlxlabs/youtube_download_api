"""
测试 CDP 下载器的浏览器重连功能。

验证当浏览器连接断开时，下载器能够自动检测并重新连接。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.downloaders.cdp.downloader import CDPDownloader


@pytest.fixture
def settings():
    """创建测试配置。"""
    return Settings(
        api_key="test-key",
        cdp_enabled=True,
        cdp_urls="http://192.168.31.222:9223",
        cdp_timeout=10,
    )


@pytest.mark.asyncio
async def test_browser_reconnection_when_disconnected(settings):
    """测试当浏览器断开连接时，能够自动重新连接。"""
    downloader = CDPDownloader(settings)

    # 模拟一个已断开连接的浏览器
    mock_browser_disconnected = MagicMock()
    mock_browser_disconnected.is_connected.return_value = False

    # 模拟一个新的健康浏览器
    mock_browser_new = MagicMock()
    mock_browser_new.is_connected.return_value = True
    mock_context = AsyncMock()
    mock_browser_new.new_context = AsyncMock(return_value=mock_context)

    with patch("src.downloaders.cdp.downloader.async_playwright") as mock_playwright:
        # 设置初始状态：存在一个已断开的浏览器
        CDPDownloader._browser = mock_browser_disconnected
        cdp_url = settings.cdp_url_list[0]
        downloader._cdp_health_status[cdp_url].is_healthy = True

        # 模拟 Playwright 连接
        mock_pw_instance = AsyncMock()
        mock_pw_instance.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser_new)
        mock_playwright.return_value.start = AsyncMock(return_value=mock_pw_instance)

        # 调用 _get_browser，应该检测到断开并重新连接
        browser, cdp_url = await downloader._get_browser()

        # 验证
        assert browser == mock_browser_new
        assert browser.is_connected()
        assert mock_browser_disconnected.is_connected.called
        assert mock_pw_instance.chromium.connect_over_cdp.called


@pytest.mark.asyncio
async def test_browser_keeps_connection_when_connected(settings):
    """测试当浏览器保持连接时，不会重新建立连接。"""
    downloader = CDPDownloader(settings)

    # 模拟一个已连接的浏览器
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True

    with patch("src.downloaders.cdp.downloader.async_playwright") as mock_playwright:
        # 设置初始状态：存在一个健康的浏览器
        CDPDownloader._browser = mock_browser
        cdp_url = settings.cdp_url_list[0]
        downloader._cdp_health_status[cdp_url].is_healthy = True

        # 调用 _get_browser
        browser, cdp_url = await downloader._get_browser()

        # 验证：返回现有浏览器，没有重新连接
        assert browser == mock_browser
        assert browser.is_connected()
        assert not mock_playwright.called  # 不应该尝试重新连接


@pytest.mark.asyncio
async def test_browser_reference_cleared_on_target_closed_error(settings):
    """测试当遇到 target closed 错误时，会清空浏览器引用。"""
    from pathlib import Path

    downloader = CDPDownloader(settings)

    # 模拟浏览器
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True
    mock_browser.contexts = []

    # 模拟 new_context 抛出 target closed 错误
    async def mock_new_context_error(*args, **kwargs):
        raise Exception("Browser.new_context: Target page, context or browser has been closed")

    mock_browser.new_context = mock_new_context_error

    with patch("src.downloaders.cdp.downloader.async_playwright"):
        # 设置初始状态
        CDPDownloader._browser = mock_browser
        cdp_url = settings.cdp_url_list[0]
        downloader._cdp_health_status[cdp_url].is_healthy = True

        # 尝试下载（应该失败）
        with pytest.raises(Exception, match="Target.*closed"):
            await downloader.download_resources(
                video_url="https://www.youtube.com/watch?v=test",
                video_id="test",
                output_dir=Path("/tmp"),
                include_audio=True,
                include_transcript=False,
            )

        # 验证：浏览器引用应该被清空
        assert CDPDownloader._browser is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
