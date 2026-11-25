"""直接用首次提取得到的 SubtitleInfo.url 下载字幕，并按三态契约选轨。"""

from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import List, Optional

import httpx

from src.db.models import ErrorCode
from src.downloaders.cdp.models import SubtitleInfo
from src.downloaders.exceptions import DownloaderError
from src.utils.logger import logger

SUBTITLE_PRIORITY = ["zh-Hans", "zh-Hant", "zh", "en"]


def _ms_to_srt_time(ms: int) -> str:
    hours = ms // 3600000
    minutes = (ms % 3600000) // 60000
    seconds = (ms % 60000) // 1000
    milliseconds = ms % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def convert_json3_to_srt(json3_path: Path) -> Optional[Path]:
    """将 YouTube json3 字幕转为 SRT；失败返回 None（调用方保留原文件）。"""
    import json

    try:
        with open(json3_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        srt_lines = []
        index = 1
        for event in data.get("events", []):
            segs = event.get("segs", [])
            text = "".join(seg.get("utf8", "") for seg in segs).strip()
            if not segs or not text or text == "\n":
                continue
            start_ms = event.get("tStartMs", 0)
            end_ms = start_ms + event.get("dDurationMs", 0)
            srt_lines += [
                str(index),
                f"{_ms_to_srt_time(start_ms)} --> {_ms_to_srt_time(end_ms)}",
                text,
                "",
            ]
            index += 1
        if not srt_lines:
            logger.warning(f"[subtitle] No valid subtitles found in {json3_path}")
            return None
        srt_path = json3_path.with_suffix(".srt")
        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        json3_path.unlink()
        return srt_path
    except Exception as e:
        logger.error(f"[subtitle] Failed to convert json3 to srt: {e}")
        return None


def sort_subtitle_tracks(
    subtitles: List[SubtitleInfo],
    original_language: Optional[str] = None,
) -> List[SubtitleInfo]:
    """
    按优先级原地排序字幕轨。

    info.language 缺失时，把带 tlang= 的机翻轨排到原语言轨之后，避免优先打翻译接口触发 429。
    """
    orig_lang_base = (
        original_language.split("-")[0].lower() if original_language else None
    )

    def priority_key(s: SubtitleInfo) -> tuple:
        is_orig_suffix = s.lang.endswith("-orig")
        lang_base = s.lang.replace("-orig", "").split("-")[0].lower()
        matches_original = 1 if orig_lang_base and lang_base == orig_lang_base else 0
        orig_priority = 0
        if matches_original:
            orig_priority = 2 if is_orig_suffix else 1

        lang_without_orig = s.lang.replace("-orig", "")
        try:
            lang_priority = float(SUBTITLE_PRIORITY.index(lang_without_orig))
        except ValueError:
            lang_priority = float(len(SUBTITLE_PRIORITY))
            for i, prio_lang in enumerate(SUBTITLE_PRIORITY):
                if lang_without_orig.startswith(prio_lang):
                    lang_priority = i + 0.5
                    break

        auto_priority = 1 if s.is_auto else 0
        tlang_penalty = (
            0
            if orig_lang_base
            else (1 if "tlang=" in (s.url or "") else 0)
        )
        return (-orig_priority, tlang_penalty, lang_priority, auto_priority)

    subtitles.sort(key=priority_key)
    return subtitles


def _load_cookie_jar(cookie_file: Path) -> MozillaCookieJar:
    jar = MozillaCookieJar()
    if cookie_file.exists():
        jar.filename = str(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
    return jar


async def fetch_subtitle(
    subtitle: SubtitleInfo,
    video_id: str,
    cookie_file: Path,
    output_dir: Path,
    downloader_name: str = "cdp",
) -> Path:
    """
    直接 GET SubtitleInfo.url 下载字幕，写入 `{video_id}.{lang}.{ext}`。

    不二次调用 yt-dlp 整视频提取。url 为空或 HTTP 失败一律上抛。
    """
    if not subtitle.url:
        raise DownloaderError(
            message="Subtitle URL is empty",
            error_code=ErrorCode.DOWNLOAD_FAILED,
            downloader=downloader_name,
            operation="transcript",
        )

    ext = subtitle.ext or "json3"
    output_path = output_dir / f"{video_id}.{subtitle.lang}.{ext}"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient(
            cookies=_load_cookie_jar(cookie_file),
            follow_redirects=True,
            timeout=30.0,
        ) as client:
            response = await client.get(subtitle.url)
    except Exception as e:
        raise DownloaderError(
            message=f"Subtitle download failed: {e}",
            error_code=ErrorCode.NETWORK_ERROR,
            downloader=downloader_name,
            operation="transcript",
        ) from e

    if response.status_code == 429:
        raise DownloaderError(
            message="Too many requests (HTTP 429)",
            error_code=ErrorCode.RATE_LIMITED,
            downloader=downloader_name,
            http_status_code=429,
            operation="transcript",
        )
    if response.status_code >= 400:
        raise DownloaderError(
            message=f"Subtitle HTTP {response.status_code}",
            error_code=ErrorCode.DOWNLOAD_FAILED,
            downloader=downloader_name,
            http_status_code=response.status_code,
            operation="transcript",
        )

    output_path.write_bytes(response.content)
    if output_path.suffix == ".json3":
        srt_file = convert_json3_to_srt(output_path)
        if srt_file:
            return srt_file
    return output_path
