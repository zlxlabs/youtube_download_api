"""
yt-dlp 下载器实现。

包装现有的 YouTubeDownloader，适配到统一的下载器接口。
"""

from pathlib import Path
from typing import Optional

from src.config import Settings
from src.core.downloader import (
    DownloadCancelledError,
    DownloadError as YtdlpDownloadError,
    YouTubeDownloader,
)
from src.db.models import ErrorCode, VideoInfo
from src.downloaders.base import BaseDownloader
from src.downloaders.exceptions import DownloaderError
from src.downloaders.models import DownloaderResult, DownloaderType, VideoMetadata
from src.utils.logger import logger


class YtdlpDownloader(BaseDownloader):
    """
    yt-dlp 下载器实现。

    使用本地 yt-dlp 库下载音频和字幕，支持 PO Token 和 Cookie 认证。
    """

    def __init__(self, settings: Settings):
        """
        初始化 yt-dlp 下载器。

        Args:
            settings: 应用配置
        """
        self.settings = settings
        self._downloader = YouTubeDownloader(settings)

    @property
    def name(self) -> str:
        """下载器名称。"""
        return "ytdlp"

    @property
    def downloader_type(self) -> DownloaderType:
        """下载器类型。"""
        return DownloaderType.YTDLP

    @property
    def is_available(self) -> bool:
        """
        检查下载器是否可用。

        yt-dlp 始终可用（无需外部依赖）。
        """
        return True

    async def download_resources(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> DownloaderResult:
        """
        下载视频音频和字幕（支持部分成功）。

        新逻辑：
        - 如果只请求音频 → 音频失败则整个失败
        - 如果只请求字幕 → 字幕失败则整个失败
        - 如果请求混合 → 至少一个成功就算部分成功

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID
            output_dir: 输出目录（临时目录）
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕

        Returns:
            DownloaderResult 包含下载结果（可能是部分成功）

        Raises:
            DownloaderError: 完全失败时抛出
            DownloadCancelledError: 下载取消时抛出（直接透传）
        """
        logger.info(
            f"[ytdlp] Downloading {video_id}: "
            f"audio={include_audio}, transcript={include_transcript}"
        )

        # 对于纯音频或纯字幕任务，保持原有逻辑（不支持部分成功）
        if not (include_audio and include_transcript):
            return await self._download_simple(
                video_url, video_id, output_dir, include_audio, include_transcript
            )

        # 混合任务：分步处理，支持部分成功
        return await self._download_with_partial_success(
            video_url, video_id, output_dir
        )

    async def _download_simple(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool,
        include_transcript: bool,
    ) -> DownloaderResult:
        """
        简单下载（纯音频或纯字幕，不支持部分成功）。

        保持原有逻辑，失败即抛出异常。
        """
        try:
            if include_audio:
                # 完整下载模式（包含音频）
                result = await self._downloader.download(
                    video_url=video_url,
                    output_dir=output_dir,
                )
                video_metadata = self._convert_video_info(result.video_info, video_id)

                return DownloaderResult(
                    success=True,
                    downloader=self.name,
                    video_metadata=video_metadata,
                    audio_path=result.audio_path,
                    transcript_path=result.transcript_path,
                    has_transcript=bool(result.transcript_path),
                )

            else:
                # 仅字幕模式
                transcript_result = await self._downloader.extract_transcript_only(
                    video_url=video_url,
                    output_dir=output_dir,
                )
                video_metadata = self._convert_video_info(
                    transcript_result.video_info, video_id
                )

                return DownloaderResult(
                    success=True,
                    downloader=self.name,
                    video_metadata=video_metadata,
                    audio_path=None,
                    transcript_path=transcript_result.transcript_path,
                    has_transcript=transcript_result.has_transcript,
                )

        except DownloadCancelledError:
            raise

        except YtdlpDownloadError as e:
            http_status = getattr(e, "http_status_code", None)
            # 视频级别错误（视频本身的问题，其他下载器也无法处理）
            video_errors = {
                ErrorCode.VIDEO_UNAVAILABLE,
                ErrorCode.VIDEO_PRIVATE,
                ErrorCode.VIDEO_REGION_BLOCKED,
                ErrorCode.VIDEO_AGE_RESTRICTED,
                ErrorCode.VIDEO_LIVE_STREAM,
            }
            stop_fallback = http_status == 403 or e.error_code in video_errors

            if stop_fallback:
                reason = (
                    "video-level issue"
                    if e.error_code in video_errors
                    else "local IP issue (HTTP 403)"
                )
                logger.warning(
                    f"[ytdlp] {reason} - will not try other downloaders"
                )

            logger.error(
                f"[ytdlp] Download failed: {e.error_code.value} - {e.message}"
            )
            raise DownloaderError(
                message=e.message,
                error_code=e.error_code,
                downloader=self.name,
                http_status_code=http_status,
                stop_fallback=stop_fallback,
                operation="audio" if include_audio else "transcript",
            ) from e

        except Exception as e:
            logger.error(f"[ytdlp] Unexpected error: {e}")
            raise DownloaderError(
                message=str(e),
                error_code=ErrorCode.DOWNLOAD_FAILED,
                downloader=self.name,
            ) from e

    async def _download_with_partial_success(
        self, video_url: str, video_id: str, output_dir: Path
    ) -> DownloaderResult:
        """
        混合任务下载（支持部分成功）。

        尝试下载音频和字幕，至少一个成功即返回部分成功结果。
        """
        video_metadata = None
        audio_path = None
        transcript_path = None
        audio_error = None
        audio_error_code = None
        transcript_error = None
        transcript_error_code = None

        # 第一步：调用完整下载（包含音频和字幕）
        try:
            result = await self._downloader.download(
                video_url=video_url,
                output_dir=output_dir,
            )

            # 成功：提取结果
            video_metadata = self._convert_video_info(result.video_info, video_id)
            audio_path = result.audio_path
            transcript_path = result.transcript_path

            # 完全成功
            return DownloaderResult(
                success=True,
                downloader=self.name,
                video_metadata=video_metadata,
                audio_path=audio_path,
                transcript_path=transcript_path,
                has_transcript=bool(transcript_path),
            )

        except DownloadCancelledError:
            # 取消异常直接抛出
            raise

        except YtdlpDownloadError as e:
            # 下载失败，记录错误但不立即抛出
            http_status = getattr(e, "http_status_code", None)
            audio_error = f"{e.error_code.value}: {e.message}"
            audio_error_code = e.error_code.value

            if http_status == 403:
                audio_error = f"IP_BANNED_403: {e.message}"

            logger.warning(
                f"[ytdlp] Full download failed: {audio_error}, "
                f"will try transcript-only mode"
            )

            # 尝试仅字幕模式（作为降级）
            try:
                transcript_result = await self._downloader.extract_transcript_only(
                    video_url=video_url,
                    output_dir=output_dir,
                )

                # 字幕成功
                video_metadata = self._convert_video_info(
                    transcript_result.video_info, video_id
                )
                transcript_path = transcript_result.transcript_path

                # 部分成功：音频失败，字幕成功
                logger.info("[ytdlp] Partial success: transcript OK, audio failed")

                return DownloaderResult(
                    success=True,
                    partial_success=True,
                    downloader=self.name,
                    video_metadata=video_metadata,
                    audio_path=None,
                    transcript_path=transcript_path,
                    has_transcript=transcript_result.has_transcript,
                    audio_error=audio_error,
                    audio_error_code=audio_error_code,
                    failure_details={
                        "audio": {
                            "requested": True,
                            "success": False,
                            "error": audio_error,
                            "error_code": audio_error_code,
                        },
                        "transcript": {
                            "requested": True,
                            "success": True,
                            "error": None,
                        },
                    },
                )

            except YtdlpDownloadError as transcript_err:
                # 字幕也失败
                transcript_error = f"{transcript_err.error_code.value}: {transcript_err.message}"
                transcript_error_code = transcript_err.error_code.value

                logger.error(
                    f"[ytdlp] Both audio and transcript failed: "
                    f"audio={audio_error}, transcript={transcript_error}"
                )

                # 完全失败
                raise DownloaderError(
                    message=f"Both failed - Audio: {audio_error}; Transcript: {transcript_error}",
                    error_code=ErrorCode.DOWNLOAD_FAILED,
                    downloader=self.name,
                    stop_fallback=(http_status == 403),
                    operation="mixed",
                ) from e

    def should_retry(self, error: Exception) -> bool:
        """
        判断错误是否应该重试当前下载器。

        临时性错误（网络错误、超时）应该重试，
        系统性错误（限流、认证失败）应该降级。

        Args:
            error: 捕获的异常

        Returns:
            True 表示应该重试，False 表示应该降级
        """
        # 取消错误不重试
        if isinstance(error, DownloadCancelledError):
            return False

        # 检查错误码
        if isinstance(error, (YtdlpDownloadError, DownloaderError)):
            error_code = getattr(error, "error_code", None)
            if error_code:
                # 临时性错误：网络问题、超时等
                retryable_codes = {
                    ErrorCode.NETWORK_ERROR,
                }
                # 应该降级的错误：限流、认证、视频问题
                fallback_codes = {
                    ErrorCode.RATE_LIMITED,
                    ErrorCode.POT_TOKEN_FAILED,
                    ErrorCode.VIDEO_UNAVAILABLE,
                    ErrorCode.VIDEO_PRIVATE,
                    ErrorCode.VIDEO_AGE_RESTRICTED,
                }

                if error_code in retryable_codes:
                    return True
                if error_code in fallback_codes:
                    return False

        # 默认：降级到下一个下载器
        return False

    def should_trigger_circuit_breaker(self, error: Exception) -> bool:
        """
        判断错误是否应该触发熔断器。

        系统性错误（限流）应该触发熔断器，
        视频特定错误（不存在、私有）不应该触发。

        Args:
            error: 捕获的异常

        Returns:
            True 表示应该计入熔断器，False 表示不计入
        """
        # 取消错误不触发熔断
        if isinstance(error, DownloadCancelledError):
            return False

        # 检查错误码
        if isinstance(error, (YtdlpDownloadError, DownloaderError)):
            error_code = getattr(error, "error_code", None)
            if error_code:
                # 应该触发熔断的错误：系统性故障
                circuit_breaker_codes = {
                    ErrorCode.RATE_LIMITED,  # 限流
                    ErrorCode.POT_TOKEN_FAILED,  # PO Token 失败
                }
                # 不应该触发熔断的错误：视频特定问题
                non_circuit_breaker_codes = {
                    ErrorCode.VIDEO_UNAVAILABLE,
                    ErrorCode.VIDEO_PRIVATE,
                    ErrorCode.VIDEO_REGION_BLOCKED,
                    ErrorCode.VIDEO_AGE_RESTRICTED,
                    ErrorCode.VIDEO_LIVE_STREAM,
                }

                if error_code in circuit_breaker_codes:
                    return True
                if error_code in non_circuit_breaker_codes:
                    return False

        # 默认：不触发熔断器（保守策略）
        return False

    async def fetch_metadata(
        self,
        video_url: str,
        video_id: str,
    ) -> Optional[dict]:
        """
        仅获取视频元数据（不下载任何文件）。

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID

        Returns:
            视频元数据字典，失败返回 None
        """
        try:
            from src.core.downloader import get_video_info

            video_info = await get_video_info(video_url, self.settings)
            return {
                "video_id": video_id,
                "title": video_info.title,
                "author": video_info.author,
                "duration": video_info.duration,
            }
        except Exception as e:
            logger.warning(f"[ytdlp] Failed to get video metadata: {e}")
            return None

    def _convert_video_info(self, video_info: VideoInfo, video_id: str) -> VideoMetadata:
        """
        转换 VideoInfo 到 VideoMetadata。

        Args:
            video_info: yt-dlp 的 VideoInfo 对象
            video_id: 视频 ID

        Returns:
            统一的 VideoMetadata 对象
        """
        return VideoMetadata(
            video_id=video_id,
            title=video_info.title,
            author=video_info.author,
            channel_id=video_info.channel_id,
            duration=video_info.duration,
            description=video_info.description,
            upload_date=video_info.upload_date,
            view_count=video_info.view_count,
            thumbnail=video_info.thumbnail,
            source_downloader=self.name,
        )

    def cancel(self) -> None:
        """取消当前下载。"""
        self._downloader.cancel()

    def reset_cancel(self) -> None:
        """重置取消状态。"""
        self._downloader.reset_cancel()
