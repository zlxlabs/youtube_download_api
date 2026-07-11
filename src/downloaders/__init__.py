"""
多下载器模块。

提供统一的下载器接口和管理器，支持多种下载方式的降级切换。
"""

from src.downloaders.base import BaseDownloader
from src.downloaders.circuit_breaker import CircuitBreaker, CircuitBreakerOpen, CircuitState
from src.downloaders.exceptions import (
    AllDownloadersFailed,
    DownloaderAttempt,
    DownloaderError,
    DownloaderNotAvailable,
)
from src.downloaders.manager import DownloaderManager
from src.downloaders.models import (
    DownloaderResult,
    DownloaderType,
    VideoMetadata,
)

__all__ = [
    # Base
    "BaseDownloader",
    # Models
    "DownloaderResult",
    "DownloaderType",
    "VideoMetadata",
    # Exceptions
    "DownloaderError",
    "DownloaderNotAvailable",
    "AllDownloadersFailed",
    "DownloaderAttempt",
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerOpen",
    "CircuitState",
    # Manager
    "DownloaderManager",
]
