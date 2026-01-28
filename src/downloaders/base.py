"""
下载器抽象基类。

定义所有下载器必须实现的接口。
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from src.downloaders.models import DownloaderResult, DownloaderType


class BaseDownloader(ABC):
    """
    下载器抽象基类。

    所有下载器实现必须继承此类并实现所有抽象方法。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        下载器名称。

        Returns:
            下载器名称（如 "ytdlp", "tikhub"）
        """
        pass

    @property
    @abstractmethod
    def downloader_type(self) -> DownloaderType:
        """
        下载器类型枚举。

        Returns:
            DownloaderType 枚举值
        """
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """
        检查下载器是否可用。

        检查必要的配置（如 API key）是否已设置。

        Returns:
            True 表示可用，False 表示不可用
        """
        pass

    @property
    def supports_resource_download(self) -> bool:
        """
        检查下载器是否支持资源下载（音频/字幕）。

        某些下载器（如 YouTube Data API v3）只提供元数据，不支持资源下载。
        这类下载器应该返回 False，以便在资源下载流程中被跳过。

        Returns:
            True 表示支持资源下载，False 表示仅支持元数据获取
        """
        return True  # 默认支持资源下载

    @abstractmethod
    async def download_resources(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> DownloaderResult:
        """
        下载视频音频和/或字幕。

        此方法执行实际的资源下载操作，包括：
        - 下载音频文件（如果 include_audio=True）
        - 获取字幕文件（如果 include_transcript=True）
        - 提取视频元数据

        与 fetch_metadata() 的区别：
        - fetch_metadata(): 仅获取元数据，不下载任何文件（快速）
        - download_resources(): 下载资源 + 获取元数据（耗时）

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID
            output_dir: 输出目录（临时目录）
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕

        Returns:
            DownloaderResult 包含下载结果

        Raises:
            DownloaderError: 下载失败时抛出
        """
        pass

    @abstractmethod
    def should_retry(self, error: Exception) -> bool:
        """
        判断错误是否应该重试当前下载器（而非降级）。

        某些临时性错误（如网络超时）应该重试当前下载器，
        而某些错误（如限流、认证失败）应该降级到下一个下载器。

        Args:
            error: 捕获的异常

        Returns:
            True 表示应该重试当前下载器，False 表示应该降级
        """
        pass

    @abstractmethod
    def should_trigger_circuit_breaker(self, error: Exception) -> bool:
        """
        判断错误是否应该触发熔断器。

        某些系统性错误（如限流）应该触发熔断器，
        而某些视频特定错误（如视频不存在）不应该触发。

        Args:
            error: 捕获的异常

        Returns:
            True 表示应该计入熔断器失败次数，False 表示不计入
        """
        pass

    @abstractmethod
    async def fetch_metadata(
        self,
        video_url: str,
        video_id: str,
    ) -> Optional[dict]:
        """
        仅获取视频元数据（不下载任何文件）。

        用途场景：
        - 人工上传时获取视频标题、作者等信息
        - 快速检查视频是否可用
        - 独立的元数据查询操作

        与 download_resources() 的区别：
        - fetch_metadata(): 仅获取元数据，不下载任何文件（快速，0.5-2秒）
        - download_resources(): 下载资源 + 获取元数据（耗时，30-60秒）

        性能特性：
        - 速度快：不涉及大文件下载
        - 成本低：某些下载器（如 TikHub）此操作仍需 API 调用
        - 风控低：轻量级操作，不易触发限流

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID

        Returns:
            视频元数据字典（包含 title, author, duration 等），失败返回 None
        """
        pass
