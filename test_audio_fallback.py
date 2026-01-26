"""
测试 audio_fallback 逻辑的脚本。

测试场景：
1. 用户请求仅字幕模式（include_audio=False, include_transcript=True）
2. 视频没有字幕
3. 系统应该自动下载音频作为 fallback
"""

import asyncio
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import Settings
from core.worker import Worker
from db.database import Database
from db.models import Task, TaskPriority, TaskStatus
from services.file_service import FileService
from services.notify import NotifyService
from services.task_service import TaskService
from utils.logger import logger


async def test_audio_fallback():
    """测试 audio_fallback 逻辑。"""
    # 初始化设置
    settings = Settings()

    # 初始化数据库
    db = Database(settings)
    await db.initialize()

    # 初始化服务
    file_service = FileService(settings, db)
    task_service = TaskService(db, file_service, settings)
    notify_service = NotifyService(settings, db, task_service)

    # 初始化 Worker
    worker = Worker(settings, db, task_service, file_service, notify_service)

    # 测试视频：选择一个没有字幕的视频
    # 注意：这里需要一个真实的没有字幕的 YouTube 视频
    test_video_url = "https://www.youtube.com/watch?v=g2Xkh2VMMp8"

    logger.info("=" * 60)
    logger.info("Testing audio_fallback logic")
    logger.info("=" * 60)

    # 创建任务（仅字幕模式）
    from db.models import VideoRequest
    request = VideoRequest(
        video_url=test_video_url,
        include_audio=False,  # 不要音频
        include_transcript=True,  # 只要字幕
        priority=TaskPriority.NORMAL,
    )

    logger.info(f"Creating task: include_audio=False, include_transcript=True")

    # 创建任务
    task = await task_service.create_task(request)
    logger.info(f"Task created: {task.id}")

    # 执行任务
    logger.info("Executing task...")
    try:
        result = await worker._execute_task(task)
        logger.info(f"Task execution completed: {result}")

        # 检查结果
        audio_fallback = result.get("audio_fallback", False)
        audio_file_id = result.get("audio_file_id")
        transcript_file_id = result.get("transcript_file_id")

        logger.info("=" * 60)
        logger.info("Test Results:")
        logger.info(f"  audio_fallback: {audio_fallback}")
        logger.info(f"  audio_file_id: {audio_file_id}")
        logger.info(f"  transcript_file_id: {transcript_file_id}")
        logger.info("=" * 60)

        # 验证结果
        if audio_fallback:
            logger.info("✅ audio_fallback logic works correctly!")
            logger.info("   System downloaded audio when transcript was not available")
        else:
            if transcript_file_id:
                logger.info("ℹ️  Video has transcript, no fallback needed")
            else:
                logger.error("❌ audio_fallback logic failed!")
                logger.error("   No transcript and no audio fallback")

    except Exception as e:
        logger.error(f"Task execution failed: {e}", exc_info=True)

    finally:
        # 清理
        await db.close()


if __name__ == "__main__":
    asyncio.run(test_audio_fallback())
