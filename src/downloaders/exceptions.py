"""
下载器异常类定义。
"""

from dataclasses import dataclass
from typing import Optional

from src.db.models import ErrorCode


@dataclass
class DownloaderAttempt:
    """
    单次下载器尝试的结构化记录。

    用于失败归因持久化：AllDownloadersFailed 携带整条降级链每个下载器的
    尝试结果，最终由 worker 序列化为 JSON 写入 tasks.failure_details 列。
    """

    downloader: str  # 下载器名称，如 "cdp" / "ytdlp" / "tikhub"
    error_code: str  # 错误码字符串（ErrorCode.value，或熔断器等非 ErrorCode 场景的自定义标识）
    message: str  # 失败原因描述


class DownloaderError(Exception):
    """下载器错误基类。"""

    def __init__(
        self,
        message: str,
        error_code: ErrorCode = ErrorCode.DOWNLOAD_FAILED,
        downloader: Optional[str] = None,
        http_status_code: Optional[int] = None,
        stop_fallback: bool = False,
        operation: Optional[str] = None,
    ):
        self.message = message
        self.error_code = error_code
        self.downloader = downloader
        self.http_status_code = http_status_code  # HTTP 状态码（用于判断是否应该重试）
        self.stop_fallback = stop_fallback  # 是否停止降级（True 表示不再尝试其他下载器）
        self.operation = operation  # 操作类型："audio" | "transcript" | "mixed"（用于熔断分级）
        super().__init__(message)

    def __str__(self) -> str:
        if self.downloader:
            return f"[{self.downloader}] {self.message}"
        return self.message


class DownloaderNotAvailable(DownloaderError):
    """下载器不可用异常。"""

    def __init__(self, downloader: str, reason: str):
        super().__init__(
            message=f"{downloader} not available: {reason}",
            error_code=ErrorCode.DOWNLOAD_FAILED,
            downloader=downloader,
        )


class AllDownloadersFailed(DownloaderError):
    """所有下载器都失败异常。"""

    def __init__(
        self,
        errors: list[str],
        error_code: ErrorCode = ErrorCode.DOWNLOAD_FAILED,
        attempts: Optional[list[DownloaderAttempt]] = None,
    ):
        self.errors = errors
        # 结构化的每次下载器尝试记录（下载器名/错误码/消息），用于失败归因持久化。
        # 旧调用点未传入时默认为空列表，保持向后兼容。
        self.attempts: list[DownloaderAttempt] = attempts or []
        error_msg = "\n".join(f"  · {e}" for e in errors)
        super().__init__(
            message=f"All downloaders failed:\n{error_msg}",
            error_code=error_code,
        )
