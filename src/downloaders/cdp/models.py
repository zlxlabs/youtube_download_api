"""
CDP 下载器专用数据模型。

提供 CDP 下载器使用的数据结构定义。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AudioInfo:
    """音频信息（从 yt-dlp 提取）。"""

    url: str  # 音频直链
    itag: Optional[int]  # 格式 ID（251/140 等）
    mime_type: str  # MIME 类型
    title: str  # 视频标题
    filesize: Optional[int]  # 预估大小（字节）
    ext: str  # 扩展名（m4a/webm）


@dataclass
class CDPHealthStatus:
    """CDP 健康状态。"""

    is_healthy: bool  # 是否健康
    last_check_time: float  # 上次检查时间
    consecutive_failures: int  # 连续失败次数
    circuit_state: str  # 熔断器状态: CLOSED/OPEN/HALF_OPEN
    circuit_open_until: float  # 熔断结束时间


@dataclass
class CDPInstanceHealth:
    """单个 CDP 实例的健康状态。"""

    cdp_url: str  # CDP 实例地址
    is_healthy: bool  # 是否健康
    last_check_time: float  # 上次检查时间
    consecutive_failures: int  # 连续失败次数
    circuit_state: str  # 熔断器状态: CLOSED/OPEN/HALF_OPEN
    circuit_open_until: float  # 熔断结束时间
    last_error: Optional[str] = None  # 最后一次错误信息


@dataclass
class CDPDownloadResult:
    """CDP 下载结果。"""

    success: bool
    file_path: Optional[Path]
    file_size: Optional[int]
    download_method: str  # curl_cffi/httpx/ytdlp
    error_code: Optional[str]
    error_message: Optional[str]
