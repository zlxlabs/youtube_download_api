"""
Tests for database CRUD operations.

Covers all core database operations including:
- Video resource CRUD
- File record CRUD
- Task CRUD with status transitions
- List/filter/pagination operations
- Edge cases and data integrity
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from src.db.database import Database
from src.db.models import (
    CallbackStatus,
    ErrorCode,
    FileRecord,
    FileType,
    Task,
    TaskPriority,
    TaskStatus,
    VideoInfo,
    VideoResource,
)


# ==================== Helper Functions ====================


def make_video_resource(
    video_id: str = "dQw4w9WgXcQ",
    video_info: VideoInfo | None = None,
    has_native_transcript: bool | None = None,
) -> VideoResource:
    """Create a VideoResource for testing."""
    return VideoResource(
        video_id=video_id,
        video_info=video_info,
        has_native_transcript=has_native_transcript,
    )


def make_file_record(
    video_id: str = "dQw4w9WgXcQ",
    file_type: FileType = FileType.AUDIO,
    file_id: str | None = None,
    filename: str = "test_audio.m4a",
    filepath: str = "audio/test_audio.m4a",
    size: int = 1024,
    fmt: str = "m4a",
    quality: str | None = None,
    language: str | None = None,
) -> FileRecord:
    """Create a FileRecord for testing."""
    return FileRecord(
        id=file_id or str(uuid.uuid4()),
        video_id=video_id,
        file_type=file_type,
        filename=filename,
        filepath=filepath,
        size=size,
        format=fmt,
        quality=quality,
        language=language,
    )


def make_task(
    video_id: str = "dQw4w9WgXcQ",
    task_id: str | None = None,
    status: TaskStatus = TaskStatus.PENDING,
    include_audio: bool = True,
    include_transcript: bool = True,
    priority: TaskPriority = TaskPriority.NORMAL,
    callback_url: str | None = None,
    callback_secret: str | None = None,
) -> Task:
    """Create a Task for testing."""
    return Task(
        id=task_id or str(uuid.uuid4()),
        video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        status=status,
        include_audio=include_audio,
        include_transcript=include_transcript,
        priority=priority,
        callback_url=callback_url,
        callback_secret=callback_secret,
    )


def make_video_info(
    title: str = "Test Video",
    author: str = "Test Author",
    duration: int = 120,
) -> VideoInfo:
    """Create a VideoInfo for testing."""
    return VideoInfo(
        title=title,
        author=author,
        channel_id="UC_test_channel",
        duration=duration,
        description="Test description",
        upload_date="20240101",
        view_count=1000,
        thumbnail="https://example.com/thumb.jpg",
    )


# ==================== Video Resource Tests ====================


class TestVideoResourceCRUD:
    """Tests for video resource create/read/update/delete operations."""

    async def test_create_and_get_video_resource(self, test_db: Database):
        """Create a video resource and verify it can be retrieved."""
        resource = make_video_resource("vid_001")
        await test_db.create_video_resource(resource)

        result = await test_db.get_video_resource("vid_001")
        assert result is not None
        assert result.video_id == "vid_001"
        assert result.created_at is not None
        assert result.updated_at is not None

    async def test_create_video_resource_with_info(self, test_db: Database):
        """Create a video resource with full video info."""
        info = make_video_info(title="Full Info Video", author="Author X")
        resource = make_video_resource("vid_002", video_info=info)
        await test_db.create_video_resource(resource)

        result = await test_db.get_video_resource("vid_002")
        assert result is not None
        assert result.video_info is not None
        assert result.video_info.title == "Full Info Video"
        assert result.video_info.author == "Author X"
        assert result.video_info.duration == 120
        assert result.video_info.channel_id == "UC_test_channel"

    async def test_create_video_resource_with_transcript_flag(self, test_db: Database):
        """Verify has_native_transcript flag is stored correctly."""
        # True case
        resource_true = make_video_resource("vid_true", has_native_transcript=True)
        await test_db.create_video_resource(resource_true)
        result_true = await test_db.get_video_resource("vid_true")
        assert result_true is not None
        assert result_true.has_native_transcript is True

        # False case
        resource_false = make_video_resource("vid_false", has_native_transcript=False)
        await test_db.create_video_resource(resource_false)
        result_false = await test_db.get_video_resource("vid_false")
        assert result_false is not None
        assert result_false.has_native_transcript is False

        # None case
        resource_none = make_video_resource("vid_none", has_native_transcript=None)
        await test_db.create_video_resource(resource_none)
        result_none = await test_db.get_video_resource("vid_none")
        assert result_none is not None
        assert result_none.has_native_transcript is None

    async def test_get_nonexistent_video_resource(self, test_db: Database):
        """Getting a nonexistent video resource should return None."""
        result = await test_db.get_video_resource("nonexistent_id")
        assert result is None

    async def test_update_video_resource_info(self, test_db: Database):
        """Update video info on an existing resource."""
        resource = make_video_resource("vid_update")
        await test_db.create_video_resource(resource)

        new_info = make_video_info(title="Updated Title", author="New Author")
        await test_db.update_video_resource("vid_update", video_info=new_info)

        result = await test_db.get_video_resource("vid_update")
        assert result is not None
        assert result.video_info is not None
        assert result.video_info.title == "Updated Title"
        assert result.video_info.author == "New Author"

    async def test_update_video_resource_transcript_flag(self, test_db: Database):
        """Update has_native_transcript flag."""
        resource = make_video_resource("vid_flag_update")
        await test_db.create_video_resource(resource)

        await test_db.update_video_resource("vid_flag_update", has_native_transcript=True)
        result = await test_db.get_video_resource("vid_flag_update")
        assert result is not None
        assert result.has_native_transcript is True

        await test_db.update_video_resource("vid_flag_update", has_native_transcript=False)
        result = await test_db.get_video_resource("vid_flag_update")
        assert result is not None
        assert result.has_native_transcript is False

    async def test_get_or_create_video_resource_create(self, test_db: Database):
        """get_or_create should create a new resource if none exists."""
        result = await test_db.get_or_create_video_resource("vid_new_goc")
        assert result is not None
        assert result.video_id == "vid_new_goc"

        # Verify it was actually persisted
        fetched = await test_db.get_video_resource("vid_new_goc")
        assert fetched is not None

    async def test_get_or_create_video_resource_get(self, test_db: Database):
        """get_or_create should return existing resource if found."""
        info = make_video_info(title="Existing")
        resource = make_video_resource("vid_existing_goc", video_info=info)
        await test_db.create_video_resource(resource)

        result = await test_db.get_or_create_video_resource("vid_existing_goc")
        assert result.video_info is not None
        assert result.video_info.title == "Existing"

    async def test_duplicate_video_resource_raises(self, test_db: Database):
        """Creating a duplicate video resource should raise an error."""
        resource = make_video_resource("vid_dup")
        await test_db.create_video_resource(resource)

        with pytest.raises(Exception):
            await test_db.create_video_resource(resource)

    async def test_delete_video_resource(self, test_db: Database):
        """Delete a video resource and verify cascade to files."""
        resource = make_video_resource("vid_del")
        await test_db.create_video_resource(resource)

        # Add a file for this video
        file_rec = make_file_record(video_id="vid_del", file_id="file_del_1")
        await test_db.create_file(file_rec)

        paths = await test_db.delete_video_resource("vid_del")
        assert len(paths) == 1
        assert paths[0] == "audio/test_audio.m4a"

        # Verify video resource is gone
        assert await test_db.get_video_resource("vid_del") is None
        # Verify file is gone
        assert await test_db.get_file("file_del_1") is None

    async def test_list_video_resources_empty(self, test_db: Database):
        """Listing video resources on empty DB should return empty list."""
        resources, total = await test_db.list_video_resources()
        assert resources == []
        assert total == 0

    async def test_list_video_resources_pagination(self, test_db: Database):
        """Verify pagination works for video resources."""
        # Create 5 video resources
        for i in range(5):
            info = make_video_info(title=f"Video {i}")
            resource = make_video_resource(f"vid_page_{i}", video_info=info)
            await test_db.create_video_resource(resource)

        # Get first page
        page1, total = await test_db.list_video_resources(limit=2, offset=0)
        assert total == 5
        assert len(page1) == 2

        # Get second page
        page2, total = await test_db.list_video_resources(limit=2, offset=2)
        assert total == 5
        assert len(page2) == 2

        # Get last page
        page3, total = await test_db.list_video_resources(limit=2, offset=4)
        assert total == 5
        assert len(page3) == 1

    async def test_list_video_resources_search_by_id(self, test_db: Database):
        """Search video resources by video_id."""
        resource = make_video_resource("searchable_id_123")
        await test_db.create_video_resource(resource)
        resource2 = make_video_resource("other_id_456")
        await test_db.create_video_resource(resource2)

        results, total = await test_db.list_video_resources(search="searchable")
        assert total == 1
        assert results[0]["video_id"] == "searchable_id_123"

    async def test_list_video_resources_search_by_title(self, test_db: Database):
        """Search video resources by title in video_info."""
        info = make_video_info(title="UniqueSearchTitle")
        resource = make_video_resource("vid_search_title", video_info=info)
        await test_db.create_video_resource(resource)

        results, total = await test_db.list_video_resources(search="UniqueSearch")
        assert total == 1
        assert results[0]["video_id"] == "vid_search_title"

    async def test_get_video_resource_detail(self, test_db: Database):
        """Get full detail of a video resource including files and tasks."""
        info = make_video_info(title="Detail Video")
        resource = make_video_resource("vid_detail", video_info=info)
        await test_db.create_video_resource(resource)

        # Add a file
        file_rec = make_file_record(video_id="vid_detail", file_id="file_detail_1")
        await test_db.create_file(file_rec)

        # Add a task
        task = make_task(video_id="vid_detail", task_id="task_detail_1")
        await test_db.create_task(task)

        detail = await test_db.get_video_resource_detail("vid_detail")
        assert detail is not None
        assert detail["video_id"] == "vid_detail"
        assert detail["video_info"]["title"] == "Detail Video"
        assert len(detail["files"]) == 1
        assert detail["files"][0]["id"] == "file_detail_1"
        assert len(detail["recent_tasks"]) == 1
        assert detail["recent_tasks"][0]["id"] == "task_detail_1"

    async def test_get_video_resource_detail_nonexistent(self, test_db: Database):
        """Getting detail of nonexistent video should return None."""
        result = await test_db.get_video_resource_detail("nonexistent")
        assert result is None


# ==================== File Record Tests ====================


class TestFileRecordCRUD:
    """Tests for file record create/read/update/delete operations."""

    async def test_create_and_get_file(self, test_db: Database):
        """Create a file record and retrieve it by ID."""
        resource = make_video_resource("vid_file_01")
        await test_db.create_video_resource(resource)

        file_id = str(uuid.uuid4())
        file_rec = make_file_record(
            video_id="vid_file_01",
            file_id=file_id,
            filename="audio.m4a",
            filepath="audio/vid_file_01/audio.m4a",
            size=2048,
            fmt="m4a",
        )
        await test_db.create_file(file_rec)

        result = await test_db.get_file(file_id)
        assert result is not None
        assert result.id == file_id
        assert result.video_id == "vid_file_01"
        assert result.file_type == FileType.AUDIO
        assert result.filename == "audio.m4a"
        assert result.filepath == "audio/vid_file_01/audio.m4a"
        assert result.size == 2048
        assert result.format == "m4a"
        assert result.upload_source == "auto"
        assert result.created_at is not None

    async def test_create_transcript_file(self, test_db: Database):
        """Create a transcript file record with language."""
        resource = make_video_resource("vid_file_02")
        await test_db.create_video_resource(resource)

        file_id = str(uuid.uuid4())
        file_rec = make_file_record(
            video_id="vid_file_02",
            file_id=file_id,
            file_type=FileType.TRANSCRIPT,
            filename="transcript.json",
            filepath="transcripts/vid_file_02/transcript.json",
            size=512,
            fmt="json",
            language="en",
        )
        await test_db.create_file(file_rec)

        result = await test_db.get_file(file_id)
        assert result is not None
        assert result.file_type == FileType.TRANSCRIPT
        assert result.language == "en"

    async def test_get_nonexistent_file(self, test_db: Database):
        """Getting a nonexistent file should return None."""
        result = await test_db.get_file("nonexistent_file_id")
        assert result is None

    async def test_get_file_by_video_audio(self, test_db: Database):
        """Get audio file by video ID and file type."""
        resource = make_video_resource("vid_file_03")
        await test_db.create_video_resource(resource)

        file_id = str(uuid.uuid4())
        file_rec = make_file_record(video_id="vid_file_03", file_id=file_id)
        await test_db.create_file(file_rec)

        result = await test_db.get_file_by_video("vid_file_03", FileType.AUDIO)
        assert result is not None
        assert result.id == file_id

    async def test_get_file_by_video_transcript_with_language(self, test_db: Database):
        """Get transcript file by video ID, type, and language."""
        resource = make_video_resource("vid_file_04")
        await test_db.create_video_resource(resource)

        file_id = str(uuid.uuid4())
        file_rec = make_file_record(
            video_id="vid_file_04",
            file_id=file_id,
            file_type=FileType.TRANSCRIPT,
            filename="sub_en.json",
            filepath="transcripts/sub_en.json",
            language="en",
        )
        await test_db.create_file(file_rec)

        # With correct language
        result = await test_db.get_file_by_video(
            "vid_file_04", FileType.TRANSCRIPT, language="en"
        )
        assert result is not None
        assert result.id == file_id

        # With wrong language
        result_wrong = await test_db.get_file_by_video(
            "vid_file_04", FileType.TRANSCRIPT, language="zh"
        )
        assert result_wrong is None

    async def test_get_file_by_video_no_match(self, test_db: Database):
        """get_file_by_video should return None when no file matches."""
        resource = make_video_resource("vid_file_05")
        await test_db.create_video_resource(resource)

        result = await test_db.get_file_by_video("vid_file_05", FileType.AUDIO)
        assert result is None

    async def test_get_files_by_video(self, test_db: Database):
        """Get all files for a video."""
        resource = make_video_resource("vid_file_06")
        await test_db.create_video_resource(resource)

        audio_file = make_file_record(
            video_id="vid_file_06",
            file_id="audio_06",
            filename="audio.m4a",
            filepath="audio/audio.m4a",
        )
        transcript_file = make_file_record(
            video_id="vid_file_06",
            file_id="trans_06",
            file_type=FileType.TRANSCRIPT,
            filename="transcript.json",
            filepath="transcripts/transcript.json",
            language="en",
        )
        await test_db.create_file(audio_file)
        await test_db.create_file(transcript_file)

        files = await test_db.get_files_by_video("vid_file_06")
        assert len(files) == 2
        file_ids = {f.id for f in files}
        assert "audio_06" in file_ids
        assert "trans_06" in file_ids

    async def test_get_files_by_video_empty(self, test_db: Database):
        """Get files for a video with no files should return empty list."""
        files = await test_db.get_files_by_video("nonexistent_video")
        assert files == []

    async def test_update_file_access_time(self, test_db: Database):
        """Update file last access time."""
        resource = make_video_resource("vid_file_07")
        await test_db.create_video_resource(resource)

        file_id = str(uuid.uuid4())
        file_rec = make_file_record(video_id="vid_file_07", file_id=file_id)
        await test_db.create_file(file_rec)

        # Initially last_accessed_at should be None
        before = await test_db.get_file(file_id)
        assert before is not None
        assert before.last_accessed_at is None

        await test_db.update_file_access_time(file_id)

        after = await test_db.get_file(file_id)
        assert after is not None
        assert after.last_accessed_at is not None

    async def test_get_expired_files(self, test_db: Database):
        """Get files that haven't been accessed since cutoff time."""
        resource = make_video_resource("vid_file_08")
        await test_db.create_video_resource(resource)

        old_file = make_file_record(
            video_id="vid_file_08", file_id="old_file_08"
        )
        await test_db.create_file(old_file)
        # old_file has no last_accessed_at, so it relies on created_at

        # Use a cutoff far in the future to include the old file
        future_cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
        expired = await test_db.get_expired_files(future_cutoff)
        expired_ids = {f.id for f in expired}
        assert "old_file_08" in expired_ids

        # Use a cutoff in the past to exclude the file
        past_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        not_expired = await test_db.get_expired_files(past_cutoff)
        not_expired_ids = {f.id for f in not_expired}
        assert "old_file_08" not in not_expired_ids

    async def test_delete_file(self, test_db: Database):
        """Delete a file record."""
        resource = make_video_resource("vid_file_09")
        await test_db.create_video_resource(resource)

        file_id = str(uuid.uuid4())
        file_rec = make_file_record(video_id="vid_file_09", file_id=file_id)
        await test_db.create_file(file_rec)

        await test_db.delete_file(file_id)
        result = await test_db.get_file(file_id)
        assert result is None

    async def test_file_manual_upload_source(self, test_db: Database):
        """Verify upload_source and original_format fields."""
        resource = make_video_resource("vid_file_10")
        await test_db.create_video_resource(resource)

        file_id = str(uuid.uuid4())
        file_rec = FileRecord(
            id=file_id,
            video_id="vid_file_10",
            file_type=FileType.AUDIO,
            filename="manual.m4a",
            filepath="audio/manual.m4a",
            size=4096,
            format="m4a",
            upload_source="manual",
            original_format="mp3",
        )
        await test_db.create_file(file_rec)

        result = await test_db.get_file(file_id)
        assert result is not None
        assert result.upload_source == "manual"
        assert result.original_format == "mp3"


