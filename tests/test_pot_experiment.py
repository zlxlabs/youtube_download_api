"""
测试命中 YouTube GVS PO Token 实验的视频下载。

测试场景：
1. web_creator + PO Token (通过 pot-provider) + cookies -> 是否正常
2. ios client 降级（不需要 PO Token） -> 是否正常
"""

import os
import tempfile
from pathlib import Path

# 清除本地代理环境变量，防止 yt-dlp 把本地代理传递给远程 pot-provider
# (pot-provider 容器内 127.0.0.1:7890 不可达)
for key in ["http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
    os.environ.pop(key, None)

import yt_dlp


VIDEO_ID = "MWvpXswLFxA"
VIDEO_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
COOKIE_FILE = "data/cookies.txt"
# pot-provider 运行在服务器上，本地通过服务器 IP 访问
POT_SERVER_URL = "http://100.68.21.80:4416"


class TestLogger:
    """捕获 yt-dlp 日志，特别关注 PO Token 和格式相关信息。"""

    def __init__(self, label: str):
        self.label = label

    def debug(self, msg: str):
        if any(kw in msg.lower() for kw in ["pot", "po token", "gvs", "experiment", "format", "player_client"]):
            print(f"  [{self.label}] DEBUG: {msg}")

    def warning(self, msg: str):
        print(f"  [{self.label}] WARN: {msg}")

    def error(self, msg: str):
        print(f"  [{self.label}] ERROR: {msg}")


def test_web_creator_with_pot_token():
    """测试 1: web_creator + PO Token + cookies"""
    print("=" * 70)
    print("TEST 1: web_creator + PO Token (via pot-provider) + cookies")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            "cookiefile": COOKIE_FILE,
            "format": "bestaudio[ext=m4a][abr<=128]/bestaudio[ext=m4a]/bestaudio",
            "outtmpl": {"default": f"{tmpdir}/%(id)s.%(ext)s"},
            "paths": {"home": tmpdir},
            "quiet": False,
            "verbose": True,
            "no_color": True,
            "no_warnings": False,
            "logger": TestLogger("web_creator+pot"),
            "impersonate": yt_dlp.networking.impersonate.ImpersonateTarget(client="chrome"),
            "extractor_args": {
                "youtube": {
                    "player_client": ["web_creator"],
                    "player_js_version": ["actual"],
                },
                "youtubepot-bgutilhttp": {
                    "base_url": [POT_SERVER_URL],
                },
            },
            "remote_components": {"ejs:github"},
            "socket_timeout": 30,
            "retries": 3,
            # 不设置 proxy，依赖环境变量清除（脚本开头已清除）
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(VIDEO_URL, download=False)
                if info:
                    formats = info.get("formats", [])
                    audio_formats = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]
                    print(f"\n  RESULT: {len(formats)} total formats, {len(audio_formats)} audio-only")
                    for af in audio_formats[:5]:
                        print(f"    - {af.get('format_id')}: {af.get('ext')} {af.get('abr', '?')}kbps "
                              f"proto={af.get('protocol')} codec={af.get('acodec')}")
                    print(f"\n  >>> TEST 1 PASSED: web_creator + PO Token works!")
                else:
                    print(f"\n  >>> TEST 1 FAILED: extract_info returned None")
        except Exception as e:
            print(f"\n  >>> TEST 1 FAILED: {e}")

    print()


def test_ios_client_fallback():
    """测试 2: ios client 降级 + PO Token（不带 cookies）"""
    print("=" * 70)
    print("TEST 2: ios client + PO Token (no cookies)")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        opts = {
            # ios 不支持 cookies，不传 cookiefile
            "format": "bestaudio[ext=m4a][abr<=128]/bestaudio[ext=m4a]/bestaudio",
            "outtmpl": {"default": f"{tmpdir}/%(id)s.%(ext)s"},
            "paths": {"home": tmpdir},
            "quiet": False,
            "verbose": True,
            "no_color": True,
            "no_warnings": False,
            "logger": TestLogger("ios+pot"),
            "impersonate": yt_dlp.networking.impersonate.ImpersonateTarget(client="chrome"),
            "extractor_args": {
                "youtube": {
                    "player_client": ["ios"],
                    "player_js_version": ["actual"],
                },
                "youtubepot-bgutilhttp": {
                    "base_url": [POT_SERVER_URL],
                },
            },
            "remote_components": {"ejs:github"},
            "socket_timeout": 30,
            "retries": 3,
            "proxy": "",
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(VIDEO_URL, download=False)
                if info:
                    formats = info.get("formats", [])
                    audio_formats = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]
                    print(f"\n  RESULT: {len(formats)} total formats, {len(audio_formats)} audio-only")
                    for af in audio_formats[:5]:
                        print(f"    - {af.get('format_id')}: {af.get('ext')} {af.get('abr', '?')}kbps "
                              f"proto={af.get('protocol')} codec={af.get('acodec')}")
                    print(f"\n  >>> TEST 2 PASSED: ios client + PO Token works!")
                else:
                    print(f"\n  >>> TEST 2 FAILED: extract_info returned None")
        except Exception as e:
            print(f"\n  >>> TEST 2 FAILED: {e}")

    print()


if __name__ == "__main__":
    print(f"yt-dlp version: {yt_dlp.version.__version__}")
    print(f"Video: {VIDEO_URL}")
    print(f"POT Server: {POT_SERVER_URL}")
    print(f"Cookie: {COOKIE_FILE}")
    print()

    test_web_creator_with_pot_token()
    test_ios_client_fallback()

    print("=" * 70)
    print("DONE")
    print("=" * 70)
