from pathlib import Path
from typing import Optional


async def migrate_audio_file_uniqueness(db, data_dir: Optional[Path] = None) -> None:
    """Deduplicate legacy audio rows and install the database uniqueness arbiter."""
    cursor = await db.execute(
        "SELECT * FROM files WHERE file_type = 'audio' ORDER BY video_id"
    )
    rows = await cursor.fetchall()
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row["video_id"], []).append(row)
    for video_id, duplicates in groups.items():
        if len(duplicates) < 2:
            continue
        def rank(row):
            physical = bool(data_dir and (data_dir / row["filepath"]).is_file())
            return (physical, row["upload_source"] == "manual", str(row["created_at"] or ""), row["id"])
        keeper = max(duplicates, key=rank)
        for row in duplicates:
            if row["id"] == keeper["id"]:
                continue
            await db.execute(
                "UPDATE tasks SET audio_file_id = ? WHERE audio_file_id = ?",
                (keeper["id"], row["id"]),
            )
            await db.execute("DELETE FROM files WHERE id = ?", (row["id"],))
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_files_video_audio "
        "ON files(video_id) WHERE file_type = 'audio'"
    )
