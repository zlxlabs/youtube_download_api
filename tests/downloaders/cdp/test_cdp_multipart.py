"""
CDP 分片多线程下载测试脚本。

专门测试 curl_cffi 分片下载功能（优化后的动态分片 + 并发控制版本）。

测试前准备：
1. 启动 Chrome with CDP：
   Windows:
   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir=C:\\temp\\chrome-cdp

   Mac:
   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-cdp

2. 运行测试：
   uv run python tests/downloaders/cdp/test_cdp_multipart.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.downloaders.cdp import CDPDownloader
from src.utils.logger import logger


async def test_cdp_multipart_download():
    """测试 CDP 分片下载器（优化版：动态分片 + 并发控制）。"""
    # 测试视频 URL
    test_url = os.getenv(
        "TEST_VIDEO_URL",
        "https://www.youtube.com/watch?v=jUO6Mcp9KYw"
    )
    video_id = test_url.split("v=")[-1].split("&")[0]

    logger.info("=" * 60)
    logger.info("CDP Multipart Download Test (Optimized Version)")
    logger.info("=" * 60)
    logger.info(f"Test URL: {test_url}")
    logger.info(f"Video ID: {video_id}")

    # 加载配置
    settings = get_settings()

    # 临时修改配置（强制启用分片下载）
    settings.cdp_enabled = True
    settings.cdp_use_curl_cffi = True
    settings.cdp_enable_multipart = True  # 启用分片下载
    settings.cdp_multipart_chunks = 6  # 最大并发数
    settings.cdp_multipart_min_size = 1 * 1024 * 1024  # 降低阈值到 1MB

    # 使用指定的 CDP URL
    cdp_url = os.getenv("CDP_URLS", "http://192.168.31.222:9223")
    settings.cdp_urls = cdp_url

    logger.info("-" * 60)
    logger.info("Configuration:")
    logger.info(f"  CDP URLs: {settings.cdp_url_list}")
    logger.info(f"  Multipart Enabled: {settings.cdp_enable_multipart}")
    logger.info(f"  Max Concurrent: {settings.cdp_multipart_chunks}")
    logger.info(f"  Min Size Threshold: {settings.cdp_multipart_min_size / 1024 / 1024:.1f}MB")
    logger.info(f"  Chunk Size Range: 2-8MB (dynamic)")
    logger.info("-" * 60)

    # 创建输出目录
    output_dir = project_root / "data" / "tmp" / "test_cdp_multipart"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 清理旧文件
    for f in output_dir.glob("*"):
        if f.is_file():
            f.unlink()

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
        return False

    logger.info("CDP downloader is available")

    # 执行下载
    start_time = time.time()
    try:
        logger.info("")
        logger.info("Starting multipart download test...")
        logger.info("(Watch for progress logs with chunk completion status)")
        logger.info("-" * 60)

        result = await downloader.download_resources(
            video_url=test_url,
            video_id=video_id,
            output_dir=output_dir,
            include_audio=True,
            include_transcript=False,
        )

        elapsed = time.time() - start_time

        logger.info("")
        logger.info("=" * 60)
        logger.info("Download Result:")
        logger.info("=" * 60)
        logger.info(f"Success: {result.success}")
        logger.info(f"Downloader: {result.downloader}")
        logger.info(f"Title: {result.video_metadata.title if result.video_metadata else 'N/A'}")
        logger.info(f"Audio Path: {result.audio_path}")
        logger.info(f"Elapsed Time: {elapsed:.2f}s")

        if result.audio_path and result.audio_path.exists():
            size_mb = result.audio_path.stat().st_size / 1024 / 1024
            speed_mbps = size_mb / elapsed
            logger.info(f"Audio Size: {size_mb:.2f} MB")
            logger.info(f"Download Speed: {speed_mbps:.2f} MB/s")
            logger.info("=" * 60)
            logger.info("[PASSED] Multipart download test completed successfully!")
            logger.info("=" * 60)
            return True
        else:
            logger.error("[FAILED] Audio file not found!")
            return False

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error("=" * 60)
        logger.error(f"Download failed after {elapsed:.2f}s: {e}")
        logger.error("=" * 60)
        import traceback
        traceback.print_exc()
        return False


def main():
    """主函数。"""
    try:
        success = asyncio.run(test_cdp_multipart_download())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
