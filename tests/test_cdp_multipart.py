"""
CDP 分片多线程下载测试脚本。

专门测试 curl_cffi 分片下载功能。

测试前准备：
1. 启动 Chrome with CDP：
   Windows:
   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\temp\\chrome-cdp

   Mac:
   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp

2. 设置环境变量：
   set CDP_ENABLED=true
   set CDP_URLS=http://127.0.0.1:9222
   set CDP_ENABLE_MULTIPART=true

3. 运行测试：
   python tests/test_cdp_multipart.py
"""

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


async def test_cdp_multipart_download():
    """测试 CDP 分片下载器。"""
    # 测试视频 URL
    test_url = os.getenv("TEST_VIDEO_URL", "https://www.youtube.com/watch?v=m5YF89Kym1Y")
    video_id = test_url.split("v=")[-1]

    logger.info("="*60)
    logger.info("CDP Multipart Download Test")
    logger.info("="*60)
    logger.info(f"Test URL: {test_url}")
    logger.info(f"Video ID: {video_id}")

    # 加载配置
    settings = get_settings()

    # 临时修改配置（强制启用分片下载）
    settings.cdp_enabled = True
    settings.cdp_use_curl_cffi = True
    settings.cdp_enable_multipart = True  # 启用分片下载
    settings.cdp_multipart_chunks = 6
    settings.cdp_multipart_min_size = 1 * 1024 * 1024  # 降低阈值到 1MB，确保测试能触发

    if not settings.cdp_urls:
        settings.cdp_urls = "http://127.0.0.1:9222"

    logger.info(f"CDP URLs: {settings.cdp_url_list}")
    logger.info(f"CDP Enabled: {settings.cdp_enabled}")
    logger.info(f"CDP Multipart Enabled: {settings.cdp_enable_multipart}")
    logger.info(f"CDP Multipart Chunks: {settings.cdp_multipart_chunks}")
    logger.info(f"CDP Multipart Min Size: {settings.cdp_multipart_min_size / 1024 / 1024:.1f}MB")

    # 创建输出目录
    output_dir = project_root / "data" / "tmp" / "test_cdp_multipart"
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
        logger.error("4. curl_cffi is installed: pip install curl-cffi")
        return

    logger.info("CDP downloader is available")

    # 执行下载
    start_time = time.time()
    try:
        logger.info("Starting multipart download test...")
        logger.info("-"*60)

        result = await downloader.download_resources(
            video_url=test_url,
            video_id=video_id,
            output_dir=output_dir,
            include_audio=True,
            include_transcript=False,
        )

        elapsed = time.time() - start_time

        logger.info("="*60)
        logger.info("Download Result:")
        logger.info("="*60)
        logger.info(f"Success: {result.success}")
        logger.info(f"Downloader: {result.downloader}")
        logger.info(f"Title: {result.video_metadata.title}")
        logger.info(f"Audio Path: {result.audio_path}")
        logger.info(f"Elapsed Time: {elapsed:.2f}s")

        if result.audio_path and result.audio_path.exists():
            size_mb = result.audio_path.stat().st_size / 1024 / 1024
            speed_mbps = size_mb / elapsed
            logger.info(f"Audio Size: {size_mb:.2f} MB")
            logger.info(f"Download Speed: {speed_mbps:.2f} MB/s")
            logger.info("="*60)
            logger.info("✓ Multipart download test PASSED!")
            logger.info("="*60)
        else:
            logger.error("✗ Audio file not found!")

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error("="*60)
        logger.error(f"Download failed after {elapsed:.2f}s: {e}")
        logger.error("="*60)
        logger.error("", exc_info=True)


def main():
    """主函数。"""
    try:
        asyncio.run(test_cdp_multipart_download())
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
