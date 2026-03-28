"""
CDP 视频播放暂停控制 - 本地测试脚本

测试目标：
1. 验证视频时长获取
2. 验证播放时长计算
3. 验证暂停操作
4. 验证多任务 Page 管理
"""

import pytest
pytestmark = pytest.mark.requires_external


import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from src.config import get_settings
from src.downloaders.cdp.human_behavior import HumanBehaviorSimulator
from src.utils.logger import logger

try:
    from playwright.async_api import async_playwright
except ImportError:
    logger.error("Playwright 未安装，请运行: uv add playwright && uv run playwright install chromium")
    sys.exit(1)


async def test_video_pause_control():
    """测试视频播放暂停控制功能"""

    logger.info("=" * 80)
    logger.info("CDP 视频播放暂停控制 - 本地测试")
    logger.info("=" * 80)

    # 获取配置
    settings = get_settings()

    # 显示配置
    logger.info("\n配置信息:")
    logger.info(f"  CDP_HUMAN_BEHAVIOR_ENABLED: {settings.cdp_human_behavior_enabled}")
    logger.info(f"  CDP_QUICK_MODE: {settings.cdp_quick_mode}")
    logger.info(f"  CDP_MIN_PLAY_DURATION: {settings.cdp_min_play_duration}s")
    logger.info(f"  CDP_MAX_PLAY_DURATION: {settings.cdp_max_play_duration}s")
    logger.info(f"  CDP_PLAY_RATIO_MIN: {settings.cdp_play_ratio_min:.0%}")
    logger.info(f"  CDP_PLAY_RATIO_MAX: {settings.cdp_play_ratio_max:.0%}")
    logger.info(f"  CDP_PAGE_ALIVE_MIN: {settings.cdp_page_alive_min}s")
    logger.info(f"  CDP_PAGE_ALIVE_MAX: {settings.cdp_page_alive_max}s")

    # 测试视频
    test_videos = [
        {
            "url": "https://www.youtube.com/watch?v=zt0JA5rxdfM",
            "id": "zt0JA5rxdfM",
            "name": "视频 1"
        },
        {
            "url": "https://www.youtube.com/watch?v=JOYSDqJdiro",
            "id": "JOYSDqJdiro",
            "name": "视频 2"
        }
    ]

    # 创建人类行为模拟器
    behavior_simulator = HumanBehaviorSimulator(settings)

    # 连接到 CDP
    logger.info("\n连接到 Chrome CDP...")
    cdp_url = settings.cdp_url_list[0]
    logger.info(f"CDP URL: {cdp_url}")

    playwright = None
    browser = None
    context = None

    try:
        # 启动 Playwright
        playwright = await async_playwright().start()

        # 连接到现有的 Chrome 实例
        browser = await playwright.chromium.connect_over_cdp(cdp_url)
        logger.info("✅ 已连接到 Chrome\n")

        # 获取或创建 context
        if browser.contexts:
            context = browser.contexts[0]
            logger.info(f"使用现有 Context (已有 {len(context.pages)} 个 Page)")
        else:
            context = await browser.new_context()
            logger.info("创建新 Context")

        # 测试每个视频
        for i, video in enumerate(test_videos, 1):
            logger.info("\n" + "=" * 80)
            logger.info(f"测试 {i}/{len(test_videos)}: {video['name']} ({video['id']})")
            logger.info("=" * 80)

            try:
                task_id = f"test_{video['id']}_{i}"

                logger.info(f"\n开始测试视频: {video['url']}")
                logger.info(f"任务 ID: {task_id}\n")

                # 清理旧 Page
                logger.info(f"当前 Context 有 {len(context.pages)} 个 Page")
                kept_page = await behavior_simulator.cleanup_old_pages(context, keep_last=True)
                if kept_page:
                    logger.info(f"保留了 1 个旧 Page（避免 Chrome 退出）")

                # 快速获取数据（会获取视频时长）
                logger.info(f"\n📊 获取视频数据...")
                page, cookie_file, headers, video_duration = await behavior_simulator.quick_fetch_data(
                    context, video['url'], video['id'], task_id
                )

                logger.info(f"\n✅ 数据获取完成:")
                logger.info(f"  - Cookies: {cookie_file.name}")
                logger.info(f"  - Headers: {len(headers)} 个")
                if video_duration:
                    logger.info(f"  - 视频时长: {video_duration:.1f}s ({video_duration/60:.1f}min)")
                else:
                    logger.info(f"  - 视频时长: 未获取到")

                # 关闭保留的旧 Page
                if kept_page and not kept_page.is_closed():
                    await kept_page.close()
                    logger.info(f"  - 已关闭旧 Page")

                logger.info(f"\n当前 Context 有 {len(context.pages)} 个 Page")

                # 启动后台人类行为模拟
                logger.info(f"\n🎬 启动后台人类行为模拟...")
                logger.info(f"预期行为: 滚动 → 观看视频 → 暂停（如果是最后一个 Page）→ 等待\n")

                behavior_task = asyncio.create_task(
                    behavior_simulator.background_human_behavior(
                        page, video['url'], video['id'], task_id, video_duration
                    )
                )

                # 等待后台任务完成（或超时）
                if i < len(test_videos):
                    # 不是最后一个视频，等待一小段时间后开始下一个任务
                    wait_time = 45
                    logger.info(f"⏳ 等待 {wait_time} 秒后开始下一个任务（模拟真实场景）...")
                    await asyncio.sleep(wait_time)
                    logger.info(f"\n✅ 等待完成，准备开始下一个任务")
                    logger.info(f"   （当前 Page 应该会被新任务关闭，后台任务应该会自动退出）")
                else:
                    # 最后一个视频，等待后台任务完成
                    logger.info(f"⏳ 这是最后一个任务，等待后台行为模拟完成...")
                    try:
                        await asyncio.wait_for(behavior_task, timeout=200)
                        logger.info(f"\n✅ 后台行为模拟完成")
                    except asyncio.TimeoutError:
                        logger.warning(f"\n⚠️ 后台任务超时（200秒），取消任务")
                        behavior_task.cancel()
                        try:
                            await behavior_task
                        except asyncio.CancelledError:
                            pass

            except Exception as e:
                logger.error(f"❌ 测试视频 {video['name']} 时出错: {e}")
                import traceback
                traceback.print_exc()
                continue

        logger.info("\n" + "=" * 80)
        logger.info("✅ 测试完成")
        logger.info("=" * 80)

        logger.info(f"\n最终状态:")
        logger.info(f"  - Context 有 {len(context.pages)} 个 Page")
        logger.info(f"  - 最后一个 Page 应该保持打开（视频已暂停）")

    finally:
        # 清理
        logger.info("\n清理资源...")
        if context:
            logger.info(f"  - 关闭前有 {len(context.pages)} 个 Page")
        if browser:
            await browser.close()
            logger.info("  - 已断开 Chrome 连接")
        if playwright:
            await playwright.stop()
            logger.info("  - 已停止 Playwright")
        logger.info("清理完成")


async def main():
    """主函数"""
    try:
        await test_video_pause_control()
    except KeyboardInterrupt:
        logger.info("\n\n⚠️ 用户中断测试")
    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
