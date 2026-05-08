"""
测试 Playwright driver 生命周期管理。

验证 playwright 实例（node driver 进程）被正确存储和清理，
防止长期运行后 driver 进程累积导致内存泄漏。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.downloaders.cdp.downloader import CDPDownloader


@pytest.fixture(autouse=True)
def reset_cdp_class_state():
    """每个测试前重置 CDPDownloader 类级别状态。"""
    CDPDownloader._browser = None
    CDPDownloader._playwright = None
    CDPDownloader._browser_lock = None
    CDPDownloader._last_health_check = 0
    CDPDownloader._cdp_health_status = {}
    CDPDownloader._circuit_breaker_state = "CLOSED"
    CDPDownloader._circuit_open_until = 0
    CDPDownloader._health_check_failures = 0
    yield
    CDPDownloader._browser = None
    CDPDownloader._playwright = None
    CDPDownloader._browser_lock = None
    CDPDownloader._cdp_health_status = {}


@pytest.fixture
def settings():
    """创建测试配置。"""
    return Settings(
        api_key="test-key",
        cdp_enabled=True,
        cdp_urls="http://localhost:9222",
        cdp_timeout=10,
    )


def _make_mock_playwright_and_browser():
    """构造 mock playwright 实例和 browser。"""
    mock_pw = AsyncMock()
    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True
    mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)
    mock_pw.stop = AsyncMock()
    return mock_pw, mock_browser


class TestPlaywrightLifecycle:
    """Playwright driver 进程生命周期测试。"""

    @pytest.mark.asyncio
    async def test_get_browser_stores_playwright_instance(self, settings):
        """首次连接时，_playwright 类变量应被赋值。"""
        downloader = CDPDownloader(settings)
        mock_pw, mock_browser = _make_mock_playwright_and_browser()

        with patch("src.downloaders.cdp.downloader.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)

            browser, _ = await downloader._get_browser()

            assert CDPDownloader._playwright is mock_pw
            assert CDPDownloader._browser is mock_browser

    @pytest.mark.asyncio
    async def test_reconnect_stops_old_playwright(self, settings):
        """重连时，旧的 playwright 实例应被 stop。"""
        downloader = CDPDownloader(settings)

        old_pw = AsyncMock()
        old_pw.stop = AsyncMock()
        CDPDownloader._playwright = old_pw

        # 模拟旧 browser 已断连
        old_browser = MagicMock()
        old_browser.is_connected.return_value = False
        CDPDownloader._browser = old_browser

        new_pw, new_browser = _make_mock_playwright_and_browser()

        with patch("src.downloaders.cdp.downloader.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=new_pw)

            browser, _ = await downloader._get_browser()

            # 旧 playwright 应被 stop
            old_pw.stop.assert_awaited_once()
            # 新 playwright 应被存储
            assert CDPDownloader._playwright is new_pw
            assert CDPDownloader._browser is new_browser

    @pytest.mark.asyncio
    async def test_close_stops_playwright(self, settings):
        """close() 应调用 playwright.stop() 释放 node driver。"""
        downloader = CDPDownloader(settings)

        mock_pw = AsyncMock()
        mock_pw.stop = AsyncMock()
        CDPDownloader._playwright = mock_pw

        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        CDPDownloader._browser = mock_browser

        # 清空 behavior simulator 的 pages
        downloader._behavior_simulator._owned_pages = set()

        await downloader.close()

        mock_browser.close.assert_awaited_once()
        mock_pw.stop.assert_awaited_once()
        assert CDPDownloader._playwright is None
        assert CDPDownloader._browser is None

    @pytest.mark.asyncio
    async def test_close_handles_playwright_stop_error(self, settings):
        """close() 中 playwright.stop() 失败不应抛异常。"""
        downloader = CDPDownloader(settings)

        mock_pw = AsyncMock()
        mock_pw.stop = AsyncMock(side_effect=Exception("stop failed"))
        CDPDownloader._playwright = mock_pw

        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock()
        CDPDownloader._browser = mock_browser

        downloader._behavior_simulator._owned_pages = set()

        # 不应抛出异常
        await downloader.close()

        assert CDPDownloader._playwright is None

    @pytest.mark.asyncio
    async def test_no_playwright_leak_on_connection_failure(self, settings):
        """连接失败时，已启动的 playwright 应被 stop。"""
        downloader = CDPDownloader(settings)

        mock_pw = AsyncMock()
        mock_pw.stop = AsyncMock()
        mock_pw.chromium.connect_over_cdp = AsyncMock(
            side_effect=Exception("connection refused")
        )

        with patch("src.downloaders.cdp.downloader.async_playwright") as mock_ap:
            mock_ap.return_value.start = AsyncMock(return_value=mock_pw)

            with pytest.raises(Exception, match="Failed to connect"):
                await downloader._get_browser()

            # 连接失败后 playwright 应被清理
            mock_pw.stop.assert_awaited_once()
            assert CDPDownloader._playwright is None

    @pytest.mark.asyncio
    async def test_existing_connected_browser_skips_playwright(self, settings):
        """已有健康连接时，不应启动新的 playwright。"""
        downloader = CDPDownloader(settings)

        mock_pw = AsyncMock()
        CDPDownloader._playwright = mock_pw

        mock_browser = MagicMock()
        mock_browser.is_connected.return_value = True
        CDPDownloader._browser = mock_browser

        with patch("src.downloaders.cdp.downloader.async_playwright") as mock_ap:
            browser, _ = await downloader._get_browser()

            # 不应创建新的 playwright
            mock_ap.return_value.start.assert_not_called()
            assert browser is mock_browser