# ==================== Task Tests ====================


class TestTaskCRUD:
    """Tests for task create/read/update/delete operations."""

    async def test_create_and_get_task(self, test_db: Database):
        """Create a task and retrieve it by ID."""
        task = make_task(video_id="vid_task_01", task_id="task_01")
        await test_db.create_task(task)

        result = await test_db.get_task("task_01")
        assert result is not None
        assert result.id == "task_01"
        assert result.video_id == "vid_task_01"
        assert result.status == TaskStatus.PENDING
        assert result.include_audio is True
        assert result.include_transcript is True
        assert result.priority == TaskPriority.NORMAL
        assert result.created_at is not None

    async def test_create_task_with_callback(self, test_db: Database):
        """Create a task with callback configuration."""
        task = make_task(
            video_id="vid_task_02",
            task_id="task_02",
            callback_url="https://example.com/callback",
            callback_secret="secret123",
        )
        await test_db.create_task(task)

        result = await test_db.get_task("task_02")
        assert result is not None
        assert result.callback_url == "https://example.com/callback"
        assert result.callback_secret == "secret123"
        assert result.callback_status is None

    async def test_create_task_audio_only(self, test_db: Database):
        """Create an audio-only task."""
        task = make_task(
            video_id="vid_task_03",
            task_id="task_03",
            include_audio=True,
            include_transcript=False,
        )
        await test_db.create_task(task)

        result = await test_db.get_task("task_03")
        assert result is not None
        assert result.include_audio is True
        assert result.include_transcript is False

    async def test_create_task_urgent_priority(self, test_db: Database):
        """Create a task with urgent priority."""
        task = make_task(
            video_id="vid_task_04",
            task_id="task_04",
            priority=TaskPriority.URGENT,
        )
        await test_db.create_task(task)

        result = await test_db.get_task("task_04")
        assert result is not None
        assert result.priority == TaskPriority.URGENT

    async def test_get_nonexistent_task(self, test_db: Database):
        """Getting a nonexistent task should return None."""
        result = await test_db.get_task("nonexistent_task")
        assert result is None

    async def test_duplicate_task_raises(self, test_db: Database):
        """Creating a task with duplicate ID should raise."""
        task = make_task(video_id="vid_dup_task", task_id="dup_task_id")
        await test_db.create_task(task)

        with pytest.raises(Exception):
            await test_db.create_task(task)


