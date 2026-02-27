"""
测试 CDP 下载器浏览器连接断开检测逻辑。

验证 _is_browser_connection_lost 能正确识别各种连接断开错误，
特别是之前遗漏的 WriteUnixTransport closed 错误。
"""

import pytest

from src.downloaders.cdp.downloader import CDPDownloader


class TestIsBrowserConnectionLost:
    """测试 _is_browser_connection_lost 方法"""

    def test_target_closed(self):
        """原有场景: Playwright target closed"""
        msg = "target closed"
        assert CDPDownloader._is_browser_connection_lost(msg) is True

    def test_target_page_closed(self):
        """原有场景: Playwright target page closed"""
        msg = "page's target closed"
        assert CDPDownloader._is_browser_connection_lost(msg) is True

    def test_write_unix_transport_closed(self):
        """修复的核心场景: WriteUnixTransport closed"""
        msg = (
            "browsercontext.new_page: unable to perform operation on "
            "<writeunixtransport closed=true reading=false 0x1460dc64a740>; "
            "the handler is closed"
        )
        assert CDPDownloader._is_browser_connection_lost(msg) is True

    def test_handler_closed_generic(self):
        """通用 handler closed 错误"""
        msg = "the handler is closed"
        assert CDPDownloader._is_browser_connection_lost(msg) is True

    def test_connection_closed(self):
        """通用连接关闭错误"""
        msg = "connection closed unexpectedly"
        assert CDPDownloader._is_browser_connection_lost(msg) is True

    def test_websocket_connection_closed(self):
        """WebSocket 连接关闭"""
        msg = "websocket connection closed"
        assert CDPDownloader._is_browser_connection_lost(msg) is True

    def test_normal_download_error(self):
        """普通下载错误不应触发连接清理"""
        msg = "http 403 forbidden"
        assert CDPDownloader._is_browser_connection_lost(msg) is False

    def test_timeout_error(self):
        """超时错误不应触发连接清理"""
        msg = "timeout 30000ms exceeded"
        assert CDPDownloader._is_browser_connection_lost(msg) is False

    def test_network_error(self):
        """网络错误不应触发连接清理"""
        msg = "net::err_connection_refused"
        assert CDPDownloader._is_browser_connection_lost(msg) is False

    def test_page_crash(self):
        """页面崩溃不应触发连接清理（页面级别，非浏览器级别）"""
        msg = "page crashed"
        assert CDPDownloader._is_browser_connection_lost(msg) is False


class TestMatchConnectionError:
    """测试 _match_connection_error 方法的日志输出"""

    def test_target_closed_label(self):
        msg = "target closed"
        assert CDPDownloader._match_connection_error(msg) == "target closed"

    def test_handler_closed_label(self):
        msg = "the handler is closed"
        assert CDPDownloader._match_connection_error(msg) == "handler closed"

    def test_connection_closed_label(self):
        msg = "connection closed"
        assert CDPDownloader._match_connection_error(msg) == "connection closed"

    def test_unknown_label(self):
        msg = "some other error"
        assert CDPDownloader._match_connection_error(msg) == "unknown connection error"
