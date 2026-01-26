"""
统一的下载器数据模型。

提供跨下载器的标准化数据结构。
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class DownloaderType(str, Enum):
    """下载器类型枚举。"""

    YTDLP = "ytdlp"
    TIKHUB = "tikhub"


@dataclass
class VideoMetadata:
    """
    统一的视频元数据模型。

    适配不同下载器返回的视频信息格式。
    """

    video_id: str
    title: Optional[str] = None
    author: Optional[str] = None
    channel_id: Optional[str] = None
    duration: Optional[int] = None  # 秒
    description: Optional[str] = None
    upload_date: Optional[str] = None
    view_count: Optional[int] = None
    thumbnail: Optional[str] = None

    # 元数据：标识数据来源
    source_downloader: Optional[str] = None


@dataclass
class DownloaderResult:
    """
    统一的下载结果模型。

    包含下载的文件路径、视频元数据和执行信息。
    支持部分成功（例如：混合任务中音频失败但字幕成功）。
    """

    # 执行信息
    success: bool
    downloader: str  # 实际使用的下载器

    # 视频元数据
    video_metadata: VideoMetadata

    # 文件路径（临时目录中的路径）
    audio_path: Optional[Path] = None
    transcript_path: Optional[Path] = None

    # 字幕信息
    has_transcript: bool = False  # 视频是否有可用字幕

    # 部分成功支持
    partial_success: bool = False  # 是否是部分成功（请求多项但只成功部分）

    # 分项错误信息（部分失败时）
    audio_error: Optional[str] = None  # 音频下载失败原因
    audio_error_code: Optional[str] = None  # 音频错误码（ErrorCode.value）
    transcript_error: Optional[str] = None  # 字幕获取失败原因
    transcript_error_code: Optional[str] = None  # 字幕错误码

    # 失败详情（结构化数据，用于分析和 API 返回）
    failure_details: Optional[dict[str, Any]] = field(default_factory=lambda: None)

    # 错误信息（完全失败时）
    error: Optional[str] = None