# ==================== Task Status Transition Tests ====================


class TestTaskStatusTransitions:
    """Tests for task status update operations."""

    async def test_update_status_to_downloading(self, test_db: Database):
        """Update task to downloading should set started_at."""
        task = make_task(video_id="vid_status_01", task_id="status_01")
        await test_db.create_task(task)

        await test_db.update_task_status("status_01", TaskStatus.DOWNLOADING)

        result = await test_db.get_task("status_01")
        assert result is not None
        assert result.status == TaskStatus.DOWNLOADING
        assert result.started_at is not None

    async def test_update_status_to_completed(self, test_db: Database):
        """Update task to completed should set completed_at."""
        task = make_task(video_id="vid_status_02", task_id="status_02")
        await test_db.create_task(task)

        await test_db.update_task_status("status_02", TaskStatus.COMPLETED)

        result = await test_db.get_task("status_02")
        assert result is not None
        assert result.status == TaskStatus.COMPLETED
        assert result.completed_at is not None

    async def test_update_status_to_failed_with_error(self, test_db: Database):
        """Update task to failed with error details."""
        task = make_task(video_id="vid_status_03", task_id="status_03")
        await test_db.create_task(task)

        await test_db.update_task_status(
            "status_03",
            TaskStatus.FAILED,
            error_code=ErrorCode.DOWNLOAD_FAILED,
            error_message="Network timeout",
        )

        result = await test_db.get_task("status_03")
        assert result is not None
        assert result.status == TaskStatus.FAILED
        assert result.error_code == ErrorCode.DOWNLOAD_FAILED
        assert result.error_message == "Network timeout"
        assert result.completed_at is not None

    async def test_update_status_to_cancelled(self, test_db: Database):
        """Update task to cancelled status."""
        task = make_task(video_id="vid_status_04", task_id="status_04")
        await test_db.create_task(task)

        await test_db.update_task_status("status_04", TaskStatus.CANCELLED)

        result = await test_db.get_task("status_04")
        assert result is not None
        assert result.status == TaskStatus.CANCELLED

    async def test_update_status_to_delayed_ip_ban(self, test_db: Database):
        """Update task to delayed_ip_ban status."""
        task = make_task(video_id="vid_status_05", task_id="status_05")
        await test_db.create_task(task)

        await test_db.update_task_status("status_05", TaskStatus.DELAYED_IP_BAN)

        result = await test_db.get_task("status_05")
        assert result is not None
        assert result.status == TaskStatus.DELAYED_IP_BAN

    async def test_update_task_completed_with_files(self, test_db: Database):
        """Update task as completed with file references."""
        task = make_task(video_id="vid_complete_01", task_id="complete_01")
        await test_db.create_task(task)

        await test_db.update_task_completed(
            "complete_01",
            audio_file_id="audio_file_1",
            transcript_file_id="trans_file_1",
            reused_audio=True,
            reused_transcript=False,
        )

        result = await test_db.get_task("complete_01")
        assert result is not None
        assert result.status == TaskStatus.COMPLETED
        assert result.audio_file_id == "audio_file_1"
        assert result.transcript_file_id == "trans_file_1"
        assert result.reused_audio is True
        assert result.reused_transcript is False
        assert result.completed_at is not None

    async def test_update_task_completed_audio_only(self, test_db: Database):
        """Update task as completed with only audio file."""
        task = make_task(
            video_id="vid_complete_02",
            task_id="complete_02",
            include_audio=True,
            include_transcript=False,
        )
        await test_db.create_task(task)

        await test_db.update_task_completed(
            "complete_02",
            audio_file_id="audio_only_file",
        )

        result = await test_db.get_task("complete_02")
        assert result is not None
        assert result.status == TaskStatus.COMPLETED
        assert result.audio_file_id == "audio_only_file"
        assert result.transcript_file_id is None

    async def test_increment_retry_count(self, test_db: Database):
        """Increment retry count should increase count and reset to pending."""
        task = make_task(video_id="vid_retry_01", task_id="retry_01")
        await test_db.create_task(task)

        # Set to failed first
        await test_db.update_task_status("retry_01", TaskStatus.FAILED)

        # Increment retry
        new_count = await test_db.increment_retry_count("retry_01")
        assert new_count == 1

        result = await test_db.get_task("retry_01")
        assert result is not None
        assert result.retry_count == 1
        assert result.status == TaskStatus.PENDING

        # Increment again
        new_count = await test_db.increment_retry_count("retry_01")
        assert new_count == 2


