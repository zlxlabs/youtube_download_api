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
    ):
        self.message = message
        self.error_code = error_code
        self.downloader = downloader
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
