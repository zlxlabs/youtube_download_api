"""
CDP 字幕下载调试测试脚本。

用于诊断特定视频的字幕下载问题。

使用方法：
    # 设置 CDP_URLS 环境变量
    $env:CDP_URLS="http://192.168.31.222:9223"

    # 运行测试
    uv run python -m tests.downloaders.cdp.test_cdp_subtitle_debug

或者直接运行：
    uv run python tests/downloaders/cdp/test_cdp_subtitle_debug.py
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Windows UTF-8 输出
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[ERROR] Playwright not installed, run: uv pip install playwright")

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    print("[ERROR] yt-dlp not installed, run: uv pip install yt-dlp")


# ==================== 配置 ====================
CDP_URL = os.getenv("CDP_URLS", "http://192.168.31.222:9223")
VIDEO_URL = "https://www.youtube.com/watch?v=Uqr2U24uxOs"
VIDEO_ID = "Uqr2U24uxOs"

# 字幕语言优先级
SUBTITLE_PRIORITY = ["zh-Hans", "zh-Hant", "zh", "en"]
NON_TRANSCRIPT_LANGS = {"live_chat", "live_chat_replay"}


def print_section(title: str):
    """打印分隔线。"""
    print(f"\n{'=' * 60}")
    print(f" {title}")
    print(f"{'=' * 60}\n")


def print_subsection(title: str):
    """打印子分隔线。"""
    print(f"\n{'-' * 40}")
    print(f" {title}")
    print(f"{'-' * 40}")


async def export_cookies_to_file(context: BrowserContext, output_path: Path) -> bool:
    """
    导出 cookies 到 Netscape 格式文件。

    Args:
        context: BrowserContext 实例
        output_path: 输出文件路径

    Returns:
        是否成功导出
    """
    try:
        cookies = await context.cookies()

        if not cookies:
            print("[WARN] No cookies found in context")
            return False

        # 转换为 Netscape 格式
        lines = ["# Netscape HTTP Cookie File"]
        for cookie in cookies:
            # 格式: domain, include_subdomains, path, secure, expires, name, value
            domain = cookie.get("domain", "")
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            path = cookie.get("path", "/")
            secure = "TRUE" if cookie.get("secure", False) else "FALSE"
            expires = str(int(cookie.get("expires", 0)))
            name = cookie.get("name", "")
            value = cookie.get("value", "")

            lines.append(f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"[OK] Exported {len(cookies)} cookies to {output_path}")
        return True

    except Exception as e:
        print(f"[ERROR] Failed to export cookies: {e}")
        return False


def extract_subtitle_info_raw(info: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 yt-dlp info 中提取原始字幕信息（不做任何过滤）。

    Args:
        info: yt-dlp 提取的视频信息

    Returns:
        包含所有字幕信息的字典
    """
    result = {
        "video_language": info.get("language"),
        "subtitles": {},
        "automatic_captions": {},
    }

    # 手动字幕
    if info.get("subtitles"):
        for lang, formats in info["subtitles"].items():
            result["subtitles"][lang] = {
                "formats": [
                    {
                        "ext": fmt.get("ext"),
                        "name": fmt.get("name"),
                        "url": fmt.get("url", "")[:100] + "..." if fmt.get("url") else None,
                    }
                    for fmt in formats
                ]
            }

    # 自动字幕
    if info.get("automatic_captions"):
        for lang, formats in info["automatic_captions"].items():
            result["automatic_captions"][lang] = {
                "formats": [
                    {
                        "ext": fmt.get("ext"),
                        "name": fmt.get("name"),
                        "url": fmt.get("url", "")[:100] + "..." if fmt.get("url") else None,
                    }
                    for fmt in formats
                ]
            }

    return result


