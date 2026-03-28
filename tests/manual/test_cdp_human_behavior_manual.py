"""
CDP 人类行为模拟手动测试脚本。

用于观察 Chrome 浏览器的实际行为。

测试前准备：
1. 启动 Chrome with CDP（保持窗口可见）：
   Windows:
   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\temp\\chrome-cdp --no-first-run --no-default-browser-check

   Mac:
   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp --no-first-run --no-default-browser-check

   Linux:
   google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp --no-first-run --no-default-browser-check

2. 设置环境变量：
   set CDP_ENABLED=true
   set CDP_URLS=http://127.0.0.1:9222
   set CDP_HUMAN_BEHAVIOR_ENABLED=true

3. 运行测试：
   python tests/test_cdp_human_behavior_manual.py

观察要点：
- 视频页面是否打开
- 视频是否开始播放（静音）
- 页面是否滚动
- 视频是否持续播放 20-40 秒（可在配置中调整）
- 页面是否保持存活 30-60 秒
- 页面是否最终关闭
- 如果启动第二个任务，旧标签页是否被关闭
"""

import pytest
pytestmark = pytest.mark.manual


import asyncio
import os
import sys
import time
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.downloaders.cdp import CDPDownloader
from src.utils.logger import logger


async def test_single_task():
    """测试单任务（观察完整的人类行为）。"""
    logger.info("=" * 80)
    logger.info("手动测试 1：单任务 - 观察人类行为模拟")
    logger.info("=" * 80)

    # 测试视频
    test_url = os.getenv("TEST_VIDEO_URL", "https://www.youtube.com/watch?v=0kARDVL2nZg")
    video_id = test_url.split("v=")[-1]

    logger.info(f"测试视频: {test_url}")
    logger.info(f"视频 ID: {video_id}")

    # 加载配置
    settings = get_settings()
    settings.cdp_enabled = True
    if not settings.cdp_urls:
        settings.cdp_urls = "http://127.0.0.1:9222"

    # 启用人类行为模拟（真实模式）
    settings.cdp_human_behavior_enabled = True
    settings.cdp_quick_mode = False
    settings.cdp_watch_duration_min = 20
    settings.cdp_watch_duration_max = 40
    settings.cdp_page_alive_min = 30
    settings.cdp_page_alive_max = 60

    logger.info("")
    logger.info("配置信息:")
    logger.info(f"  CDP URLs: {settings.cdp_url_list}")
    logger.info(f"  人类行为模拟: {settings.cdp_human_behavior_enabled}")
    logger.info(f"  快速模式: {settings.cdp_quick_mode}")
    logger.info(f"  观看时长: {settings.cdp_watch_duration_min}-{settings.cdp_watch_duration_max} 秒")
    logger.info(f"  页面存活: {settings.cdp_page_alive_min}-{settings.cdp_page_alive_max} 秒")

    # 创建输出目录
    output_dir = project_root / "data" / "tmp" / "test_cdp_human_behavior"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 创建下载器
    downloader = CDPDownloader(settings)

    if not downloader.is_available:
        logger.error("CDP 下载器不可用！")
        logger.error("请检查：")
        logger.error("1. Chrome 是否已启动并开启 CDP（端口 9222）")
        logger.error("2. 环境变量 CDP_ENABLED=true")
        logger.error("3. Playwright 是否已安装: pip install playwright")
        return

    logger.info("")
    logger.info("=" * 80)
    logger.info("开始下载...")
    logger.info("=" * 80)
    logger.info("")
    logger.info("请观察 Chrome 窗口的行为：")
    logger.info("  1. 视频页面是否打开")
    logger.info("  2. 视频是否开始播放（静音）")
    logger.info("  3. 页面是否滚动")
    logger.info("  4. 视频是否持续播放 20-40 秒")
    logger.info("  5. 页面是否保持存活 30-60 秒")
    logger.info("  6. 页面是否最终关闭")
    logger.info("")

    # 执行下载
    try:
        start = time.time()
        result = await downloader.download_resources(
            video_url=test_url,
            video_id=video_id,
            output_dir=output_dir,
            include_audio=True,
            include_transcript=False,
        )
        main_elapsed = time.time() - start

        logger.info("")
        logger.info("=" * 80)
        logger.info("主流程完成")
        logger.info("=" * 80)
        logger.info(f"主流程耗时: {main_elapsed:.1f} 秒")
        logger.info(f"下载成功: {result.success}")
        logger.info(f"下载器: {result.downloader}")

        if result.success:
            logger.info(f"标题: {result.video_metadata.title}")
            logger.info(f"音频文件: {result.audio_path}")

            if result.audio_path and result.audio_path.exists():
                size_mb = result.audio_path.stat().st_size / 1024 / 1024
                logger.info(f"文件大小: {size_mb:.2f} MB")

        logger.info("")
        logger.info("=" * 80)
        logger.info("后台任务正在运行...")
        logger.info("=" * 80)
        logger.info("")
        logger.info("后台任务将在 50-100 秒内完成，请继续观察 Chrome 窗口")
        logger.info("（预期行为：视频继续播放，页面保持打开，最终自动关闭）")

        # 等待后台任务完成
        logger.info("")
        logger.info("等待后台任务完成（最多 120 秒）...")
        await asyncio.sleep(120)

        logger.info("")
        logger.info("=" * 80)
        logger.info("测试完成")
        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"下载失败: {e}", exc_info=True)


