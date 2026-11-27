from pathlib import Path
import sqlite3

import pytest

from src.db.database import Database
from src.db.file_uniqueness_migration import migrate_audio_file_uniqueness


async def _add_audio(db: Database, file_id: str, video_id: str, quality: str, source: str, created: str, filepath: str) -> None:
    await db.execute(
        "INSERT INTO files (id, video_id, file_type, filename, filepath, quality, upload_source, created_at) "
        "VALUES (?, ?, 'audio', ?, ?, ?, ?, ?)",
        (file_id, video_id, file_id, filepath, quality, source, created),
    )


@pytest.mark.asyncio
async def test_migration_prefers_physical_then_manual_then_latest(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    try:
        await db.execute("DROP INDEX ux_files_video_audio")
        (tmp_path / "present.m4a").write_bytes(b"audio")
        await _add_audio(db, "missing-manual", "physical", "128", "manual", "2025-01-01", "missing.m4a")
        await _add_audio(db, "physical-auto", "physical", "256", "auto", "2020-01-01", "present.m4a")
        await _add_audio(db, "old-manual", "manual", "128", "manual", "2020-01-01", "missing1.m4a")
        await _add_audio(db, "new-auto", "manual", "256", "auto", "2025-01-01", "missing2.m4a")
        await _add_audio(db, "newest", "latest", "128", "auto", "2025-02-01", "missing3.m4a")
        await _add_audio(db, "older", "latest", "256", "auto", "2025-01-01", "missing4.m4a")
        await db.execute(
            "INSERT INTO tasks (id, video_id, video_url, audio_file_id) VALUES ('task', 'physical', 'url', 'missing-manual')"
        )
        await migrate_audio_file_uniqueness(db, tmp_path)
        for video_id, expected in (("physical", "physical-auto"), ("manual", "old-manual"), ("latest", "newest")):
            cursor = await db.execute("SELECT id FROM files WHERE video_id = ? AND file_type='audio'", (video_id,))
            assert (await cursor.fetchone())["id"] == expected
        cursor = await db.execute("SELECT audio_file_id FROM tasks WHERE id='task'")
        assert (await cursor.fetchone())["audio_file_id"] == "physical-auto"
        with pytest.raises(sqlite3.IntegrityError):
            await _add_audio(db, "duplicate", "physical", "320", "auto", "2026-01-01", "duplicate.m4a")
    finally:
        await db.disconnect()
