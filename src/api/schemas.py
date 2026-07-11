"""
Pydantic models for API request/response schemas.

Provides type-safe validation for all API endpoints.
"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from src.db.models import CallbackStatus, ErrorCode, TaskPriority, TaskStatus
from src.utils.helpers import extract_video_id


# ==================== Request Schemas ====================


class CreateTaskRequest(BaseModel):
    """Request schema for creating a download task."""

    video_url: str = Field(
        ...,
        description="YouTube video URL",
        examples=["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    )
    priority: TaskPriority = Field(
        default=TaskPriority.NORMAL,
        description="Task priority: 'urgent' for immediate processing, 'normal' for regular queue (default)",
    )
    callback_url: Optional[HttpUrl] = Field(
        default=None,
        description="Webhook URL for completion callback",
    )
    callback_secret: Optional[str] = Field(
        default=None,
        description="HMAC-SHA256 secret for callback signature verification",
        min_length=8,
        max_length=256,
    )
    include_audio: bool = Field(
        default=True,
        description="Whether to download audio file",
    )
    include_transcript: bool = Field(
        default=True,
        description="Whether to fetch transcript/subtitles",
    )

    @field_validator("video_url")
    @classmethod
    def validate_video_url(cls, v: str) -> str:
        """Validate that the URL is a valid YouTube video URL."""
        video_id = extract_video_id(v)
        if not video_id:
            raise ValueError("Invalid YouTube video URL")
        return v

    def model_post_init(self, __context: Any) -> None:
        """Validate that at least one of include_audio or include_transcript is True."""
        if not self.include_audio and not self.include_transcript:
            raise ValueError(
                "At least one of include_audio or include_transcript must be True"
            )


# ==================== Response Schemas ====================


class VideoInfoResponse(BaseModel):
    """Video information in response."""

    title: Optional[str] = None
    author: Optional[str] = None
    channel_id: Optional[str] = None
    duration: Optional[int] = Field(None, description="Duration in seconds")
    description: Optional[str] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    thumbnail: Optional[str] = None


class FileInfoResponse(BaseModel):
    """File information in response."""

    url: str = Field(..., description="Download URL")
    size: Optional[int] = Field(None, description="File size in bytes")
    format: Optional[str] = Field(None, description="File format (audio: m4a/webm, transcript: json)")
    bitrate: Optional[int] = Field(None, description="Audio bitrate (for audio files)")
    language: Optional[str] = Field(
        None, description="Language code (for transcript files)"
    )


class FilesResponse(BaseModel):
    """Files information in response."""

    audio: Optional[FileInfoResponse] = None
    transcript: Optional[FileInfoResponse] = None


class RequestModeResponse(BaseModel):
    """Request mode information in response."""

    include_audio: bool = True
    include_transcript: bool = True


class ResultInfoResponse(BaseModel):
    """Result information showing actual execution details."""

    has_transcript: bool = Field(
        ..., description="Whether the video has available transcript"
    )
    audio_fallback: bool = Field(
        default=False,
        description="Whether audio was downloaded as fallback (transcript_only mode but no transcript available)",
    )
    reused_audio: bool = Field(
        default=False,
        description="Whether audio file was retrieved from cache",
    )
    reused_transcript: bool = Field(
        default=False,
        description="Whether transcript file was retrieved from cache",
    )
    partial_success: bool = Field(
        default=False,
        description="Whether this is a partial success (e.g., audio failed but transcript succeeded)",
    )
    failure_details: Optional[dict] = Field(
        default=None,
        description="Detailed information about what succeeded/failed (for partial success cases)",
    )


class ErrorInfoResponse(BaseModel):
    """Error information in response."""

    code: ErrorCode
    message: str
    retry_count: int = 0


class TaskResponse(BaseModel):
    """Response schema for task details."""

    task_id: Optional[str] = Field(
        None, description="Task ID (null for cache hits)"
    )
    status: TaskStatus
    video_id: str
    video_url: Optional[str] = None
    priority: Optional[TaskPriority] = Field(
        None, description="Task priority (null for cache hits)"
    )
    video_info: Optional[VideoInfoResponse] = None
    files: Optional[FilesResponse] = None
    error: Optional[ErrorInfoResponse] = None

    # Cache hit indicator
    cache_hit: bool = Field(
        default=False,
        description="True if response is from cache (no task created)",
    )

    # Request mode (what was requested)
    request: Optional[RequestModeResponse] = None

    # Result info (what actually happened, for completed tasks)
    result: Optional[ResultInfoResponse] = None

    # Queue information (for pending tasks)
    position: Optional[int] = Field(None, description="Position in queue")
    estimated_wait: Optional[int] = Field(
        None, description="Estimated wait time in seconds"
    )

    # Progress (for downloading tasks)
    progress: Optional[int] = Field(None, ge=0, le=100)

    # Timestamps
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    # Additional message
    message: Optional[str] = None

    model_config = {"from_attributes": True}


class CreateTaskResponse(TaskResponse):
    """Response schema for task creation."""

    pass


class TaskListResponse(BaseModel):
    """Response schema for task list."""

    tasks: list[TaskResponse]
    total: int
    limit: int
    offset: int


class CancelTaskResponse(BaseModel):
    """Response schema for task cancellation."""

    task_id: str
    status: TaskStatus
    message: str


# ==================== Health Check Schemas ====================


class ComponentStatus(BaseModel):
    """Individual component status."""

    database: str = "ok"
    pot_provider: str = "ok"
    disk_space: str = "ok"
    ip_ban: str = "normal"
    config_warnings: list[str] = Field(default_factory=list)


class QueueStatus(BaseModel):
    """Queue statistics."""

    pending: int = 0
    downloading: int = 0


class HealthResponse(BaseModel):
    """Response schema for health check."""

    status: str = "healthy"
    version: str
    components: ComponentStatus
    queue: QueueStatus
    uptime: int = Field(..., description="Uptime in seconds")


# ==================== Callback Schemas ====================


class CallbackPayload(BaseModel):
    """Webhook callback payload."""

    task_id: str
    status: TaskStatus
    video_id: str
    video_info: Optional[VideoInfoResponse] = None
    files: Optional[FilesResponse] = None
    error: Optional[ErrorInfoResponse] = None
    expires_at: Optional[datetime] = None


# ==================== Error Response Schemas ====================


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str
    error_code: Optional[str] = None


class VideoNotDownloadableResponse(BaseModel):
    """
    Response body returned when a video is rejected by the pre-creation
    availability check (precheck).

    Returned with HTTP 422 when the video is known to be undownloadable
    before any download task is created — e.g. it is currently live,
    an upcoming premiere, unavailable, private, or region blocked.
    """

    error_code: ErrorCode = Field(
        ..., description="Machine-readable error code, e.g. VIDEO_LIVE_STREAM"
    )
    message: str = Field(..., description="Human-readable explanation")
    video_id: str = Field(..., description="YouTube video ID that was rejected")


class ValidationErrorDetail(BaseModel):
    """Validation error detail."""

    loc: list[Any]
    msg: str
    type: str


class ValidationErrorResponse(BaseModel):
    """Validation error response."""

    detail: list[ValidationErrorDetail]


# ==================== Manual Upload Schemas ====================


class ManualUploadMetadata(BaseModel):
    """Optional metadata for manual upload."""

    title: Optional[str] = Field(None, description="Video title")
    author: Optional[str] = Field(None, description="Video author/channel name")
    duration: Optional[int] = Field(None, description="Video duration in seconds")
    channel_id: Optional[str] = Field(None, description="Channel ID")
    description: Optional[str] = Field(None, description="Video description")


class ManualUploadResponse(BaseModel):
    """Response schema for manual upload."""

    task_id: Optional[str] = None
    status: str = "completed"
    video_id: str
    video_url: Optional[str] = None
    cache_hit: bool = False
    upload_source: str = "manual"
    video_info: Optional[VideoInfoResponse] = None
    files: Optional[FilesResponse] = None
    original_format: Optional[str] = None
    metadata_source: Optional[str] = Field(
        None, description="'auto' if fetched from YouTube, 'manual' if user-provided"
    )
    message: Optional[str] = None


class VideoStatusResponse(BaseModel):
    """Response schema for video status check."""

    video_id: str
    has_audio: bool
    has_transcript: bool
    audio_source: Optional[str] = Field(None, description="'auto' or 'manual'")
    transcript_source: Optional[str] = Field(None, description="'auto' or 'manual'")
    audio_created_at: Optional[datetime] = None
    transcript_created_at: Optional[datetime] = None
    can_upload_audio: bool = Field(..., description="Whether audio upload is allowed")
    video_info: Optional[VideoInfoResponse] = Field(None, description="Video metadata if available")


class ManualUploadItem(BaseModel):
    """Manual upload item in list."""

    video_id: str
    file_id: str
    title: Optional[str] = None
    author: Optional[str] = None
    size: Optional[int] = Field(None, description="File size in bytes")
    format: Optional[str] = Field(None, description="Current format (m4a)")
    original_format: Optional[str] = Field(None, description="Original uploaded format")
    created_at: datetime


class ManualUploadListResponse(BaseModel):
    """Response schema for manual upload list."""

    uploads: list[ManualUploadItem]
    total: int
    limit: int
    offset: int


# ==================== Video Info Schemas ====================


class VideoInfoDetailResponse(BaseModel):
    """Response schema for video metadata query."""

    video_id: str = Field(..., description="YouTube video ID")
    video_info: VideoInfoResponse = Field(..., description="Video metadata")
    cached: bool = Field(
        ...,
        description="True if metadata retrieved from database cache, False if fetched from API"
    )
    metadata_source: str = Field(
        ...,
        description="Metadata source: cached / youtube_data_api / ytdlp / tikhub"
    )
    fetched_at: datetime = Field(..., description="Metadata fetch/update timestamp")


# ==================== Download Stats Schemas ====================


class FailureSplitStats(BaseModel):
    """失败归因拆分：内容级（视频本身问题）vs 系统级（下载器/网络/风控等）。"""

    content_level: int = Field(..., description="内容级失败数（error_code 以 VIDEO_ 开头）")
    system_level: int = Field(..., description="系统级失败数（其余 error_code）")
    content_level_ratio: float = Field(..., description="内容级失败占比（0-1）")
    system_level_ratio: float = Field(..., description="系统级失败占比（0-1）")


class DownloaderDistribution(BaseModel):
    """音频/字幕下载器归属分布，NULL 归为 'unknown'（未知/历史数据/复用缓存未下载）。"""

    audio_downloader: dict[str, int] = Field(..., description="音频下载器名称 -> 任务数")
    transcript_downloader: dict[str, int] = Field(..., description="字幕下载器名称 -> 任务数")


class WeeklyTrendItem(BaseModel):
    """单个 ISO 周的完成/失败趋势。"""

    week: str = Field(..., description="ISO 8601 周标识，如 '2026-W28'")
    completed: int = Field(..., description="该周完成任务数")
    failed: int = Field(..., description="该周失败任务数")


class DownloadStatsResponse(BaseModel):
    """下载失败归因统计响应（GET /api/v1/stats/downloads）。"""

    days: int = Field(..., description="统计时间窗口（天数）")
    total: int = Field(..., description="窗口内任务总数")
    by_status: dict[str, int] = Field(..., description="任务状态 -> 计数")
    failures_by_error_code: dict[str, int] = Field(
        ..., description="失败任务 error_code -> 计数"
    )
    failure_split: FailureSplitStats = Field(..., description="内容级/系统级失败拆分")
    by_downloader: DownloaderDistribution = Field(..., description="下载器归属分布")
    weekly_trend: list[WeeklyTrendItem] = Field(..., description="按 ISO 周的完成/失败趋势")
