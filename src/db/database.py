"""
SQLite database connection and operations.

Provides async database operations using aiosqlite.
Architecture: Video -> Files <- Task (video owns files, tasks reference files)
"""

import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiosqlite

from src.core.ip_ban_models import IPBanLevel
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
from src.utils.logger import logger


# ==================== SQLite DateTime Type Converters ====================
# 注册 datetime 类型转换器，让 SQLite 自动将 TIMESTAMP 列解析为 datetime 对象


def _adapt_datetime_iso(val: datetime) -> str:
    """
    将 datetime 对象转换为 ISO 8601 格式字符串（用于存储）。

    始终存储为 UTC 时间。
    """
    # 如果是 naive datetime，假设为 UTC
    if val.tzinfo is None:
        val = val.replace(tzinfo=timezone.utc)
    # 转换为 UTC 并格式化为 ISO 8601（带 'Z' 后缀表示 UTC）
    return val.astimezone(timezone.utc).isoformat()


def _convert_timestamp(val: bytes) -> datetime:
    """
    将 SQLite TIMESTAMP 字符串转换为 UTC datetime 对象（用于读取）。

    支持多种格式：
    - ISO 8601 with T: "2026-01-28T15:07:47.123456+00:00" 或 "2026-01-28T15:07:47Z"
    - ISO 8601 with space: "2026-01-28 15:07:47.123456+00:00"
    - SQLite 默认: "2026-01-28 15:07:47.123456" 或 "2026-01-28 15:07:47"

    返回带 UTC 时区信息的 datetime 对象。
    """
    val_str = val.decode("utf-8")

    # 优先尝试 fromisoformat（支持多种格式，包括带时区的）
    try:
        # 处理 'Z' 后缀（表示 UTC）
        if val_str.endswith("Z"):
            val_str = val_str[:-1] + "+00:00"
        # fromisoformat 支持 "T" 和空格分隔符
        # 但不直接支持空格+时区格式，需要替换为 T
        if " " in val_str and ("+" in val_str or val_str.endswith("Z")):
            # 格式: "2026-01-28 15:07:47.123456+00:00"
            # 替换第一个空格为 T
            val_str = val_str.replace(" ", "T", 1)

        dt = datetime.fromisoformat(val_str)
        # 如果解析成功但没有时区信息，手动添加 UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    # 降级：解析不带时区的格式，然后手动添加 UTC 时区
    try:
        # 支持微秒
        if "." in val_str:
            dt = datetime.strptime(val_str, "%Y-%m-%d %H:%M:%S.%f")
        else:
            dt = datetime.strptime(val_str, "%Y-%m-%d %H:%M:%S")
        # 标记为 UTC 时区
        return dt.replace(tzinfo=timezone.utc)
    except ValueError as e:
        logger.error(f"Failed to parse timestamp: {val_str}, error: {e}")
        # 最后降级：返回 Unix epoch（而不是当前时间，避免混淆）
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


# 注册类型转换器（模块级别，只注册一次）
sqlite3.register_adapter(datetime, _adapt_datetime_iso)
sqlite3.register_converter("TIMESTAMP", _convert_timestamp)


