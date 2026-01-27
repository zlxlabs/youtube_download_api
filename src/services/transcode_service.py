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
    SUPPORTED_AUDIO_FORMATS = {".m4a", ".mp3", ".aac", ".opus", ".wav", ".flac", ".ogg"}

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
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout or b"", stderr or b""
        except NotImplementedError:
            logger.warning(log_fallback)
            return await asyncio.to_thread(self._run_command_sync, cmd, timeout)

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

        logger.info(f"Validating file: {input_file}")
        is_valid = await self.validate_file(input_file)
        if not is_valid:
            raise TranscodeError(f"Invalid or corrupted file: {input_file}")

        if output_filename is None:
            output_filename = input_file.stem

        output_file = output_dir / f"{output_filename}.m4a"

        if input_file.suffix.lower() == ".m4a":
            current_bitrate = await self._get_audio_bitrate(input_file)
            if current_bitrate and abs(current_bitrate - target_bitrate) < 10:
                logger.info("File already in m4a format with correct bitrate, skipping transcode")
                import shutil

                shutil.copy(str(input_file), str(output_file))
                return output_file

        cmd = [
            "ffmpeg",
            "-i", str(input_file),
            "-vn",
            "-acodec", "aac",
            "-b:a", f"{target_bitrate}k",
            "-y",
            str(output_file),
        ]

        logger.info(
            f"Transcoding {input_file.suffix} to m4a (bitrate: {target_bitrate}kbps)"
        )

        try:
            returncode, stdout, stderr = await self._run_command(
                cmd,
                timeout=600,
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
            raise TranscodeError("Transcode operation timed out")
        except TranscodeError:
            raise
        except Exception as e:
            logger.error(f"Transcode error: {e}", exc_info=True)
            raise TranscodeError(f"Transcode failed: {e}") from e

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
