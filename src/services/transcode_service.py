"""
转码服务模块。

使用 ffmpeg 将音频/视频文件转码为标准格式（m4a, 128kbps）。
"""

import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from src.utils.logger import logger


class TranscodeError(Exception):
    """转码失败异常。"""

    pass


class TranscodeService:
    """
    转码服务。

    使用 ffmpeg 将各种格式的音频/视频转码为统一的 m4a 格式。
    """

    SUPPORTED_VIDEO_FORMATS = {".mp4", ".webm", ".mkv", ".avi", ".mov"}
    SUPPORTED_AUDIO_FORMATS = {".m4a", ".mp3", ".aac", ".opus", ".wav", ".flac", ".ogg", ".webm"}

    # 超时配置常量
    TRANSCODE_BASE_TIMEOUT = 60  # 基础超时时间（秒）
    TRANSCODE_TIMEOUT_PER_10MB = 30  # 每 10MB 增加的超时时间（秒）
    TRANSCODE_MIN_TIMEOUT = 120  # 最小超时时间（秒）
    TRANSCODE_MAX_TIMEOUT = 3600  # 最大超时时间（秒），1 小时
    REMUX_BASE_TIMEOUT = 30  # remux 基础超时时间（秒）
    REMUX_TIMEOUT_PER_10MB = 5  # remux 每 10MB 增加的超时时间（秒）
    REMUX_MIN_TIMEOUT = 60  # remux 最小超时时间（秒）
    REMUX_MAX_TIMEOUT = 600  # remux 最大超时时间（秒）

    def __init__(self) -> None:
        if not self._check_ffmpeg():
            logger.warning("ffmpeg not found, transcoding may fail")

    def _check_ffmpeg(self) -> bool:
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _calculate_transcode_timeout(self, file_size_bytes: int) -> int:
        """
        根据文件大小计算转码超时时间。

        Args:
            file_size_bytes: 文件大小（字节）

        Returns:
            超时时间（秒）
        """
        file_size_mb = file_size_bytes / (1024 * 1024)
        timeout = self.TRANSCODE_BASE_TIMEOUT + (file_size_mb / 10) * self.TRANSCODE_TIMEOUT_PER_10MB
        timeout = max(self.TRANSCODE_MIN_TIMEOUT, min(int(timeout), self.TRANSCODE_MAX_TIMEOUT))
        return timeout

    def _calculate_remux_timeout(self, file_size_bytes: int) -> int:
        """
        根据文件大小计算 remux 超时时间。

        Remux 不需要重新编码，速度更快，所以超时时间更短。

        Args:
            file_size_bytes: 文件大小（字节）

        Returns:
            超时时间（秒）
        """
        file_size_mb = file_size_bytes / (1024 * 1024)
        timeout = self.REMUX_BASE_TIMEOUT + (file_size_mb / 10) * self.REMUX_TIMEOUT_PER_10MB
        timeout = max(self.REMUX_MIN_TIMEOUT, min(int(timeout), self.REMUX_MAX_TIMEOUT))
        return timeout

    def is_supported_format(self, file_path: Path) -> bool:
        suffix = file_path.suffix.lower()
        return suffix in self.SUPPORTED_VIDEO_FORMATS or suffix in self.SUPPORTED_AUDIO_FORMATS

    def get_format_type(self, file_path: Path) -> Optional[str]:
        suffix = file_path.suffix.lower()
        if suffix in self.SUPPORTED_VIDEO_FORMATS:
            return "video"
        if suffix in self.SUPPORTED_AUDIO_FORMATS:
            return "audio"
        return None

    @staticmethod
    def _run_command_sync(cmd: list[str], timeout: int) -> tuple[int, bytes, bytes]:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout or b"", result.stderr or b""
        except subprocess.TimeoutExpired:
            return 1, b"", b"command timed out"
        except FileNotFoundError:
            return 1, b"", b"command not found"

    async def _run_command(
        self,
        cmd: list[str],
        timeout: int,
        log_fallback: str,
    ) -> tuple[int, bytes, bytes]:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            # communicate() 返回后进程必已退出，returncode 不会为 None
            returncode = proc.returncode if proc.returncode is not None else -1
            return returncode, stdout or b"", stderr or b""
        except NotImplementedError:
            logger.warning(log_fallback)
            return await asyncio.to_thread(self._run_command_sync, cmd, timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # wait_for/cancel 只取消 communicate() 协程，子进程仍在后台运行，
            # 必须显式 kill，否则 ffmpeg/ffprobe 会泄漏并持续占用 CPU
            if proc is not None:
                await self._kill_process(proc)
            raise

    @staticmethod
    async def _kill_process(proc: asyncio.subprocess.Process) -> None:
        """强制终止子进程并回收，避免僵尸进程。"""
        if proc.returncode is not None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            logger.error(f"Subprocess pid={proc.pid} did not exit after kill")

    async def validate_file(self, file_path: Path) -> bool:
        try:
            if not file_path.exists():
                logger.error(f"File does not exist: {file_path}")
                return False

            file_size = file_path.stat().st_size
            if file_size == 0:
                logger.error(f"File is empty: {file_path}")
                return False

            logger.debug(
                f"Validating file: {file_path} (size: {file_size / 1024 / 1024:.2f} MB)"
            )

            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ]

            returncode, stdout, stderr = await self._run_command(
                cmd,
                timeout=30,
                log_fallback="Async subprocess not supported, falling back to sync ffprobe",
            )

            if returncode != 0:
                stderr_text = stderr.decode() if stderr else "(no error output)"
                logger.error(
                    f"ffprobe validation failed (returncode={returncode}): {stderr_text}"
                )
                return False

            duration_str = stdout.decode().strip()
            if not duration_str or duration_str == "N/A":
                logger.error(
                    f"File has no valid duration: {file_path} (output: '{duration_str}')"
                )
                return False

            logger.debug(f"File validation passed: duration={duration_str}s")
            return True

        except asyncio.TimeoutError:
            logger.error(f"File validation timed out after 30s: {file_path}")
            return False
        except Exception as e:
            logger.error(
                f"File validation error: {type(e).__name__}: {str(e) or repr(e)}",
                exc_info=True,
            )
            return False

    async def transcode_to_m4a(
        self,
        input_file: Path,
        output_dir: Path,
        target_bitrate: int = 128,
        output_filename: Optional[str] = None,
    ) -> Path:
        if not input_file.exists():
            raise TranscodeError(f"Input file not found: {input_file}")

        # 获取文件大小用于计算动态超时
        file_size = input_file.stat().st_size
        file_size_mb = file_size / (1024 * 1024)

        logger.info(f"Validating file: {input_file}")
        is_valid = await self.validate_file(input_file)
        if not is_valid:
            raise TranscodeError(f"Invalid or corrupted file: {input_file}")

        if output_filename is None:
            output_filename = input_file.stem

        output_file = output_dir / f"{output_filename}.m4a"

        # If AAC audio is already present, remux (copy) to m4a for speed.
        audio_codec = await self._get_audio_codec(input_file)
        if audio_codec == "aac":
            remux_timeout = self._calculate_remux_timeout(file_size)
            logger.info(
                f"AAC detected, remuxing audio stream to m4a (no re-encode), "
                f"file size: {file_size_mb:.2f}MB, timeout: {remux_timeout}s"
            )
            cmd = [
                "ffmpeg",
                "-i", str(input_file),
                "-vn",
                "-c:a", "copy",
                "-y",
                str(output_file),
            ]

            returncode, _, stderr = await self._run_command(
                cmd,
                timeout=remux_timeout,
                log_fallback="Async subprocess not supported, falling back to sync ffmpeg",
            )

            if returncode == 0 and output_file.exists():
                if await self.validate_file(output_file):
                    logger.info(f"Remux completed: {output_file}")
                    return output_file
            else:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.warning(f"Remux failed (returncode={returncode}): {error_msg}")
                if output_file.exists():
                    try:
                        output_file.unlink()
                    except OSError:
                        pass

        cmd = [
            "ffmpeg",
            "-i", str(input_file),
            "-vn",
            "-acodec", "aac",
            "-b:a", f"{target_bitrate}k",
            "-y",
            str(output_file),
        ]

        transcode_timeout = self._calculate_transcode_timeout(file_size)
        logger.info(
            f"Transcoding {input_file.suffix} to m4a (bitrate: {target_bitrate}kbps), "
            f"file size: {file_size_mb:.2f}MB, timeout: {transcode_timeout}s"
        )

        try:
            returncode, stdout, stderr = await self._run_command(
                cmd,
                timeout=transcode_timeout,
                log_fallback="Async subprocess not supported, falling back to sync ffmpeg",
            )

            if returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"ffmpeg failed (returncode={returncode}): {error_msg}")
                raise TranscodeError(f"Transcode failed: {error_msg}")

            if not output_file.exists():
                raise TranscodeError("Output file not created")

            if not await self.validate_file(output_file):
                raise TranscodeError("Output file validation failed")

            logger.info(f"Transcode completed: {output_file}")
            return output_file

        except asyncio.TimeoutError:
            logger.error(f"Transcode timed out: {input_file}")
            self._cleanup_partial_output(output_file)
            raise TranscodeError("Transcode operation timed out")
        except TranscodeError:
            self._cleanup_partial_output(output_file)
            raise
        except Exception as e:
            logger.error(f"Transcode error: {e}", exc_info=True)
            self._cleanup_partial_output(output_file)
            raise TranscodeError(f"Transcode failed: {e}") from e

    @staticmethod
    def _cleanup_partial_output(output_file: Path) -> None:
        """转码失败时删除部分写入的输出文件，避免残留文件占用磁盘。"""
        if output_file.exists():
            try:
                output_file.unlink()
                logger.info(f"Removed partial transcode output: {output_file}")
            except OSError as e:
                logger.warning(f"Failed to remove partial output {output_file}: {e}")

    async def _get_audio_bitrate(self, file_path: Path) -> Optional[int]:
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=bit_rate",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ]
            returncode, stdout, _ = await self._run_command(
                cmd,
                timeout=10,
                log_fallback="Async subprocess not supported, falling back to sync ffprobe",
            )

            if returncode == 0:
                bitrate_str = stdout.decode().strip()
                if bitrate_str and bitrate_str != "N/A":
                    bitrate_bps = int(bitrate_str)
                    return bitrate_bps // 1000

        except Exception as e:
            logger.debug(f"Failed to get bitrate: {e}")

        return None

    async def _get_audio_codec(self, file_path: Path) -> Optional[str]:
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ]
            returncode, stdout, _ = await self._run_command(
                cmd,
                timeout=10,
                log_fallback="Async subprocess not supported, falling back to sync ffprobe",
            )
            if returncode == 0:
                codec = stdout.decode().strip()
                return codec or None
        except Exception as e:
            logger.debug(f"Failed to get codec: {e}")
        return None

    async def extract_audio_metadata(self, file_path: Path) -> dict:
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration,bit_rate:stream=codec_name,bit_rate",
                "-of", "json",
                str(file_path),
            ]
            returncode, stdout, _ = await self._run_command(
                cmd,
                timeout=10,
                log_fallback="Async subprocess not supported, falling back to sync ffprobe",
            )

            if returncode == 0:
                import json

                data = json.loads(stdout.decode())

                duration = None
                if "format" in data and "duration" in data["format"]:
                    try:
                        duration = int(float(data["format"]["duration"]))
                    except (ValueError, TypeError):
                        pass

                bitrate = None
                if "format" in data and "bit_rate" in data["format"]:
                    try:
                        bitrate = int(data["format"]["bit_rate"]) // 1000
                    except (ValueError, TypeError):
                        pass

                return {
                    "duration": duration,
                    "bitrate": bitrate,
                }

        except Exception as e:
            logger.warning(f"Failed to extract metadata: {e}")

        return {}
