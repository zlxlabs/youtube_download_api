"""
CDP 音频下载模块。

负责音频下载的所有逻辑：
- yt-dlp 音频 URL 提取
- curl_cffi 下载（单线程 + 分片）
- yt-dlp 兜底下载
- 文件命名和路径管理
"""

import asyncio
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.cdp.models import AudioInfo
from src.downloaders.exceptions import DownloaderError
from src.services.transcode_service import TranscodeService, TranscodeError
from src.utils.helpers import sanitize_filename
from src.utils.logger import logger


class AudioDownloader:
    """
    CDP 音频下载器。

    实现三层降级下载策略：
    1. curl_cffi 分片下载（大文件，最快）
    2. curl_cffi 单线程下载（TLS 指纹模拟，最优）
    3. yt-dlp 直接下载（兜底）
    """

    def __init__(self, settings: Settings, downloader_name: str = "cdp"):
        """
        初始化音频下载器。

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
            "format": "bestaudio",
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

            # 构造 AudioInfo
            audio_info = AudioInfo(
                url=audio_url,
                itag=self._parse_itag(audio_url),
                mime_type=info.get("ext", "m4a"),
                title=info.get("title", f"youtube_{video_id}"),
                filesize=info.get("filesize") or info.get("filesize_approx"),
                ext=info.get("ext", "m4a"),
            )

            logger.info(
                f"[{self.downloader_name}] Extracted audio URL: itag={audio_info.itag}, "
                f"size={audio_info.filesize or 'unknown'}, ext={audio_info.ext}"
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
            return ydl.extract_info(video_url, download=False)

    async def _download_with_curl_cffi_multipart(
        self,
        url: str,
        target_path: Path,
        expected_size: int,
        headers: Dict[str, str],
    ) -> bool:
        """
        curl_cffi 分片多线程下载（实验性功能）。

        将文件分割为多个块并发下载，然后合并。适合大文件下载，
        但需注意 YouTube 对并发请求的限制。

        策略：
        - 并发数：6个分片（平衡速度和反爬风险）
        - 每个分片独立 Range 请求
        - 所有分片共享相同的 Headers + TLS 指纹
        - 按顺序合并，避免文件破损
        - 支持分片级别的断点续传

        Args:
            url: 下载 URL
            target_path: 目标文件路径
            expected_size: 文件大小（必需，用于计算分片）
            headers: 请求头（从 CDP 提取）

        Returns:
            bool: 下载是否成功
        """
        if not expected_size:
            logger.warning(f"[{self.downloader_name}] Cannot use multipart without expected_size")
            return False

        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            logger.warning(f"[{self.downloader_name}] curl_cffi not installed, skipping multipart")
            return False

        num_chunks = self.settings.cdp_multipart_chunks
        logger.info(
            f"[{self.downloader_name}] Starting multipart download: {num_chunks} chunks, "
            f"size={expected_size / 1024 / 1024:.2f}MB"
        )

        # 计算每个分片的 Range
        chunk_size = expected_size // num_chunks
        ranges = []
        for i in range(num_chunks):
            start = i * chunk_size
            # 最后一个分片包含剩余所有字节
            end = start + chunk_size - 1 if i < num_chunks - 1 else expected_size - 1
            ranges.append((i, start, end))

        # 分片文件路径
        part_files = [
            target_path.with_suffix(f"{target_path.suffix}.part{i}")
            for i in range(num_chunks)
        ]

        # 下载单个分片
        async def download_chunk(chunk_idx: int, start: int, end: int) -> None:
            """下载单个分片（在线程池中执行）"""
            part_file = part_files[chunk_idx]
            chunk_headers = headers.copy()
            chunk_headers["Range"] = f"bytes={start}-{end}"

            # 检查是否已下载（断点续传）
            if part_file.exists():
                existing_size = part_file.stat().st_size
                expected_chunk_size = end - start + 1
                if existing_size >= expected_chunk_size:
                    logger.debug(
                        f"[{self.downloader_name}] Chunk {chunk_idx} already downloaded, skipping"
                    )
                    return

            def _sync_download():
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
                    with part_file.open("wb") as f:
                        for chunk_data in response.iter_content(chunk_size=8192):
                            if chunk_data:
                                f.write(chunk_data)

                    logger.debug(
                        f"[{self.downloader_name}] Chunk {chunk_idx} downloaded: "
                        f"{part_file.stat().st_size / 1024:.2f}KB"
                    )
                finally:
                    response.close()

            await asyncio.to_thread(_sync_download)

        # 并发下载所有分片
        try:
            tasks = [download_chunk(idx, start, end) for idx, start, end in ranges]
            await asyncio.gather(*tasks)
        except DownloaderError:
            # 清理分片文件
            for pf in part_files:
                if pf.exists():
                    pf.unlink()
            raise
        except Exception as e:
            logger.error(f"[{self.downloader_name}] Multipart download failed: {e}")
            # 清理分片文件
            for pf in part_files:
                if pf.exists():
                    pf.unlink()
            return False

        # 合并分片
        logger.info(f"[{self.downloader_name}] Merging chunks...")
        try:
            with target_path.open("wb") as outfile:
                for i, part_file in enumerate(part_files):
                    if not part_file.exists():
                        raise Exception(f"Chunk {i} file not found: {part_file}")

                    with part_file.open("rb") as infile:
                        outfile.write(infile.read())

                    # 删除分片文件
                    part_file.unlink()
                    logger.debug(f"[{self.downloader_name}] Merged and deleted chunk {i}")

            # 校验文件大小
            final_size = target_path.stat().st_size
            if final_size < expected_size * 0.95:
                logger.warning(
                    f"[{self.downloader_name}] Multipart size mismatch: got {final_size}, expected {expected_size}"
                )
                return False

            logger.info(
                f"[{self.downloader_name}] Multipart download completed: {final_size / 1024 / 1024:.2f}MB"
            )
            return True

        except Exception as e:
            logger.error(f"[{self.downloader_name}] Failed to merge chunks: {e}")
            # 清理残留文件
            if target_path.exists():
                target_path.unlink()
            for pf in part_files:
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
        如果文件不是 m4a 格式，转换为 m4a。

        遵循项目标准：所有音频文件统一输出为 M4A 格式（128kbps AAC）。

        Args:
            file_path: 原始音频文件路径
            output_dir: 输出目录

        Returns:
            Path: m4a 文件路径（可能是原文件或转换后的文件）

        Raises:
            DownloaderError: 转码失败
        """
        # 检查文件格式
        if file_path.suffix.lower() == ".m4a":
            logger.debug(f"[{self.downloader_name}] File already in m4a format: {file_path}")
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