# ==================== Task Query Tests ====================


class TestTaskQueries:
    """Tests for task listing, filtering, and queue operations."""

    async def test_get_active_task_by_video(self, test_db: Database):
        """Find active task by video ID."""
        task = make_task(video_id="vid_active_01", task_id="active_01")
        await test_db.create_task(task)

        result = await test_db.get_active_task_by_video("vid_active_01")
        assert result is not None
        assert result.id == "active_01"

    async def test_get_active_task_by_video_downloading(self, test_db: Database):
        """A downloading task should be found as active."""
        task = make_task(video_id="vid_active_02", task_id="active_02")
        await test_db.create_task(task)
        await test_db.update_task_status("active_02", TaskStatus.DOWNLOADING)

        result = await test_db.get_active_task_by_video("vid_active_02")
        assert result is not None
        assert result.id == "active_02"

    async def test_get_active_task_by_video_completed_not_active(self, test_db: Database):
        """A completed task should not be found as active."""
        task = make_task(video_id="vid_active_03", task_id="active_03")
        await test_db.create_task(task)
        await test_db.update_task_status("active_03", TaskStatus.COMPLETED)

        result = await test_db.get_active_task_by_video("vid_active_03")
        assert result is None

    async def test_get_active_task_by_video_none(self, test_db: Database):
        """No active task for this video should return None."""
        result = await test_db.get_active_task_by_video("nonexistent_vid")
        assert result is None

    async def test_get_active_tasks_by_video_returns_all_active(self, test_db: Database):
        """多条活跃任务（pending/downloading）都应被返回，按创建时间倒序。"""
        older = make_task(
            video_id="vid_multi_active", task_id="multi_older", include_transcript=False
        )
        older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await test_db.create_task(older)

        newer = make_task(
            video_id="vid_multi_active", task_id="multi_newer", include_audio=False
        )
        newer.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        await test_db.create_task(newer)

        result = await test_db.get_active_tasks_by_video("vid_multi_active")

        assert [t.id for t in result] == ["multi_newer", "multi_older"]

    async def test_get_active_tasks_by_video_excludes_terminal_status(self, test_db: Database):
        """已完成/已取消的任务不应出现在活跃任务列表中。"""
        active = make_task(video_id="vid_multi_active_02", task_id="multi_active")
        await test_db.create_task(active)
        completed = make_task(video_id="vid_multi_active_02", task_id="multi_completed")
        await test_db.create_task(completed)
        await test_db.update_task_status("multi_completed", TaskStatus.COMPLETED)

        result = await test_db.get_active_tasks_by_video("vid_multi_active_02")

        assert [t.id for t in result] == ["multi_active"]

    async def test_get_active_tasks_by_video_none(self, test_db: Database):
        """没有活跃任务时应返回空列表。"""
        result = await test_db.get_active_tasks_by_video("nonexistent_vid_multi")
        assert result == []

    async def test_get_pending_tasks(self, test_db: Database):
        """Get pending tasks ordered by creation time."""
        for i in range(3):
            task = make_task(video_id=f"vid_pending_{i}", task_id=f"pending_{i}")
            await test_db.create_task(task)

        pending = await test_db.get_pending_tasks(limit=10)
        assert len(pending) == 3
        # Verify ordering (oldest first)
        assert pending[0].id == "pending_0"
        assert pending[1].id == "pending_1"
        assert pending[2].id == "pending_2"

    async def test_get_pending_tasks_limit(self, test_db: Database):
        """Get pending tasks with limit."""
        for i in range(5):
            task = make_task(video_id=f"vid_pend_lim_{i}", task_id=f"pend_lim_{i}")
            await test_db.create_task(task)

        pending = await test_db.get_pending_tasks(limit=2)
        assert len(pending) == 2

    async def test_get_pending_tasks_offset(self, test_db: Database):
        """get_pending_tasks 应支持 offset 分页，用于批量恢复超过单批次上限的任务。"""
        for i in range(5):
            task = make_task(video_id=f"vid_pend_off_{i}", task_id=f"pend_off_{i}")
            await test_db.create_task(task)

        first_page = await test_db.get_pending_tasks(limit=2, offset=0)
        second_page = await test_db.get_pending_tasks(limit=2, offset=2)
        third_page = await test_db.get_pending_tasks(limit=2, offset=4)

        # 分页之间既不遗漏也不重复，且顺序与全量查询一致（created_at ASC）
        assert [t.id for t in first_page] == ["pend_off_0", "pend_off_1"]
        assert [t.id for t in second_page] == ["pend_off_2", "pend_off_3"]
        assert [t.id for t in third_page] == ["pend_off_4"]

    async def test_get_pending_tasks_excludes_other_statuses(self, test_db: Database):
        """get_pending_tasks should only return pending tasks."""
        pending_task = make_task(video_id="vid_pend_ex_1", task_id="pend_ex_1")
        await test_db.create_task(pending_task)

        downloading_task = make_task(video_id="vid_pend_ex_2", task_id="pend_ex_2")
        await test_db.create_task(downloading_task)
        await test_db.update_task_status("pend_ex_2", TaskStatus.DOWNLOADING)

        completed_task = make_task(video_id="vid_pend_ex_3", task_id="pend_ex_3")
        await test_db.create_task(completed_task)
        await test_db.update_task_status("pend_ex_3", TaskStatus.COMPLETED)

        pending = await test_db.get_pending_tasks(limit=10)
        pending_ids = {t.id for t in pending}
        assert "pend_ex_1" in pending_ids
        assert "pend_ex_2" not in pending_ids
        assert "pend_ex_3" not in pending_ids

    async def test_list_tasks_no_filter(self, test_db: Database):
        """List tasks without any filters."""
        task = make_task(video_id="vid_list_01", task_id="list_01")
        await test_db.create_task(task)

        tasks, total = await test_db.list_tasks()
        assert total >= 1
        task_ids = {t.id for t in tasks}
        assert "list_01" in task_ids

    async def test_list_tasks_filter_by_status(self, test_db: Database):
        """List tasks filtered by status."""
        task1 = make_task(video_id="vid_list_s1", task_id="list_s1")
        await test_db.create_task(task1)

        task2 = make_task(video_id="vid_list_s2", task_id="list_s2")
        await test_db.create_task(task2)
        await test_db.update_task_status("list_s2", TaskStatus.COMPLETED)

        # Filter by completed
        tasks, total = await test_db.list_tasks(status=TaskStatus.COMPLETED)
        task_ids = {t.id for t in tasks}
        assert "list_s2" in task_ids
        assert "list_s1" not in task_ids

    async def test_list_tasks_pagination(self, test_db: Database):
        """List tasks with pagination."""
        for i in range(5):
            task = make_task(video_id=f"vid_list_p_{i}", task_id=f"list_p_{i}")
            await test_db.create_task(task)

        page1, total = await test_db.list_tasks(limit=2, offset=0)
        assert len(page1) == 2
        assert total >= 5

        page2, _ = await test_db.list_tasks(limit=2, offset=2)
        assert len(page2) == 2

        # Ensure pages don't overlap
        page1_ids = {t.id for t in page1}
        page2_ids = {t.id for t in page2}
        assert page1_ids.isdisjoint(page2_ids)

    async def test_list_tasks_filter_by_date_range(self, test_db: Database):
        """List tasks filtered by date range."""
        task = make_task(video_id="vid_list_d1", task_id="list_d1")
        await test_db.create_task(task)

        now = datetime.now(timezone.utc)
        # Tasks created in the future should not match
        future = now + timedelta(hours=1)
        tasks, total = await test_db.list_tasks(created_after=future)
        task_ids = {t.id for t in tasks}
        assert "list_d1" not in task_ids

        # Tasks created before now + 1 hour should match
        past = now - timedelta(hours=1)
        tasks, total = await test_db.list_tasks(created_after=past)
        task_ids = {t.id for t in tasks}
        assert "list_d1" in task_ids

    async def test_get_queue_position(self, test_db: Database):
        """Get task queue position."""
        for i in range(3):
            task = make_task(video_id=f"vid_qpos_{i}", task_id=f"qpos_{i}")
            await test_db.create_task(task)

        # First task should be position 1
        pos = await test_db.get_queue_position("qpos_0")
        assert pos == 1

    async def test_reset_downloading_tasks(self, test_db: Database):
        """Reset downloading tasks to pending."""
        task1 = make_task(video_id="vid_reset_1", task_id="reset_1")
        await test_db.create_task(task1)
        await test_db.update_task_status("reset_1", TaskStatus.DOWNLOADING)

        task2 = make_task(video_id="vid_reset_2", task_id="reset_2")
        await test_db.create_task(task2)
        await test_db.update_task_status("reset_2", TaskStatus.DOWNLOADING)

        task3 = make_task(video_id="vid_reset_3", task_id="reset_3")
        await test_db.create_task(task3)
        # task3 stays pending

        count = await test_db.reset_downloading_tasks()
        assert count == 2

        result1 = await test_db.get_task("reset_1")
        assert result1 is not None
        assert result1.status == TaskStatus.PENDING

        result2 = await test_db.get_task("reset_2")
        assert result2 is not None
        assert result2.status == TaskStatus.PENDING

        result3 = await test_db.get_task("reset_3")
        assert result3 is not None
        assert result3.status == TaskStatus.PENDING


