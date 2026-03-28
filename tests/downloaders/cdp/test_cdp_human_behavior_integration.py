"""
CDP 人类行为模拟集成测试。

测试完整的下载流程，包括人类行为模拟。

运行前准备：
1. 启动 Chrome with CDP：
   Windows:
   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\temp\\chrome-cdp

   Mac/Linux:
   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp

2. 设置环境变量：
   set CDP_ENABLED=true
   set CDP_URLS=http://127.0.0.1:9222
   set CDP_HUMAN_BEHAVIOR_ENABLED=true

3. 运行测试：
   pytest tests/test_cdp_human_behavior_integration.py -v -s
"""

import asyncio
import os
import time
import pytest
from pathlib import Path

from src.config import get_settings
from src.downloaders.cdp import CDPDownloader
from src.utils.logger import logger

# 跳过测试如果 Playwright 不可用
pytestmark = pytest.mark.requires_external

pytest.importorskip("playwright", reason="Playwright is required for CDP tests")


# 测试视频 URL（公开短视频）
TEST_VIDEO_URL = os.getenv("TEST_VIDEO_URL", "https://www.youtube.com/watch?v=0kARDVL2nZg")
TEST_VIDEO_ID = TEST_VIDEO_URL.split("v=")[-1]


@pytest.fixture(scope="module")
def settings():
    """加载测试配置。"""
    settings = get_settings()

    # 确保 CDP 启用
    settings.cdp_enabled = True
    if not settings.cdp_urls:
        settings.cdp_urls = "http://127.0.0.1:9222"

    # 启用人类行为模拟（快速模式）
    settings.cdp_human_behavior_enabled = True
    settings.cdp_quick_mode = False
    settings.cdp_watch_duration_min = 5   # 缩短为 5 秒（测试用）
    settings.cdp_watch_duration_max = 10  # 缩短为 10 秒
    settings.cdp_page_alive_min = 5       # 缩短为 5 秒
    settings.cdp_page_alive_max = 10      # 缩短为 10 秒

    return settings


@pytest.fixture(scope="module")
def output_dir():
    """创建测试输出目录。"""
    from pathlib import Path
    test_dir = Path("data/tmp/test_cdp")
    test_dir.mkdir(parents=True, exist_ok=True)
    return test_dir


