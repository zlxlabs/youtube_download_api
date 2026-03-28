import io
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import UploadFile

from src.config import Settings
from src.db.database import Database
from src.db.models import VideoInfo
from src.services.file_service import FileService
from src.services.manual_upload_service import ManualUploadError, ManualUploadService
from src.services.transcode_service import TranscodeError


class DummyTranscodeService:
    SUPPORTED_VIDEO_FORMATS = {".mp4"}
    SUPPORTED_AUDIO_FORMATS = {".m4a"}

    def is_supported_format(self, file_path: Path) -> bool:
        return True

    async def transcode_to_m4a(self, input_file: Path, output_dir: Path, target_bitrate: int = 128):
        output_path = output_dir / f"{input_file.stem}.m4a"
        shutil.copy(input_file, output_path)
        return output_path


class FailingTranscodeService(DummyTranscodeService):
    async def transcode_to_m4a(self, input_file: Path, output_dir: Path, target_bitrate: int = 128):
        raise TranscodeError("Transcode failed")


@pytest.mark.asyncio
async def test_manual_upload_success(tmp_path: Path):
    settings = Settings(
        api_key="test",
        wecom_webhook_url="",
        data_dir=tmp_path,
        manual_upload_enabled=True,
        manual_upload_allowed_video_formats=".mp4",
        manual_upload_allowed_audio_formats=".m4a",
    )
    settings.ensure_directories()

    db = Database(settings.db_path)
    await db.connect()

    file_service = FileService(db, settings)
    transcode_service = DummyTranscodeService()

    # Mock DownloaderManager, get_metadata 返回测试元数据
    downloader_manager = MagicMock()
    downloader_manager.get_metadata = AsyncMock(return_value={
        "title": "Test Title",
        "author": "Test Author",
        "duration": 123,
    })

    service = ManualUploadService(
        db=db,
        file_service=file_service,
        transcode_service=transcode_service,
        downloader_manager=downloader_manager,
        settings=settings,
    )

    content = b"fake-data"
    upload = UploadFile(filename="test.mp4", file=io.BytesIO(content))
    upload.size = len(content)

    result = await service.handle_upload(
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        uploaded_file=upload,
        manual_metadata=None,
    )

    file_record = result["audio_file"]
    assert file_record.upload_source == "manual"
    assert file_record.original_format == "mp4"

    list_result = await service.list_manual_uploads(limit=10, offset=0)
    assert list_result["total"] == 1
    assert list_result["uploads"][0]["video_id"] == "dQw4w9WgXcQ"

    await db.disconnect()


@pytest.mark.asyncio
async def test_manual_upload_metadata_cached_on_failure(tmp_path: Path):
    settings = Settings(
        api_key="test",
        wecom_webhook_url="",
        data_dir=tmp_path,
        manual_upload_enabled=True,
        manual_upload_allowed_video_formats=".mp4",
        manual_upload_allowed_audio_formats=".m4a",
    )
    settings.ensure_directories()

    db = Database(settings.db_path)
    await db.connect()

    file_service = FileService(db, settings)
    transcode_service = FailingTranscodeService()

    # Mock DownloaderManager, get_metadata 返回测试元数据
    downloader_manager = MagicMock()
    downloader_manager.get_metadata = AsyncMock(return_value={
        "title": "Test Title",
        "author": "Test Author",
        "duration": 123,
    })

    service = ManualUploadService(
        db=db,
        file_service=file_service,
        transcode_service=transcode_service,
        downloader_manager=downloader_manager,
        settings=settings,
    )

    content = b"fake-data"
    upload = UploadFile(filename="test.mp4", file=io.BytesIO(content))
    upload.size = len(content)

    with pytest.raises(ManualUploadError):
        await service.handle_upload(
            video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            uploaded_file=upload,
            manual_metadata=None,
        )

    video_resource = await db.get_video_resource("dQw4w9WgXcQ")
    assert video_resource is not None
    assert video_resource.video_info is not None
    assert video_resource.video_info.title == "Test Title"

    await db.disconnect()