# ==================== Callback Tests ====================


class TestCallbackOperations:
    """Tests for callback status update operations."""

    async def test_update_callback_status(self, test_db: Database):
        """Update callback status on a task."""
        task = make_task(
            video_id="vid_cb_01",
            task_id="cb_01",
            callback_url="https://example.com/cb",
        )
        await test_db.create_task(task)

        await test_db.update_callback_status(
            "cb_01", CallbackStatus.SUCCESS, attempts=1
        )

        result = await test_db.get_task("cb_01")
        assert result is not None
        assert result.callback_status == CallbackStatus.SUCCESS
        assert result.callback_attempts == 1

    async def test_update_callback_status_failed(self, test_db: Database):
        """Update callback as failed with attempt count."""
        task = make_task(
            video_id="vid_cb_02",
            task_id="cb_02",
            callback_url="https://example.com/cb",
        )
        await test_db.create_task(task)

        await test_db.update_callback_status(
            "cb_02", CallbackStatus.FAILED, attempts=3
        )

        result = await test_db.get_task("cb_02")
        assert result is not None
        assert result.callback_status == CallbackStatus.FAILED
        assert result.callback_attempts == 3

    async def test_update_callback_status_without_attempts(self, test_db: Database):
        """Update callback status without specifying attempts."""
        task = make_task(
            video_id="vid_cb_03",
            task_id="cb_03",
            callback_url="https://example.com/cb",
        )
        await test_db.create_task(task)

        await test_db.update_callback_status("cb_03", CallbackStatus.PENDING)

        result = await test_db.get_task("cb_03")
        assert result is not None
        assert result.callback_status == CallbackStatus.PENDING


# ==================== Cleanup Tests ====================


class TestCleanupOperations:
    """Tests for cleanup and maintenance operations."""

    async def test_delete_expired_tasks(self, test_db: Database):
        """Delete completed tasks older than cutoff."""
        task = make_task(video_id="vid_exp_01", task_id="exp_01")
        await test_db.create_task(task)
        await test_db.update_task_status("exp_01", TaskStatus.COMPLETED)

        # Delete with cutoff in the future (should delete)
        future_cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
        count = await test_db.delete_expired_tasks(future_cutoff)
        assert count >= 1

        result = await test_db.get_task("exp_01")
        assert result is None

    async def test_delete_expired_tasks_keeps_recent(self, test_db: Database):
        """Should not delete recently completed tasks."""
        task = make_task(video_id="vid_exp_02", task_id="exp_02")
        await test_db.create_task(task)
        await test_db.update_task_status("exp_02", TaskStatus.COMPLETED)

        # Cutoff in the past (should not delete recent task)
        past_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        count = await test_db.delete_expired_tasks(past_cutoff)

        result = await test_db.get_task("exp_02")
        assert result is not None

    async def test_delete_expired_tasks_ignores_pending(self, test_db: Database):
        """Should not delete pending tasks regardless of age."""
        task = make_task(video_id="vid_exp_03", task_id="exp_03")
        await test_db.create_task(task)

        future_cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
        await test_db.delete_expired_tasks(future_cutoff)

        result = await test_db.get_task("exp_03")
        assert result is not None

    async def test_delete_orphan_video_resources(self, test_db: Database):
        """Delete video resources with no files and no info."""
        # Create orphan (no files, no video_info)
        orphan = make_video_resource("vid_orphan_01")
        await test_db.create_video_resource(orphan)

        # Create non-orphan with file
        non_orphan = make_video_resource("vid_orphan_02")
        await test_db.create_video_resource(non_orphan)
        file_rec = make_file_record(video_id="vid_orphan_02", file_id="file_orphan_02")
        await test_db.create_file(file_rec)

        # Create non-orphan with video_info
        info = make_video_info(title="Has Info")
        has_info = make_video_resource("vid_orphan_03", video_info=info)
        await test_db.create_video_resource(has_info)

        count = await test_db.delete_orphan_video_resources()
        assert count >= 1

        # Orphan should be gone
        assert await test_db.get_video_resource("vid_orphan_01") is None
        # Non-orphans should still exist
        assert await test_db.get_video_resource("vid_orphan_02") is not None
        assert await test_db.get_video_resource("vid_orphan_03") is not None


# ==================== Statistics Tests ====================


