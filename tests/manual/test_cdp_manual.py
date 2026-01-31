"""
CDP 下载器手动测试脚本。

测试前准备：
1. 启动 Chrome with CDP：
   Windows:
   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\temp\\chrome-cdp

   Mac:
   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp

2. 设置环境变量：
   set CDP_ENABLED=true
   set CDP_URLS=http://127.0.0.1:9222

3. 运行测试：
   python tests/test_cdp_manual.py
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.downloaders.cdp import CDPDownloader
from src.utils.logger import logger


async def test_cdp_download():
    """测试 CDP 下载器。"""
    # 测试视频 URL
    test_url = os.getenv("TEST_VIDEO_URL", "https://www.youtube.com/watch?v=0kARDVL2nZg")
    video_id = test_url.split("v=")[-1]

    logger.info("="*60)
    logger.info("CDP Downloader Manual Test")
    logger.info("="*60)
    logger.info(f"Test URL: {test_url}")
    logger.info(f"Video ID: {video_id}")

    # 加载配置
    settings = get_settings()

    # 临时修改配置（用于测试）
    settings.cdp_enabled = True
    if not settings.cdp_urls:
        settings.cdp_urls = "http://127.0.0.1:9222"

    logger.info(f"CDP URLs: {settings.cdp_url_list}")
    logger.info(f"CDP Enabled: {settings.cdp_enabled}")

    # 创建输出目录
    output_dir = project_root / "data" / "tmp" / "test_cdp"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Output dir: {output_dir}")

    # 创建下载器
    downloader = CDPDownloader(settings)

    # 检查是否可用
    if not downloader.is_available:
        logger.error("CDP downloader is not available!")
        logger.error("Please check:")
        logger.error("1. Chrome is running with CDP enabled")
        logger.error("2. CDP_ENABLED=true")
        logger.error("3. Playwright is installed: pip install playwright")
        return

    logger.info("CDP downloader is available")

    # 执行下载
    try:
        logger.info("Starting download...")
        result = await downloader.download_resources(
            video_url=test_url,
            video_id=video_id,
            output_dir=output_dir,
            include_audio=True,
            include_transcript=False,
        )

        logger.info("="*60)
        logger.info("Download Result:")
        logger.info("="*60)
        logger.info(f"Success: {result.success}")
        logger.info(f"Downloader: {result.downloader}")
        logger.info(f"Title: {result.video_metadata.title}")
        logger.info(f"Audio Path: {result.audio_path}")

        if result.audio_path and result.audio_path.exists():
            size_mb = result.audio_path.stat().st_size / 1024 / 1024
            logger.info(f"Audio Size: {size_mb:.2f} MB")
            logger.info("✓ Download successful!")
        else:
            logger.error("✗ Audio file not found!")

    except Exception as e:
        logger.error(f"Download failed: {e}", exc_info=True)


def main():
    """主函数。"""
    try:
        asyncio.run(test_cdp_download())
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
