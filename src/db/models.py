"""
Database models and enums.

Defines data structures for video resources, files, and tasks.
Architecture: Video -> Files <- Task (video owns files, tasks reference files)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    """Task status enumeration."""

    PENDING = "pending"  # Waiting for download
    DOWNLOADING = "downloading"  # Currently downloading
    COMPLETED = "completed"  # Successfully completed
    FAILED = "failed"  # Failed after all retries
    CANCELLED = "cancelled"  # Cancelled by user
    DELAYED_IP_BAN = "delayed_ip_ban"  # Delayed due to IP ban (will retry later)


class ErrorCode(str, Enum):
    """Error code enumeration."""

    # Video issues
    VIDEO_UNAVAILABLE = "VIDEO_UNAVAILABLE"  # Video doesn't exist / deleted
    VIDEO_PRIVATE = "VIDEO_PRIVATE"  # Private video
    VIDEO_REGION_BLOCKED = "VIDEO_REGION_BLOCKED"  # Region restricted
    VIDEO_AGE_RESTRICTED = "VIDEO_AGE_RESTRICTED"  # Age restricted
    VIDEO_LIVE_STREAM = "VIDEO_LIVE_STREAM"  # Live stream not supported

    # Download issues
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"  # General download failure
    RATE_LIMITED = "RATE_LIMITED"  # Rate limited by YouTube
    NETWORK_ERROR = "NETWORK_ERROR"  # Network error

    # System issues
    POT_TOKEN_FAILED = "POT_TOKEN_FAILED"  # PO Token acquisition failed
    INTERNAL_ERROR = "INTERNAL_ERROR"  # Internal error

    # CDP-specific errors
    CDP_CONNECTION_FAILED = "CDP_CONNECTION_FAILED"  # Cannot connect to Chrome
    CDP_CONNECTION_TIMEOUT = "CDP_CONNECTION_TIMEOUT"  # Connection timeout
    CDP_CHROME_NOT_READY = "CDP_CHROME_NOT_READY"  # Chrome not ready
    CDP_CIRCUIT_BREAKER_OPEN = "CDP_CIRCUIT_BREAKER_OPEN"  # Circuit breaker open
    CDP_PAGE_LOAD_FAILED = "CDP_PAGE_LOAD_FAILED"  # Page load failed
    CDP_PAGE_TIMEOUT = "CDP_PAGE_TIMEOUT"  # Page load timeout
    CDP_COOKIE_EXPORT_FAILED = "CDP_COOKIE_EXPORT_FAILED"  # Cookie export failed
    CDP_NO_COOKIES = "CDP_NO_COOKIES"  # No valid cookies
    CDP_NO_AUDIO_URL = "CDP_NO_AUDIO_URL"  # No audio URL from yt-dlp
    CDP_YTDLP_FAILED = "CDP_YTDLP_FAILED"  # yt-dlp parsing failed
    CDP_NSIG_FAILED = "CDP_NSIG_FAILED"  # nsig/n challenge failed (global yt-dlp issue)
    CDP_DOWNLOAD_FAILED = "CDP_DOWNLOAD_FAILED"  # Download failed (general)
    CDP_DOWNLOAD_403 = "CDP_DOWNLOAD_403"  # HTTP 403 error
    CDP_DOWNLOAD_TIMEOUT = "CDP_DOWNLOAD_TIMEOUT"  # Download timeout
    CDP_SIZE_MISMATCH = "CDP_SIZE_MISMATCH"  # File size mismatch
    CDP_TRANSCODE_FAILED = "CDP_TRANSCODE_FAILED"  # Transcode to m4a failed

    # Task-level errors
    TASK_TIMEOUT = "TASK_TIMEOUT"  # Task-level timeout (safety net)


# 元数据/资源获取阶段的"内容级终态错误"：由视频自身客观状态导致、与具体下载器
# 实现/出口位置无关的错误码（视频不存在/私有/直播/年龄限制）——不管用哪个下载器、
# 从哪台机器发起请求，结果都一样，因此可以作为全局终态立即终止降级链；也是
# get_download_stats() 里 failure_split 字段区分 content_level / system_level
# 失败归因的唯一事实来源。
#
# 各下载器的 fetch_metadata() 遇到这些错误时必须抛出 DownloaderError（而非吞掉
# 返回 None），供 DownloaderManager.get_metadata(raise_content_errors=True) 感知
# 并终止降级链——这是 TaskService precheck 422 拦截依赖的唯一信号源。
#
# 注意：VIDEO_REGION_BLOCKED（地区限制）不在这个集合里。地区限制是"下载器/出口
# 位置相关"的错误，不是视频的客观状态——本地部署的 ytdlp 被某个地区封锁，不代表
# 其他下载器（如远端服务 TikHub）也下载不了同一个视频。若把它当全局终态处理，
# 会出现"本地 ytdlp 探测到地区限制 -> 丢弃前面已经成功的 TikHub 结果 -> precheck
# 422 拒绝一个实际可下载的视频"这种误判（外部 review 第13轮问题2）；同理，
# failure_split 若用 error_code 前缀（VIDEO_*）而非这个集合来判定内容级失败，
# 会把地区限制误计入 content_level，在遇到地区限制的部署里让比率失真（外部
# review 第14轮问题1）。因此地区限制不终止降级链、不触发 precheck 422、也不算
# failure_split 的内容级失败；只有 UNAVAILABLE/PRIVATE/LIVE/AGE_RESTRICTED 这类
# 视频客观状态才算。
#
# 定义放在这个无下游依赖的叶子模块（而非 src.downloaders.models 或 manager.py），
# 是因为 ErrorCode 本身就定义在这里，且 src.db.database（数据库聚合层）也需要
# 引用这个集合——db 层不应反向依赖 downloaders 层。src.downloaders.models 从这里
# 导入并保留同名属性，manager.py 又从 downloaders.models 导入并保留同名模块
# 属性，因此 `from src.downloaders.manager import CONTENT_LEVEL_ERROR_CODES` /
# `from src.downloaders.models import CONTENT_LEVEL_ERROR_CODES` 的旧引用均不受
# 影响。
CONTENT_LEVEL_ERROR_CODES = frozenset(
    {
        ErrorCode.VIDEO_UNAVAILABLE,
        ErrorCode.VIDEO_PRIVATE,
        ErrorCode.VIDEO_LIVE_STREAM,
        ErrorCode.VIDEO_AGE_RESTRICTED,
    }
)


class CallbackStatus(str, Enum):
    """Callback status enumeration."""

    PENDING = "pending"  # Not yet sent
    SUCCESS = "success"  # Successfully delivered
    FAILED = "failed"  # Failed after all retries


class FileType(str, Enum):
    """File type enumeration."""

    AUDIO = "audio"
    TRANSCRIPT = "transcript"


class TaskPriority(str, Enum):
    """Task priority enumeration."""

    URGENT = "urgent"  # 紧急任务，立即处理
    NORMAL = "normal"  # 普通任务，正常排队（默认）


def calculate_queue_priority(
    user_priority: TaskPriority,
    include_audio: bool,
    include_transcript: bool,
) -> int:
    """
    Calculate queue priority based on user priority and task type.

    优先级体系（3 级 + 重试）：
    - 0: urgent（任何类型）- 全局最高优先级
    - 1: normal + transcript_only - 字幕任务（轻量级，低风控）
    - 2: normal + audio/mixed - 音频/混合任务（重量级，高风控）
    - 3: retry - 重试任务（最低优先级）

    核心原则：
    - urgent 最优先，不论音频还是字幕
    - normal 任务中，字幕优先于音频

    Args:
        user_priority: 用户指定的优先级（urgent/normal）
        include_audio: 是否包含音频下载
        include_transcript: 是否包含字幕下载

    Returns:
        Queue priority (0=highest, 3=lowest for retry)
    """
    # urgent 任务全局最高优先级
    if user_priority == TaskPriority.URGENT:
        return 0

    # normal 任务根据类型分级
    if not include_audio and include_transcript:
        # 仅字幕任务：高优先级（轻量级，低风控）
        return 1
    else:
        # 音频/混合任务：中等优先级（重量级，高风控）
        return 2


# 重试任务的队列优先级（最低优先级）
RETRY_QUEUE_PRIORITY = 3


@dataclass
class VideoInfo:
    """Video information extracted from YouTube."""

    title: Optional[str] = None
    author: Optional[str] = None
    channel_id: Optional[str] = None
    duration: Optional[int] = None
    description: Optional[str] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    thumbnail: Optional[str] = None
    # 直播状态: "none" | "upcoming" | "live"
    live_broadcast_content: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "author": self.author,
            "channel_id": self.channel_id,
            "duration": self.duration,
            "description": self.description,
            "upload_date": self.upload_date,
            "view_count": self.view_count,
            "thumbnail": self.thumbnail,
            "live_broadcast_content": self.live_broadcast_content,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional["VideoInfo"]:
        """Create from dictionary."""
        if not data:
            return None
        return cls(
            title=data.get("title"),
            author=data.get("author"),
            channel_id=data.get("channel_id"),
            duration=data.get("duration"),
            description=data.get("description"),
            upload_date=data.get("upload_date"),
            view_count=data.get("view_count"),
            thumbnail=data.get("thumbnail"),
            live_broadcast_content=data.get("live_broadcast_content"),
        )


@dataclass
class VideoResource:
    """
    Video resource entity - the core entity.

    One video_id corresponds to one record, storing video metadata.
    Files are associated with video_id, not task_id.
    """

    video_id: str  # YouTube video ID (primary key)
    video_info: Optional[VideoInfo] = None  # Video metadata
    has_native_transcript: Optional[bool] = None  # Whether video has native subtitles (cached)

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "video_id": self.video_id,
            "video_info": self.video_info.to_dict() if self.video_info else None,
            "has_native_transcript": self.has_native_transcript,
        }


@dataclass
class FileRecord:
    """
    File record entity - resource entity.

    Files are indexed by video_id, supporting multiple files per video.
    Unique constraint: (video_id, file_type, quality, language)
    """

    id: str  # UUID for download URL
    video_id: str  # Associated video (not task_id anymore)
    file_type: FileType  # audio / transcript

    # File attributes
    filename: str  # Actual filename
    filepath: str  # Relative path from data_dir
    size: Optional[int] = None  # File size in bytes
    format: Optional[str] = None  # Audio: m4a/webm, Transcript: json

    # Extended attributes (for future multi-version support)
    quality: Optional[str] = None  # Audio: 128 / 320
    language: Optional[str] = None  # Transcript: en / zh
    upload_source: str = "auto"  # 'auto' (downloaded) or 'manual' (uploaded)
    original_format: Optional[str] = None  # Original format before transcoding

    # Lifecycle
    created_at: Optional[datetime] = None
    last_accessed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


@dataclass
class Task:
    """
    Task record entity - request entity.

    Tasks are triggers that reference existing resources or trigger new downloads.
    Tasks don't own files; they reference files via file_ids.
    """

    id: str  # UUID
    video_id: str  # YouTube video ID
    video_url: str  # Original URL
    status: TaskStatus = TaskStatus.PENDING

    # Request parameters (what client wants)
    include_audio: bool = True  # Whether to download audio
    include_transcript: bool = True  # Whether to fetch transcript
    priority: TaskPriority = TaskPriority.NORMAL  # Task priority (urgent/normal)

    # File references (pointing to files table, may reuse existing files)
    audio_file_id: Optional[str] = None
    transcript_file_id: Optional[str] = None

    # Reuse flags (for statistics/debugging)
    reused_audio: bool = False  # Whether audio file was reused
    reused_transcript: bool = False  # Whether transcript file was reused

    # Downloader attribution（哪个下载器最终产出了该文件；NULL=未知/历史数据/复用缓存未下载）
    audio_downloader: Optional[str] = None  # 产出音频文件的下载器名称，如 "cdp" / "ytdlp" / "tikhub"
    transcript_downloader: Optional[str] = None  # 产出字幕文件的下载器名称

    # Callback configuration
    callback_url: Optional[str] = None
    callback_secret: Optional[str] = None
    callback_status: Optional[CallbackStatus] = None
    callback_attempts: int = 0

    # Error information
    error_code: Optional[ErrorCode] = None
    error_message: Optional[str] = None
    retry_count: int = 0

    # Partial success support (for mixed tasks)
    partial_success: bool = False  # 是否是部分成功（如：音频失败但字幕成功）
    failure_details: Optional[str] = None  # JSON string: 失败详情（哪些成功，哪些失败）

    # Timestamps
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Progress tracking (not persisted)
    progress: int = field(default=0, compare=False)


# Retry configuration for different error types
# 所有可重试错误统一改为最多重试 1 次
RETRY_CONFIG: dict[ErrorCode, dict[str, Any]] = {
    # Retryable errors - 统一最多重试 1 次
    ErrorCode.NETWORK_ERROR: {
        "max_retries": 1,
        "backoff": [300],  # 5分钟后重试
        "jitter": 60,  # 随机抖动 0-60秒
    },
    ErrorCode.RATE_LIMITED: {
        "max_retries": 1,
        "backoff": [600],  # 10分钟后重试
        "jitter": 120,  # 随机抖动 0-120秒
    },
    ErrorCode.POT_TOKEN_FAILED: {
        "max_retries": 1,
        "backoff": [180],  # 3分钟后重试
        "jitter": 60,
    },
    ErrorCode.DOWNLOAD_FAILED: {
        "max_retries": 1,
        "backoff": [300],  # 5分钟后重试
        "jitter": 60,
    },
    ErrorCode.TASK_TIMEOUT: {
        "max_retries": 1,
        "backoff": [300],  # 5分钟后重试
        "jitter": 60,
    },
    # Non-retryable errors (fail immediately)
    ErrorCode.VIDEO_UNAVAILABLE: {"max_retries": 0},
    ErrorCode.VIDEO_PRIVATE: {"max_retries": 0},
    ErrorCode.VIDEO_REGION_BLOCKED: {"max_retries": 0},
    ErrorCode.VIDEO_AGE_RESTRICTED: {"max_retries": 0},
    ErrorCode.VIDEO_LIVE_STREAM: {"max_retries": 0},
    ErrorCode.INTERNAL_ERROR: {"max_retries": 0},
}


def is_retryable_error(error_code: ErrorCode) -> bool:
    """
    Check if an error code is retryable.

    Args:
        error_code: The error code to check.

    Returns:
        True if the error is retryable.
    """
    config = RETRY_CONFIG.get(error_code, {})
    max_retries: int = config.get("max_retries", 0)
    return max_retries > 0