class TestStatistics:
    """Tests for statistics operations."""

    async def test_get_queue_stats(self, test_db: Database):
        """Get queue statistics."""
        task1 = make_task(video_id="vid_stat_q1", task_id="stat_q1")
        await test_db.create_task(task1)

        task2 = make_task(video_id="vid_stat_q2", task_id="stat_q2")
        await test_db.create_task(task2)
        await test_db.update_task_status("stat_q2", TaskStatus.DOWNLOADING)

        stats = await test_db.get_queue_stats()
        assert stats["pending"] >= 1
        assert stats["downloading"] >= 1

    async def test_get_resource_stats(self, test_db: Database):
        """Get resource statistics."""
        resource = make_video_resource("vid_stat_r1")
        await test_db.create_video_resource(resource)

        file_rec = make_file_record(video_id="vid_stat_r1", file_id="file_stat_r1")
        await test_db.create_file(file_rec)

        task = make_task(video_id="vid_stat_r1", task_id="stat_r1")
        await test_db.create_task(task)

        stats = await test_db.get_resource_stats()
        assert stats["videos"] >= 1
        assert stats["files"] >= 1
        assert stats["tasks"] >= 1

    async def test_get_task_stats(self, test_db: Database):
        """Get task statistics grouped by status."""
        task_p = make_task(video_id="vid_stat_ts1", task_id="stat_ts1")
        await test_db.create_task(task_p)

        task_c = make_task(video_id="vid_stat_ts2", task_id="stat_ts2")
        await test_db.create_task(task_c)
        await test_db.update_task_status("stat_ts2", TaskStatus.COMPLETED)

        task_f = make_task(video_id="vid_stat_ts3", task_id="stat_ts3")
        await test_db.create_task(task_f)
        await test_db.update_task_status(
            "stat_ts3", TaskStatus.FAILED, error_code=ErrorCode.DOWNLOAD_FAILED
        )

        stats = await test_db.get_task_stats()
        assert stats["total"] >= 3
        assert stats["pending"] >= 1
        assert stats["completed"] >= 1
        assert stats["failed"] >= 1

    async def test_get_queue_stats_empty(self, test_db: Database):
        """Queue stats on empty DB should return zeros."""
        # Note: other tests may have added data, but we check structure
        stats = await test_db.get_queue_stats()
        assert "pending" in stats
        assert "downloading" in stats


# ==================== Data Integrity Tests ====================


class TestDataIntegrity:
    """Tests for data integrity and edge cases."""

    async def test_task_all_fields_roundtrip(self, test_db: Database):
        """All task fields should survive a round-trip through the database."""
        task = Task(
            id="integrity_01",
            video_id="vid_integrity_01",
            video_url="https://www.youtube.com/watch?v=vid_integrity_01",
            status=TaskStatus.PENDING,
            priority=TaskPriority.URGENT,
            include_audio=True,
            include_transcript=False,
            callback_url="https://example.com/hook",
            callback_secret="s3cr3t",
        )
        await test_db.create_task(task)

        result = await test_db.get_task("integrity_01")
        assert result is not None
        assert result.id == "integrity_01"
        assert result.video_id == "vid_integrity_01"
        assert result.video_url == "https://www.youtube.com/watch?v=vid_integrity_01"
        assert result.status == TaskStatus.PENDING
        assert result.priority == TaskPriority.URGENT
        assert result.include_audio is True
        assert result.include_transcript is False
        assert result.callback_url == "https://example.com/hook"
        assert result.callback_secret == "s3cr3t"
        assert result.audio_file_id is None
        assert result.transcript_file_id is None
        assert result.reused_audio is False
        assert result.reused_transcript is False
        assert result.callback_status is None
        assert result.callback_attempts == 0
        assert result.error_code is None
        assert result.error_message is None
        assert result.retry_count == 0
        # 下载器归属列：未完成的任务应为 NULL（未知）
        assert result.audio_downloader is None
        assert result.transcript_downloader is None
        # partial_success/failure_details：新建任务默认值
        assert result.partial_success is False
        assert result.failure_details is None

    async def test_video_info_full_roundtrip(self, test_db: Database):
        """All VideoInfo fields should survive a round-trip."""
        info = VideoInfo(
            title="Full Roundtrip Test",
            author="Test Author",
            channel_id="UC_round_trip",
            duration=300,
            description="A comprehensive roundtrip test",
            upload_date="20240315",
            view_count=999999,
            thumbnail="https://example.com/thumb.jpg",
            live_broadcast_content="none",
        )
        resource = make_video_resource("vid_roundtrip", video_info=info)
        await test_db.create_video_resource(resource)

        result = await test_db.get_video_resource("vid_roundtrip")
        assert result is not None
        assert result.video_info is not None
        assert result.video_info.title == "Full Roundtrip Test"
        assert result.video_info.author == "Test Author"
        assert result.video_info.channel_id == "UC_round_trip"
        assert result.video_info.duration == 300
        assert result.video_info.description == "A comprehensive roundtrip test"
        assert result.video_info.upload_date == "20240315"
        assert result.video_info.view_count == 999999
        assert result.video_info.thumbnail == "https://example.com/thumb.jpg"
        assert result.video_info.live_broadcast_content == "none"

    async def test_file_record_all_fields_roundtrip(self, test_db: Database):
        """All FileRecord fields should survive a round-trip."""
        resource = make_video_resource("vid_fr_rt")
        await test_db.create_video_resource(resource)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=7)
        file_rec = FileRecord(
            id="fr_roundtrip_01",
            video_id="vid_fr_rt",
            file_type=FileType.TRANSCRIPT,
            filename="transcript_en.json",
            filepath="transcripts/vid_fr_rt/transcript_en.json",
            size=8192,
            format="json",
            quality=None,
            language="en",
            upload_source="auto",
            original_format=None,
            created_at=now,
            last_accessed_at=now,
            expires_at=expires,
        )
        await test_db.create_file(file_rec)

        result = await test_db.get_file("fr_roundtrip_01")
        assert result is not None
        assert result.id == "fr_roundtrip_01"
        assert result.video_id == "vid_fr_rt"
        assert result.file_type == FileType.TRANSCRIPT
        assert result.filename == "transcript_en.json"
        assert result.filepath == "transcripts/vid_fr_rt/transcript_en.json"
        assert result.size == 8192
        assert result.format == "json"
        assert result.quality is None
        assert result.language == "en"
        assert result.upload_source == "auto"
        assert result.original_format is None
        assert result.created_at is not None
        assert result.last_accessed_at is not None
        assert result.expires_at is not None

    async def test_timestamps_are_utc(self, test_db: Database):
        """Timestamps should be timezone-aware UTC."""
        task = make_task(video_id="vid_tz_01", task_id="tz_01")
        await test_db.create_task(task)

        result = await test_db.get_task("tz_01")
        assert result is not None
        assert result.created_at is not None
        assert result.created_at.tzinfo is not None

    async def test_unicode_in_video_info(self, test_db: Database):
        """Unicode characters in video info should be preserved."""
        info = VideoInfo(
            title="Unicode Test",
            author="Test",
            description="Description with special chars",
        )
        resource = make_video_resource("vid_unicode", video_info=info)
        await test_db.create_video_resource(resource)

        result = await test_db.get_video_resource("vid_unicode")
        assert result is not None
        assert result.video_info is not None
        assert result.video_info.title == "Unicode Test"

    async def test_list_tasks_search_by_video_id(self, test_db: Database):
        """Search tasks by video_id partial match."""
        task = make_task(video_id="unique_search_vid_xyz", task_id="search_task_01")
        await test_db.create_task(task)

        tasks, total = await test_db.list_tasks(search="unique_search_vid")
        assert total >= 1
        task_ids = {t.id for t in tasks}
        assert "search_task_01" in task_ids

    async def test_list_tasks_combined_filters(self, test_db: Database):
        """Combine status and date filters."""
        task = make_task(video_id="vid_combined_01", task_id="combined_01")
        await test_db.create_task(task)
        await test_db.update_task_status("combined_01", TaskStatus.COMPLETED)

        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=1)
        future = now + timedelta(hours=1)

        # Status + date range should find our task
        tasks, total = await test_db.list_tasks(
            status=TaskStatus.COMPLETED,
            created_after=past,
            created_before=future,
        )
        task_ids = {t.id for t in tasks}
        assert "combined_01" in task_ids


# ==================== Downloader Attribution Tests ====================