async def test_ytdlp_extract_info(cookie_file: Path) -> Optional[Dict[str, Any]]:
    """
    测试 yt-dlp 提取视频信息。

    Args:
        cookie_file: cookies 文件路径

    Returns:
        yt-dlp 提取的视频信息
    """
    print_subsection("Step 2: yt-dlp extract_info (with format selection)")

    if not YTDLP_AVAILABLE:
        print("[ERROR] yt-dlp not available")
        return None

    ydl_opts = {
        "cookiefile": str(cookie_file),
        "quiet": False,  # 显示输出
        "no_warnings": False,  # 显示警告
        "skip_download": True,
        "simulate": False,
        "extract_flat": False,
        "no_color": True,
        # 不设置 format，获取完整信息
    }

    print(f"[INFO] yt-dlp options: cookiefile={cookie_file}")
    print(f"[INFO] Extracting info for: {VIDEO_URL}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(VIDEO_URL, download=False)

        if not info:
            print("[ERROR] yt-dlp returned None")
            return None

        print(f"[OK] Got video info:")
        print(f"     Title: {info.get('title', 'N/A')}")
        print(f"     Duration: {info.get('duration', 'N/A')} seconds")
        print(f"     Language: {info.get('language', 'N/A')}")
        print(f"     Uploader: {info.get('uploader', 'N/A')}")

        return info

    except Exception as e:
        print(f"[ERROR] yt-dlp extract_info failed: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_ytdlp_extract_info_subtitle_only(cookie_file: Path) -> Optional[Dict[str, Any]]:
    """
    测试 yt-dlp 提取视频信息（仅字幕模式，跳过格式选择）。

    关键点：设置 skip_download=True + 不进行格式选择。

    Args:
        cookie_file: cookies 文件路径

    Returns:
        yt-dlp 提取的视频信息
    """
    print_subsection("Step 2b: yt-dlp extract_info (subtitle-only mode)")

    if not YTDLP_AVAILABLE:
        print("[ERROR] yt-dlp not available")
        return None

    # 关键：使用 extract_flat 或 skip_download 来跳过格式选择
    # 但 extract_flat 会跳过字幕信息的提取
    # 正确做法：使用 listsubtitles 或者处理 "no formats" 的情况

    ydl_opts = {
        "cookiefile": str(cookie_file),
        "quiet": False,
        "no_warnings": False,
        "skip_download": True,
        "simulate": True,  # 模拟模式，不真正下载
        "extract_flat": False,  # 需要完整信息
        "no_color": True,
        # 关键：设置 format 为 None 或者使用 ignore_no_formats_error
        "ignore_no_formats_error": True,  # 忽略"没有可用格式"的错误
        # 指定只获取字幕信息
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": SUBTITLE_PRIORITY,
    }

    print(f"[INFO] yt-dlp options (subtitle-only mode):")
    print(f"       cookiefile: {cookie_file}")
    print(f"       ignore_no_formats_error: True")
    print(f"       simulate: True")
    print(f"[INFO] Extracting info for: {VIDEO_URL}")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(VIDEO_URL, download=False)

        if not info:
            print("[ERROR] yt-dlp returned None")
            return None

        print(f"[OK] Got video info (subtitle-only mode):")
        print(f"     Title: {info.get('title', 'N/A')}")
        print(f"     Duration: {info.get('duration', 'N/A')} seconds")
        print(f"     Language: {info.get('language', 'N/A')}")
        print(f"     Uploader: {info.get('uploader', 'N/A')}")

        # 检查字幕信息
        subtitles = info.get("subtitles", {})
        auto_captions = info.get("automatic_captions", {})
        print(f"     Manual subtitles: {len(subtitles)} languages")
        print(f"     Auto captions: {len(auto_captions)} languages")

        return info

    except Exception as e:
        print(f"[ERROR] yt-dlp extract_info (subtitle-only) failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def analyze_subtitles(info: Dict[str, Any]):
    """
    分析字幕信息。

    Args:
        info: yt-dlp 提取的视频信息
    """
    print_subsection("Step 3: Analyze Subtitles")

    raw_subs = extract_subtitle_info_raw(info)

    print(f"[INFO] Video original language: {raw_subs['video_language']}")

    # 手动字幕
    print(f"\n[INFO] Manual subtitles: {len(raw_subs['subtitles'])} language(s)")
    if raw_subs["subtitles"]:
        for lang, data in list(raw_subs["subtitles"].items())[:10]:
            formats = [f["ext"] for f in data["formats"] if f["ext"]]
            print(f"       - {lang}: formats={formats}")
        if len(raw_subs["subtitles"]) > 10:
            print(f"       ... and {len(raw_subs['subtitles']) - 10} more")
    else:
        print("       (none)")

    # 自动字幕
    print(f"\n[INFO] Automatic captions: {len(raw_subs['automatic_captions'])} language(s)")
    if raw_subs["automatic_captions"]:
        for lang, data in list(raw_subs["automatic_captions"].items())[:10]:
            formats = [f["ext"] for f in data["formats"] if f["ext"]]
            print(f"       - {lang}: formats={formats}")
        if len(raw_subs["automatic_captions"]) > 10:
            print(f"       ... and {len(raw_subs['automatic_captions']) - 10} more")
    else:
        print("       (none)")

    # 检查优先语言
    print(f"\n[INFO] Checking priority languages: {SUBTITLE_PRIORITY}")
    for lang in SUBTITLE_PRIORITY:
        in_manual = lang in raw_subs["subtitles"]
        in_auto = lang in raw_subs["automatic_captions"]
        status = "manual" if in_manual else ("auto" if in_auto else "not found")
        print(f"       - {lang}: {status}")


async def test_ytdlp_download_subtitle(cookie_file: Path, output_dir: Path) -> Optional[Path]:
    """
    测试 yt-dlp 下载字幕（使用项目当前配置）。

    Args:
        cookie_file: cookies 文件路径
        output_dir: 输出目录

    Returns:
        下载的字幕文件路径
    """
    print_subsection("Step 4: yt-dlp download_subtitle (with player_client)")

    if not YTDLP_AVAILABLE:
        print("[ERROR] yt-dlp not available")
        return None

    ydl_opts = {
        "cookiefile": str(cookie_file),
        "quiet": False,
        "no_warnings": False,
        "skip_download": True,  # 不下载视频/音频
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "json3/vtt/best",  # 优先 json3
        "subtitleslangs": SUBTITLE_PRIORITY,
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "paths": {"home": str(output_dir)},
        # 使用与主下载器相同的客户端策略 (这个可能是问题所在！)
        "extractor_args": {
            "youtube": {
                "player_client": ["web_creator"],
            }
        },
    }

    print(f"[INFO] yt-dlp subtitle options:")
    print(f"       cookiefile: {cookie_file}")
    print(f"       output_dir: {output_dir}")
    print(f"       subtitleslangs: {SUBTITLE_PRIORITY}")
    print(f"       subtitlesformat: json3/vtt/best")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([VIDEO_URL])

        # 查找下载的字幕文件
        print(f"\n[INFO] Checking output directory: {output_dir}")
        files = list(output_dir.iterdir())
        print(f"[INFO] Files in output directory: {len(files)}")

        subtitle_file = None
        for file in files:
            print(f"       - {file.name} ({file.stat().st_size} bytes)")
            if file.stem.startswith(VIDEO_ID) and file.suffix in [
                ".srt", ".vtt", ".json3", ".ttml", ".json"
            ]:
                subtitle_file = file

        if subtitle_file:
            print(f"\n[OK] Subtitle downloaded: {subtitle_file.name}")

            # 显示字幕内容预览
            try:
                with open(subtitle_file, "r", encoding="utf-8") as f:
                    content = f.read()
                print(f"\n[INFO] Subtitle content preview (first 500 chars):")
                print("-" * 40)
                print(content[:500])
                if len(content) > 500:
                    print("...")
                print("-" * 40)
            except Exception as e:
                print(f"[WARN] Failed to read subtitle content: {e}")

            return subtitle_file
        else:
            print("[WARN] No subtitle file found after download")
            return None

    except Exception as e:
        print(f"[ERROR] yt-dlp subtitle download failed: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_ytdlp_download_subtitle_no_player_client(
    cookie_file: Path, output_dir: Path
) -> Optional[Path]:
    """
    测试 yt-dlp 下载字幕（不使用 player_client）。

    Args:
        cookie_file: cookies 文件路径
        output_dir: 输出目录

    Returns:
        下载的字幕文件路径
    """
    print_subsection("Step 4b: yt-dlp download_subtitle (WITHOUT player_client)")

    if not YTDLP_AVAILABLE:
        print("[ERROR] yt-dlp not available")
        return None

    ydl_opts = {
        "cookiefile": str(cookie_file),
        "quiet": False,
        "no_warnings": False,
        "skip_download": True,  # 不下载视频/音频
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "json3/vtt/best",  # 优先 json3
        "subtitleslangs": SUBTITLE_PRIORITY,
        "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
        "paths": {"home": str(output_dir)},
        # 关键：不设置 player_client，使用 yt-dlp 默认行为
        "ignore_no_formats_error": True,  # 忽略格式错误
    }

    print(f"[INFO] yt-dlp subtitle options (NO player_client):")
    print(f"       cookiefile: {cookie_file}")
    print(f"       output_dir: {output_dir}")
    print(f"       subtitleslangs: {SUBTITLE_PRIORITY}")
    print(f"       subtitlesformat: json3/vtt/best")
    print(f"       ignore_no_formats_error: True")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([VIDEO_URL])

        # 查找下载的字幕文件
        print(f"\n[INFO] Checking output directory: {output_dir}")
        files = list(output_dir.iterdir())
        print(f"[INFO] Files in output directory: {len(files)}")

        subtitle_file = None
        for file in files:
            print(f"       - {file.name} ({file.stat().st_size} bytes)")
            if file.stem.startswith(VIDEO_ID) and file.suffix in [
                ".srt", ".vtt", ".json3", ".ttml", ".json"
            ]:
                subtitle_file = file

        if subtitle_file:
            print(f"\n[OK] Subtitle downloaded: {subtitle_file.name}")

            # 显示字幕内容预览
            try:
                with open(subtitle_file, "r", encoding="utf-8") as f:
                    content = f.read()
                print(f"\n[INFO] Subtitle content preview (first 500 chars):")
                print("-" * 40)
                print(content[:500])
                if len(content) > 500:
                    print("...")
                print("-" * 40)
            except Exception as e:
                print(f"[WARN] Failed to read subtitle content: {e}")

            return subtitle_file
        else:
            print("[WARN] No subtitle file found after download")
            return None

    except Exception as e:
        print(f"[ERROR] yt-dlp subtitle download (no player_client) failed: {e}")
        import traceback
        traceback.print_exc()
        return None


async def main():
    """主测试函数。"""
    print_section("CDP Subtitle Download Debug Test")

    print(f"Configuration:")
    print(f"  CDP URL: {CDP_URL}")
    print(f"  Video URL: {VIDEO_URL}")
    print(f"  Video ID: {VIDEO_ID}")
    print(f"  Playwright available: {PLAYWRIGHT_AVAILABLE}")
    print(f"  yt-dlp available: {YTDLP_AVAILABLE}")

    if not PLAYWRIGHT_AVAILABLE or not YTDLP_AVAILABLE:
        print("\n[FATAL] Missing required dependencies")
        return

    # 创建临时目录
    temp_dir = Path(tempfile.mkdtemp(prefix="cdp_subtitle_test_"))
    cookie_file = temp_dir / "cookies.txt"
    output_dir = temp_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  Temp dir: {temp_dir}")
    print(f"  Cookie file: {cookie_file}")
    print(f"  Output dir: {output_dir}")

    browser = None
    playwright = None

    try:
        # Step 1: 连接 CDP 并获取 cookies
        print_subsection("Step 1: Connect to CDP and export cookies")

        playwright = await async_playwright().start()
        print(f"[INFO] Connecting to CDP: {CDP_URL}")

        browser = await playwright.chromium.connect_over_cdp(
            CDP_URL,
            timeout=30000,
        )
        print(f"[OK] Connected to browser")

        # 获取或创建 context
        if browser.contexts:
            context = browser.contexts[0]
            print(f"[INFO] Using existing context")
        else:
            context = await browser.new_context()
            print(f"[INFO] Created new context")

        # 创建页面并访问视频
        page = await context.new_page()
        print(f"[INFO] Navigating to {VIDEO_URL}")

        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
            print(f"[OK] Page loaded")

            # 等待一下确保 cookies 生成
            await asyncio.sleep(2)

        except Exception as e:
            print(f"[WARN] Page navigation issue: {e}")

        # 导出 cookies
        if not await export_cookies_to_file(context, cookie_file):
            print("[ERROR] Failed to export cookies, trying without cookies...")
            cookie_file = None

        # 关闭页面
        await page.close()

        # Step 2: 提取视频信息（先尝试常规模式）
        info = await test_ytdlp_extract_info(cookie_file or Path("/dev/null"))

        # 如果常规模式失败，尝试 subtitle-only 模式
        if not info:
            print("\n[INFO] Regular extract_info failed, trying subtitle-only mode...")
            info = await test_ytdlp_extract_info_subtitle_only(cookie_file or Path("/dev/null"))

        if info:
            # Step 3: 分析字幕
            analyze_subtitles(info)

            # Step 4: 下载字幕（使用 player_client - 当前项目配置）
            subtitle_path = await test_ytdlp_download_subtitle(
                cookie_file or Path("/dev/null"),
                output_dir,
            )

            # Step 4b: 如果 Step 4 失败，尝试不使用 player_client
            subtitle_path_alt = None
            if not subtitle_path:
                print("\n[INFO] Step 4 failed, trying without player_client...")
                # 使用不同的输出目录
                output_dir_alt = temp_dir / "output_alt"
                output_dir_alt.mkdir(parents=True, exist_ok=True)
                subtitle_path_alt = await test_ytdlp_download_subtitle_no_player_client(
                    cookie_file or Path("/dev/null"),
                    output_dir_alt,
                )

            # 总结
            print_section("Summary")

            if subtitle_path:
                print("[SUCCESS] Subtitle download test passed (with player_client)!")
                print(f"  Downloaded file: {subtitle_path}")
            elif subtitle_path_alt:
                print("[SUCCESS] Subtitle download test passed (WITHOUT player_client)!")
                print(f"  Downloaded file: {subtitle_path_alt}")
                print("\n[DIAGNOSIS] The issue is in download_subtitle() method:")
                print("  The 'player_client' setting is causing the failure.")
                print("  File: src/downloaders/cdp/audio_downloader.py")
                print("  Lines: 617-623")
                print("  Fix: Remove or update the 'extractor_args' setting")
            else:
                print("[FAILED] Subtitle download test failed!")
                print("  Possible reasons:")
                print("  1. Video has no subtitles (manual or auto)")
                print("  2. Subtitles are disabled for this video")
                print("  3. yt-dlp couldn't fetch subtitle URLs")
                print("  4. Network or cookie issues")
        else:
            print_section("Summary")
            print("[FAILED] Could not extract video info")
            print("  Possible reasons:")
            print("  1. Invalid video URL")
            print("  2. Video is private or unavailable")
            print("  3. Cookie/authentication issues")
            print("  4. Rate limiting")

    except Exception as e:
        print(f"\n[FATAL] Test failed with error: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # 清理
        if browser:
            # 不关闭 browser（共享连接）
            pass
        if playwright:
            await playwright.stop()

        print(f"\n[INFO] Temp files preserved at: {temp_dir}")
        print("       You can inspect the files for debugging.")


if __name__ == "__main__":
    asyncio.run(main())
