"""
CDP 下载器字幕下载端到端测试。

使用真实 Chrome CDP 连接测试字幕下载功能。

运行：
    uv run python tests/manual/test_cdp_transcript.py
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from src.config import get_settings
from src.downloaders.cdp import CDPDownloader
from src.utils.logger import logger


async def test_cdp_transcript():
    """测试 CDP 字幕下载。"""
    # 使用一个有英文字幕的短视频
    test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    video_id = "jNQXAC9IVRw"

    logger.info("=" * 60)
    logger.info("CDP Transcript Download Test")
    logger.info("=" * 60)
    logger.info(f"Test URL: {test_url}")
    logger.info(f"Video ID: {video_id}")

    settings = get_settings()
    settings.cdp_enabled = True

    logger.info(f"CDP URLs: {settings.cdp_url_list}")

    output_dir = project_root / "data" / "tmp" / "test_cdp_transcript"
    output_dir.mkdir(parents=True, exist_ok=True)

    downloader = CDPDownloader(settings)

    if not downloader.is_available:
        logger.error("CDP downloader is not available!")
        return

    logger.info("CDP downloader is available, starting transcript download...")

    try:
        result = await downloader.download_resources(
            video_url=test_url,
            video_id=video_id,
            output_dir=output_dir,
            include_audio=False,
            include_transcript=True,
        )

        logger.info("=" * 60)
        logger.info("Download Result:")
        logger.info("=" * 60)
        logger.info(f"Success: {result.success}")
        logger.info(f"Downloader: {result.downloader}")
        logger.info(f"Transcript Path: {result.transcript_path}")

        if result.transcript_path and result.transcript_path.exists():
            size_kb = result.transcript_path.stat().st_size / 1024
            logger.info(f"Transcript Size: {size_kb:.2f} KB")

            # 显示前几行内容
            content = result.transcript_path.read_text(encoding="utf-8")
            preview = content[:500]
            logger.info(f"Transcript preview:\n{preview}")
            logger.info("PASS: Transcript download successful!")
        else:
            logger.error("FAIL: Transcript file not found!")

    except Exception as e:
        logger.error(f"FAIL: Download failed: {e}", exc_info=True)


def main():
    try:
        asyncio.run(test_cdp_transcript())
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
