"""
人工上传服务模块。

处理人工上传音频文件的完整流程：验证、转码、存储、元数据管理。
"""

import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from src.config import Settings
from src.db.database import Database
from src.db.models import FileType, VideoInfo, VideoResource
from src.downloaders.manager import DownloaderManager
from src.services.file_service import FileService
from src.services.transcode_service import TranscodeError, TranscodeService
from src.utils.helpers import extract_video_id
from src.utils.logger import logger


class ManualUploadError(Exception):
    """人工上传错误基类。"""

    pass


class AudioAlreadyExistsError(ManualUploadError):
    """音频文件已存在错误。"""

    def __init__(self, video_id: str, existing_source: str):
        self.video_id = video_id
        self.existing_source = existing_source
        super().__init__(f"Audio file already exists for video {video_id}")


class InvalidFileFormatError(ManualUploadError):
    """文件格式无效错误。"""

    pass


class FileTooLargeError(ManualUploadError):
    """文件过大错误。"""

    pass


class ManualUploadService:
    """
    人工上传服务。

    协调文件上传、转码、元数据解析和存储的完整流程。
    """

    ILLEGAL_CHARS_PATTERN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

    def __init__(
        self,
        db: Database,
        file_service: FileService,
        transcode_service: TranscodeService,
        downloader_manager: DownloaderManager,
        settings: Settings,
    ) -> None:
        self.db = db
        self.file_service = file_service
        self.transcode_service = transcode_service
        self.downloader_manager = downloader_manager
        self.settings = settings

    @staticmethod
    def _has_metadata(info: Optional[VideoInfo]) -> bool:
        if not info:
            return False
        return any(
            getattr(info, field) is not None
            for field in (
                "title",
                "author",
                "channel_id",
                "duration",
                "description",
                "upload_date",
                "view_count",
                "thumbnail",
            )
        )

    @staticmethod
    def _dict_to_video_info(metadata: dict) -> VideoInfo:
        """
        将元数据字典转换为 VideoInfo 对象。

        Args:
            metadata: 元数据字典

        Returns:
            VideoInfo 对象
        """
        return VideoInfo(
            title=metadata.get("title"),
            author=metadata.get("author"),
            channel_id=metadata.get("channel_id"),
            duration=metadata.get("duration"),
            description=metadata.get("description"),
            upload_date=metadata.get("upload_date"),
            view_count=metadata.get("view_count"),
            thumbnail=metadata.get("thumbnail"),
        )

    @staticmethod
    def _merge_metadata(
        auto_metadata: Optional[VideoInfo],
        manual_metadata: Optional[dict],
    ) -> VideoInfo:
        """
        合并自动获取和手动提供的元数据。

        手动元数据优先级更高，会覆盖自动获取的元数据。

        Args:
            auto_metadata: 自动获取的元数据
            manual_metadata: 手动提供的元数据

        Returns:
            合并后的 VideoInfo 对象
        """
        result = VideoInfo()

        # 首先使用自动获取的元数据
        if auto_metadata:
            result.title = auto_metadata.title
            result.author = auto_metadata.author
            result.channel_id = auto_metadata.channel_id
            result.duration = auto_metadata.duration
            result.description = auto_metadata.description
            result.upload_date = auto_metadata.upload_date
            result.view_count = auto_metadata.view_count
            result.thumbnail = auto_metadata.thumbnail

        # 然后用手动提供的元数据覆盖
        if manual_metadata:
            if manual_metadata.get("title"):
                result.title = manual_metadata["title"]
            if manual_metadata.get("author"):
                result.author = manual_metadata["author"]
            if manual_metadata.get("channel_id"):
                result.channel_id = manual_metadata["channel_id"]
            if manual_metadata.get("duration") is not None:
                result.duration = manual_metadata["duration"]
            if manual_metadata.get("description"):
                result.description = manual_metadata["description"]
            if manual_metadata.get("upload_date"):
                result.upload_date = manual_metadata["upload_date"]
            if manual_metadata.get("view_count") is not None:
                result.view_count = manual_metadata["view_count"]
            if manual_metadata.get("thumbnail"):
                result.thumbnail = manual_metadata["thumbnail"]

        return result

    @staticmethod
    def sanitize_filename(filename: str, max_length: int = 100) -> str:
        path = Path(filename)
        name = path.stem
        ext = path.suffix

        name = ManualUploadService.ILLEGAL_CHARS_PATTERN.sub("_", name)
        name = name.replace("：", "_").replace("｜", "_").replace("、", "_")
        name = name.strip(". ")

        if len(name) > max_length:
            name = name[:max_length]

        if not name:
            name = "uploaded_file"

        return f"{name}{ext}"

    def _get_allowed_formats(self) -> set[str]:
        formats: set[str] = set()
        for ext in (
            self.settings.manual_upload_allowed_video_formats.split(",")
            + self.settings.manual_upload_allowed_audio_formats.split(",")
        ):
            ext = ext.strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = f".{ext}"
            formats.add(ext)
        return formats

    async def handle_upload(
        self,
        video_url: str,
        uploaded_file: UploadFile,
        manual_metadata: Optional[dict] = None,
    ) -> dict:
        if not self.settings.manual_upload_enabled:
            raise ManualUploadError("Manual upload is disabled")

        video_id = extract_video_id(video_url)
        if not video_id:
            raise ManualUploadError("Invalid YouTube URL")

        logger.info(f"[ManualUpload] Starting upload for video {video_id}")

        max_size = self.settings.manual_upload_max_size_mb * 1024 * 1024
        if uploaded_file.size and uploaded_file.size > max_size:
            raise FileTooLargeError(
                f"File too large: {uploaded_file.size / 1024 / 1024:.2f}MB "
                f"(max: {self.settings.manual_upload_max_size_mb}MB)"
            )

        existing_audio = await self.file_service.get_existing_file(
            video_id=video_id,
            file_type=FileType.AUDIO,
        )

        if existing_audio:
            logger.warning(f"[ManualUpload] Audio already exists for {video_id}")
            raise AudioAlreadyExistsError(video_id, existing_audio.upload_source)

        existing_transcript = await self.file_service.get_existing_file(
            video_id=video_id,
            file_type=FileType.TRANSCRIPT,
        )

        if existing_transcript:
            logger.info(
                f"[ManualUpload] Transcript already exists for {video_id}, "
                "will add audio to complement it"
            )

        video_resource = await self.db.get_video_resource(video_id)
        auto_metadata = None

        if video_resource:
            logger.debug(
                f"[ManualUpload] Found existing video_resource for {video_id}, "
                f"has_video_info={video_resource.video_info is not None}"
            )
            if video_resource.video_info:
                logger.info(
                    f"[ManualUpload] Using cached metadata from database for {video_id}"
                )
                auto_metadata = video_resource.video_info
            else:
                logger.debug(
                    f"[ManualUpload] video_resource exists but video_info is None, "
                    "will fetch from API"
                )
                # 使用 DownloaderManager 获取元数据（支持数据库缓存）
                metadata_dict = await self.downloader_manager.get_metadata(
                    video_url, video_id
                )
                auto_metadata = self._dict_to_video_info(metadata_dict) if metadata_dict else None
        else:
            logger.info(
                f"[ManualUpload] No existing video_resource found, "
                f"fetching metadata from API for {video_id}"
            )
            # 使用 DownloaderManager 获取元数据（支持数据库缓存）
            metadata_dict = await self.downloader_manager.get_metadata(
                video_url, video_id
            )
            auto_metadata = self._dict_to_video_info(metadata_dict) if metadata_dict else None

        merged_metadata = self._merge_metadata(
            auto_metadata, manual_metadata
        )

        if self._has_metadata(merged_metadata):
            if not video_resource:
                logger.info(
                    f"[ManualUpload] Creating video resource with metadata for {video_id}"
                )
                video_resource = VideoResource(
                    video_id=video_id,
                    video_info=merged_metadata,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                await self.db.create_video_resource(video_resource)
            elif video_resource.video_info is None or manual_metadata:
                logger.info(
                    f"[ManualUpload] Updating video resource metadata for {video_id}"
                )
                await self.db.update_video_resource(
                    video_id=video_id,
                    video_info=merged_metadata,
                )

        temp_dir = Path(tempfile.mkdtemp(prefix="manual_upload_"))
        try:
            original_filename = uploaded_file.filename or "uploaded_file.tmp"
            file_extension = Path(original_filename).suffix or ".tmp"
            temp_filename = f"{uuid.uuid4().hex}{file_extension}"
            original_path = temp_dir / temp_filename
            original_format = file_extension.lstrip(".")

            logger.info(
                f"[ManualUpload] Saving uploaded file: {original_filename} "
                f"(temp: {temp_filename})"
            )

            content = await uploaded_file.read()

            if not content:
                raise ManualUploadError("Uploaded file is empty")

            with open(original_path, "wb") as f:
                f.write(content)

            if not original_path.exists():
                raise ManualUploadError(f"Failed to save file to {original_path}")

            saved_size = original_path.stat().st_size
            if saved_size != len(content):
                raise ManualUploadError(
                    f"File size mismatch: uploaded {len(content)} bytes, "
                    f"saved {saved_size} bytes"
                )

            logger.debug(
                f"[ManualUpload] File saved successfully: {saved_size / 1024 / 1024:.2f} MB"
            )

            allowed_formats = self._get_allowed_formats()
            if original_path.suffix.lower() not in allowed_formats:
                raise InvalidFileFormatError(
                    f"Unsupported file format: {original_format}. "
                    f"Supported: {', '.join(sorted(allowed_formats))}"
                )

            if not self.transcode_service.is_supported_format(original_path):
                raise InvalidFileFormatError(
                    f"Unsupported file format: {original_format}. "
                    f"Supported: {', '.join(sorted(allowed_formats))}"
                )

            logger.info(f"[ManualUpload] Transcoding {original_format} to m4a")
            try:
                m4a_path = await self.transcode_service.transcode_to_m4a(
                    input_file=original_path,
                    output_dir=temp_dir,
                    target_bitrate=self.settings.audio_quality,
                )
            except TranscodeError as e:
                raise ManualUploadError(str(e)) from e

            if not video_resource:
                logger.info(f"[ManualUpload] Creating video resource for {video_id}")
                video_resource = VideoResource(
                    video_id=video_id,
                    video_info=merged_metadata,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                await self.db.create_video_resource(video_resource)

            logger.info(f"[ManualUpload] Creating file record")
            file_record = await self.file_service.create_file_record(
                video_id=video_id,
                file_type=FileType.AUDIO,
                source_path=m4a_path,
                quality=str(self.settings.audio_quality),
                video_title=merged_metadata.title if merged_metadata else None,
                upload_source="manual",
                original_format=original_format,
            )

            logger.info(f"[ManualUpload] Upload completed successfully: {file_record.id}")

            return {
                "video_id": video_id,
                "video_info": merged_metadata,
                "audio_file": file_record,
                "transcript_file": existing_transcript,
                "original_format": original_format,
                "metadata_source": "auto" if auto_metadata else "manual",
            }

        finally:
            import shutil

            try:
                shutil.rmtree(temp_dir)
                logger.debug(f"[ManualUpload] Cleaned up temp directory: {temp_dir}")
            except Exception as e:
                logger.warning(f"[ManualUpload] Failed to cleanup temp dir: {e}")

    async def get_video_status(self, video_id: str) -> dict:
        files = await self.file_service.get_all_files_for_video(video_id)
        audio_file = files.get("audio")
        transcript_file = files.get("transcript")

        return {
            "video_id": video_id,
            "has_audio": audio_file is not None,
            "has_transcript": transcript_file is not None,
            "audio_source": audio_file.upload_source if audio_file else None,
            "transcript_source": transcript_file.upload_source if transcript_file else None,
            "audio_created_at": audio_file.created_at if audio_file else None,
            "transcript_created_at": transcript_file.created_at if transcript_file else None,
            "can_upload_audio": audio_file is None,
        }

    async def list_manual_uploads(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        cursor = await self.db.execute(
            """
            SELECT f.*, vr.video_info
            FROM files f
            LEFT JOIN video_resources vr ON f.video_id = vr.video_id
            WHERE f.file_type = 'audio' AND f.upload_source = 'manual'
            ORDER BY f.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()

        count_cursor = await self.db.execute(
            """
            SELECT COUNT(*)
            FROM files
            WHERE file_type = 'audio' AND upload_source = 'manual'
            """
        )
        total = (await count_cursor.fetchone())[0]

        uploads = []
        for row in rows:
            import json

            video_info = None
            if row["video_info"]:
                video_info_dict = json.loads(row["video_info"])
                video_info = VideoInfo.from_dict(video_info_dict)

            uploads.append(
                {
                    "video_id": row["video_id"],
                    "file_id": row["id"],
                    "title": video_info.title if video_info else None,
                    "author": video_info.author if video_info else None,
                    "size": row["size"],
                    "format": row["format"],
                    "original_format": row["original_format"],
                    "created_at": row["created_at"],
                }
            )

        return {
            "uploads": uploads,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def delete_manual_upload(self, video_id: str) -> bool:
        cursor = await self.db.execute(
            """
            SELECT id FROM files
            WHERE video_id = ? AND file_type = 'audio' AND upload_source = 'manual'
            """,
            (video_id,),
        )
        row = await cursor.fetchone()

        if not row:
            return False

        file_id = row["id"]
        file_record = await self.db.get_file(file_id)
        if not file_record:
            return False

        file_path = self.settings.data_dir / file_record.filepath
        if file_path.exists():
            file_path.unlink()
            logger.info(f"[ManualUpload] Deleted file: {file_path}")

        await self.db.delete_file(file_id)
        logger.info(f"[ManualUpload] Deleted manual upload for video {video_id}")

        return True
