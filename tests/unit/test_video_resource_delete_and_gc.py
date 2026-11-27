import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.file_service import FileService


@pytest.mark.asyncio
async def test_cleanup_orphan_files_grace_dry_run_and_delete(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    transcript_dir = tmp_path / "transcript"
    audio_dir.mkdir()
    transcript_dir.mkdir()
    db = AsyncMock()
    db.get_file.return_value = None
    service = FileService(db, MagicMock(audio_dir=audio_dir, transcript_dir=transcript_dir))
    aged = audio_dir / f"{uuid4()}_orphan.mp3"
    recent = audio_dir / f"{uuid4()}_recent.mp3"
    non_uuid = audio_dir / "not-a-uuid_file.mp3"
    aged.write_bytes(b"old")
    recent.write_bytes(b"new")
    non_uuid.write_bytes(b"keep")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    os.utime(aged, (old_time, old_time))

    dry = await service.cleanup_orphan_files(grace_period_seconds=600, dry_run=True)
    assert dry == {"scanned": 2, "orphans_found": 1, "orphans_deleted": 0, "skipped_within_grace": 1}
    assert aged.exists()
    deleted = await service.cleanup_orphan_files(grace_period_seconds=600)
    assert deleted["orphans_deleted"] == 1
    assert not aged.exists()
    assert recent.exists()
    assert non_uuid.exists()


@pytest.mark.asyncio
async def test_cleanup_orphan_files_preserves_database_file(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    transcript_dir = tmp_path / "transcript"
    audio_dir.mkdir()
    transcript_dir.mkdir()
    file_id = str(uuid4())
    managed = audio_dir / f"{file_id}_managed.mp3"
    managed.write_bytes(b"managed")
    old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    os.utime(managed, (old_time, old_time))
    db = AsyncMock()
    db.get_file.return_value = object()
    service = FileService(db, MagicMock(audio_dir=audio_dir, transcript_dir=transcript_dir))

    result = await service.cleanup_orphan_files()

    assert result["orphans_found"] == 0
    assert managed.exists()
    db.get_file.assert_awaited_once_with(file_id)


@pytest.mark.asyncio
async def test_delete_video_resource_resolves_relative_path_from_data_dir(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    audio_dir = data_dir / "audio"
    audio_dir.mkdir(parents=True)
    stored = audio_dir / "resource.mp3"
    stored.write_bytes(b"audio")
    db = AsyncMock()
    db.get_files_by_video.return_value = [MagicMock(id="file-id", filepath="audio/resource.mp3")]
    service = FileService(db, MagicMock(audio_dir=audio_dir, transcript_dir=data_dir / "transcript"))
    service.data_dir = data_dir

    result = await service.delete_video_resource("video")

    assert result == {"files_deleted": 1, "files_missing": 0, "files_failed": 0}
    assert not stored.exists()
    db.delete_video_resource.assert_awaited_once_with("video")


@pytest.mark.asyncio
async def test_delete_video_resource_reports_missing_file(tmp_path: Path) -> None:
    db = AsyncMock()
    db.get_files_by_video.return_value = [MagicMock(id="file-id", filepath="audio/missing.mp3")]
    service = FileService(db, MagicMock(audio_dir=tmp_path / "audio", transcript_dir=tmp_path / "transcript"))
    service.data_dir = tmp_path

    result = await service.delete_video_resource("video")

    assert result["files_missing"] == 1
    assert result["files_deleted"] == 0
    assert result["files_failed"] == 0
    db.delete_video_resource.assert_awaited_once_with("video")


@pytest.mark.asyncio
async def test_delete_route_from_different_cwd_deletes_stored_relative_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api import video_resource_routes
    from src.config import Settings, get_settings
    from src.db.database import Database, get_database
    from src.db.models import FileType

    data_dir = tmp_path / "storage"
    settings = Settings(api_key="test-key", wecom_webhook_url="", data_dir=data_dir)
    settings.ensure_directories()
    db = Database(settings.db_path)
    await db.connect()
    video_id = "delete-test-video"
    await db.get_or_create_video_resource(video_id)
    service = FileService(db, settings)
    source = tmp_path / "source.mp3"
    source.write_bytes(b"audio")
    record = await service.create_file_record(video_id, FileType.AUDIO, source)
    stored = data_dir / record.filepath
    other_cwd = tmp_path / "different-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    app = FastAPI()
    app.include_router(video_resource_routes.router)
    video_resource_routes.set_file_service(service)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.delete(
                f"/api/v1/video-resources/{video_id}",
                headers={"X-API-Key": "test-key"},
            )
        assert response.status_code == 200
        assert response.json()["files_deleted"] == 1
        assert not stored.exists()
        assert not (other_cwd / record.filepath).exists()
    finally:
        video_resource_routes.set_file_service(None)  # type: ignore[arg-type]
        app.dependency_overrides.clear()
        await db.disconnect()