async def test_concurrent_tasks():
    """测试并发任务（观察单 Page 策略）。"""
    logger.info("=" * 80)
    logger.info("手动测试 2：并发任务 - 观察单 Page 策略")
    logger.info("=" * 80)

    # 测试视频
    test_url = os.getenv("TEST_VIDEO_URL", "https://www.youtube.com/watch?v=0kARDVL2nZg")
    video_id = test_url.split("v=")[-1]

    # 加载配置
    settings = get_settings()
    settings.cdp_enabled = True
    if not settings.cdp_urls:
        settings.cdp_urls = "http://127.0.0.1:9222"

    # 启用人类行为模拟
    settings.cdp_human_behavior_enabled = True
    settings.cdp_quick_mode = False
    settings.cdp_watch_duration_min = 20
    settings.cdp_watch_duration_max = 40
    settings.cdp_page_alive_min = 30
    settings.cdp_page_alive_max = 60

    logger.info("")
    logger.info("观察要点：")
    logger.info("  1. 任务 A 启动，打开视频页面")
    logger.info("  2. 等待 10 秒")
    logger.info("  3. 任务 B 启动，关闭任务 A 的页面（模拟人类关闭旧标签页）")
    logger.info("  4. 任务 B 的页面继续播放")
    logger.info("  5. 任何时刻只有一个视频在播放")
    logger.info("")

    # 创建输出目录
    output_dir = project_root / "data" / "tmp" / "test_cdp_concurrent"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 创建下载器
    downloader = CDPDownloader(settings)

    if not downloader.is_available:
        logger.error("CDP 下载器不可用！")
        return

    logger.info("")
    logger.info("=" * 80)
    logger.info("启动任务 A...")
    logger.info("=" * 80)

    # 启动任务 A
    async def task_a():
        try:
            result = await downloader.download_resources(
                video_url=test_url,
                video_id=video_id,
                output_dir=output_dir / "task_a",
                include_audio=True,
                include_transcript=False,
            )
            logger.info(f"[任务 A] 完成: {result.success}")
        except Exception as e:
            logger.error(f"[任务 A] 失败: {e}")

    task_a_coro = asyncio.create_task(task_a())

    # 等待 10 秒
    logger.info("")
    logger.info("等待 10 秒...")
    await asyncio.sleep(10)

    logger.info("")
    logger.info("=" * 80)
    logger.info("启动任务 B...")
    logger.info("=" * 80)
    logger.info("")
    logger.info("观察：任务 A 的页面是否被关闭？")

    # 启动任务 B
    async def task_b():
        try:
            result = await downloader.download_resources(
                video_url=test_url,
                video_id=video_id,
                output_dir=output_dir / "task_b",
                include_audio=True,
                include_transcript=False,
            )
            logger.info(f"[任务 B] 完成: {result.success}")
        except Exception as e:
            logger.error(f"[任务 B] 失败: {e}")

    task_b_coro = asyncio.create_task(task_b())

    # 等待所有任务完成
    logger.info("")
    logger.info("等待所有任务完成（包括后台任务）...")
    await asyncio.gather(task_a_coro, task_b_coro)

    # 等待后台任务
    await asyncio.sleep(120)

    logger.info("")
    logger.info("=" * 80)
    logger.info("测试完成")
    logger.info("=" * 80)


async def main():
    """主函数。"""
    logger.info("")
    logger.info("#" * 80)
    logger.info("CDP 人类行为模拟手动测试")
    logger.info("#" * 80)
    logger.info("")

    # 选择测试
    print("请选择测试：")
    print("1. 单任务测试（观察完整的人类行为）")
    print("2. 并发任务测试（观察单 Page 策略）")
    print("3. 运行所有测试")

    choice = input("\n请输入选项（1/2/3）: ").strip()

    if choice == "1":
        await test_single_task()
    elif choice == "2":
        await test_concurrent_tasks()
    elif choice == "3":
        await test_single_task()
        logger.info("\n\n")
        await test_concurrent_tasks()
    else:
        logger.error("无效的选项")
        return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n测试被用户中断")
    except Exception as e:
        logger.error(f"\n测试失败: {e}", exc_info=True)
        sys.exit(1)
