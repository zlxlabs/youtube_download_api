"""
CDP 人类行为模拟单元测试。

测试 HumanBehaviorSimulator 的各个方法。
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Settings
from src.downloaders.cdp.human_behavior import HumanBehaviorSimulator

# 跳过测试如果 Playwright 不可用
pytest.importorskip("playwright", reason="Playwright is required for CDP tests")


@pytest.fixture
def settings():
    """创建测试配置。"""
    return Settings(
        # 基础配置
        data_dir=Path("./data"),
        api_key="test-key",

        # CDP 人类行为配置（快速模式，用于测试）
        cdp_human_behavior_enabled=True,
        cdp_quick_mode=False,
        cdp_watch_duration_min=5,  # 最小允许值（配置验证）
        cdp_watch_duration_max=10,  # 最小允许值
        cdp_page_alive_min=10,      # 最小允许值
        cdp_page_alive_max=20,      # 最小允许值
        cdp_scroll_probability=0.8,
        cdp_pause_probability=0.2,
    )


@pytest.fixture
def simulator(settings):
    """创建人类行为模拟器实例。"""
    return HumanBehaviorSimulator(settings)


class TestCleanupOldPages:
    """测试清理旧 Page 功能。"""

    @pytest.mark.asyncio
    async def test_cleanup_empty_context(self, simulator):
        """测试清理空 Context（无 Page）。"""
        # 模拟空 Context
        context = MagicMock()
        context.pages = []

        # 调用清理（应该无操作）
        await simulator.cleanup_old_pages(context)

        # 验证：无异常

    @pytest.mark.asyncio
    async def test_cleanup_multiple_pages(self, simulator):
        """测试清理多个 Page。"""
        # 模拟 3 个 Page
        page1 = AsyncMock()
        page1.is_closed = MagicMock(return_value=False)
        page1.url = "https://www.youtube.com/watch?v=test1"

        page2 = AsyncMock()
        page2.is_closed = MagicMock(return_value=False)
        page2.url = "https://www.youtube.com/watch?v=test2"

        page3 = AsyncMock()
        page3.is_closed = MagicMock(return_value=False)
        page3.url = "https://www.youtube.com/watch?v=test3"

        # 注册到 _owned_pages（cleanup_old_pages 只清理 owned pages）
        simulator._owned_pages = {page1, page2, page3}

        context = MagicMock()
        context.pages = [page1, page2, page3]

        # 调用清理（keep_last=True 会保留最后一个）
        kept = await simulator.cleanup_old_pages(context, keep_last=False)

        # 验证：所有 Page 都被关闭
        page1.close.assert_called_once()
        page2.close.assert_called_once()
        page3.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_page_already_closed(self, simulator):
        """测试清理已关闭的 Page（不抛异常）。"""
        # 模拟已关闭的 Page（is_closed 返回 True）
        page = AsyncMock()
        page.is_closed = MagicMock(return_value=True)
        page.url = "https://www.youtube.com/watch?v=test"
        page.close.side_effect = Exception("Page already closed")

        # 注册到 _owned_pages
        simulator._owned_pages = {page}

        context = MagicMock()
        context.pages = [page]

        # 调用清理（is_closed=True 的页面会被 cleanup 过滤掉）
        await simulator.cleanup_old_pages(context, keep_last=False)

        # 验证：is_closed=True 的页面不会被尝试关闭（被过滤了）
        page.close.assert_not_called()


class TestSleepWithPageCheck:
    """测试分段睡眠功能。"""

    @pytest.mark.asyncio
    async def test_sleep_full_duration(self, simulator):
        """测试完整睡眠（Page 未关闭）。"""
        # 模拟 Page
        page = MagicMock()
        # is_closed 是同步方法
        page.is_closed = MagicMock(return_value=False)

        # 睡眠 1 秒（检查间隔 0.5 秒）
        import time
        start = time.time()
        await simulator._sleep_with_page_check(page, 1.0, "test_video", check_interval=0.5)
        elapsed = time.time() - start

        # 验证：耗时约 1 秒（±0.2秒误差）
        assert 0.8 <= elapsed <= 1.2

    @pytest.mark.asyncio
    async def test_sleep_early_exit(self, simulator):
        """测试提前退出（Page 被关闭）。"""
        # 模拟 Page（0.5 秒后关闭）
        page = MagicMock()
        call_count = 0

        def is_closed_side_effect():
            nonlocal call_count
            call_count += 1
            return call_count > 2  # 第 3 次检查时返回 True

        # is_closed 是同步方法
        page.is_closed = MagicMock(side_effect=is_closed_side_effect)

        # 睡眠 5 秒（检查间隔 0.3 秒）
        import time
        start = time.time()
        await simulator._sleep_with_page_check(page, 5.0, "test_video", check_interval=0.3)
        elapsed = time.time() - start

        # 验证：提前退出（< 1 秒）
        assert elapsed < 1.0


class TestSimulateScroll:
    """测试滚动模拟功能。"""

    @pytest.mark.asyncio
    async def test_scroll_success(self, simulator):
        """测试滚动成功（强制轻度滚动）。"""
        # 模拟 Page
        page = AsyncMock()
        # is_closed() 是同步方法，返回 False
        page.is_closed = MagicMock(return_value=False)
        page.evaluate = AsyncMock()

        # Mock random.random() 返回 0.5，确保触发轻度滚动（0.4 < 0.5 < 0.75）
        with patch('random.random', return_value=0.5):
            # 调用滚动
            await simulator._simulate_scroll(page)

        # 验证：调用了 evaluate（轻度滚动会调用 1-2 次）
        assert page.evaluate.call_count >= 1
        # 验证：JS 代码包含 scrollBy（新逻辑使用 scrollBy）
        js_code = page.evaluate.call_args[0][0]
        assert "scrollBy" in js_code

    @pytest.mark.asyncio
    async def test_scroll_no_scroll(self, simulator):
        """测试不滚动场景（40% 概率）。"""
        # 模拟 Page
        page = AsyncMock()
        page.is_closed = MagicMock(return_value=False)
        page.evaluate = AsyncMock()

        # Mock random.random() 返回 0.3，触发"不滚动"逻辑（< 0.4）
        with patch('random.random', return_value=0.3):
            await simulator._simulate_scroll(page)

        # 验证：未调用 evaluate
        page.evaluate.assert_not_called()

    @pytest.mark.asyncio
    async def test_scroll_page_closed(self, simulator):
        """测试 Page 已关闭时跳过滚动。"""
        # 模拟已关闭的 Page
        page = MagicMock()
        # is_closed 是同步方法
        page.is_closed = MagicMock(return_value=True)

        # 调用滚动（应该立即返回）
        await simulator._simulate_scroll(page)

        # 验证：未调用 evaluate

    @pytest.mark.asyncio
    async def test_scroll_error_handling(self, simulator):
        """测试滚动异常处理。"""
        # 模拟 Page（evaluate 抛异常）
        page = AsyncMock()
        # is_closed 是同步方法
        page.is_closed = MagicMock(return_value=False)
        page.evaluate.side_effect = Exception("Scroll failed")

        # 调用滚动（应该捕获异常，不抛出）
        await simulator._simulate_scroll(page)

        # 验证：无异常抛出


class TestSimulatePauseResume:
    """测试暂停/恢复模拟功能。"""

    @pytest.mark.asyncio
    async def test_pause_resume_success(self, simulator):
        """测试暂停/恢复成功。"""
        # 模拟 Page
        page = AsyncMock()
        # is_closed() 是同步方法，返回 False
        page.is_closed = MagicMock(return_value=False)
        page.evaluate = AsyncMock()

        # 调用暂停/恢复（总时长 1 秒）
        await simulator._simulate_pause_resume(page, 1.0)

        # 验证：调用了 evaluate（至少 2 次：pause + resume）
        assert page.evaluate.call_count >= 2

    @pytest.mark.asyncio
    async def test_pause_resume_page_closed_early(self, simulator):
        """测试 Page 提前关闭时退出。"""
        # 模拟 Page（第 2 次检查时关闭）
        page = MagicMock()
        call_count = 0

        def is_closed_side_effect():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        # is_closed 是同步方法
        page.is_closed = MagicMock(side_effect=is_closed_side_effect)

        # 调用暂停/恢复（应该提前退出）
        await simulator._simulate_pause_resume(page, 5.0)

        # 验证：无异常


class TestCookiesToNetscape:
    """测试 Cookies 转换功能。"""

    def test_convert_valid_cookies(self, simulator):
        """测试转换有效的 cookies。"""
        cookies = [
            {
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "expires": 1735689600,  # 2025-01-01
                "name": "CONSENT",
                "value": "YES+cb"
            },
            {
                "domain": ".google.com",
                "path": "/",
                "secure": False,
                "expires": -1,
                "name": "NID",
                "value": "test_value"
            }
        ]

        result = simulator._cookies_to_netscape(cookies)

        # 验证：包含 header
        assert "# Netscape HTTP Cookie File" in result

        # 验证：包含 YouTube cookie
        assert "CONSENT" in result
        assert "YES+cb" in result

        # 验证：包含 Google cookie
        assert "NID" in result
        assert "test_value" in result

    def test_filter_non_youtube_cookies(self, simulator):
        """测试过滤非 YouTube/Google cookies。"""
        cookies = [
            {
                "domain": ".youtube.com",
                "path": "/",
                "secure": True,
                "expires": 0,
                "name": "test1",
                "value": "value1"
            },
            {
                "domain": ".example.com",  # 应该被过滤
                "path": "/",
                "secure": False,
                "expires": 0,
                "name": "test2",
                "value": "value2"
            }
        ]

        result = simulator._cookies_to_netscape(cookies)

        # 验证：包含 YouTube cookie
        assert "test1" in result

        # 验证：不包含非 YouTube cookie
        assert "test2" not in result


class TestBackgroundHumanBehavior:
    """测试后台人类行为模拟（集成测试）。"""

    @pytest.mark.asyncio
    async def test_background_behavior_page_already_closed(self, simulator, tmp_path):
        """测试 Page 已关闭时立即退出。"""
        # 模拟已关闭的 Page
        page = MagicMock()
        # is_closed 是同步方法
        page.is_closed = MagicMock(return_value=True)

        # 设置临时目录
        simulator.settings.data_dir = tmp_path

        # 调用后台任务（应该立即返回）
        import time
        start = time.time()
        await simulator.background_human_behavior(
            page, "https://www.youtube.com/watch?v=test", "test_video", "task_123"
        )
        elapsed = time.time() - start

        # 验证：立即返回（< 0.5 秒）
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_background_behavior_page_closed_during_execution(self, simulator, tmp_path):
        """测试执行期间 Page 被关闭。"""
        # 模拟 Page（2 秒后关闭）
        page = AsyncMock()
        call_count = 0

        def is_closed_side_effect():
            nonlocal call_count
            call_count += 1
            # 第 3 次检查时返回 True（约 1-2 秒后）
            return call_count > 3

        # is_closed 是同步方法
        page.is_closed = MagicMock(side_effect=is_closed_side_effect)
        page.evaluate = AsyncMock()

        # 设置临时目录
        simulator.settings.data_dir = tmp_path

        # 创建临时 cookie 文件
        cookie_file = tmp_path / "tmp" / "task_123.cookies.txt"
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text("test cookies", encoding="utf-8")

        # 调用后台任务（应该检测到关闭并退出）
        import time
        start = time.time()
        await simulator.background_human_behavior(
            page, "https://www.youtube.com/watch?v=test", "test_video", "task_123"
        )
        elapsed = time.time() - start

        # 验证：提前退出（< 10 秒，正常完整流程需要 15-30 秒）
        assert elapsed < 10.0

        # 验证：cookie 文件被清理
        assert not cookie_file.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
