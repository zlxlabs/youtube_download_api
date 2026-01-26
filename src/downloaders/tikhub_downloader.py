"""
TikHub API 下载器实现。

使用 TikHub 的 YouTube API 获取视频信息和下载链接。
"""

import asyncio
from pathlib import Path
from typing import Any, Optional

import httpx

from src.config import Settings
from src.core.downloader import DownloadCancelledError
from src.db.models import ErrorCode
from src.downloaders.base import BaseDownloader
from src.downloaders.exceptions import DownloaderError, DownloaderNotAvailable
from src.downloaders.models import DownloaderResult, DownloaderType, VideoMetadata
from src.utils.logger import logger


# TikHub API endpoints
TIKHUB_BASE_URL = "https://api.tikhub.io/api/v1/youtube/web"
TIKHUB_VIDEO_INFO_ENDPOINT = f"{TIKHUB_BASE_URL}/get_video_info"


class TikHubDownloader(BaseDownloader):
    """
    TikHub API 下载器实现。

    使用 TikHub API 获取视频元数据和直链下载音频。
    优点：稳定性高，不受 YouTube 限流影响。
    缺点：需要付费 API（0.002$/次）。
    """

    def __init__(self, settings: Settings):
        """
        初始化 TikHub 下载器。

        Args:
            settings: 应用配置
        """
        self.settings = settings
        self.api_key = settings.tikhub_api_key

        # 配置 httpx 客户端
        # - 启用自动重定向（YouTube 音频链接有 302）
        # - 设置合理的超时（连接 30s，读取 5 分钟）
        # - 增加连接池限制
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, read=300.0),  # 连接 30s，读取 5 分钟
            follow_redirects=True,
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

        logger.debug(
            f"[tikhub] HTTP client initialized: "
            f"timeout=(connect=30s, read=300s), follow_redirects=True"
        )

    @property
    def name(self) -> str:
        """下载器名称。"""
        return "tikhub"

    @property
    def downloader_type(self) -> DownloaderType:
        """下载器类型。"""
        return DownloaderType.TIKHUB

    @property
    def is_available(self) -> bool:
        """
        检查下载器是否可用。

        需要配置 TIKHUB_API_KEY。
        """
        return bool(self.api_key)

    async def download(
        self,
        video_url: str,
        video_id: str,
        output_dir: Path,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> DownloaderResult:
        """
        下载视频音频和字幕。

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID
            output_dir: 输出目录（临时目录）
            include_audio: 是否下载音频
            include_transcript: 是否获取字幕

        Returns:
            DownloaderResult 包含下载结果

        Raises:
            DownloaderNotAvailable: 如果 API key 未配置
            DownloaderError: 下载失败时抛出
        """
        if not self.is_available:
            raise DownloaderNotAvailable(self.name, "API key not configured")

        logger.info(
            f"[tikhub] Downloading {video_id}: audio={include_audio}, transcript={include_transcript}"
        )

        try:
            # 1. 获取视频信息和资源链接
            video_data = await self._fetch_video_info(
                video_id, include_audio=include_audio, include_transcript=include_transcript
            )

            # 2. 解析视频元数据
            video_metadata = self._parse_video_metadata(video_data, video_id)

            # 3. 下载音频（如果需要）
            audio_path = None
            if include_audio:
                audio_url = self._select_audio_url(video_data)
                if audio_url:
                    # 直接使用简单下载（更可靠）
                    # TODO: 调查为什么流式下载会卡住
                    logger.info("[tikhub] Using simple download (non-streaming) for reliability")
                    audio_path = await self._download_audio_simple(
                        audio_url, output_dir, video_id
                    )
                else:
                    raise DownloaderError(
                        message="No audio URL available",
                        error_code=ErrorCode.DOWNLOAD_FAILED,
                        downloader=self.name,
                    )

            # 4. 下载字幕（如果需要）
            transcript_path = None
            has_transcript = False
            if include_transcript:
                subtitle_info = self._select_subtitle(video_data)
                if subtitle_info:
                    has_transcript = True
                    transcript_path = await self._download_subtitle(
                        subtitle_info, output_dir, video_id
                    )

            logger.info(
                f"[tikhub] Download completed: audio={audio_path}, transcript={transcript_path}"
            )

            return DownloaderResult(
                success=True,
                downloader=self.name,
                video_metadata=video_metadata,
                audio_path=audio_path,
                transcript_path=transcript_path,
                has_transcript=has_transcript,
            )

        except DownloadCancelledError:
            # 下载取消异常直接向上抛出
            raise

        except DownloaderError:
            # 已经是 DownloaderError，直接抛出
            raise

        except httpx.HTTPStatusError as e:
            # HTTP 错误
            error_msg = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error(f"[tikhub] HTTP error: {error_msg}")

            # 判断错误类型
            if e.response.status_code == 401:
                error_code = ErrorCode.DOWNLOAD_FAILED
                msg = "Authentication failed (invalid API key)"
            elif e.response.status_code == 429:
                error_code = ErrorCode.RATE_LIMITED
                msg = "API rate limit exceeded"
            else:
                error_code = ErrorCode.DOWNLOAD_FAILED
                msg = error_msg

            raise DownloaderError(
                message=msg,
                error_code=error_code,
                downloader=self.name,
            ) from e

        except httpx.TimeoutException as e:
            # 超时错误
            logger.error(f"[tikhub] Request timeout: {e}")
            raise DownloaderError(
                message="API request timeout",
                error_code=ErrorCode.NETWORK_ERROR,
                downloader=self.name,
            ) from e

        except Exception as e:
            # 其他未预期错误
            logger.error(f"[tikhub] Unexpected error: {e}")
            raise DownloaderError(
                message=str(e),
                error_code=ErrorCode.DOWNLOAD_FAILED,
                downloader=self.name,
            ) from e

    async def _fetch_video_info(
        self,
        video_id: str,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> dict[str, Any]:
        """
        从 TikHub API 获取视频信息。

        Args:
            video_id: YouTube 视频 ID
            include_audio: 是否请求音频信息
            include_transcript: 是否请求字幕信息

        Returns:
            TikHub API 返回的视频数据

        Raises:
            httpx.HTTPStatusError: HTTP 错误
            DownloaderError: API 返回错误
        """
        params = {
            "video_id": video_id,
            "url_access": "normal",  # 包含音视频直链
            "lang": "zh-CN",
            "videos": "false",  # 不需要视频
            "audios": "auto" if include_audio else "false",
            "subtitles": "true" if include_transcript else "false",
            "related": "false",  # 不需要相关视频
        }

        headers = {
            "accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        logger.debug(f"[tikhub] Fetching video info: {video_id}")

        response = await self.client.get(
            TIKHUB_VIDEO_INFO_ENDPOINT,
            params=params,
            headers=headers,
        )

        response.raise_for_status()

        data = response.json()

        # 检查 API 响应
        if data.get("code") != 200:
            error_msg = data.get("message", "Unknown error")
            raise DownloaderError(
                message=f"TikHub API error: {error_msg}",
                error_code=ErrorCode.DOWNLOAD_FAILED,
                downloader=self.name,
            )

        return data.get("data", {})

    def _parse_video_metadata(
        self, video_data: dict[str, Any], video_id: str
    ) -> VideoMetadata:
        """
        解析 TikHub 返回的视频元数据。

        Args:
            video_data: TikHub API 返回的数据
            video_id: 视频 ID

        Returns:
            VideoMetadata 对象
        """
        channel = video_data.get("channel", {})

        return VideoMetadata(
            video_id=video_id,
            title=video_data.get("title"),
            author=channel.get("name"),
            channel_id=channel.get("id"),
            duration=video_data.get("lengthSeconds"),
            description=video_data.get("description"),
            upload_date=None,  # TikHub 不提供此字段
            view_count=video_data.get("viewCount"),
            thumbnail=self._select_thumbnail(video_data.get("thumbnails", [])),
            source_downloader=self.name,
        )

    def _select_thumbnail(self, thumbnails: list[dict]) -> Optional[str]:
        """选择最佳缩略图 URL。"""
        if not thumbnails:
            return None

        # 选择最大分辨率的缩略图
        best = max(thumbnails, key=lambda t: t.get("width", 0) * t.get("height", 0))
        return best.get("url")

    def _select_audio_url(self, video_data: dict[str, Any]) -> Optional[str]:
        """
        选择最佳音频 URL。

        优先选择 M4A 格式，128kbps 左右。

        Args:
            video_data: TikHub API 返回的数据

        Returns:
            音频 URL，如果没有则返回 None
        """
        audios = video_data.get("audios", {})
        if audios.get("errorId") != "Success":
            logger.warning(f"[tikhub] Audio not available: {audios.get('errorId')}")
            return None

        items = audios.get("items", [])
        if not items:
            return None

        # 筛选 M4A 格式，非 DRC
        m4a_items = [
            item
            for item in items
            if item.get("extension") == "m4a" and not item.get("isDrc", False)
        ]

        if not m4a_items:
            # 如果没有 M4A，选择第一个可用的
            logger.warning("[tikhub] No M4A format available, using first available")
            return items[0].get("url")

        # 选择最接近 128kbps 的（假设 size 与 bitrate 成正比）
        # TikHub 返回的 m4a 通常是固定码率
        selected = m4a_items[0]

        logger.info(
            f"[tikhub] Selected audio: {selected.get('extension')} "
            f"{selected.get('sizeText')} "
            f"(DRC={selected.get('isDrc', False)})"
        )

        return selected.get("url")

    def _select_subtitle(self, video_data: dict[str, Any]) -> Optional[dict]:
        """
        选择最佳字幕。

        优先级：中文 > 英文。

        Args:
            video_data: TikHub API 返回的数据

        Returns:
            字幕信息字典，如果没有则返回 None
        """
        subtitles = video_data.get("subtitles", {})
        if subtitles.get("errorId") != "Success":
            logger.info(f"[tikhub] Subtitles not available: {subtitles.get('errorId')}")
            return None

        items = subtitles.get("items", [])
        if not items:
            return None

        # 优先级：zh -> en -> 其他
        priority_codes = ["zh", "en"]

        for code in priority_codes:
            for item in items:
                if item.get("code") == code:
                    logger.info(f"[tikhub] Selected subtitle: {code}")
                    return item

        # 如果没有优先语言，选择第一个
        logger.info(f"[tikhub] Selected subtitle: {items[0].get('code')} (fallback)")
        return items[0]

    async def _download_audio_simple(
        self, audio_url: str, output_dir: Path, video_id: str
    ) -> Path:
        """
        简单下载方式（非流式，一次性读取全部内容）。

        用于调试或作为流式下载的备选方案。
        """
        output_path = output_dir / f"{video_id}.m4a"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[tikhub] Using simple download method (non-streaming)")
        logger.info(f"[tikhub] Full download URL:\n{audio_url}")

        try:
            timeout = httpx.Timeout(30.0, read=300.0)

            # 一次性下载全部内容
            logger.info("[tikhub] Sending GET request...")
            response = await self.client.get(audio_url, timeout=timeout)
            response.raise_for_status()

            logger.info(f"[tikhub] Got response: {response.status_code}, size: {len(response.content) / 1024 / 1024:.1f}MB")

            # 写入文件
            output_path.write_bytes(response.content)

            logger.info(f"[tikhub] File saved to: {output_path.name}")
            return output_path

        except Exception as e:
            logger.error(f"[tikhub] Simple download failed: {type(e).__name__}: {e}")
            raise DownloaderError(
                message=f"Audio download failed: {e}",
                error_code=ErrorCode.NETWORK_ERROR,
                downloader=self.name,
            ) from e

    async def _download_audio(
        self, audio_url: str, output_dir: Path, video_id: str
    ) -> Path:
        """
        下载音频文件。

        Args:
            audio_url: 音频直链 URL
            output_dir: 输出目录
            video_id: 视频 ID（用于文件名）

        Returns:
            下载的文件路径

        Raises:
            DownloaderError: 下载失败
        """
        output_path = output_dir / f"{video_id}.m4a"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 显示完整 URL 供外部测试
        logger.info(f"[tikhub] Full download URL:\n{audio_url}")

        try:
            # 使用独立的超时设置：连接超时 30s，读取超时 300s
            timeout = httpx.Timeout(30.0, read=300.0)

            logger.info(f"[tikhub] Starting stream download...")

            # 使用 stream 方式下载大文件
            async with self.client.stream("GET", audio_url, timeout=timeout) as response:
                logger.info(f"[tikhub] Got response: {response.status_code}")
                response.raise_for_status()

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0
                last_log_percent = 0

                logger.info(
                    f"[tikhub] File size: {total_size / 1024 / 1024:.1f}MB, "
                    f"starting download to {output_path.name}"
                )

                logger.info("[tikhub] Opening file for writing...")
                with open(output_path, "wb") as f:
                    chunk_count = 0
                    logger.info("[tikhub] Starting to iterate chunks...")

                    try:
                        async for chunk in response.aiter_bytes(chunk_size=65536):  # 64KB chunks
                            if chunk_count == 0:
                                logger.info(f"[tikhub] Received first chunk: {len(chunk)} bytes")

                            f.write(chunk)
                            downloaded += len(chunk)
                            chunk_count += 1

                            # 每 10 个 chunk（约 640KB）输出一次调试信息
                            if chunk_count % 10 == 0:
                                logger.info(
                                    f"[tikhub] Downloaded {chunk_count} chunks, "
                                    f"{downloaded / 1024 / 1024:.1f}MB"
                                )

                            # 每下载 20% 记录一次进度（INFO 级别）
                            if total_size > 0:
                                current_percent = int((downloaded / total_size) * 100)
                                if current_percent >= last_log_percent + 20 or downloaded >= total_size:
                                    logger.info(
                                        f"[tikhub] Progress: {downloaded / 1024 / 1024:.1f}MB "
                                        f"/ {total_size / 1024 / 1024:.1f}MB ({current_percent}%)"
                                    )
                                    last_log_percent = current_percent
                    except Exception as iter_error:
                        logger.error(f"[tikhub] Error during chunk iteration: {type(iter_error).__name__}: {iter_error}")
                        raise

                logger.info(f"[tikhub] Finished iterating, total chunks: {chunk_count}")

            logger.info(
                f"[tikhub] Download completed: {downloaded / 1024 / 1024:.1f}MB "
                f"saved to {output_path.name}"
            )
            return output_path

        except httpx.TimeoutException as e:
            logger.error(f"[tikhub] Download timeout: {e}")
            raise DownloaderError(
                message=f"Audio download timeout after {e}",
                error_code=ErrorCode.NETWORK_ERROR,
                downloader=self.name,
            ) from e

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[tikhub] HTTP error {e.response.status_code} while downloading audio"
            )
            raise DownloaderError(
                message=f"Audio download failed: HTTP {e.response.status_code}",
                error_code=ErrorCode.NETWORK_ERROR,
                downloader=self.name,
            ) from e

        except Exception as e:
            logger.error(
                f"[tikhub] Download failed: {type(e).__name__}: {e}",
                exc_info=True
            )
            raise DownloaderError(
                message=f"Audio download failed: {e}",
                error_code=ErrorCode.NETWORK_ERROR,
                downloader=self.name,
            ) from e

    async def _download_subtitle(
        self, subtitle_info: dict, output_dir: Path, video_id: str
    ) -> Optional[Path]:
        """
        下载字幕文件。

        TikHub 返回的是字幕 URL，需要下载并转换为 SRT 格式。

        Args:
            subtitle_info: 字幕信息字典
            output_dir: 输出目录
            video_id: 视频 ID（用于文件名）

        Returns:
            下载的文件路径，失败返回 None
        """
        subtitle_url = subtitle_info.get("url")
        if not subtitle_url:
            return None

        lang = subtitle_info.get("code", "unknown")
        output_path = output_dir / f"{video_id}.{lang}.srt"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[tikhub] Downloading subtitle ({lang}): {subtitle_url[:80]}...")

        try:
            # TikHub 字幕 URL 指向 YouTube 的 timedtext API，返回 JSON3 格式
            # 需要使用 TikHub 的字幕转换 API
            from src.services.tikhub_service import TikHubService

            tikhub_service = TikHubService(self.settings)
            success = await tikhub_service.fetch_subtitle(
                subtitle_url=subtitle_url,
                output_path=output_path,
                output_format="srt",
            )

            if success:
                logger.info(f"[tikhub] Subtitle saved to: {output_path}")
                return output_path
            else:
                logger.warning("[tikhub] Failed to fetch subtitle")
                return None

        except Exception as e:
            logger.warning(f"[tikhub] Failed to download subtitle: {e}")
            return None

    def should_retry(self, error: Exception) -> bool:
        """
        判断错误是否应该重试当前下载器。

        TikHub 是付费 API，大部分错误不应该重试而应该降级。

        Args:
            error: 捕获的异常

        Returns:
            True 表示应该重试，False 表示应该降级
        """
        # 取消错误不重试
        if isinstance(error, DownloadCancelledError):
            return False

        # 检查错误码
        if isinstance(error, DownloaderError):
            error_code = error.error_code

            # TikHub API 的错误通常应该降级而不是重试
            # 因为是付费服务，重试不会改善结果，反而浪费配额
            # 唯一例外：真正的网络超时（而不是 HTTP 错误）
            if error_code == ErrorCode.NETWORK_ERROR:
                # 检查是否是 HTTP 错误（如 302, 403 等）
                # 这些应该降级，因为重试不会解决问题
                error_msg = str(error.message).lower()
                if any(keyword in error_msg for keyword in ["http", "redirect", "status"]):
                    logger.debug(f"[tikhub] HTTP error detected, will fallback instead of retry")
                    return False

                # 真正的网络超时可以重试一次
                return True

            # 其他错误降级
            return False

        # 默认：降级
        return False

    def should_trigger_circuit_breaker(self, error: Exception) -> bool:
        """
        判断错误是否应该触发熔断器。

        API 配额用尽、认证失败等应该触发熔断器。

        Args:
            error: 捕获的异常

        Returns:
            True 表示应该计入熔断器，False 表示不计入
        """
        # 取消错误不触发熔断
        if isinstance(error, DownloadCancelledError):
            return False

        # 检查错误码
        if isinstance(error, DownloaderError):
            error_code = error.error_code

            # 应该触发熔断的错误
            circuit_breaker_codes = {
                ErrorCode.RATE_LIMITED,  # API 限流
            }

            if error_code in circuit_breaker_codes:
                return True

        # 默认：不触发熔断器
        return False

    async def get_video_metadata(
        self,
        video_url: str,
        video_id: str,
    ) -> Optional[dict]:
        """
        仅获取视频元数据（不下载）。

        Args:
            video_url: YouTube 视频 URL
            video_id: YouTube 视频 ID

        Returns:
            视频元数据字典，失败返回 None
        """
        if not self.is_available:
            return None

        try:
            # 获取视频信息（不包含音频和字幕链接，节省配额）
            video_data = await self._fetch_video_info(
                video_id, include_audio=False, include_transcript=False
            )

            return {
                "video_id": video_id,
                "title": video_data.get("title"),
                "author": video_data.get("channel", {}).get("name"),
                "duration": video_data.get("lengthSeconds"),
            }
        except Exception as e:
            logger.warning(f"[tikhub] Failed to get video metadata: {e}")
            return None

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self.client.aclose()
