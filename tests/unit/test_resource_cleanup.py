"""
Tests for resource cleanup on shutdown.

Covers:
- Fix 6: DownloaderManager.close() called during shutdown
- Fix 7: CDPDownloader.close() properly cleans up browser and pages
- Fix 8: DownloaderManager.close() isolates exceptions between downloaders
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDownloaderManagerClose:
    """Fix 6 & 8: DownloaderManager.close() with exception isolation."""

    @pytest.mark.asyncio
    async def test_close_calls_all_downloaders(self):
        """close() should call close() on each downloader that has it."""
        from src.downloaders.manager import DownloaderManager

        settings = MagicMock()
        settings.downloader_priority = ""
        settings.youtube_data_api_enabled = False
        settings.tikhub_enabled = False
        settings.cdp_enabled = False
        settings.ytdlp_enabled = False

        db = AsyncMock()
        manager = DownloaderManager(settings, db)

        # Create mock downloaders with close methods
        d1 = MagicMock()
        d1.name = "mock1"
        d1.close = AsyncMock()

        d2 = MagicMock()
        d2.name = "mock2"
        d2.close = AsyncMock()

        manager.downloaders = [d1, d2]

        await manager.close()

        d1.close.assert_awaited_once()
        d2.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_isolates_exceptions(self):
        """If one downloader's close() fails, others still get closed."""
        from src.downloaders.manager import DownloaderManager

        settings = MagicMock()
        settings.downloader_priority = ""
        settings.youtube_data_api_enabled = False
        settings.tikhub_enabled = False
        settings.cdp_enabled = False
        settings.ytdlp_enabled = False

        db = AsyncMock()
        manager = DownloaderManager(settings, db)

        d1 = MagicMock()
        d1.name = "failing"
        d1.close = AsyncMock(side_effect=RuntimeError("close failed"))

        d2 = MagicMock()
        d2.name = "healthy"
        d2.close = AsyncMock()

        manager.downloaders = [d1, d2]

        # Should not raise
        await manager.close()

        d1.close.assert_awaited_once()
        d2.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_skips_downloaders_without_close(self):
        """Downloaders without close() method are skipped."""
        from src.downloaders.manager import DownloaderManager

        settings = MagicMock()
        settings.downloader_priority = ""
        settings.youtube_data_api_enabled = False
        settings.tikhub_enabled = False
        settings.cdp_enabled = False
        settings.ytdlp_enabled = False

        db = AsyncMock()
        manager = DownloaderManager(settings, db)

        d_no_close = MagicMock(spec=[])  # No close attribute
        d_with_close = MagicMock()
        d_with_close.name = "with_close"
        d_with_close.close = AsyncMock()

        manager.downloaders = [d_no_close, d_with_close]

        await manager.close()
        d_with_close.close.assert_awaited_once()


class TestCDPDownloaderClose:
    """Fix 7 & 9: CDPDownloader.close() cleans up browser and owned pages."""

    @pytest.mark.asyncio
    async def test_close_closes_browser(self):
        """close() should close the shared browser instance."""
        from src.downloaders.cdp.downloader import CDPDownloader

        settings = MagicMock()
        settings.cdp_enabled = True
        settings.cdp_url_list = []
        settings.cdp_human_behavior_enabled = False
        settings.download_concurrency = 1

        downloader = CDPDownloader(settings)

        mock_browser = AsyncMock()
        CDPDownloader._browser = mock_browser

        await downloader.close()

        mock_browser.close.assert_awaited_once()
        assert CDPDownloader._browser is None

    @pytest.mark.asyncio
    async def test_close_cleans_owned_pages(self):
        """close() should close all owned pages from behavior simulator."""
        from src.downloaders.cdp.downloader import CDPDownloader

        settings = MagicMock()
        settings.cdp_enabled = True
        settings.cdp_url_list = []
        settings.cdp_human_behavior_enabled = False
        settings.download_concurrency = 1

        downloader = CDPDownloader(settings)
        CDPDownloader._browser = None  # No browser to close

        # Mock owned pages
        page1 = MagicMock()
        page1.is_closed.return_value = False
        page1.close = AsyncMock()

        page2 = MagicMock()
        page2.is_closed.return_value = True  # Already closed
        page2.close = AsyncMock()

        downloader._behavior_simulator._owned_pages = {page1, page2}

        await downloader.close()

        page1.close.assert_awaited_once()
        page2.close.assert_not_awaited()  # Already closed, skip
        assert len(downloader._behavior_simulator._owned_pages) == 0

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """Calling close() twice should not raise."""
        from src.downloaders.cdp.downloader import CDPDownloader

        settings = MagicMock()
        settings.cdp_enabled = True
        settings.cdp_url_list = []
        settings.cdp_human_behavior_enabled = False
        settings.download_concurrency = 1

        downloader = CDPDownloader(settings)
        CDPDownloader._browser = None

        await downloader.close()  # First call
        await downloader.close()  # Second call - should not raise

    @pytest.mark.asyncio
    async def test_close_handles_browser_error(self):
        """close() should handle browser.close() errors gracefully."""
        from src.downloaders.cdp.downloader import CDPDownloader

        settings = MagicMock()
        settings.cdp_enabled = True
        settings.cdp_url_list = []
        settings.cdp_human_behavior_enabled = False
        settings.download_concurrency = 1

        downloader = CDPDownloader(settings)

        mock_browser = AsyncMock()
        mock_browser.close = AsyncMock(side_effect=RuntimeError("connection lost"))
        CDPDownloader._browser = mock_browser

        # Should not raise
        await downloader.close()
        assert CDPDownloader._browser is None  # Still cleaned up