class Database:
    """Async SQLite database manager."""

    def __init__(self, db_path: Path):
        """
        Initialize database manager.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Establish database connection and create tables."""
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        self._connection.row_factory = aiosqlite.Row

        await self._create_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def disconnect(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database disconnected")

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """
        Context manager for database transactions.

        Yields:
            Database connection with transaction support.
        """
        if not self._connection:
            raise RuntimeError("Database not connected")

        try:
            yield self._connection
            await self._connection.commit()
        except Exception:
            await self._connection.rollback()
            raise

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        """
        Execute SQL statement.

        Args:
            sql: SQL statement.
            params: Query parameters.

        Returns:
            Cursor for the executed query.
        """
        if not self._connection:
            raise RuntimeError("Database not connected")
        return await self._connection.execute(sql, params)

    async def _run_migrations(self) -> None:
        """Run database migrations for schema updates."""
        # Check if tasks table exists
        cursor = await self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
        )
        tasks_table_exists = await cursor.fetchone()

        if tasks_table_exists:
            # Get existing columns
            cursor = await self.execute("PRAGMA table_info(tasks)")
            columns = await cursor.fetchall()
            column_names = {col["name"] for col in columns}

            # Migration 1: Add priority column
            if "priority" not in column_names:
                await self.execute(
                    "ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'"
                )
                logger.info("Migration: Added 'priority' column to tasks table")

            # Migration 2: Add partial_success column
            if "partial_success" not in column_names:
                await self.execute(
                    "ALTER TABLE tasks ADD COLUMN partial_success INTEGER DEFAULT 0"
                )
                logger.info("Migration: Added 'partial_success' column to tasks table")

            # Migration 3: Add failure_details column
            if "failure_details" not in column_names:
                await self.execute(
                    "ALTER TABLE tasks ADD COLUMN failure_details TEXT"
                )
                logger.info("Migration: Added 'failure_details' column to tasks table")

            # Migration 4/5: Add downloader attribution columns
            # 记录音频/字幕分别由哪个下载器最终产出，用于失败归因统计
            # （此前只能翻日志才能回答"哪个下载器完成的下载"）。NULL 表示未知/历史数据。
            if "audio_downloader" not in column_names:
                await self.execute(
                    "ALTER TABLE tasks ADD COLUMN audio_downloader TEXT"
                )
                logger.info("Migration: Added 'audio_downloader' column to tasks table")

            if "transcript_downloader" not in column_names:
                await self.execute(
                    "ALTER TABLE tasks ADD COLUMN transcript_downloader TEXT"
                )
                logger.info("Migration: Added 'transcript_downloader' column to tasks table")

        # Check if files table exists for manual upload migrations
        cursor = await self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='files'"
        )
        files_table_exists = await cursor.fetchone()

        if files_table_exists:
            cursor = await self.execute("PRAGMA table_info(files)")
            columns = await cursor.fetchall()
            column_names = {col["name"] for col in columns}

            if "upload_source" not in column_names:
                await self.execute(
                    "ALTER TABLE files ADD COLUMN upload_source TEXT DEFAULT 'auto'"
                )
                logger.info("Migration: Added 'upload_source' column to files table")

            if "original_format" not in column_names:
                await self.execute(
                    "ALTER TABLE files ADD COLUMN original_format TEXT"
                )
                logger.info("Migration: Added 'original_format' column to files table")

            # Backfill manual uploads when original_format is present
            await self.execute(
                """
                UPDATE files
                SET upload_source = 'manual'
                WHERE (upload_source IS NULL OR upload_source = '' OR upload_source = 'auto')
                AND original_format IS NOT NULL
                """
            )

        # Migration 4: Add event_type column to ip_ban_history (existing table
        # created before IP ban persistence feature landed only had episode-style
        # columns; event_type turns it into an append-only event log so
        # append_ip_ban_history can distinguish triggered/upgraded/downgraded/
        # recovered/restored events without overloading unrelated columns).
        cursor = await self.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ip_ban_history'"
        )
        ip_ban_history_exists = await cursor.fetchone()

        if ip_ban_history_exists:
            cursor = await self.execute("PRAGMA table_info(ip_ban_history)")
            columns = await cursor.fetchall()
            column_names = {col["name"] for col in columns}

            if "event_type" not in column_names:
                await self.execute(
                    "ALTER TABLE ip_ban_history ADD COLUMN event_type TEXT NOT NULL DEFAULT 'triggered'"
                )
                logger.info(
                    "Migration: Added 'event_type' column to ip_ban_history table"
                )

        # Create IP ban tables if not exist
        await self._create_ip_ban_tables()

    async def _create_ip_ban_tables(self) -> None:
        """Create IP ban related tables."""
        # IP ban status table (single row)
        await self.execute("""
            CREATE TABLE IF NOT EXISTS ip_ban_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_level TEXT NOT NULL DEFAULT 'normal',
                banned_at TIMESTAMP,
                last_attempt_at TIMESTAMP,
                failed_attempts INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Initialize default row if not exists
        await self.execute("""
            INSERT OR IGNORE INTO ip_ban_status (id, current_level)
            VALUES (1, 'normal')
        """)

        # IP ban history table（追加写入的事件日志：每次触发/升级/降级/恢复/
        # 启动恢复都插入一条新记录，而不是原地更新一条"熔断episode"记录）
        await self.execute("""
            CREATE TABLE IF NOT EXISTS ip_ban_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL DEFAULT 'triggered',
                ban_level TEXT NOT NULL,
                trigger_source TEXT,
                trigger_task_id TEXT,
                trigger_downloader TEXT,
                trigger_error TEXT,
                banned_at TIMESTAMP NOT NULL,
                recovered_at TIMESTAMP,
                duration_seconds INTEGER,
                probe_count INTEGER DEFAULT 0,
                recovery_method TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_ip_ban_history_banned_at "
            "ON ip_ban_history(banned_at)"
        )
        await self.execute(
            "CREATE INDEX IF NOT EXISTS idx_ip_ban_history_ban_level "
            "ON ip_ban_history(ban_level)"
        )

    async def _create_tables(self) -> None:
        """Create database tables if not exist."""
        async with self.transaction():
            # Run migrations before creating tables
            await self._run_migrations()
            # Video resources table (core entity)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS video_resources (
                    video_id TEXT PRIMARY KEY,
                    video_info TEXT,
                    has_native_transcript INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Files table (indexed by video_id, not task_id)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    size INTEGER,
                    format TEXT,
                    quality TEXT,
                    language TEXT,
                    upload_source TEXT DEFAULT 'auto',
                    original_format TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    FOREIGN KEY (video_id) REFERENCES video_resources(video_id),
                    UNIQUE(video_id, file_type, quality, language)
                )
            """)

            # Tasks table (request entity, references files)
            await self.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    include_audio INTEGER DEFAULT 1,
                    include_transcript INTEGER DEFAULT 1,
                    audio_file_id TEXT,
                    transcript_file_id TEXT,
                    reused_audio INTEGER DEFAULT 0,
                    reused_transcript INTEGER DEFAULT 0,
                    audio_downloader TEXT,
                    transcript_downloader TEXT,
                    callback_url TEXT,
                    callback_secret TEXT,
                    callback_status TEXT,
                    callback_attempts INTEGER DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    partial_success INTEGER DEFAULT 0,
                    failure_details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (video_id) REFERENCES video_resources(video_id),
                    FOREIGN KEY (audio_file_id) REFERENCES files(id),
                    FOREIGN KEY (transcript_file_id) REFERENCES files(id)
                )
            """)

            # Create indexes
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_video_resources_updated ON video_resources(updated_at)"
            )
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_video_id ON files(video_id)"
            )
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_expires ON files(expires_at)"
            )
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_last_accessed ON files(last_accessed_at)"
            )
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
            )
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)"
            )
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_video_id ON tasks(video_id)"
            )
            await self.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)"
            )

    # ==================== Video Resource Operations ====================

    async def get_video_resource(self, video_id: str) -> Optional[VideoResource]:
        """
        Get video resource by video ID.

        Args:
            video_id: YouTube video ID.

        Returns:
            VideoResource or None if not found.
        """
        cursor = await self.execute(
            "SELECT * FROM video_resources WHERE video_id = ?", (video_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_video_resource(row) if row else None

    async def create_video_resource(self, resource: VideoResource) -> None:
        """
        Create a new video resource record.

        Args:
            resource: VideoResource object to create.
        """
        video_info_json = (
            json.dumps(resource.video_info.to_dict()) if resource.video_info else None
        )
        now = datetime.now(timezone.utc)

        async with self.transaction():
            await self.execute(
                """
                INSERT INTO video_resources (
                    video_id, video_info, has_native_transcript, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    resource.video_id,
                    video_info_json,
                    1 if resource.has_native_transcript else (0 if resource.has_native_transcript is False else None),
                    resource.created_at or now,
                    resource.updated_at or now,
                ),
            )
        logger.debug(f"Video resource created: {resource.video_id}")

    async def update_video_resource(
        self,
        video_id: str,
        video_info: Optional[VideoInfo] = None,
        has_native_transcript: Optional[bool] = None,
    ) -> None:
        """
        Update video resource information.

        Args:
            video_id: YouTube video ID.
            video_info: Video metadata to update.
            has_native_transcript: Whether video has native subtitles.
        """
        now = datetime.now(timezone.utc)
        updates = ["updated_at = ?"]
        params: list[Any] = [now]

        if video_info is not None:
            updates.append("video_info = ?")
            params.append(json.dumps(video_info.to_dict()))

        if has_native_transcript is not None:
            updates.append("has_native_transcript = ?")
            params.append(1 if has_native_transcript else 0)

        params.append(video_id)

        async with self.transaction():
            await self.execute(
                f"UPDATE video_resources SET {', '.join(updates)} WHERE video_id = ?",
                tuple(params),
            )
        logger.debug(f"Video resource updated: {video_id}")

    async def get_or_create_video_resource(self, video_id: str) -> VideoResource:
        """
        Get existing video resource or create a new one.

        Args:
            video_id: YouTube video ID.

        Returns:
            VideoResource object.
        """
        existing = await self.get_video_resource(video_id)
        if existing:
            return existing

        now = datetime.now(timezone.utc)
        resource = VideoResource(
            video_id=video_id,
            created_at=now,
            updated_at=now,
        )
        await self.create_video_resource(resource)
        return resource

    async def list_video_resources(
        self,
        search: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        列出视频资源，支持搜索和分页。

        Args:
            search: 搜索关键词（匹配 video_id 或标题）
            limit: 每页数量
            offset: 偏移量

        Returns:
            元组 (资源列表, 总数)
            资源列表中每项包含：
            - video_id: 视频 ID
            - video_info: 视频信息字典
            - has_native_transcript: 是否有原生字幕
            - audio_count: 音频文件数
            - transcript_count: 字幕文件数
            - upload_source: 上传来源 (manual/auto)
            - created_at: 创建时间
            - updated_at: 更新时间
        """
        # 构建查询条件
        where_clause = "WHERE 1=1"
        params: list[Any] = []

        if search:
            where_clause += " AND (vr.video_id LIKE ? OR json_extract(vr.video_info, '$.title') LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern])

        # 获取总数
        count_sql = f"""
            SELECT COUNT(*)
            FROM video_resources vr
            {where_clause}
        """
        count_cursor = await self.execute(count_sql, tuple(params))
        count_row = await count_cursor.fetchone()
        total = count_row[0] if count_row else 0  # type: ignore[index]

        # 获取分页数据（使用 LEFT JOIN 聚合文件统计）
        params.extend([limit, offset])
        query_sql = f"""
            SELECT
                vr.video_id,
                vr.video_info,
                vr.has_native_transcript,
                vr.created_at,
                vr.updated_at,
                COUNT(CASE WHEN f.file_type = 'audio' THEN 1 END) as audio_count,
                COUNT(CASE WHEN f.file_type = 'transcript' THEN 1 END) as transcript_count,
                MAX(CASE WHEN f.upload_source = 'manual' THEN 'manual' ELSE 'auto' END) as upload_source
            FROM video_resources vr
            LEFT JOIN files f ON vr.video_id = f.video_id
            {where_clause}
            GROUP BY vr.video_id
            ORDER BY vr.updated_at DESC
            LIMIT ? OFFSET ?
        """
        cursor = await self.execute(query_sql, tuple(params))
        rows = await cursor.fetchall()

        # 转换为字典列表
        resources = []
        for row in rows:
            video_info_dict = None
            if row["video_info"]:
                video_info_dict = json.loads(row["video_info"])

            has_native = row["has_native_transcript"]
            has_native_transcript = None
            if has_native is not None:
                has_native_transcript = bool(has_native)

            # 将 datetime 转换为 ISO 格式字符串以便序列化
            # SQLite 可能返回字符串或 datetime 对象
            created_at_val = row["created_at"]
            updated_at_val = row["updated_at"]

            if created_at_val:
                created_at_str = created_at_val.isoformat() if hasattr(created_at_val, 'isoformat') else str(created_at_val)
            else:
                created_at_str = None

            if updated_at_val:
                updated_at_str = updated_at_val.isoformat() if hasattr(updated_at_val, 'isoformat') else str(updated_at_val)
            else:
                updated_at_str = None

            resources.append({
                "video_id": row["video_id"],
                "video_info": video_info_dict,
                "has_native_transcript": has_native_transcript,
                "audio_count": row["audio_count"],
                "transcript_count": row["transcript_count"],
                "upload_source": row["upload_source"],
                "created_at": created_at_str,
                "updated_at": updated_at_str,
            })

        logger.debug(f"Listed {len(resources)} video resources (total: {total})")
        return resources, total

    async def get_video_resource_detail(self, video_id: str) -> Optional[dict[str, Any]]:
        """
        获取视频资源完整信息。

        Args:
            video_id: YouTube 视频 ID

        Returns:
            包含视频信息、文件列表、任务历史的字典，或 None（如果不存在）
        """
        # 获取视频资源基本信息
        resource = await self.get_video_resource(video_id)
        if not resource:
            return None

        # 获取关联的文件列表
        files = await self.get_files_by_video(video_id)

        # 获取相关任务历史（最近 10 条）
        cursor = await self.execute(
            """
            SELECT * FROM tasks
            WHERE video_id = ?
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (video_id,),
        )
        task_rows = await cursor.fetchall()
        tasks = [self._row_to_task(row) for row in task_rows]

        # 构建返回结构（将 datetime 转换为 ISO 格式字符串）
        # 辅助函数：安全地转换 datetime 为字符串
        def to_iso_string(dt):
            if not dt:
                return None
            return dt.isoformat() if hasattr(dt, 'isoformat') else str(dt)

        result = {
            "video_id": resource.video_id,
            "video_info": resource.video_info.to_dict() if resource.video_info else None,
            "has_native_transcript": resource.has_native_transcript,
            "created_at": to_iso_string(resource.created_at),
            "updated_at": to_iso_string(resource.updated_at),
            "files": [
                {
                    "id": f.id,
                    "file_type": f.file_type.value,
                    "filename": f.filename,
                    "filepath": f.filepath,
                    "size": f.size,
                    "format": f.format,
                    "quality": f.quality,
                    "language": f.language,
                    "upload_source": f.upload_source,
                    "original_format": f.original_format,
                    "created_at": to_iso_string(f.created_at),
                }
                for f in files
            ],
            "recent_tasks": [
                {
                    "id": t.id,
                    "status": t.status.value,
                    "priority": t.priority.value,
                    "include_audio": t.include_audio,
                    "include_transcript": t.include_transcript,
                    "reused_audio": t.reused_audio,
                    "reused_transcript": t.reused_transcript,
                    "error_code": t.error_code.value if t.error_code else None,
                    "error_message": t.error_message,
                    "created_at": to_iso_string(t.created_at),
                    "completed_at": to_iso_string(t.completed_at),
                }
                for t in tasks
            ],
        }

        logger.debug(f"Fetched video resource detail: {video_id}")
        return result

    async def delete_video_resource(self, video_id: str) -> list[str]:
        """
        删除视频资源（使用事务）。

        级联删除 files 表记录，保留 tasks 表记录作为审计日志。

        Args:
            video_id: YouTube 视频 ID

        Returns:
            被删除文件的路径列表（用于后续物理删除）
        """
        # 先获取所有关联的文件路径
        files = await self.get_files_by_video(video_id)
        file_paths = [f.filepath for f in files]

        async with self.transaction():
            # 删除关联的文件记录
            await self.execute(
                "DELETE FROM files WHERE video_id = ?",
                (video_id,)
            )

            # 删除视频资源记录
            await self.execute(
                "DELETE FROM video_resources WHERE video_id = ?",
                (video_id,)
            )

            # 注意：不删除 tasks 表记录，保留作为审计日志

        logger.info(f"Deleted video resource: {video_id} (removed {len(file_paths)} files)")
        return file_paths

    # ==================== File Operations ====================

    async def create_file(self, file: FileRecord) -> None:
        """
        Create a new file record.

        Args:
            file: File record to create.
        """
        async with self.transaction():
            await self.execute(
                """
                INSERT INTO files (
                    id, video_id, file_type, filename, filepath, size, format,
                    quality, language, upload_source, original_format,
                    created_at, last_accessed_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file.id,
                    file.video_id,
                    file.file_type.value,
                    file.filename,
                    file.filepath,
                    file.size,
                    file.format,
                    file.quality,
                    file.language,
                    file.upload_source,
                    file.original_format,
                    file.created_at or datetime.now(timezone.utc),
                    file.last_accessed_at,
                    file.expires_at,
                ),
            )
        logger.debug(f"File record created: {file.id} ({file.file_type.value})")

    async def get_file(self, file_id: str) -> Optional[FileRecord]:
        """
        Get file by ID.

        Args:
            file_id: File UUID.

        Returns:
            FileRecord or None if not found.
        """
        cursor = await self.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = await cursor.fetchone()
        return self._row_to_file(row) if row else None

    async def get_file_by_video(
        self,
        video_id: str,
        file_type: FileType,
        quality: Optional[str] = None,
        language: Optional[str] = None,
    ) -> Optional[FileRecord]:
        """
        Get file by video ID and type.

        Args:
            video_id: YouTube video ID.
            file_type: Type of file (audio/transcript).
            quality: Audio quality (optional).
            language: Transcript language (optional).

        Returns:
            FileRecord or None if not found.
        """
        # Build query based on parameters
        sql = "SELECT * FROM files WHERE video_id = ? AND file_type = ?"
        params: list[Any] = [video_id, file_type.value]

        if quality is not None:
            sql += " AND quality = ?"
            params.append(quality)
        else:
            sql += " AND (quality IS NULL OR quality = '')"

        if language is not None:
            sql += " AND language = ?"
            params.append(language)

        cursor = await self.execute(sql, tuple(params))
        row = await cursor.fetchone()
        return self._row_to_file(row) if row else None

    async def get_files_by_video(self, video_id: str) -> list[FileRecord]:
        """
        Get all files for a video.

        Args:
            video_id: YouTube video ID.

        Returns:
            List of file records.
        """
        cursor = await self.execute(
            "SELECT * FROM files WHERE video_id = ?", (video_id,)
        )
        rows = await cursor.fetchall()
        return [self._row_to_file(row) for row in rows]

    async def update_file_access_time(self, file_id: str) -> None:
        """
        Update file last access time.

        Args:
            file_id: File UUID.
        """
        async with self.transaction():
            await self.execute(
                "UPDATE files SET last_accessed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc), file_id),
            )

    async def get_expired_files(self, cutoff_time: datetime) -> list[FileRecord]:
        """
        Get files that haven't been accessed since cutoff time.

        Args:
            cutoff_time: Cutoff datetime for last access.

        Returns:
            List of expired file records.
        """
        cursor = await self.execute(
            """
            SELECT * FROM files
            WHERE last_accessed_at < ? OR (last_accessed_at IS NULL AND created_at < ?)
            """,
            (cutoff_time, cutoff_time),
        )
        rows = await cursor.fetchall()
        return [self._row_to_file(row) for row in rows]

    async def delete_file(self, file_id: str) -> None:
        """
        Delete file record.

        Args:
            file_id: File UUID.
        """
        async with self.transaction():
            await self.execute("DELETE FROM files WHERE id = ?", (file_id,))
        logger.debug(f"File record deleted: {file_id}")

    # ==================== Task Operations ====================

    async def create_task(self, task: Task) -> None:
        """
        Create a new task in database.

        Args:
            task: Task object to create.
        """
        async with self.transaction():
            await self.execute(
                """
                INSERT INTO tasks (
                    id, video_id, video_url, status, priority,
                    include_audio, include_transcript,
                    audio_file_id, transcript_file_id,
                    reused_audio, reused_transcript,
                    callback_url, callback_secret, callback_status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.video_id,
                    task.video_url,
                    task.status.value,
                    task.priority.value,
                    1 if task.include_audio else 0,
                    1 if task.include_transcript else 0,
                    task.audio_file_id,
                    task.transcript_file_id,
                    1 if task.reused_audio else 0,
                    1 if task.reused_transcript else 0,
                    task.callback_url,
                    task.callback_secret,
                    task.callback_status.value if task.callback_status else None,
                    task.created_at or datetime.now(timezone.utc),
                ),
            )
        logger.debug(f"Task created: {task.id}")

    async def get_task(self, task_id: str) -> Optional[Task]:
        """
        Get task by ID.

        Args:
            task_id: Task UUID.

        Returns:
            Task object or None if not found.
        """
        cursor = await self.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return self._row_to_task(row) if row else None

    async def get_active_task_by_video(self, video_id: str) -> Optional[Task]:
        """
        Find the most recent active (pending/downloading) task by video ID.

        注意：同一视频允许存在多条活跃任务（如先建了一个 audio 任务，后来又建了一个
        transcript 任务），本方法只返回最新一条。需要遍历全部活跃任务做覆盖判断的
        场景（如 TaskService 的去重覆盖校验），请用 get_active_tasks_by_video。

        Args:
            video_id: YouTube video ID.

        Returns:
            Task object or None if not found.
        """
        cursor = await self.execute(
            """
            SELECT * FROM tasks
            WHERE video_id = ? AND status IN ('pending', 'downloading')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (video_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_task(row) if row else None

    async def get_active_tasks_by_video(self, video_id: str) -> list[Task]:
        """
        Find all active (pending/downloading) tasks by video ID.

        与 get_active_task_by_video 的区别：后者只返回最新一条，可能漏掉更早创建、
        仍在跑但请求范围不同的活跃任务（例如旧的 audio-only 任务 + 新的
        transcript-only 任务同时活跃时，只看最新一条会让覆盖判断出现假阴性，
        导致重复创建任务）。调用方需要遍历全部活跃任务做覆盖判断时应使用本方法。

        Args:
            video_id: YouTube video ID.

        Returns:
            按创建时间倒序排列的活跃任务列表，没有则返回空列表。
        """
        cursor = await self.execute(
            """
            SELECT * FROM tasks
            WHERE video_id = ? AND status IN ('pending', 'downloading')
            ORDER BY created_at DESC
            """,
            (video_id,),
        )
        rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        search: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        """
        List tasks with pagination and filters.

        Args:
            status: Filter by status.
            search: Search keyword (matches video_id or video title from video_info).
            created_after: Filter tasks created after this datetime.
            created_before: Filter tasks created before this datetime.
            limit: Maximum number of results.
            offset: Number of results to skip.

        Returns:
            Tuple of (tasks list, total count).
        """
        where_clause = "WHERE 1=1"
        params: list[Any] = []

        if status:
            where_clause += " AND status = ?"
            params.append(status.value)

        if search:
            # 搜索 video_id 或从 video_resources 表中获取标题
            where_clause += """
                AND (
                    tasks.video_id LIKE ?
                    OR tasks.video_id IN (
                        SELECT video_id FROM video_resources
                        WHERE json_extract(video_info, '$.title') LIKE ?
                    )
                )
            """
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern])

        if created_after:
            where_clause += " AND created_at >= ?"
            params.append(created_after)

        if created_before:
            where_clause += " AND created_at <= ?"
            params.append(created_before)

        count_cursor = await self.execute(
            f"SELECT COUNT(*) FROM tasks {where_clause}", tuple(params)
        )
        count_row = await count_cursor.fetchone()
        total = count_row[0] if count_row else 0  # type: ignore[index]

        params.extend([limit, offset])
        cursor = await self.execute(
            f"""
            SELECT * FROM tasks
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )

        rows = await cursor.fetchall()
        tasks = [self._row_to_task(row) for row in rows]

        logger.debug(f"Listed {len(tasks)} tasks (total: {total}, filters: status={status}, search={search})")
        return tasks, total

    async def get_pending_tasks(self, limit: int = 10, offset: int = 0) -> list[Task]:
        """
        Get pending tasks ordered by creation time.

        offset 参数用于分批拉取（配合 TaskService.restore_pending_tasks 分页
        恢复全部 pending 任务，避免单次查询硬编码上限导致堆积任务被静默丢弃）。
        ORDER BY 追加 id 作为次级排序键，保证 created_at 相同时跨页结果依然
        稳定、不重复、不遗漏。

        Args:
            limit: Maximum number of tasks to return.
            offset: Number of tasks to skip（用于分页）。

        Returns:
            List of pending tasks.
        """
        cursor = await self.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_task(row) for row in rows]

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        error_code: Optional[ErrorCode] = None,
        error_message: Optional[str] = None,
        failure_details: Optional[str] = None,
    ) -> None:
        """
        Update task status.

        Args:
            task_id: Task UUID.
            status: New status.
            error_code: Error code if failed.
            error_message: Error message if failed.
            failure_details: 失败归因详情（JSON 字符串），记录降级链中每个下载器的
                尝试结果（下载器名/error_code/message）。仅在 status=FAILED 时写入，
                其他状态传入会被忽略。
        """
        now = datetime.now(timezone.utc)

        async with self.transaction():
            if status == TaskStatus.DOWNLOADING:
                await self.execute(
                    "UPDATE tasks SET status = ?, started_at = ? WHERE id = ?",
                    (status.value, now, task_id),
                )
            elif status == TaskStatus.COMPLETED:
                await self.execute(
                    "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?",
                    (status.value, now, task_id),
                )
            elif status == TaskStatus.FAILED:
                await self.execute(
                    """
                    UPDATE tasks
                    SET status = ?, error_code = ?, error_message = ?,
                        failure_details = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (
                        status.value,
                        error_code.value if error_code else None,
                        error_message,
                        failure_details,
                        now,
                        task_id,
                    ),
                )
            else:
                await self.execute(
                    "UPDATE tasks SET status = ? WHERE id = ?",
                    (status.value, task_id),
                )

        logger.debug(f"Task {task_id} status updated to {status.value}")

    async def update_task_completed(
        self,
        task_id: str,
        audio_file_id: Optional[str] = None,
        transcript_file_id: Optional[str] = None,
        reused_audio: bool = False,
        reused_transcript: bool = False,
        audio_downloader: Optional[str] = None,
        transcript_downloader: Optional[str] = None,
    ) -> None:
        """
        Update task as completed with file references.

        Args:
            task_id: Task UUID.
            audio_file_id: Audio file UUID (may be None).
            transcript_file_id: Transcript file UUID (may be None).
            reused_audio: Whether audio file was reused.
            reused_transcript: Whether transcript file was reused.
            audio_downloader: 产出音频文件的下载器名称（复用缓存时应为 None，
                由 reused_audio 标志表达来源，不写占位值）。
            transcript_downloader: 产出字幕文件的下载器名称（同上）。
        """
        now = datetime.now(timezone.utc)

        async with self.transaction():
            await self.execute(
                """
                UPDATE tasks
                SET status = ?, audio_file_id = ?, transcript_file_id = ?,
                    reused_audio = ?, reused_transcript = ?,
                    audio_downloader = ?, transcript_downloader = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    TaskStatus.COMPLETED.value,
                    audio_file_id,
                    transcript_file_id,
                    1 if reused_audio else 0,
                    1 if reused_transcript else 0,
                    audio_downloader,
                    transcript_downloader,
                    now,
                    task_id,
                ),
            )

        logger.info(f"Task {task_id} completed")

    async def increment_retry_count(self, task_id: str) -> int:
        """
        Increment task retry count.

        Args:
            task_id: Task UUID.

        Returns:
            New retry count.
        """
        async with self.transaction():
            await self.execute(
                "UPDATE tasks SET retry_count = retry_count + 1, status = 'pending' WHERE id = ?",
                (task_id,),
            )
            cursor = await self.execute(
                "SELECT retry_count FROM tasks WHERE id = ?", (task_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def reset_downloading_tasks(self) -> int:
        """
        Reset all downloading tasks to pending (for recovery after restart).

        Returns:
            Number of tasks reset.
        """
        async with self.transaction():
            cursor = await self.execute(
                "UPDATE tasks SET status = 'pending' WHERE status = 'downloading'"
            )
            count = cursor.rowcount
            if count > 0:
                logger.warning(f"Reset {count} downloading tasks to pending")
            return count

    async def get_queue_position(self, task_id: str) -> int:
        """
        Get task position in queue.

        Args:
            task_id: Task UUID.

        Returns:
            Position in queue (1-based), 0 if not in queue.
        """
        cursor = await self.execute(
            """
            SELECT COUNT(*) + 1 FROM tasks
            WHERE status = 'pending'
            AND created_at < (SELECT created_at FROM tasks WHERE id = ?)
            """,
            (task_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    # ==================== Callback Operations ====================

    async def update_callback_status(
        self,
        task_id: str,
        status: CallbackStatus,
        attempts: Optional[int] = None,
    ) -> None:
        """
        Update task callback status.

        Args:
            task_id: Task UUID.
            status: Callback status.
            attempts: Number of callback attempts.
        """
        async with self.transaction():
            if attempts is not None:
                await self.execute(
                    """
                    UPDATE tasks
                    SET callback_status = ?, callback_attempts = ?
                    WHERE id = ?
                    """,
                    (status.value, attempts, task_id),
                )
            else:
                await self.execute(
                    "UPDATE tasks SET callback_status = ? WHERE id = ?",
                    (status.value, task_id),
                )

    # ==================== Cleanup Operations ====================

    async def delete_expired_tasks(self, cutoff_time: datetime) -> int:
        """
        Delete expired tasks (completed tasks older than cutoff).

        Args:
            cutoff_time: Cutoff datetime.

        Returns:
            Number of tasks deleted.
        """
        async with self.transaction():
            cursor = await self.execute(
                """
                DELETE FROM tasks
                WHERE status = 'completed' AND completed_at < ?
                """,
                (cutoff_time,),
            )
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Deleted {count} expired tasks")
            return count

    async def delete_orphan_video_resources(self) -> int:
        """
        Delete video resources with no files.

        Returns:
            Number of resources deleted.
        """
        async with self.transaction():
            cursor = await self.execute(
                """
                DELETE FROM video_resources
                WHERE video_id NOT IN (SELECT DISTINCT video_id FROM files)
                AND (video_info IS NULL OR video_info = '')
                AND has_native_transcript IS NULL
                """
            )
            count = cursor.rowcount
            if count > 0:
                logger.info(f"Deleted {count} orphan video resources")
            return count

    # ==================== Statistics ====================

    async def get_queue_stats(self) -> dict[str, int]:
        """
        Get queue statistics.

        Returns:
            Dictionary with pending and downloading counts.
        """
        cursor = await self.execute(
            """
            SELECT status, COUNT(*) as count
            FROM tasks
            WHERE status IN ('pending', 'downloading')
            GROUP BY status
            """
        )
        rows = await cursor.fetchall()

        stats = {"pending": 0, "downloading": 0}
        for row in rows:
            stats[row["status"]] = row["count"]

        return stats

    async def get_resource_stats(self) -> dict[str, int]:
        """
        Get resource statistics.

        Returns:
            Dictionary with video, file, and task counts.
        """
        video_cursor = await self.execute("SELECT COUNT(*) FROM video_resources")
        video_row = await video_cursor.fetchone()
        video_count = video_row[0] if video_row else 0  # type: ignore[index]

        file_cursor = await self.execute("SELECT COUNT(*) FROM files")
        file_row = await file_cursor.fetchone()
        file_count = file_row[0] if file_row else 0  # type: ignore[index]

        task_cursor = await self.execute("SELECT COUNT(*) FROM tasks")
        task_row = await task_cursor.fetchone()
        task_count = task_row[0] if task_row else 0  # type: ignore[index]

        return {
            "videos": video_count,
            "files": file_count,
            "tasks": task_count,
        }

    async def get_task_stats(self) -> dict[str, int]:
        """
        获取任务统计信息（按状态分组）。

        Returns:
            包含各状态任务数量的字典。
        """
        cursor = await self.execute(
            """
            SELECT status, COUNT(*) as count
            FROM tasks
            GROUP BY status
            """
        )
        rows = await cursor.fetchall()

        # 初始化所有状态为 0
        stats = {
            "total": 0,
            "pending": 0,
            "downloading": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }

        # 填充实际统计
        for row in rows:
            status = row["status"]
            count = row["count"]
            if status in stats:
                stats[status] = count
            stats["total"] += count

        return stats

    async def get_download_stats(self, days: int = 30) -> dict[str, Any]:
        """
        获取下载失败归因统计（供 GET /api/v1/stats/downloads 端点使用）。

        聚合最近 N 天的任务数据：状态分布、失败 error_code 分布、
        内容级（VIDEO_* 前缀）vs 系统级失败拆分、音频/字幕下载器归属分布、
        按周（自然周，非严格 ISO 8601 周）的完成/失败趋势。全部通过 SQL
        GROUP BY 聚合，不在 Python 中遍历全表，避免任务量增长后端点变慢。

        Args:
            days: 统计时间窗口（天数）。

        Returns:
            聚合结果字典，字段：total / by_status / failures_by_error_code /
            failure_split / by_downloader / weekly_trend。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        # 1. 状态分布 + 总数
        status_cursor = await self.execute(
            """
            SELECT status, COUNT(*) as count
            FROM tasks
            WHERE created_at >= ?
            GROUP BY status
            """,
            (cutoff,),
        )
        status_rows = await status_cursor.fetchall()
        by_status: dict[str, int] = {row["status"]: row["count"] for row in status_rows}
        total = sum(by_status.values())

        # 2. 失败任务按 error_code 分布
        error_cursor = await self.execute(
            """
            SELECT error_code, COUNT(*) as count
            FROM tasks
            WHERE created_at >= ? AND status = 'failed' AND error_code IS NOT NULL
            GROUP BY error_code
            """,
            (cutoff,),
        )
        error_rows = await error_cursor.fetchall()
        failures_by_error_code: dict[str, int] = {
            row["error_code"]: row["count"] for row in error_rows
        }

        # 3. 失败归因拆分：内容级（VIDEO_ 前缀，视频本身问题，无法通过重试/换下载器解决）
        #    vs 系统级（下载器/网络/风控等，理论上可通过技术手段改善）
        content_level = sum(
            count
            for code, count in failures_by_error_code.items()
            if code.startswith("VIDEO_")
        )
        system_level = sum(
            count
            for code, count in failures_by_error_code.items()
            if not code.startswith("VIDEO_")
        )
        total_failures = content_level + system_level
        failure_split = {
            "content_level": content_level,
            "system_level": system_level,
            "content_level_ratio": (content_level / total_failures) if total_failures else 0.0,
            "system_level_ratio": (system_level / total_failures) if total_failures else 0.0,
        }

        # 4. 音频/字幕下载器归属分布（NULL 归为 'unknown'：未知/历史数据/复用缓存未下载）
        audio_downloader_cursor = await self.execute(
            """
            SELECT COALESCE(audio_downloader, 'unknown') as downloader, COUNT(*) as count
            FROM tasks
            WHERE created_at >= ?
            GROUP BY downloader
            """,
            (cutoff,),
        )
        audio_downloader_rows = await audio_downloader_cursor.fetchall()
        audio_downloader_dist: dict[str, int] = {
            row["downloader"]: row["count"] for row in audio_downloader_rows
        }

        transcript_downloader_cursor = await self.execute(
            """
            SELECT COALESCE(transcript_downloader, 'unknown') as downloader, COUNT(*) as count
            FROM tasks
            WHERE created_at >= ?
            GROUP BY downloader
            """,
            (cutoff,),
        )
        transcript_downloader_rows = await transcript_downloader_cursor.fetchall()
        transcript_downloader_dist: dict[str, int] = {
            row["downloader"]: row["count"] for row in transcript_downloader_rows
        }

        by_downloader = {
            "audio_downloader": audio_downloader_dist,
            "transcript_downloader": transcript_downloader_dist,
        }

        # 5. 按周统计完成/失败趋势
        # 用 %Y-W%W（公历年 + 周一起始的年内周序，00-53）拼出周标签，如 "2026-W27"。
        # 不用 strftime('%G-W%V', ...)（ISO 8601 年份+周号）：%G/%V 是 SQLite
        # 3.46.0（2024-05）才加入的，本地/生产环境的 SQLite（含 Debian bookworm
        # 上 python:3.11-slim 自带的 3.40.1）都更旧，遇到不支持的格式码
        # strftime 直接返回 NULL，GROUP BY 会把所有行并成一个 {None: ...} 桶，
        # 统计整体失真。%Y/%W 是 SQLite 从最早版本就支持的格式码，牺牲的是
        # 严格 ISO 周语义：年末/年初几天可能出现"日期属于上一/下一个 ISO 周"
        # 但这里仍按公历年份分桶的情况，跨年边界的周标签可能与真正的 ISO
        # 8601 周号不完全一致，属已知折衷（换取版本兼容性）。
        weekly_cursor = await self.execute(
            """
            SELECT strftime('%Y', created_at) || '-W' || strftime('%W', created_at)
                   as week,
                   status, COUNT(*) as count
            FROM tasks
            WHERE created_at >= ? AND status IN ('completed', 'failed')
            GROUP BY week, status
            ORDER BY week
            """,
            (cutoff,),
        )
        weekly_rows = await weekly_cursor.fetchall()
        weekly_map: dict[str, dict[str, int]] = {}
        for row in weekly_rows:
            week = row["week"]
            weekly_map.setdefault(week, {"completed": 0, "failed": 0})
            weekly_map[week][row["status"]] = row["count"]

        weekly_trend = [
            {"week": week, "completed": counts["completed"], "failed": counts["failed"]}
            for week, counts in sorted(weekly_map.items())
        ]

        return {
            "total": total,
            "by_status": by_status,
            "failures_by_error_code": failures_by_error_code,
            "failure_split": failure_split,
            "by_downloader": by_downloader,
            "weekly_trend": weekly_trend,
        }

    # ==================== IP Ban Persistence Operations ====================
    # 为 IPBanCircuitBreaker（src/core/ip_ban_breaker.py，被动探测型三级熔断器）
    # 提供状态持久化。背景：熔断器状态完全在内存，D3 自动部署每次 push 到 main
    # 都会重启容器，重启后如果不恢复，服务会误判为 NORMAL 全速请求 YouTube，
    # 加重封禁。这里的方法只负责读写，何时调用由 worker.py 的回调/启动恢复
    # 流程决定，Database 本身不感知熔断器的业务语义。

    async def save_ip_ban_state(
        self,
        current_level: IPBanLevel,
        banned_at: Optional[datetime],
        last_attempt_at: Optional[datetime],
        failed_attempts: int,
    ) -> None:
        """
        持久化 IP 熔断器当前状态（upsert 单行表 ip_ban_status，id 恒为 1）。

        通常由 IPBanCircuitBreaker 的 on_state_change 回调驱动，在每次状态
        变更（触发/升级/降级/恢复/延长/启动恢复）后调用。

        Note:
            banned_at / last_attempt_at 应传入 IPBanCircuitBreaker 内部使用的
            naive datetime（datetime.now()）。存储时按现有全局适配器规则处理
            （naive 视为 UTC 写入），读取时 load_ip_ban_state 会去掉时区信息
            还原为 naive，与 breaker 内部的时间运算保持兼容。

        Args:
            current_level: 当前熔断级别
            banned_at: 熔断触发时间（NORMAL 时为 None）
            last_attempt_at: 最近一次（被动探测）尝试时间
            failed_attempts: 熔断期间失败尝试次数
        """
        now = datetime.now(timezone.utc)

        async with self.transaction():
            await self.execute(
                """
                INSERT INTO ip_ban_status (
                    id, current_level, banned_at, last_attempt_at,
                    failed_attempts, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_level = excluded.current_level,
                    banned_at = excluded.banned_at,
                    last_attempt_at = excluded.last_attempt_at,
                    failed_attempts = excluded.failed_attempts,
                    updated_at = excluded.updated_at
                """,
                (
                    current_level.value,
                    banned_at,
                    last_attempt_at,
                    failed_attempts,
                    now,
                ),
            )

        logger.debug(
            f"IP ban state persisted: level={current_level.value}, "
            f"failed_attempts={failed_attempts}"
        )

    async def load_ip_ban_state(self) -> Optional[dict[str, Any]]:
        """
        读取持久化的 IP 熔断器状态（服务启动时调用，用于恢复熔断状态）。

        Returns:
            字典，包含：
            - current_level (IPBanLevel): 当前熔断级别
            - banned_at (Optional[datetime]): 熔断触发时间（naive）
            - last_attempt_at (Optional[datetime]): 最近一次尝试时间（naive）
            - failed_attempts (int): 熔断期间失败尝试次数

            单行表记录不存在时返回 None（正常情况下建表逻辑会预置一条
            current_level='normal' 的种子行，只有该行被手动删除等异常场景
            才会出现 None；调用方对 None 与 NORMAL 应做相同处理：不恢复）。
        """
        cursor = await self.execute(
            "SELECT current_level, banned_at, last_attempt_at, failed_attempts "
            "FROM ip_ban_status WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        def _to_naive(value: Optional[datetime]) -> Optional[datetime]:
            """去掉时区信息，还原为 IPBanCircuitBreaker 内部使用的 naive datetime。"""
            if value is None:
                return None
            return value.replace(tzinfo=None)

        return {
            "current_level": IPBanLevel(row["current_level"]),
            "banned_at": _to_naive(row["banned_at"]),
            "last_attempt_at": _to_naive(row["last_attempt_at"]),
            "failed_attempts": row["failed_attempts"] or 0,
        }

    async def append_ip_ban_history(
        self,
        event_type: str,
        ban_level: str,
        trigger_source: Optional[str] = None,
        trigger_task_id: Optional[str] = None,
        trigger_downloader: Optional[str] = None,
        trigger_error: Optional[str] = None,
        banned_at: Optional[datetime] = None,
        recovered_at: Optional[datetime] = None,
        duration_seconds: Optional[int] = None,
        probe_count: int = 0,
        recovery_method: Optional[str] = None,
    ) -> None:
        """
        追加一条 IP 熔断历史事件记录（append-only 事件日志，不更新已有记录）。

        Args:
            event_type: 事件类型 -- triggered(触发) | upgraded(升级) |
                downgraded(降级) | recovered(恢复正常) | restored(启动时从
                持久化恢复)
            ban_level: 事件发生后的熔断级别（IPBanLevel.value）
            trigger_source: 触发来源（如 'audio' | 'transcript' | 'mixed'），可选
            trigger_task_id: 触发事件的任务 ID，可选
            trigger_downloader: 触发事件的下载器名称，可选
            trigger_error: 触发原因/错误描述，可选
            banned_at: 本次事件对应的熔断开始时间；未提供时使用当前时间
            recovered_at: 恢复时间（通常仅 recovered 事件填写）
            duration_seconds: 本次熔断持续时长（秒），可选
            probe_count: 探测次数，默认 0
            recovery_method: 恢复方式（如 'auto_probe' | 'restored'），可选
        """
        now = datetime.now(timezone.utc)

        async with self.transaction():
            await self.execute(
                """
                INSERT INTO ip_ban_history (
                    event_type, ban_level, trigger_source, trigger_task_id,
                    trigger_downloader, trigger_error, banned_at, recovered_at,
                    duration_seconds, probe_count, recovery_method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    ban_level,
                    trigger_source,
                    trigger_task_id,
                    trigger_downloader,
                    trigger_error,
                    banned_at or now,
                    recovered_at,
                    duration_seconds,
                    probe_count,
                    recovery_method,
                ),
            )

        logger.info(f"IP ban history recorded: event={event_type}, level={ban_level}")

    # ==================== Helper Methods ====================

    def _row_to_video_resource(self, row: aiosqlite.Row) -> VideoResource:
        """Convert database row to VideoResource object."""
        video_info = None
        if row["video_info"]:
            video_info = VideoInfo.from_dict(json.loads(row["video_info"]))

        has_native = row["has_native_transcript"]
        has_native_transcript = None
        if has_native is not None:
            has_native_transcript = bool(has_native)

        return VideoResource(
            video_id=row["video_id"],
            video_info=video_info,
            has_native_transcript=has_native_transcript,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_task(self, row: aiosqlite.Row) -> Task:
        """Convert database row to Task object."""
        return Task(
            id=row["id"],
            video_id=row["video_id"],
            video_url=row["video_url"],
            status=TaskStatus(row["status"]),
            priority=TaskPriority(row["priority"]) if row["priority"] else TaskPriority.NORMAL,
            include_audio=bool(row["include_audio"]),
            include_transcript=bool(row["include_transcript"]),
            audio_file_id=row["audio_file_id"],
            transcript_file_id=row["transcript_file_id"],
            reused_audio=bool(row["reused_audio"]),
            reused_transcript=bool(row["reused_transcript"]),
            audio_downloader=row["audio_downloader"],
            transcript_downloader=row["transcript_downloader"],
            callback_url=row["callback_url"],
            callback_secret=row["callback_secret"],
            callback_status=CallbackStatus(row["callback_status"])
            if row["callback_status"]
            else None,
            callback_attempts=row["callback_attempts"] or 0,
            error_code=ErrorCode(row["error_code"]) if row["error_code"] else None,
            error_message=row["error_message"],
            retry_count=row["retry_count"] or 0,
            partial_success=bool(row["partial_success"]),
            failure_details=row["failure_details"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def _row_to_file(self, row: aiosqlite.Row) -> FileRecord:
        """Convert database row to FileRecord object."""
        row_keys = set(row.keys())
        upload_source = row["upload_source"] if "upload_source" in row_keys else "auto"
        if not upload_source:
            upload_source = "auto"
        original_format = row["original_format"] if "original_format" in row_keys else None
        return FileRecord(
            id=row["id"],
            video_id=row["video_id"],
            file_type=FileType(row["file_type"]),
            filename=row["filename"],
            filepath=row["filepath"],
            size=row["size"],
            format=row["format"],
            quality=row["quality"],
            language=row["language"],
            upload_source=upload_source,
            original_format=original_format,
            created_at=row["created_at"],
            last_accessed_at=row["last_accessed_at"],
            expires_at=row["expires_at"],
        )


# Global database instance (initialized in main.py)
db: Optional[Database] = None


def set_database(database: Database) -> None:
    """
    设置全局数据库实例。

    Args:
        database: 数据库实例
    """
    global db
    db = database


async def get_database() -> Database:
    """
    Get database instance.

    Returns:
        Database instance.

    Raises:
        RuntimeError: If database not initialized.
    """
    if db is None:
        raise RuntimeError("Database not initialized")
    return db
