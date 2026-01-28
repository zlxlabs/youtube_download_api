"""
YouTube Data API v3 下载器实现。

仅用于获取视频元数据，不支持资源下载。
"""

import re
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.base import BaseDownloader
from src.downloaders.exceptions import DownloaderError
from src.downloaders.models import DownloaderResult, DownloaderType, VideoMetadata
from src.utils.logger import logger


class YoutubeDataApiDownloader(BaseDownloader):
    """
    YouTube Data API v3 下载器。

    职责：
    - 仅实现 fetch_metadata()，用于获取视频元数据
    - 不实现 download_resources()，资源下载由其他下载器负责

    优势：
    - 官方 API，稳定性高
    - 获取元数据快速（通常 < 1 秒）
    - 不受 YouTube 爬虫限流影响

    限制：
    - 每日配额 10,000 units（videos.list 消耗 1 unit）
    - 仅提供元数据，不提供下载链接
    - 需要 API Key
    """

    def __init__(self, settings: Settings):
        """
        初始化 YouTube Data API v3 下载器。

        Args:
            settings: 应用配置
        """
        self.settings = settings
        self.api_key = getattr(settings, "youtube_data_api_key", None)
        self._youtube_service = None

    @property
    def name(self) -> str:
        """下载器名称。"""
        return "youtube_data_api"

    @property
    def downloader_type(self) -> DownloaderType:
        """下载器类型。"""
        return DownloaderType.YOUTUBE_DATA_API

    @property
    def is_available(self) -> bool:
        """
        检查下载器是否可用。

        需要配置 YOUTUBE_DATA_API_KEY。
        """
        return bool(self.api_key)

    @property
    def supports_resource_download(self) -> bool:
        """
        YouTube Data API v3 不支持资源下载。

        仅支持元数据获取（标题、作者、时长等），不提供音频/字幕下载链接。
        资源下载应使用 ytdlp 或 tikhub 下载器。
        """
        return False

    def _get_youtube_service(self):
        """
        获取 YouTube API 服务实例（懒加载）。

        Returns:
            YouTube API 服务实例
        """
        if not self._youtube_service:
            try:
                self._youtube_service = build(
                    "youtube",
                    "v3",
                    developerKey=self.api_key,
                    cache_discovery=False,  # 禁用缓存，避免权限问题
                )
                logger.debug("YouTube API service initialized")
            except Exception as e:
                logger.error(f"Failed to initialize YouTube API service: {e}")
                raise

        return self._youtube_service

    def _parse_duration(self, iso_duration: str) -> Optional[int]:
        """
        解析 ISO 8601 时长格式（如 PT1H2M3S）为秒数。

        Args:
            iso_duration: ISO 8601 时长字符串（如 "PT1H2M3S"）

        Returns:
            时长（秒），解析失败返回 None

        Example:
            >>> _parse_duration("PT1H2M3S")
            3723
            >>> _parse_duration("PT15M30S")
            930
        """
        try:
            # 正则匹配 ISO 8601 时长格式
            pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
            match = re.match(pattern, iso_duration)

            if not match:
                return None

            hours, minutes, seconds = match.groups()
            total_seconds = 0

            if hours:
                total_seconds += int(hours) * 3600
            if minutes:
                total_seconds += int(minutes) * 60
            if seconds:
                total_seconds += int(seconds)

            return total_seconds

        except Exception as e:
            logger.warning(f"Failed to parse duration '{iso_duration}': {e}")
            return None

    async def fetch_metadata(
        self,
        video_url: str,
        video_id: str,
    ) -> Optional[dict]:
        """
        使用 YouTube Data API v3 获取视频元数据。

        调用 videos.list API，消耗 1 配额单位。

        API 文档：
        https://developers.google.com/youtube/v3/docs/videos/list

        Args:
            video_url: YouTube 视频 URL（未使用，仅保留接口一致性）
            video_id: YouTube 视频 ID

        Returns:
            视频元数据字典，包含：
            - title: 标题
            - author: 频道名称
            - channel_id: 频道 ID
            - duration: 时长（秒）
            - description: 描述
            - upload_date: 上传日期（YYYYMMDD 格式）
            - view_count: 观看次数
            - thumbnail: 缩略图 URL
            - available_captions: 可用字幕列表（语言代码）

        Raises:
            DownloaderError: API 调用失败时抛出
        """
        logger.info(f"[youtube_data_api] Fetching metadata for {video_id}")

        try:
            youtube = self._get_youtube_service()

            # 调用 videos.list API
            # part 参数指定要获取的资源部分
            # snippet: 基本信息（标题、描述、缩略图等）
            # contentDetails: 内容详情（时长等）
            # statistics: 统计信息（观看数、点赞数等）
            request = youtube.videos().list(
                part="snippet,contentDetails,statistics",
                id=video_id,
            )

            response = request.execute()

            # 检查是否找到视频
            if not response.get("items"):
                logger.warning(f"[youtube_data_api] Video not found: {video_id}")
                raise DownloaderError(
                    message=f"Video not found: {video_id}",
                    error_code=ErrorCode.VIDEO_UNAVAILABLE,
                    downloader=self.name,
                )

            # 提取视频信息
            video = response["items"][0]
            snippet = video.get("snippet", {})
            content_details = video.get("contentDetails", {})
            statistics = video.get("statistics", {})

            # 解析时长
            duration_iso = content_details.get("duration", "PT0S")
            duration = self._parse_duration(duration_iso)

            # 解析上传日期（ISO 8601 → YYYYMMDD）
            published_at = snippet.get("publishedAt", "")
            upload_date = published_at.replace("-", "").split("T")[0] if published_at else None

            # 获取最高分辨率缩略图
            thumbnails = snippet.get("thumbnails", {})
            thumbnail = (
                thumbnails.get("maxres", {}).get("url")
                or thumbnails.get("high", {}).get("url")
                or thumbnails.get("medium", {}).get("url")
                or thumbnails.get("default", {}).get("url")
            )

            # 构建元数据
            metadata = {
                "title": snippet.get("title"),
                "author": snippet.get("channelTitle"),
                "channel_id": snippet.get("channelId"),
                "duration": duration,
                "description": snippet.get("description"),
                "upload_date": upload_date,
                "view_count": int(statistics.get("viewCount", 0)) or None,
                "thumbnail": thumbnail,
            }

            # 尝试获取字幕列表（可选，需要额外 API 调用）
            # 注意：captions.list 需要 OAuth 认证或视频所有者授权
            # 暂时不实现，保留扩展空间
            # metadata["available_captions"] = await self._get_caption_list(video_id)

            logger.info(
                f"[youtube_data_api] ✓ Metadata fetched: {video_id} "
                f"(title: {metadata.get('title', 'N/A')[:50]})"
            )

            return metadata

        except HttpError as e:
            # HTTP 错误（API 限流、配额超限、认证失败等）
            status_code = e.resp.status
            error_reason = e.error_details[0].get("reason", "unknown") if e.error_details else "unknown"

            logger.error(
                f"[youtube_data_api] API error: HTTP {status_code} - {error_reason}"
            )

            # 判断错误类型
            if status_code == 403:
                if "quotaExceeded" in error_reason:
                    error_code = ErrorCode.RATE_LIMITED
                    message = "YouTube Data API quota exceeded"
                elif "accessNotConfigured" in error_reason:
                    error_code = ErrorCode.DOWNLOAD_FAILED
                    message = "YouTube Data API is not enabled for this project"
                else:
                    error_code = ErrorCode.DOWNLOAD_FAILED
                    message = f"YouTube Data API access denied: {error_reason}"
            elif status_code == 404:
                error_code = ErrorCode.VIDEO_UNAVAILABLE
                message = f"Video not found: {video_id}"
            elif status_code == 400:
                error_code = ErrorCode.DOWNLOAD_FAILED
                message = f"Invalid API request: {error_reason}"
            else:
                error_code = ErrorCode.NETWORK_ERROR
                message = f"YouTube Data API error: HTTP {status_code}"

            raise DownloaderError(
                message=message,
                error_code=error_code,
                downloader=self.name,
                http_status_code=status_code,
            ) from e

        except Exception as e:
            logger.error(f"[youtube_data_api] Unexpected error: {e}")
            raise DownloaderError(
                message=str(e),
                error_code=ErrorCode.DOWNLOAD_FAILED,
                downloader=self.name,
            ) from e

    async def download_resources(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> DownloaderResult:
        """
        不支持资源下载。

        YouTube Data API v3 仅提供元数据，不提供下载链接。
        资源下载应使用 ytdlp 或 tikhub 下载器。

        Raises:
            NotImplementedError: 始终抛出，提示不支持此操作
        """
        raise NotImplementedError(
            f"{self.name} does not support resource downloading. "
            f"Use ytdlp or tikhub for audio/transcript downloads."
        )

    def should_retry(self, error: Exception) -> bool:
        """
        判断错误是否应该重试当前下载器。

        YouTube Data API 错误通常不应该重试（配额问题），应该降级。

        Args:
            error: 捕获的异常

        Returns:
            False（不重试，直接降级）
        """
        # API 限流、配额超限等错误不应该重试
        # 应该降级到其他下载器
        return False

    def should_trigger_circuit_breaker(self, error: Exception) -> bool:
        """
        判断错误是否应该触发熔断器。

        系统性错误（配额超限）应该触发熔断器，
        视频特定错误（视频不存在）不应该触发。

        Args:
            error: 捕获的异常

        Returns:
            True 表示应该触发熔断器，False 表示不触发
        """
        if isinstance(error, DownloaderError):
            # 配额超限、限流 → 触发熔断器
            if error.error_code in (ErrorCode.RATE_LIMITED,):
                return True

            # 视频不存在、私有视频等 → 不触发熔断器
            if error.error_code in (
                ErrorCode.VIDEO_UNAVAILABLE,
                ErrorCode.VIDEO_PRIVATE,
                ErrorCode.VIDEO_REGION_BLOCKED,
                ErrorCode.VIDEO_AGE_RESTRICTED,
            ):
                return False

        # 其他错误 → 触发熔断器
        return True
