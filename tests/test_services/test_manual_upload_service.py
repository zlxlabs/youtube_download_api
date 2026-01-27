import io
import shutil
from pathlib import Path

import pytest
from fastapi import UploadFile

from src.config import Settings
from src.db.database import Database
from src.db.models import VideoInfo
from src.services.file_service import FileService
from src.services.manual_upload_service import ManualUploadError, ManualUploadService
from src.services.transcode_service import TranscodeError


class DummyMetadataService:
    async def fetch_youtube_metadata(self, video_url: str, video_id: str) -> VideoInfo:
        return VideoInfo(title="Test Title", author="Test Author", duration=123)

    def merge_metadata(self, auto_metadata: VideoInfo, manual_metadata: dict | None) -> VideoInfo:
        result = VideoInfo()
        if auto_metadata:
            result.title = auto_metadata.title
            result.author = auto_metadata.author
            result.channel_id = auto_metadata.channel_id
            result.duration = auto_metadata.duration
            result.description = auto_metadata.description
            result.upload_date = auto_metadata.upload_date
            result.view_count = auto_metadata.view_count
            result.thumbnail = auto_metadata.thumbnail

        if manual_metadata:
            if manual_metadata.get("title"):
                result.title = manual_metadata["title"]
            if manual_metadata.get("author"):
                result.author = manual_metadata["author"]
        return result


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
    metadata_service = DummyMetadataService()
    transcode_service = DummyTranscodeService()

    service = ManualUploadService(
        db=db,
        file_service=file_service,
        transcode_service=transcode_service,
        metadata_service=metadata_service,
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
    metadata_service = DummyMetadataService()
    transcode_service = FailingTranscodeService()

    service = ManualUploadService(
        db=db,
        file_service=file_service,
        transcode_service=transcode_service,
        metadata_service=metadata_service,
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
