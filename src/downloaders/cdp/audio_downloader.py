"""
CDP 资源下载模块。

负责音频和字幕下载的所有逻辑：
- yt-dlp 音频/字幕 URL 提取
- curl_cffi 下载（单线程 + 分片）
- yt-dlp 兜底下载
- 文件命名和路径管理
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.cdp.models import AudioInfo, ExtractedInfo, SubtitleInfo
from src.downloaders.exceptions import DownloaderError
from src.services.transcode_service import TranscodeService, TranscodeError
from src.utils.helpers import sanitize_filename
from src.utils.logger import logger


# 字幕语言优先级
SUBTITLE_PRIORITY = ["zh-Hans", "zh-Hant", "zh", "en"]

# 非字幕类型的"语言"（需要过滤）
NON_TRANSCRIPT_LANGS = {"live_chat", "live_chat_replay"}


class AudioDownloader:
    """
    CDP 资源下载器。

    实现三层降级下载策略：
    1. curl_cffi 分片下载（大文件，最快）
    2. curl_cffi 单线程下载（TLS 指纹模拟，最优）
    3. yt-dlp 直接下载（兜底）

    同时支持字幕提取和下载。
    """

    def __init__(self, settings: Settings, downloader_name: str = "cdp"):
        """
        初始化资源下载器。

        Args:
            settings: 应用配置
            downloader_name: 下载器名称（用于日志和错误报告）
        """
        self.settings = settings
        self.downloader_name = downloader_name
        self._transcode_service = TranscodeService()

    async def extract_audio_url(
        self,
        video_url: str,
        video_id: str,
        cookie_file: Path,
        task_id: str,
        pot_token: Optional[str] = None,
    ) -> AudioInfo:
        """
        使用 yt-dlp + cookies 提取音频信息（避免触发下载）。

        Args:
            video_url: 视频 URL
            video_id: 视频 ID
            cookie_file: cookies 文件路径
            task_id: 任务 ID
            pot_token: 可选的 PO Token

        Returns:
            AudioInfo: 音频信息

        Raises:
            DownloaderError: 提取失败
        """
        if not YTDLP_AVAILABLE:
            raise DownloaderError(
                message="yt-dlp not available",
                error_code=ErrorCode.CDP_YTDLP_FAILED,
                downloader=self.downloader_name,
            )

        logger.debug(f"[{self.downloader_name}] Extracting audio URL for {video_id}")

        ydl_opts = {
            "cookiefile": str(cookie_file),
            # 排除 DASH 格式，优先选择直接可下载的音频文件
            # 格式优先级：m4a > webm，排除需要清单文件的格式
            "format": "bestaudio[protocol!^=http_dash_segments][protocol!=m3u8]/bestaudio",
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,  # 关键：禁止下载
            "simulate": False,
            "extract_flat": False,
            "no_color": True,
        }

        # 可选：注入 poToken
        if pot_token:
            ydl_opts["extractor_args"] = {"youtube": {"po_token": [pot_token]}}
            logger.info(f"[{self.downloader_name}] Using poToken for {video_id}")

        try:
            # 在线程池中执行（避免阻塞）
            info = await asyncio.to_thread(self._ytdlp_extract_info, video_url, ydl_opts)

            if not info:
                raise DownloaderError(
                    message="yt-dlp returned no info",
                    error_code=ErrorCode.CDP_YTDLP_FAILED,
                    downloader=self.downloader_name,
                )

            # 提取音频 URL
            audio_url = info.get("url")
            if not audio_url:
                raise DownloaderError(
                    message="No audio URL found in yt-dlp output",
                    error_code=ErrorCode.CDP_NO_AUDIO_URL,
                    downloader=self.downloader_name,
                )

            # 验证 URL：确保不是清单文件（MPD/m3u8）
            protocol = info.get("protocol", "")
            format_id = info.get("format_id", "")
            ext = info.get("ext", "")

            logger.debug(
                f"[{self.downloader_name}] Extracted format info: "
                f"protocol={protocol}, format_id={format_id}, ext={ext}"
            )

            # 检查是否是清单文件协议
            invalid_protocols = ["http_dash_segments", "m3u8", "m3u8_native"]
            if any(proto in protocol for proto in invalid_protocols):
                raise DownloaderError(
                    message=f"yt-dlp returned manifest file (protocol={protocol}), not direct audio URL",
                    error_code=ErrorCode.CDP_NO_AUDIO_URL,
                    downloader=self.downloader_name,
                )

            # 检查 URL 是否包含清单文件特征
            url_lower = audio_url.lower()
            if ".mpd" in url_lower or ".m3u8" in url_lower or "manifest" in url_lower:
                raise DownloaderError(
                    message=f"yt-dlp returned manifest URL, not direct audio URL",
                    error_code=ErrorCode.CDP_NO_AUDIO_URL,
                    downloader=self.downloader_name,
                )

            # 构造 AudioInfo
            audio_info = AudioInfo(
                url=audio_url,
                itag=self._parse_itag(audio_url),
                mime_type=info.get("ext", "m4a"),
                title=info.get("title", f"youtube_{video_id}"),
                filesize=info.get("filesize") or info.get("filesize_approx"),
                ext=ext or "m4a",
            )

            logger.info(
                f"[{self.downloader_name}] Extracted audio URL: itag={audio_info.itag}, "
                f"size={audio_info.filesize or 'unknown'}, ext={audio_info.ext}, protocol={protocol}"
            )

            return audio_info

        except Exception as e:
            if isinstance(e, DownloaderError):
                raise
            raise DownloaderError(
                message=f"yt-dlp extraction failed: {str(e)}",
                error_code=ErrorCode.CDP_YTDLP_FAILED,
                downloader=self.downloader_name,
            )

    async def extract_video_info(
        self,
        video_url: str,
        video_id: str,
        cookie_file: Path,
        task_id: str,
        pot_token: Optional[str] = None,
        include_audio: bool = True,
        include_transcript: bool = True,
    ) -> ExtractedInfo:
        """
        使用 yt-dlp + cookies 提取视频完整信息（音频 + 字幕）。

        一次 yt-dlp 调用同时获取音频 URL 和字幕信息，避免重复请求。

        Args:
            video_url: 视频 URL
            video_id: 视频 ID
            cookie_file: cookies 文件路径
            task_id: 任务 ID
            pot_token: 可选的 PO Token
            include_audio: 是否提取音频信息
            include_transcript: 是否提取字幕信息

        Returns:
            ExtractedInfo: 包含音频和字幕信息

        Raises:
            DownloaderError: 提取失败
        """
        if not YTDLP_AVAILABLE:
            raise DownloaderError(
                message="yt-dlp not available",
                error_code=ErrorCode.CDP_YTDLP_FAILED,
                downloader=self.downloader_name,
            )

        logger.debug(
            f"[{self.downloader_name}] Extracting video info for {video_id}: "
            f"audio={include_audio}, transcript={include_transcript}"
        )

        ydl_opts = {
            "cookiefile": str(cookie_file),
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "simulate": False,
            "extract_flat": False,
            "no_color": True,
        }

        # 如果需要音频，设置格式选择
        if include_audio:
            ydl_opts["format"] = "bestaudio[protocol!^=http_dash_segments][protocol!=m3u8]/bestaudio"

        # 可选：注入 poToken
        if pot_token:
            ydl_opts["extractor_args"] = {"youtube": {"po_token": [pot_token]}}
            logger.info(f"[{self.downloader_name}] Using poToken for {video_id}")

        try:
            # 在线程池中执行（避免阻塞）
            info = await asyncio.to_thread(self._ytdlp_extract_info, video_url, ydl_opts)

            if not info:
                raise DownloaderError(
                    message="yt-dlp returned no info",
                    error_code=ErrorCode.CDP_YTDLP_FAILED,
                    downloader=self.downloader_name,
                )

            title = info.get("title", f"youtube_{video_id}")

            # 提取音频信息
            audio_info = None
            if include_audio:
                audio_url = info.get("url")
                if audio_url:
                    # 验证不是清单文件
                    protocol = info.get("protocol", "")
                    invalid_protocols = ["http_dash_segments", "m3u8", "m3u8_native"]
                    url_lower = audio_url.lower()

                    if not any(proto in protocol for proto in invalid_protocols) and \
                       ".mpd" not in url_lower and ".m3u8" not in url_lower:
                        ext = info.get("ext", "m4a")
                        audio_info = AudioInfo(
                            url=audio_url,
                            itag=self._parse_itag(audio_url),
                            mime_type=ext,
                            title=title,
                            filesize=info.get("filesize") or info.get("filesize_approx"),
                            ext=ext,
                        )
                        logger.info(
                            f"[{self.downloader_name}] Extracted audio: "
                            f"itag={audio_info.itag}, ext={audio_info.ext}"
                        )
                    else:
                        logger.warning(
                            f"[{self.downloader_name}] Audio URL is manifest file, skipping"
                        )

            # 提取字幕信息
            subtitles: List[SubtitleInfo] = []
            if include_transcript:
                subtitles = self._extract_subtitle_info(info)
                logger.info(
                    f"[{self.downloader_name}] Found {len(subtitles)} subtitle(s): "
                    f"{[s.lang for s in subtitles[:5]]}"
                    f"{'...' if len(subtitles) > 5 else ''}"
                )

            return ExtractedInfo(
                audio_info=audio_info,
                subtitles=subtitles,
                title=title,
                raw_info=info if self.settings.debug else None,
            )

        except Exception as e:
            if isinstance(e, DownloaderError):
                raise
            raise DownloaderError(
                message=f"yt-dlp extraction failed: {str(e)}",
                error_code=ErrorCode.CDP_YTDLP_FAILED,
                downloader=self.downloader_name,
            )

    def _extract_subtitle_info(self, info: Dict[str, Any]) -> List[SubtitleInfo]:
        """
        从 yt-dlp info 中提取字幕信息。

        按优先级排序：中文 > 英文 > 其他，手动字幕 > 自动字幕。

        Args:
            info: yt-dlp 提取的视频信息

        Returns:
            按优先级排序的字幕列表
        """
        subtitles: List[SubtitleInfo] = []

        # 提取手动字幕
        if info.get("subtitles"):
            for lang, formats in info["subtitles"].items():
                if lang in NON_TRANSCRIPT_LANGS or lang.startswith("live_chat"):
                    continue
                url, ext = self._find_best_subtitle_url(formats)
                if url:
                    subtitles.append(SubtitleInfo(
                        lang=lang,
                        url=url,
                        ext=ext,
                        is_auto=False,
                    ))

        # 提取自动字幕
        if info.get("automatic_captions"):
            for lang, formats in info["automatic_captions"].items():
                if lang in NON_TRANSCRIPT_LANGS or lang.startswith("live_chat"):
                    continue
                # 跳过已有手动字幕的语言
                if any(s.lang == lang and not s.is_auto for s in subtitles):
                    continue
                url, ext = self._find_best_subtitle_url(formats)
                if url:
                    subtitles.append(SubtitleInfo(
                        lang=lang,
                        url=url,
                        ext=ext,
                        is_auto=True,
                    ))

        # 按优先级排序
        def priority_key(s: SubtitleInfo) -> tuple:
            try:
                lang_priority = float(SUBTITLE_PRIORITY.index(s.lang))
            except ValueError:
                # 检查是否是优先语言的变体（如 zh-TW）
                for i, prio_lang in enumerate(SUBTITLE_PRIORITY):
                    if s.lang.startswith(prio_lang):
                        lang_priority = i + 0.5
                        break
                else:
                    lang_priority = float(len(SUBTITLE_PRIORITY))
            # 手动字幕优先于自动字幕
            auto_priority = 1 if s.is_auto else 0
            return (lang_priority, auto_priority)

        subtitles.sort(key=priority_key)
        return subtitles

    def _find_best_subtitle_url(
        self, formats: List[Dict[str, Any]]
    ) -> tuple[Optional[str], str]:
        """
        从字幕格式列表中选择最佳 URL。

        优先 json3 格式（便于后续处理）。

        Args:
            formats: yt-dlp 字幕格式列表

        Returns:
            (url, ext) 元组
        """
        if not formats:
            return None, ""

        # 优先 json3 格式
        for fmt in formats:
            if fmt.get("ext") == "json3":
                url = fmt.get("url")
                if url:
                    return str(url), "json3"

        # 其次 vtt 格式
        for fmt in formats:
            if fmt.get("ext") == "vtt":
                url = fmt.get("url")
                if url:
                    return str(url), "vtt"

        # 最后尝试任何可用格式
        for fmt in formats:
            url = fmt.get("url")
            if url:
                return str(url), fmt.get("ext", "vtt")

        return None, ""

    async def download_subtitle(
        self,
        video_url: str,
        video_id: str,
        cookie_file: Path,
        output_dir: Path,
        subtitle_lang: Optional[str] = None,
    ) -> Optional[Path]:
        """
        使用 yt-dlp 下载字幕。

        Args:
            video_url: 视频 URL
            video_id: 视频 ID
            cookie_file: cookies 文件路径
            output_dir: 输出目录
            subtitle_lang: 指定字幕语言（None 表示按优先级自动选择）

        Returns:
            字幕文件路径，失败返回 None
        """
        if not YTDLP_AVAILABLE:
            logger.error(f"[{self.downloader_name}] yt-dlp not available for subtitle download")
            return None

        logger.info(f"[{self.downloader_name}] Downloading subtitle for {video_id}")

        ydl_opts = {
            "cookiefile": str(cookie_file),
            "quiet": False,
            "no_warnings": False,
            "skip_download": True,  # 不下载视频/音频
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitlesformat": "json3/vtt/best",
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            "paths": {"home": str(output_dir)},
            # 使用与主下载器相同的客户端策略
            "extractor_args": {
                "youtube": {
                    "player_client": ["tv_embedded", "web_creator"],
                }
            },
        }

        # 设置字幕语言
        if subtitle_lang:
            ydl_opts["subtitleslangs"] = [subtitle_lang]
        else:
            ydl_opts["subtitleslangs"] = SUBTITLE_PRIORITY

        try:
            # 在线程池中执行
            await asyncio.to_thread(self._ytdlp_download_subtitle, video_url, ydl_opts)

            # 查找下载的字幕文件
            for file in output_dir.iterdir():
                if file.stem.startswith(video_id) and file.suffix in [
                    ".vtt", ".srt", ".json3", ".ttml", ".json"
                ]:
                    logger.info(
                        f"[{self.downloader_name}] Subtitle downloaded: "
                        f"{file.name} ({file.stat().st_size} bytes)"
                    )
                    return file

            logger.warning(f"[{self.downloader_name}] No subtitle file found after download")
            return None

        except Exception as e:
            logger.error(f"[{self.downloader_name}] Subtitle download failed: {e}")
            return None

    def _ytdlp_download_subtitle(self, video_url: str, ydl_opts: dict) -> None:
        """在同步上下文中执行 yt-dlp 字幕下载（用于线程池）。"""
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

    async def download_audio(
        self,
        audio_info: AudioInfo,
        video_id: str,
        task_id: str,
        output_dir: Path,
        headers: Dict[str, str],
    ) -> Path:
        """
        下载音频（三层降级，使用真实 Headers）。

        降级策略（从最优到兜底）：
        1. curl_cffi 分片下载（启用时，大文件使用）- 最快
        2. 失败 → curl_cffi 单线程（TLS 指纹 + Headers）- 最优
        3. 失败 → yt-dlp 直接下载（使用 cookies）- 兜底

        Args:
            audio_info: 音频信息
            video_id: 视频 ID
            task_id: 任务 ID
            output_dir: 输出目录
            headers: 真实请求 headers

        Returns:
            Path: 音频文件路径

        Raises:
            DownloaderError: 下载失败
        """
        logger.info(f"[{self.downloader_name}] Downloading audio for {video_id}")

        # 生成目标文件名
        safe_title = self._sanitize_filename(audio_info.title)
        filename = f"{safe_title}_itag{audio_info.itag or 'na'}.{audio_info.ext}"
        target_path = output_dir / filename

        # 收集所有错误
        errors = []

        # 1. 优先：curl_cffi 分片下载（如果启用且文件足够大）
        if (
            self.settings.cdp_use_curl_cffi
            and self.settings.cdp_enable_multipart
            and audio_info.filesize
            and audio_info.filesize >= self.settings.cdp_multipart_min_size
        ):
            try:
                success = await self._download_with_curl_cffi_multipart(
                    url=audio_info.url,
                    target_path=target_path,
                    expected_size=audio_info.filesize,
                    headers=headers,
                )
                if success:
                    logger.info(
                        f"[{self.downloader_name}] Downloaded via curl_cffi (multipart): {target_path}"
                    )
                    # 转码为 m4a（如果需要）
                    final_path = await self._convert_to_m4a_if_needed(target_path, output_dir)
                    return final_path
            except DownloaderError as e:
                # 403 错误：停止尝试，直接抛出（触发 IP 熔断）
                if e.error_code == ErrorCode.CDP_DOWNLOAD_403:
                    logger.error(f"[{self.downloader_name}] HTTP 403 detected, stopping download attempts")
                    raise
                errors.append(f"curl_cffi_multipart: {e.message}")
                logger.warning(
                    f"[{self.downloader_name}] curl_cffi multipart download failed: {e.message}, "
                    "falling back to single-thread"
                )
            except Exception as e:
                errors.append(f"curl_cffi_multipart: {str(e)}")
                logger.warning(
                    f"[{self.downloader_name}] curl_cffi multipart download failed: {e}, "
                    "falling back to single-thread"
                )

        # 2. 降级：curl_cffi 单线程下载（TLS 指纹模拟 + 真实 Headers）
        if self.settings.cdp_use_curl_cffi:
            try:
                success = await self._download_with_curl_cffi(
                    url=audio_info.url,
                    target_path=target_path,
                    expected_size=audio_info.filesize,
                    headers=headers,
                )
                if success:
                    logger.info(f"[{self.downloader_name}] Downloaded via curl_cffi: {target_path}")
                    # 转码为 m4a（如果需要）
                    final_path = await self._convert_to_m4a_if_needed(target_path, output_dir)
                    return final_path
            except DownloaderError as e:
                # 403 错误：停止尝试，直接抛出（触发 IP 熔断）
                if e.error_code == ErrorCode.CDP_DOWNLOAD_403:
                    logger.error(f"[{self.downloader_name}] HTTP 403 detected, stopping download attempts")
                    raise
                errors.append(f"curl_cffi: {e.message}")
                logger.warning(f"[{self.downloader_name}] curl_cffi download failed: {e.message}")
            except Exception as e:
                errors.append(f"curl_cffi: {str(e)}")
                logger.warning(f"[{self.downloader_name}] curl_cffi download failed: {e}")

        # 3. 兜底：yt-dlp 直接下载（使用 cookies）
        logger.warning(f"[{self.downloader_name}] Falling back to yt-dlp download")
        try:
            # 获取 cookie 文件路径
            cookie_file = self.settings.data_dir / "tmp" / f"{task_id}.cookies.txt"

            ytdlp_path = await self._download_with_ytdlp(
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                cookie_file=cookie_file,
                output_dir=output_dir,
                expected_filename=filename,
            )
            if ytdlp_path and ytdlp_path.exists():
                logger.info(f"[{self.downloader_name}] Downloaded via yt-dlp: {ytdlp_path}")
                # 转码为 m4a（如果需要）
                final_path = await self._convert_to_m4a_if_needed(ytdlp_path, output_dir)
                return final_path
        except Exception as e:
            errors.append(f"ytdlp: {str(e)}")
            logger.error(f"[{self.downloader_name}] yt-dlp download failed: {e}")

        # 所有方法都失败
        all_errors = "\n".join(f"  - {e}" for e in errors)
        raise DownloaderError(
            message=f"All download methods failed:\n{all_errors}",
            error_code=ErrorCode.CDP_DOWNLOAD_FAILED,
            downloader=self.downloader_name,
        )

    def _sanitize_filename(self, text: str) -> str:
        """
        清理文件名中的非法字符，并限制文件名长度。

        为了避免"文件名过长"错误，需要限制文件名的字节长度而非字符长度。
        考虑到后续会添加 _itagXXX.ext.partN 等后缀（约30-40字节），
        这里限制基础文件名为最多80字节（约26-27个中文字符）。

        Args:
            text: 原始文件名

        Returns:
            str: 清理后的安全文件名
        """
        if not text:
            return "youtube_audio"

        # 使用通用的 sanitize_filename 函数，限制为80字节
        # 文件系统通常限制文件名为255字节
        # 保留80字节给基础文件名，剩余175字节给 _itag251.webm.part0 等后缀
        return sanitize_filename(text, max_bytes=80)

    def _parse_itag(self, url: str) -> Optional[int]:
        """从 URL 中解析 itag。"""
        try:
            query = parse_qs(urlparse(url).query)
            itag = query.get("itag", [None])[0]
            return int(itag) if itag else None
        except Exception:
            return None

    def _ytdlp_extract_info(self, video_url: str, ydl_opts: dict) -> dict:
        """在同步上下文中执行 yt-dlp 提取（用于线程池）。"""
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(video_url, download=False)
            return result if result else {}

    def _calculate_dynamic_chunks(
        self,
        total_size: int,
        min_chunk_mb: float = 2.0,
        max_chunk_mb: float = 8.0,
    ) -> list[tuple[int, int, int]]:
        """
        动态计算分片，每个分片大小在指定范围内随机。

        模拟浏览器播放器的动态缓冲行为，分片大小不规整，
        降低被风控系统识别为下载工具的风险。

        Args:
            total_size: 文件总大小（字节）
            min_chunk_mb: 最小分片大小（MB）
            max_chunk_mb: 最大分片大小（MB）

        Returns:
            List[(chunk_idx, start_byte, end_byte)]
        """
        import random

        min_chunk = int(min_chunk_mb * 1024 * 1024)
        max_chunk = int(max_chunk_mb * 1024 * 1024)

        ranges = []
        current_pos = 0
        chunk_idx = 0

        while current_pos < total_size:
            remaining = total_size - current_pos

            if remaining <= max_chunk:
                # 剩余不足一个最大分片，直接取完
                ranges.append((chunk_idx, current_pos, total_size - 1))
                break

            # 随机分片大小
            chunk_size = random.randint(min_chunk, max_chunk)
            end = current_pos + chunk_size - 1

            ranges.append((chunk_idx, current_pos, end))
            current_pos = end + 1
            chunk_idx += 1

        return ranges

    async def _download_with_curl_cffi_multipart(
        self,
        url: str,
        target_path: Path,
        expected_size: int,
        headers: Dict[str, str],
    ) -> bool:
        """
        curl_cffi 分片并发下载（模拟浏览器播放器行为）。

        改进策略（降低风控风险）：
        - 动态分片：每个分片 2-8MB 随机大小，模拟播放器缓冲
        - 并发控制：信号量限制最多 6 个并发（浏览器连接数限制）
        - 顺序启动：按分片顺序启动任务，模拟播放器预加载
        - 随机延迟：每个任务启动前添加随机延迟
        - 进度日志：实时显示下载进度

        Args:
            url: 下载 URL
            target_path: 目标文件路径
            expected_size: 文件大小（必需，用于计算分片）
            headers: 请求头（从 CDP 提取）

        Returns:
            bool: 下载是否成功
        """
        import random

        if not expected_size:
            logger.warning(f"[{self.downloader_name}] Cannot use multipart without expected_size")
            return False

        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            logger.warning(f"[{self.downloader_name}] curl_cffi not installed, skipping multipart")
            return False

        # 动态计算分片（2-8MB 随机）
        ranges = self._calculate_dynamic_chunks(expected_size)
        num_chunks = len(ranges)

        # 并发控制配置
        max_concurrent = self.settings.cdp_multipart_chunks  # 复用配置项作为最大并发数
        max_retries = 3  # 单个分片最大重试次数

        logger.info(
            f"[{self.downloader_name}] Starting multipart download: "
            f"{num_chunks} chunks (2-8MB each), max_concurrent={max_concurrent}, "
            f"total_size={expected_size / 1024 / 1024:.2f}MB"
        )

        # 分片文件路径映射
        part_files = {
            idx: target_path.with_suffix(f"{target_path.suffix}.part{idx}")
            for idx, _, _ in ranges
        }

        # 进度追踪
        completed_chunks = 0
        downloaded_bytes = 0
        progress_lock = asyncio.Lock()

        # 信号量控制并发
        semaphore = asyncio.Semaphore(max_concurrent)

        async def download_chunk_with_retry(chunk_idx: int, start: int, end: int) -> int:
            """
            下载单个分片（带重试机制）。

            Args:
                chunk_idx: 分片索引
                start: 起始字节
                end: 结束字节

            Returns:
                int: 下载的字节数

            Raises:
                DownloaderError: 重试耗尽后仍失败
            """
            nonlocal completed_chunks, downloaded_bytes

            async with semaphore:
                # 随机启动延迟（100-400ms），模拟播放器渐进式请求
                delay = random.uniform(0.1, 0.4)
                await asyncio.sleep(delay)

                part_file = part_files[chunk_idx]
                chunk_size = end - start + 1
                chunk_headers = headers.copy()
                chunk_headers["Range"] = f"bytes={start}-{end}"

                # 检查是否已下载（断点续传）
                if part_file.exists():
                    existing_size = part_file.stat().st_size
                    if existing_size >= chunk_size:
                        logger.debug(
                            f"[{self.downloader_name}] Chunk {chunk_idx} already exists, skipping"
                        )
                        async with progress_lock:
                            completed_chunks += 1
                            downloaded_bytes += existing_size
                        return existing_size

                def _sync_download() -> int:
                    """同步下载逻辑（在线程池中执行）"""
                    response = curl_requests.get(
                        url,
                        headers=chunk_headers,
                        impersonate="chrome120",
                        verify=False,
                        timeout=(30, 120),
                        allow_redirects=True,
                        stream=True,
                    )

                    try:
                        if response.status_code == 403:
                            raise DownloaderError(
                                message=f"HTTP 403 for chunk {chunk_idx}",
                                error_code=ErrorCode.CDP_DOWNLOAD_403,
                                downloader=self.downloader_name,
                                http_status_code=403,
                                stop_fallback=True,
                            )

                        if response.status_code not in (200, 206):
                            raise DownloaderError(
                                message=f"HTTP {response.status_code} for chunk {chunk_idx}",
                                error_code=ErrorCode.CDP_DOWNLOAD_FAILED,
                                downloader=self.downloader_name,
                                http_status_code=response.status_code,
                            )

                        # 流式写入分片文件
                        bytes_written = 0
                        with part_file.open("wb") as f:
                            for chunk_data in response.iter_content(chunk_size=8192):
                                if chunk_data:
                                    f.write(chunk_data)
                                    bytes_written += len(chunk_data)

                        return bytes_written
                    finally:
                        response.close()

                # 重试逻辑
                last_error: Optional[Exception] = None
                for attempt in range(max_retries):
                    try:
                        bytes_downloaded = await asyncio.to_thread(_sync_download)

                        # 更新进度
                        async with progress_lock:
                            completed_chunks += 1
                            downloaded_bytes += bytes_downloaded
                            progress_pct = (downloaded_bytes / expected_size) * 100
                            logger.info(
                                f"[{self.downloader_name}] Progress: {completed_chunks}/{num_chunks} chunks, "
                                f"{downloaded_bytes / 1024 / 1024:.2f}/{expected_size / 1024 / 1024:.2f}MB "
                                f"({progress_pct:.1f}%)"
                            )

                        return bytes_downloaded

                    except DownloaderError as e:
                        # 403 错误不重试，直接抛出
                        if e.error_code == ErrorCode.CDP_DOWNLOAD_403:
                            raise
                        last_error = e
                    except Exception as e:
                        last_error = e

                    # 重试前清理可能的部分文件
                    if part_file.exists():
                        part_file.unlink()

                    # 指数退避延迟（1s, 2s, 4s）
                    if attempt < max_retries - 1:
                        retry_delay = (2 ** attempt) * (1 + random.uniform(0, 0.5))
                        logger.warning(
                            f"[{self.downloader_name}] Chunk {chunk_idx} failed (attempt {attempt + 1}/{max_retries}), "
                            f"retrying in {retry_delay:.1f}s: {last_error}"
                        )
                        await asyncio.sleep(retry_delay)

                # 所有重试都失败
                raise DownloaderError(
                    message=f"Chunk {chunk_idx} failed after {max_retries} retries: {last_error}",
                    error_code=ErrorCode.CDP_DOWNLOAD_FAILED,
                    downloader=self.downloader_name,
                )

        # 按顺序创建任务（信号量控制实际并发）
        try:
            tasks = []
            for idx, start, end in ranges:
                # 微小间隔确保任务按顺序排队获取信号量
                await asyncio.sleep(0.02)  # 20ms
                task = asyncio.create_task(download_chunk_with_retry(idx, start, end))
                tasks.append(task)

            await asyncio.gather(*tasks)

        except DownloaderError:
            # 清理分片文件
            for pf in part_files.values():
                if pf.exists():
                    pf.unlink()
            raise
        except Exception as e:
            logger.error(f"[{self.downloader_name}] Multipart download failed: {e}")
            # 清理分片文件
            for pf in part_files.values():
                if pf.exists():
                    pf.unlink()
            return False

        # 合并分片
        logger.info(f"[{self.downloader_name}] Merging {num_chunks} chunks...")
        try:
            with target_path.open("wb") as outfile:
                for idx, _, _ in ranges:
                    part_file = part_files[idx]
                    if not part_file.exists():
                        raise Exception(f"Chunk {idx} file not found: {part_file}")

                    with part_file.open("rb") as infile:
                        outfile.write(infile.read())

                    # 删除分片文件
                    part_file.unlink()

            # 校验文件大小
            final_size = target_path.stat().st_size
            if final_size < expected_size * 0.95:
                logger.warning(
                    f"[{self.downloader_name}] Multipart size mismatch: "
                    f"got {final_size}, expected {expected_size}"
                )
                return False

            logger.info(
                f"[{self.downloader_name}] Multipart download completed: "
                f"{final_size / 1024 / 1024:.2f}MB, {num_chunks} chunks merged"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.downloader_name}] Failed to merge chunks: {e}")
            # 清理残留文件
            if target_path.exists():
                target_path.unlink()
            for pf in part_files.values():
                if pf.exists():
                    pf.unlink()
            return False

    async def _download_with_curl_cffi(
        self,
        url: str,
        target_path: Path,
        expected_size: Optional[int],
        headers: Dict[str, str],
    ) -> bool:
        """
        curl_cffi 下载（TLS 指纹模拟）。

        Args:
            url: 下载 URL
            target_path: 目标文件路径
            expected_size: 预期文件大小
            headers: 请求头

        Returns:
            bool: 下载是否成功
        """
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            logger.warning(f"[{self.downloader_name}] curl_cffi not installed, skipping")
            return False

        logger.debug(f"[{self.downloader_name}] Attempting download with curl_cffi")

        temp_path = target_path.with_suffix(target_path.suffix + ".part")
        resume_from = temp_path.stat().st_size if temp_path.exists() else 0

        # 如果已下载完成
        if expected_size and resume_from >= expected_size:
            temp_path.replace(target_path)
            return True

        # 设置 Range header（断点续传）
        request_headers = headers.copy()
        if resume_from:
            request_headers["Range"] = f"bytes={resume_from}-"

        try:
            # 使用 curl_cffi 流式下载（Chrome 120 TLS 指纹）
            def _curl_cffi_download():
                """同步流式下载（在线程池中执行）"""
                response = curl_requests.get(
                    url,
                    headers=request_headers,
                    impersonate="chrome120",
                    verify=False,
                    timeout=(30, 120),
                    allow_redirects=True,
                    stream=True,
                )

                try:
                    # 检查状态码
                    if response.status_code == 403:
                        raise DownloaderError(
                            message=f"HTTP 403 for {url}",
                            error_code=ErrorCode.CDP_DOWNLOAD_403,
                            downloader=self.downloader_name,
                            http_status_code=403,
                            stop_fallback=True,
                        )

                    if response.status_code not in (200, 206):
                        raise DownloaderError(
                            message=f"HTTP {response.status_code}",
                            error_code=ErrorCode.CDP_DOWNLOAD_FAILED,
                            downloader=self.downloader_name,
                            http_status_code=response.status_code,
                        )

                    # 流式写入文件
                    mode = "ab" if resume_from else "wb"
                    with temp_path.open(mode) as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                finally:
                    response.close()

            # 在线程池中执行（避免阻塞）
            await asyncio.to_thread(_curl_cffi_download)

            # 校验文件大小
            final_size = temp_path.stat().st_size if temp_path.exists() else 0
            if expected_size and final_size < expected_size * 0.95:
                logger.warning(
                    f"[{self.downloader_name}] Size mismatch: got {final_size}, expected {expected_size}"
                )
                return False

            # 移动到最终位置
            temp_path.replace(target_path)
            return True

        except DownloaderError:
            raise
        except Exception as e:
            logger.warning(f"[{self.downloader_name}] curl_cffi download error: {e}")
            return False

    async def _download_with_ytdlp(
        self,
        video_url: str,
        cookie_file: Path,
        output_dir: Path,
        expected_filename: str,
    ) -> Optional[Path]:
        """
        yt-dlp 直接下载（兜底方案）。

        使用 yt-dlp 直接下载音频文件（使用 cookies）。

        Args:
            video_url: YouTube 视频 URL
            cookie_file: cookies 文件路径
            output_dir: 输出目录
            expected_filename: 预期文件名

        Returns:
            Path: 下载的文件路径，失败返回 None
        """
        if not YTDLP_AVAILABLE:
            logger.error(f"[{self.downloader_name}] yt-dlp not available")
            return None

        logger.info(f"[{self.downloader_name}] Downloading with yt-dlp")

        # 构造输出模板（使用原始文件名）
        outtmpl = str(output_dir / expected_filename.replace(".webm", ".%(ext)s").replace(".m4a", ".%(ext)s"))

        ydl_opts = {
            "cookiefile": str(cookie_file),
            "format": "bestaudio",
            "quiet": True,
            "no_warnings": True,
            "outtmpl": outtmpl,
            "noplaylist": True,
        }

        try:
            # 在线程池中执行（避免阻塞）
            downloaded_path = await asyncio.to_thread(
                self._ytdlp_download_sync, video_url, ydl_opts
            )
            return downloaded_path
        except Exception as e:
            logger.error(f"[{self.downloader_name}] yt-dlp download failed: {e}")
            return None

    def _ytdlp_download_sync(self, video_url: str, ydl_opts: dict) -> Optional[Path]:
        """在同步上下文中执行 yt-dlp 下载（用于线程池）。"""
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                if not info:
                    return None
                downloaded_file = Path(ydl.prepare_filename(info))
                return downloaded_file
        except Exception as e:
            logger.error(f"[{self.downloader_name}] yt-dlp sync download error: {e}")
            return None

    async def _convert_to_m4a_if_needed(
        self,
        file_path: Path,
        output_dir: Path,
    ) -> Path:
        """
        如果文件不是 m4a 格式且启用了转码，转换为 m4a。

        转码功能默认关闭，可通过 CDP_TRANSCODE_TO_M4A=true 启用。
        关闭时保留原始格式（如 webm），节省转码时间。

        Args:
            file_path: 原始音频文件路径
            output_dir: 输出目录

        Returns:
            Path: 音频文件路径（原文件或转换后的 m4a 文件）

        Raises:
            DownloaderError: 转码失败
        """
        # 检查文件格式
        if file_path.suffix.lower() == ".m4a":
            logger.debug(f"[{self.downloader_name}] File already in m4a format: {file_path}")
            return file_path

        # 检查是否启用转码
        if not self.settings.cdp_transcode_to_m4a:
            logger.info(
                f"[{self.downloader_name}] Transcode disabled, keeping original format: {file_path.name}"
            )
            return file_path

        logger.info(
            f"[{self.downloader_name}] Converting {file_path.suffix} to m4a: {file_path.name}"
        )

        try:
            # 调用转码服务
            m4a_path = await self._transcode_service.transcode_to_m4a(
                input_file=file_path,
                output_dir=output_dir,
                target_bitrate=self.settings.audio_quality,
                output_filename=file_path.stem,
            )

            # 转码成功，删除原始文件
            try:
                file_path.unlink()
                logger.debug(f"[{self.downloader_name}] Deleted original file: {file_path}")
            except Exception as e:
                logger.warning(
                    f"[{self.downloader_name}] Failed to delete original file {file_path}: {e}"
                )

            logger.info(
                f"[{self.downloader_name}] Transcode completed: "
                f"{m4a_path.stat().st_size / 1024 / 1024:.2f}MB"
            )

            return m4a_path

        except TranscodeError as e:
            raise DownloaderError(
                message=f"Failed to convert {file_path.suffix} to m4a: {str(e)}",
                error_code=ErrorCode.CDP_TRANSCODE_FAILED,
                downloader=self.downloader_name,
            )
        except Exception as e:
            raise DownloaderError(
                message=f"Unexpected error during transcoding: {str(e)}",
                error_code=ErrorCode.CDP_TRANSCODE_FAILED,
                downloader=self.downloader_name,
            )