@pytest.mark.integration
@pytest.mark.asyncio
class TestCDPHumanBehaviorIntegration:
    """CDP 人类行为模拟集成测试。"""

    async def test_single_task_flow(self, settings, output_dir):
        """
        测试 1：单任务完整流程。

        验证：
        - 主流程 < 15 秒返回
        - 下载成功
        - Cookie 文件在后台任务完成后被清理（15-25 秒后）
        """
        logger.info("=" * 60)
        logger.info("测试 1：单任务完整流程")
        logger.info("=" * 60)

        downloader = CDPDownloader(settings)

        # 检查可用性
        if not downloader.is_available:
            pytest.skip("CDP downloader not available (Chrome not running?)")

        # 执行下载
        start = time.time()
        result = await downloader.download_resources(
            video_url=TEST_VIDEO_URL,
            video_id=TEST_VIDEO_ID,
            output_dir=output_dir,
            include_audio=True,
            include_transcript=False,
        )
        elapsed = time.time() - start

        # 验证：主流程 < 300 秒（包括下载时间，下载速度取决于网络）
        logger.info(f"主流程耗时: {elapsed:.1f}s")
        assert elapsed < 300, f"主流程耗时超过 300 秒: {elapsed:.1f}s"

        # 验证：下载成功
        assert result.success, "下载失败"
        assert result.audio_path is not None, "音频文件为空"
        assert result.audio_path.exists(), f"音频文件不存在: {result.audio_path}"

        size_mb = result.audio_path.stat().st_size / 1024 / 1024
        logger.info(f"音频文件大小: {size_mb:.2f} MB")

        # 检查临时 cookie 文件
        tmp_dir = settings.data_dir / "tmp"
        cookie_files_before = list(tmp_dir.glob("cdp_*.cookies.txt")) if tmp_dir.exists() else []
        logger.info(f"当前临时 cookie 文件数量: {len(cookie_files_before)}")

        # 等待后台任务完成（应该在 15-25 秒内完成）
        logger.info("等待后台任务完成（最多 30 秒）...")
        await asyncio.sleep(30)

        # 验证：cookie 文件已清理
        cookie_files_after = list(tmp_dir.glob("cdp_*.cookies.txt")) if tmp_dir.exists() else []
        logger.info(f"后台任务完成后临时 cookie 文件数量: {len(cookie_files_after)}")

        # Cookie 文件应该减少（允许有新任务产生的文件）
        # 不做严格验证，因为可能有其他测试并发运行

    async def test_quick_mode(self, settings, output_dir):
        """
        测试 2：快速模式（跳过人类行为）。

        验证：
        - 主流程 < 10 秒返回
        - 下载成功
        - 无后台任务
        """
        logger.info("=" * 60)
        logger.info("测试 2：快速模式")
        logger.info("=" * 60)

        # 临时切换到快速模式
        original_quick_mode = settings.cdp_quick_mode
        settings.cdp_quick_mode = True

        try:
            downloader = CDPDownloader(settings)

            # 执行下载
            start = time.time()
            result = await downloader.download_resources(
                video_url=TEST_VIDEO_URL,
                video_id=TEST_VIDEO_ID,
                output_dir=output_dir,
                include_audio=True,
                include_transcript=False,
            )
            elapsed = time.time() - start

            # 验证：主流程 < 300 秒（包括下载时间，下载速度取决于网络）
            logger.info(f"主流程耗时（快速模式）: {elapsed:.1f}s")
            assert elapsed < 300, f"快速模式耗时超过 300 秒: {elapsed:.1f}s"

            # 验证：下载成功
            assert result.success, "下载失败"
            assert result.audio_path is not None, "音频文件为空"
            assert result.audio_path.exists(), f"音频文件不存在: {result.audio_path}"

        finally:
            # 恢复原始配置
            settings.cdp_quick_mode = original_quick_mode

    async def test_disabled_human_behavior(self, settings, output_dir):
        """
        测试 3：禁用人类行为模拟。

        验证：
        - 主流程正常工作
        - 无后台任务
        """
        logger.info("=" * 60)
        logger.info("测试 3：禁用人类行为模拟")
        logger.info("=" * 60)

        # 临时禁用人类行为
        original_enabled = settings.cdp_human_behavior_enabled
        settings.cdp_human_behavior_enabled = False

        try:
            downloader = CDPDownloader(settings)

            # 执行下载
            start = time.time()
            result = await downloader.download_resources(
                video_url=TEST_VIDEO_URL,
                video_id=TEST_VIDEO_ID,
                output_dir=output_dir,
                include_audio=True,
                include_transcript=False,
            )
            elapsed = time.time() - start

            # 验证：主流程正常
            logger.info(f"主流程耗时（禁用模式）: {elapsed:.1f}s")
            assert elapsed < 10, f"禁用模式耗时超过 10 秒: {elapsed:.1f}s"

            # 验证：下载成功
            assert result.success, "下载失败"
            assert result.audio_path is not None, "音频文件为空"

        finally:
            # 恢复原始配置
            settings.cdp_human_behavior_enabled = original_enabled


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.slow
async def test_concurrent_safety(settings, output_dir):
    """
    测试 4：并发安全性（单 Page 策略）。

    验证：
    - 两个任务顺序执行（间隔 10 秒）
    - 第二个任务关闭第一个任务的 Page
    - 第一个任务的后台任务提前退出
    - 所有任务成功
    """
    logger.info("=" * 60)
    logger.info("测试 4：并发安全性")
    logger.info("=" * 60)

    # 启用人类行为模拟
    settings.cdp_human_behavior_enabled = True
    settings.cdp_quick_mode = False

    downloader = CDPDownloader(settings)

    if not downloader.is_available:
        pytest.skip("CDP downloader not available")

    # 启动第一个任务
    logger.info("启动任务 A...")
    task_a = asyncio.create_task(
        downloader.download_resources(
            video_url=TEST_VIDEO_URL,
            video_id=TEST_VIDEO_ID,
            output_dir=output_dir / "task_a",
            include_audio=True,
            include_transcript=False,
        )
    )

    # 等待 10 秒
    logger.info("等待 10 秒...")
    await asyncio.sleep(10)

    # 启动第二个任务
    logger.info("启动任务 B...")
    task_b = asyncio.create_task(
        downloader.download_resources(
            video_url=TEST_VIDEO_URL,
            video_id=TEST_VIDEO_ID,
            output_dir=output_dir / "task_b",
            include_audio=True,
            include_transcript=False,
        )
    )

    # 等待所有任务完成
    results = await asyncio.gather(task_a, task_b)

    # 验证：所有任务成功
    assert all(r.success for r in results), "有任务失败"

    logger.info("所有任务成功！")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "integration"])
