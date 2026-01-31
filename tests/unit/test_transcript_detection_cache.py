"""
测试字幕探测结果缓存功能

验证即使下载失败，字幕可用性探测结果也会被保存，避免重复 API 调用。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from datetime import datetime

from src.core.worker import DownloadWorker
from src.db.models import Task, TaskStatus, VideoInfo, FileRecord, FileType
from src.downloaders.models import DownloaderResult, VideoMetadata
from src.downloaders.exceptions import DownloaderError, AllDownloadersFailed
from src.db.models import ErrorCode


@pytest.fixture
def mock_dependencies():
    """创建测试所需的 mock 对象"""
    settings = MagicMock()
    settings.audio_quality = 128

    return {
        "db": AsyncMock(),
        "file_service": AsyncMock(),
        "task_service": AsyncMock(),
        "notify_service": AsyncMock(),
        "callback_service": AsyncMock(),
        "settings": settings,
    }


@pytest.fixture
def sample_task():
    """创建示例任务"""
    return Task(
        id="test-task-001",
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_id="dQw4w9WgXcQ",
        include_audio=False,  # 只请求字幕
        include_transcript=True,
        status=TaskStatus.DOWNLOADING,
        created_at=datetime.now(),
    )


@pytest.fixture
def sample_video_metadata():
    """创建示例视频元数据"""
    return VideoMetadata(
        video_id="dQw4w9WgXcQ",
        title="Test Video",
        author="Test Channel",
        channel_id="UC123456789",
        duration=180,
        description="Test description",
        upload_date="20240101",
        view_count=1000,
        thumbnail="https://example.com/thumb.jpg",
    )


@pytest.fixture
def sample_downloader_result_no_transcript(sample_video_metadata):
    """创建探测到无字幕的下载器结果"""
    return DownloaderResult(
        success=True,
        downloader="ytdlp",
        video_metadata=sample_video_metadata,
        audio_path=None,
        transcript_path=None,
        has_transcript=False,  # 关键：视频无字幕
    )


@pytest.fixture
def worker(mock_dependencies):
    """创建 Worker 实例"""
    with patch("src.core.worker.DownloaderManager"), \
         patch("src.core.worker.IPBanCircuitBreaker"):
        worker = DownloadWorker(
            db=mock_dependencies["db"],
            settings=mock_dependencies["settings"],
            task_service=mock_dependencies["task_service"],
            file_service=mock_dependencies["file_service"],
            callback_service=mock_dependencies["callback_service"],
            notify_service=mock_dependencies["notify_service"],
        )
        # 替换为 mock 对象
        worker.downloader_manager = AsyncMock()
        return worker


class TestTranscriptDetectionCache:
    """字幕探测结果缓存测试"""

    @pytest.mark.asyncio
    async def test_save_transcript_detection_on_audio_fallback_failure(
        self,
        worker,
        mock_dependencies,
        sample_task,
        sample_downloader_result_no_transcript,
    ):
        """
        测试场景：
        1. 用户请求只下载字幕
        2. 探测发现视频无字幕
        3. 触发音频降级
        4. 音频下载失败
        5. 验证字幕探测结果（has_transcript=False）被保存
        """
        # 配置 mock：首次下载探测到无字幕
        worker.downloader_manager.download_with_fallback = AsyncMock(
            side_effect=[
                sample_downloader_result_no_transcript,  # 首次下载：探测到无字幕
                DownloaderError(  # 音频降级：下载失败
                    message="Audio download failed",
                    error_code=ErrorCode.DOWNLOAD_FAILED,
                ),
            ]
        )

        # 配置 file_service：无缓存文件
        mock_dependencies["file_service"].get_all_files_for_video.return_value = {
            "audio": None,
            "transcript": None,
        }

        # 执行任务（预期会抛出异常）
        with pytest.raises(DownloaderError):
            await worker._execute_download_with_manager(
                task=sample_task,
                existing_audio=None,
                existing_transcript=None,
                need_audio=False,
                need_transcript=True,
            )

        # 验证：即使下载失败，update_video_resource 也被调用了
        mock_dependencies["db"].update_video_resource.assert_called_once()

        # 提取调用参数
        call_args = mock_dependencies["db"].update_video_resource.call_args
        assert call_args.kwargs["video_id"] == "dQw4w9WgXcQ"
        assert call_args.kwargs["has_native_transcript"] is False  # 关键验证

        # 验证视频元数据也被保存
        saved_video_info = call_args.kwargs["video_info"]
        assert saved_video_info.title == "Test Video"

    @pytest.mark.asyncio
    async def test_save_transcript_detection_on_first_download_failure(
        self,
        worker,
        mock_dependencies,
        sample_task,
    ):
        """
        测试场景：首次下载就失败（没有探测结果）
        验证不会崩溃，也不会调用 update_video_resource
        """
        # 配置 mock：首次下载就失败
        worker.downloader_manager.download_with_fallback = AsyncMock(
            side_effect=DownloaderError(
                message="Network error",
                error_code=ErrorCode.NETWORK_ERROR,
            )
        )

        # 配置 file_service：无缓存文件
        mock_dependencies["file_service"].get_all_files_for_video.return_value = {
            "audio": None,
            "transcript": None,
        }

        # 执行任务（预期会抛出异常）
        with pytest.raises(DownloaderError):
            await worker._execute_download_with_manager(
                task=sample_task,
                existing_audio=None,
                existing_transcript=None,
                need_audio=False,
                need_transcript=True,
            )

        # 验证：没有探测结果，不应调用 update_video_resource
        mock_dependencies["db"].update_video_resource.assert_not_called()

    @pytest.mark.asyncio
    async def test_normal_flow_still_works(
        self,
        worker,
        mock_dependencies,
        sample_task,
        sample_video_metadata,
    ):
        """
        测试场景：正常流程（有字幕且下载成功）
        验证修复不影响正常功能
        """
        # 创建成功的下载结果
        success_result = DownloaderResult(
            success=True,
            downloader="ytdlp",
            video_metadata=sample_video_metadata,
            audio_path=None,
            transcript_path=Path("/tmp/transcript.json"),
            has_transcript=True,
        )

        # 配置 mock
        worker.downloader_manager.download_with_fallback = AsyncMock(
            return_value=success_result
        )
        mock_dependencies["file_service"].get_all_files_for_video.return_value = {
            "audio": None,
            "transcript": None,
        }
        mock_dependencies["file_service"].create_file_record.return_value = FileRecord(
            id="file-001",
            video_id="dQw4w9WgXcQ",
            file_type=FileType.TRANSCRIPT,
            filename="file-001.json",
            filepath="files/file-001.json",
            size=1024,
            created_at=datetime.now(),
        )

        # 模拟文件存在
        with patch("pathlib.Path.exists", return_value=True):
            result = await worker._execute_download_with_manager(
                task=sample_task,
                existing_audio=None,
                existing_transcript=None,
                need_audio=False,
                need_transcript=True,
            )

        # 验证正常流程
        assert result["transcript_file_id"] == "file-001"
        assert result["reused_transcript"] is False

        # 验证 update_video_resource 被调用（正常路径）
        mock_dependencies["db"].update_video_resource.assert_called_once()
        call_args = mock_dependencies["db"].update_video_resource.call_args
        assert call_args.kwargs["has_native_transcript"] is True

    @pytest.mark.asyncio
    async def test_audio_fallback_with_existing_audio(
        self,
        worker,
        mock_dependencies,
        sample_task,
        sample_downloader_result_no_transcript,
    ):
        """
        测试场景：
        1. 用户请求字幕
        2. 探测到无字幕
        3. 触发音频降级
        4. 发现数据库已有音频缓存
        5. 直接使用缓存，不重新下载
        """
        # 配置 mock
        worker.downloader_manager.download_with_fallback = AsyncMock(
            return_value=sample_downloader_result_no_transcript
        )

        # 创建现有音频文件
        existing_audio = FileRecord(
            id="audio-001",
            video_id="dQw4w9WgXcQ",
            file_type=FileType.AUDIO,
            filename="audio-001.m4a",
            filepath="files/audio-001.m4a",
            size=5242880,
            created_at=datetime.now(),
        )

        result = await worker._execute_download_with_manager(
            task=sample_task,
            existing_audio=existing_audio,  # 传入现有音频
            existing_transcript=None,
            need_audio=False,
            need_transcript=True,
        )

        # 验证：只调用了一次下载（没有触发音频重新下载）
        assert worker.downloader_manager.download_with_fallback.call_count == 1

        # 验证：使用了现有音频
        assert result["audio_file_id"] == "audio-001"
        assert result["reused_audio"] is True
        assert result["audio_fallback"] is True

        # 验证：探测结果被保存
        mock_dependencies["db"].update_video_resource.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
