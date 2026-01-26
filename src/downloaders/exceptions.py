"""
下载器异常类定义。
"""

from typing import Optional

from src.db.models import ErrorCode


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

    def __init__(self, errors: list[str]):
        self.errors = errors
        error_msg = "\n".join(f"  - {e}" for e in errors)
        super().__init__(
            message=f"All downloaders failed:\n{error_msg}",
            error_code=ErrorCode.DOWNLOAD_FAILED,
        )
