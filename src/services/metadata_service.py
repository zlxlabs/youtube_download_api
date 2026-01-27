"""
元数据解析服务模块。

从 YouTube 获取视频元数据（不下载音频）。
支持 TikHub 和 yt-dlp 两种方案，优先使用 TikHub，失败时自动降级。
"""

from typing import Optional

from src.config import Settings
from src.core.downloader import DownloadError, get_video_info
from src.db.models import VideoInfo
from src.downloaders.exceptions import DownloaderError, DownloaderNotAvailable
from src.downloaders.tikhub_downloader import TikHubDownloader
from src.utils.logger import logger


class MetadataService:
    """
    元数据解析服务。

    优先使用 TikHub 获取元数据（稳定可靠），失败时降级到 yt-dlp。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.tikhub_downloader: Optional[TikHubDownloader] = None
        if settings.tikhub_api_key:
            try:
                self.tikhub_downloader = TikHubDownloader(settings)
                logger.debug("[MetadataService] TikHub enabled as primary method")
            except Exception as e:
                logger.warning(f"[MetadataService] Failed to init TikHub: {e}")

    async def fetch_youtube_metadata(
        self,
        video_url: str,
        video_id: str,
    ) -> Optional[VideoInfo]:
        """
        从 YouTube 获取视频元数据（不下载）。

        优先使用 TikHub（稳定可靠），失败时降级到 yt-dlp。
        """
        if self.tikhub_downloader and self.tikhub_downloader.is_available:
            try:
                logger.info(f"[MetadataService] Fetching metadata for {video_id} via TikHub")

                video_data = await self.tikhub_downloader._fetch_video_info(
                    video_id,
                    include_audio=False,
                    include_transcript=False,
                )

                tikhub_metadata = self.tikhub_downloader._parse_video_metadata(
                    video_data, video_id
                )

                video_info = VideoInfo(
                    title=tikhub_metadata.title,
                    author=tikhub_metadata.author,
                    channel_id=tikhub_metadata.channel_id,
                    duration=tikhub_metadata.duration,
                    description=tikhub_metadata.description,
                    upload_date=tikhub_metadata.upload_date,
                    view_count=tikhub_metadata.view_count,
                    thumbnail=tikhub_metadata.thumbnail,
                )

                logger.info(
                    f"[MetadataService] TikHub success: {video_info.title} "
                    f"by {video_info.author}"
                )

                return video_info

            except (DownloaderError, DownloaderNotAvailable) as e:
                logger.warning(f"[MetadataService] TikHub failed: {e}")
            except Exception as e:
                logger.warning(f"[MetadataService] TikHub unexpected error: {e}")

        try:
            logger.info(f"[MetadataService] Falling back to yt-dlp for {video_id}")
            video_info = await get_video_info(video_url, self.settings)

            logger.info(
                f"[MetadataService] yt-dlp success: {video_info.title} "
                f"by {video_info.author} ({video_info.duration}s)"
            )

            return video_info

        except DownloadError as e:
            logger.warning(
                f"[MetadataService] yt-dlp failed: {e.error_code.value} - {e.message}"
            )
        except Exception as e:
            logger.warning(f"[MetadataService] yt-dlp unexpected error: {e}")

        logger.error(f"[MetadataService] All methods failed for {video_id}")
        return None

    def merge_metadata(
        self,
        auto_metadata: Optional[VideoInfo],
        manual_metadata: Optional[dict],
    ) -> VideoInfo:
        result = VideoInfo()

        if auto_metadata:
            result.title = auto_metadata.title
            result.author = auto_metadata.author
            result.channel_id = auto_metadata.channel_id
            result.duration = auto_metadata.duration
            result.description = auto_metadata.description
            result.upload_date = auto_metadata.upload_date
            result.view_count = auto_metadata.view_count
            result.thumbnail = auto_metadata.thumbnail

        if manual_metadata:
            if manual_metadata.get("title"):
                result.title = manual_metadata["title"]
            if manual_metadata.get("author"):
                result.author = manual_metadata["author"]
            if manual_metadata.get("channel_id"):
                result.channel_id = manual_metadata["channel_id"]
            if manual_metadata.get("duration") is not None:
                result.duration = manual_metadata["duration"]
            if manual_metadata.get("description"):
                result.description = manual_metadata["description"]
            if manual_metadata.get("upload_date"):
                result.upload_date = manual_metadata["upload_date"]
            if manual_metadata.get("view_count") is not None:
                result.view_count = manual_metadata["view_count"]
            if manual_metadata.get("thumbnail"):
                result.thumbnail = manual_metadata["thumbnail"]

        return result