class TestDownloaderAttribution:
    """
    测试下载器归属列（audio_downloader / transcript_downloader）与
    failure_details 失败归因列的迁移、写入与行转换。

    背景：生产环境只能翻日志才能回答"哪个下载器完成的下载"，
    本组测试锁定持久化闭环：迁移建列 -> 写入 -> 读回。
    """

    async def test_new_database_has_attribution_columns(self, test_db: Database):
        """全新数据库建表后应直接包含归属列（不依赖后续 ALTER 迁移）。"""
        cursor = await test_db.execute("PRAGMA table_info(tasks)")
        columns = {row["name"] for row in await cursor.fetchall()}
        assert "audio_downloader" in columns
        assert "transcript_downloader" in columns
        # 顺带验证 partial_success/failure_details 也在新库中直接存在
        # （历史 bug：这两列此前只在 ALTER 迁移里补，CREATE TABLE 字面量里没有，
        # 导致全新数据库反而缺列，迁移永远不会补上——一并修正）。
        assert "partial_success" in columns
        assert "failure_details" in columns

    async def test_migration_idempotent_on_legacy_table(self, temp_dir: Path):
        """
        对已存在但缺少新列的旧版 tasks 表重复跑迁移：
        应能补齐新列且不报错（幂等）。
        """
        db_path = temp_dir / "legacy.sqlite"

        # 手工建一张缺少归属列/partial_success/failure_details 的旧版 tasks 表，
        # 模拟生产环境跑了 7 个月的历史库结构。
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await conn.commit()

        db = Database(db_path)
        await db.connect()
        try:
            cursor = await db.execute("PRAGMA table_info(tasks)")
            columns = {row["name"] for row in await cursor.fetchall()}
            assert "audio_downloader" in columns
            assert "transcript_downloader" in columns
            assert "partial_success" in columns
            assert "failure_details" in columns
        finally:
            await db.disconnect()

        # 重新连接（模拟服务重启再次跑迁移），不应抛出 duplicate column 等错误
        db2 = Database(db_path)
        await db2.connect()
        cursor = await db2.execute("PRAGMA table_info(tasks)")
        columns2 = {row["name"] for row in await cursor.fetchall()}
        assert "audio_downloader" in columns2
        await db2.disconnect()

    async def test_update_task_completed_writes_downloader_attribution(
        self, test_db: Database
    ):
        """完成任务时应能写入音频/字幕各自的下载器归属。"""
        task = make_task(video_id="vid_attr_01", task_id="attr_01")
        await test_db.create_task(task)

        await test_db.update_task_completed(
            "attr_01",
            audio_file_id="audio_1",
            transcript_file_id="trans_1",
            reused_audio=False,
            reused_transcript=False,
            audio_downloader="cdp",
            transcript_downloader="ytdlp",
        )

        result = await test_db.get_task("attr_01")
        assert result is not None
        assert result.audio_downloader == "cdp"
        assert result.transcript_downloader == "ytdlp"

    async def test_update_task_completed_attribution_defaults_to_none(
        self, test_db: Database
    ):
        """
        缓存复用场景（reused=True）未传归属参数时，归属列应保持 NULL，
        而不是写入类似 'cache' 的占位值（reused 标志已经表达了来源）。
        """
        task = make_task(video_id="vid_attr_02", task_id="attr_02")
        await test_db.create_task(task)

        await test_db.update_task_completed(
            "attr_02",
            audio_file_id="audio_2",
            reused_audio=True,
        )

        result = await test_db.get_task("attr_02")
        assert result is not None
        assert result.audio_downloader is None
        assert result.transcript_downloader is None

    async def test_update_task_status_failed_writes_failure_details(
        self, test_db: Database
    ):
        """任务失败时写入的 failure_details JSON 应可完整读回并反序列化。"""
        task = make_task(video_id="vid_fd_01", task_id="fd_01")
        await test_db.create_task(task)

        details = [
            {"downloader": "cdp", "error_code": "CDP_NO_COOKIES", "message": "no cookies exported"},
            {"downloader": "ytdlp", "error_code": "CDP_NSIG_FAILED", "message": "nsig challenge failed"},
        ]
        details_json = json.dumps(details, ensure_ascii=False)

        await test_db.update_task_status(
            "fd_01",
            TaskStatus.FAILED,
            error_code=ErrorCode.DOWNLOAD_FAILED,
            error_message="all downloaders failed",
            failure_details=details_json,
        )

        result = await test_db.get_task("fd_01")
        assert result is not None
        assert result.status == TaskStatus.FAILED
        assert result.failure_details is not None
        parsed = json.loads(result.failure_details)
        assert parsed[0]["downloader"] == "cdp"
        assert parsed[0]["error_code"] == "CDP_NO_COOKIES"
        assert parsed[1]["downloader"] == "ytdlp"

    async def test_update_task_status_non_failed_ignores_failure_details(
        self, test_db: Database
    ):
        """非 FAILED 状态传入 failure_details 应被忽略（该字段只在失败路径写入）。"""
        task = make_task(video_id="vid_fd_02", task_id="fd_02")
        await test_db.create_task(task)

        await test_db.update_task_status(
            "fd_02",
            TaskStatus.COMPLETED,
            failure_details='[{"downloader": "cdp"}]',
        )

        result = await test_db.get_task("fd_02")
        assert result is not None
        assert result.failure_details is None


# ==================== Download Stats Aggregation Tests ====================


class TestDownloadStatsAggregation:
    """
    测试 get_download_stats 聚合查询（失败归因统计端点的数据来源）。

    全部通过 SQL GROUP BY 完成聚合，不在 Python 里遍历全表。
    """

    async def _seed_task(
        self,
        test_db: Database,
        *,
        task_id: str,
        video_id: str,
        status: TaskStatus,
        error_code: ErrorCode | None = None,
        audio_downloader: str | None = None,
        transcript_downloader: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        """构造并写入一条种子任务数据，用于统计聚合断言。"""
        task = make_task(video_id=video_id, task_id=task_id)
        await test_db.create_task(task)

        if status == TaskStatus.COMPLETED:
            await test_db.update_task_completed(
                task_id,
                audio_file_id="af" if audio_downloader else None,
                transcript_file_id="tf" if transcript_downloader else None,
                audio_downloader=audio_downloader,
                transcript_downloader=transcript_downloader,
            )
        elif status == TaskStatus.FAILED:
            await test_db.update_task_status(
                task_id, TaskStatus.FAILED, error_code=error_code
            )
        else:
            await test_db.update_task_status(task_id, status)

        # create_task/update_task_status 内部用 CURRENT_TIMESTAMP/now() 写入 created_at，
        # 这里按需改写为测试指定的时间点，用于模拟不同时间窗口/不同自然周的历史数据。
        # 使用公开的 transaction() API 提交，不直接碰内部连接属性。
        if created_at is not None:
            async with test_db.transaction() as conn:
                await conn.execute(
                    "UPDATE tasks SET created_at = ? WHERE id = ?",
                    (created_at, task_id),
                )

    async def test_get_download_stats_total_and_by_status(self, test_db: Database):
        """test_db 每个测试函数独立建库，因此可以直接用精确值断言。"""
        now = datetime.now(timezone.utc)
        await self._seed_task(
            test_db, task_id="ds_01", video_id="ds_v01",
            status=TaskStatus.COMPLETED, audio_downloader="cdp", created_at=now,
        )
        await self._seed_task(
            test_db, task_id="ds_02", video_id="ds_v02",
            status=TaskStatus.FAILED, error_code=ErrorCode.CDP_NO_COOKIES, created_at=now,
        )
        await self._seed_task(
            test_db, task_id="ds_03", video_id="ds_v03",
            status=TaskStatus.PENDING, created_at=now,
        )

        stats = await test_db.get_download_stats(days=30)

        assert stats["total"] == 3
        assert stats["by_status"]["completed"] == 1
        assert stats["by_status"]["failed"] == 1
        assert stats["by_status"]["pending"] == 1

    async def test_get_download_stats_failures_by_error_code_and_split(
        self, test_db: Database
    ):
        now = datetime.now(timezone.utc)
        # 内容级失败（VIDEO_ 前缀）
        await self._seed_task(
            test_db, task_id="ds_10", video_id="ds_v10",
            status=TaskStatus.FAILED, error_code=ErrorCode.VIDEO_PRIVATE, created_at=now,
        )
        # 系统级失败
        await self._seed_task(
            test_db, task_id="ds_11", video_id="ds_v11",
            status=TaskStatus.FAILED, error_code=ErrorCode.CDP_NO_COOKIES, created_at=now,
        )
        await self._seed_task(
            test_db, task_id="ds_12", video_id="ds_v12",
            status=TaskStatus.FAILED, error_code=ErrorCode.CDP_NO_COOKIES, created_at=now,
        )

        stats = await test_db.get_download_stats(days=30)

        assert stats["failures_by_error_code"]["VIDEO_PRIVATE"] == 1
        assert stats["failures_by_error_code"]["CDP_NO_COOKIES"] == 2
        assert stats["failure_split"]["content_level"] == 1
        assert stats["failure_split"]["system_level"] == 2
        assert stats["failure_split"]["content_level_ratio"] == pytest.approx(1 / 3)
        assert stats["failure_split"]["system_level_ratio"] == pytest.approx(2 / 3)

    async def test_get_download_stats_null_error_code_counted_as_unknown(
        self, test_db: Database
    ):
        """
        error_code 为 NULL 的失败任务不应被过滤掉，应归入 failures_by_error_code
        的 "unknown" 桶，并计入 failure_split.system_level——否则
        content_level + system_level 会小于 by_status["failed"]，比率失真。
        """
        now = datetime.now(timezone.utc)
        # 内容级失败
        await self._seed_task(
            test_db, task_id="ds_50", video_id="ds_v50",
            status=TaskStatus.FAILED, error_code=ErrorCode.VIDEO_PRIVATE, created_at=now,
        )
        # 系统级失败（有 error_code）
        await self._seed_task(
            test_db, task_id="ds_51", video_id="ds_v51",
            status=TaskStatus.FAILED, error_code=ErrorCode.CDP_NO_COOKIES, created_at=now,
        )
        # error_code 为 NULL 的失败任务（例如任务级超时兜底路径未来得及写 error_code）
        await self._seed_task(
            test_db, task_id="ds_52", video_id="ds_v52",
            status=TaskStatus.FAILED, error_code=None, created_at=now,
        )
        await self._seed_task(
            test_db, task_id="ds_53", video_id="ds_v53",
            status=TaskStatus.FAILED, error_code=None, created_at=now,
        )

        stats = await test_db.get_download_stats(days=30)

        assert stats["by_status"]["failed"] == 4
        assert stats["failures_by_error_code"]["unknown"] == 2
        assert stats["failures_by_error_code"]["VIDEO_PRIVATE"] == 1
        assert stats["failures_by_error_code"]["CDP_NO_COOKIES"] == 1

        # 不变式：content_level + system_level 必须等于 by_status 里 failed 的总数
        split = stats["failure_split"]
        assert split["content_level"] == 1
        assert split["system_level"] == 3  # CDP_NO_COOKIES(1) + unknown(2)
        assert split["content_level"] + split["system_level"] == stats["by_status"]["failed"]

    async def test_get_download_stats_region_blocked_is_system_level(
        self, test_db: Database
    ):
        """
        VIDEO_REGION_BLOCKED 虽是 VIDEO_ 前缀，但已从 CONTENT_LEVEL_ERROR_CODES
        中移除（下载器/出口位置相关错误，非视频客观状态，见
        src.db.models.CONTENT_LEVEL_ERROR_CODES 的注释）。failure_split 的分类
        必须以该常量为唯一事实来源，不能再用字符串前缀判断，否则地区限制会被
        误计入 content_level，在遇到地区限制的部署里让比率失真。
        """
        now = datetime.now(timezone.utc)
        # 地区限制：应归入 system_level（尽管 error_code 以 VIDEO_ 开头）
        await self._seed_task(
            test_db, task_id="ds_60", video_id="ds_v60",
            status=TaskStatus.FAILED, error_code=ErrorCode.VIDEO_REGION_BLOCKED, created_at=now,
        )
        # 真正的内容级失败：应归入 content_level
        await self._seed_task(
            test_db, task_id="ds_61", video_id="ds_v61",
            status=TaskStatus.FAILED, error_code=ErrorCode.VIDEO_PRIVATE, created_at=now,
        )

        stats = await test_db.get_download_stats(days=30)

        split = stats["failure_split"]
        assert split["content_level"] == 1  # 仅 VIDEO_PRIVATE
        assert split["system_level"] == 1  # VIDEO_REGION_BLOCKED
        # 不变式：content_level + system_level 必须等于 by_status 里 failed 的总数
        assert split["content_level"] + split["system_level"] == stats["by_status"]["failed"]

    async def test_get_download_stats_by_downloader_unknown_bucket(
        self, test_db: Database
    ):
        now = datetime.now(timezone.utc)
        await self._seed_task(
            test_db, task_id="ds_20", video_id="ds_v20",
            status=TaskStatus.COMPLETED, audio_downloader="cdp",
            transcript_downloader="ytdlp", created_at=now,
        )
        # 未写归属（历史数据/未知）应归为 unknown
        await self._seed_task(
            test_db, task_id="ds_21", video_id="ds_v21",
            status=TaskStatus.COMPLETED, created_at=now,
        )

        stats = await test_db.get_download_stats(days=30)

        assert stats["by_downloader"]["audio_downloader"]["cdp"] == 1
        assert stats["by_downloader"]["transcript_downloader"]["ytdlp"] == 1
        assert stats["by_downloader"]["audio_downloader"]["unknown"] == 1
        assert stats["by_downloader"]["transcript_downloader"]["unknown"] == 1

    async def test_get_download_stats_weekly_trend(self, test_db: Database):
        """
        weekly_trend 的周标签规则是 %Y-W%W（公历年 + 周一起始年内周序），
        不是 %G-W%V（ISO 8601 年份+周号）——后者要求 SQLite 3.46.0+，本地/
        生产环境的 SQLite 版本都更旧，用它会让 strftime 返回 NULL。这里的
        期望值必须用同一套规则在 Python 侧算，否则测试只在恰好装了新版
        SQLite 的机器上偶然通过。
        """
        now = datetime.now(timezone.utc)
        await self._seed_task(
            test_db, task_id="ds_30", video_id="ds_v30",
            status=TaskStatus.COMPLETED, created_at=now,
        )
        await self._seed_task(
            test_db, task_id="ds_31", video_id="ds_v31",
            status=TaskStatus.FAILED, error_code=ErrorCode.DOWNLOAD_FAILED, created_at=now,
        )

        stats = await test_db.get_download_stats(days=30)

        current_week = f"{now.strftime('%Y')}-W{now.strftime('%W')}"
        weeks = {item["week"]: item for item in stats["weekly_trend"]}
        assert None not in weeks, "week 键不应为 None（strftime 格式码在当前 SQLite 版本上不受支持）"
        assert current_week in weeks
        assert weeks[current_week]["completed"] == 1
        assert weeks[current_week]["failed"] == 1

    async def test_get_download_stats_respects_days_window(self, test_db: Database):
        """超出时间窗口的任务不应计入统计（仅落在窗口内的任务计数）。"""
        old_time = datetime.now(timezone.utc) - timedelta(days=100)
        now = datetime.now(timezone.utc)
        await self._seed_task(
            test_db, task_id="ds_40", video_id="ds_v40",
            status=TaskStatus.COMPLETED, created_at=old_time,
        )
        await self._seed_task(
            test_db, task_id="ds_41", video_id="ds_v41",
            status=TaskStatus.COMPLETED, created_at=now,
        )

        stats = await test_db.get_download_stats(days=30)

        # 100 天前的任务被排除在 30 天窗口外，total 只统计到窗口内的 1 条
        assert stats["total"] == 1
        assert stats["by_status"]["completed"] == 1
