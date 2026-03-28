"""
测试 TikHub 字幕下载功能。

完整测试从获取字幕 URL 到下载字幕文件的流程。
"""

import pytest
pytestmark = pytest.mark.requires_external


import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

import httpx

# 设置标准输出编码为 UTF-8（Windows）
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")

# TikHub API 配置
TIKHUB_API_KEY = "8p4MD000miJyswYg42K8nmQfwD1R0rf4rr0jmW3CjmFq/XQxdd1R/COJPg=="
TIKHUB_BASE_URL = "https://api.tikhub.io/api/v1/youtube/web"
TIKHUB_VIDEO_INFO_ENDPOINT = f"{TIKHUB_BASE_URL}/get_video_info"
TIKHUB_SUBTITLE_API = "https://api.tikhub.io/api/v1/youtube/web/get_video_subtitles"

# 测试视频 ID
VIDEO_ID = "ADW8IDQ-5Ws"

# 输出目录
OUTPUT_DIR = Path(__file__).parent / "output"


async def get_subtitle_url() -> str | None:
    """获取字幕 URL。"""
    print("\n=== 步骤 1: 获取字幕 URL ===")

    params = {
        "video_id": VIDEO_ID,
        "url_access": "normal",
        "lang": "zh-CN",
        "videos": "false",
        "audios": "false",
        "subtitles": "true",
        "related": "false",
    }

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {TIKHUB_API_KEY}",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                TIKHUB_VIDEO_INFO_ENDPOINT,
                params=params,
                headers=headers,
            )

            response.raise_for_status()
            data = response.json()

            if data.get("code") != 200:
                print(f"[FAIL] API 错误: {data.get('message')}")
                return None

            video_data = data.get("data", {})
            subtitles = video_data.get("subtitles", {})

            if subtitles.get("errorId") != "Success":
                print(f"[FAIL] 无字幕: {subtitles.get('errorId')}")
                return None

            items = subtitles.get("items", [])
            if not items:
                print("[FAIL] 字幕列表为空")
                return None

            # 选择第一个字幕
            subtitle = items[0]
            subtitle_url = subtitle.get("url")

            print(f"[OK] 获取到字幕 URL")
            print(f"  语言: {subtitle.get('code')}")
            print(f"  名称: {subtitle.get('name')}")
            print(f"  URL: {subtitle_url[:100]}...")

            return subtitle_url

    except Exception as e:
        print(f"[FAIL] 获取字幕 URL 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return None


async def download_subtitle(subtitle_url: str) -> bool:
    """下载字幕文件。"""
    print("\n=== 步骤 2: 下载字幕 ===")

    # 构建 API 请求参数
    params = {
        "subtitle_url": subtitle_url,
        "format": "srt",
        "fix_overlap": "true",
    }

    api_url = f"{TIKHUB_SUBTITLE_API}?{urlencode(params)}"

    print(f"API URL: {api_url[:150]}...")

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {TIKHUB_API_KEY}",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            print("正在请求 TikHub 字幕 API...")
            response = await client.get(api_url, headers=headers)

            print(f"HTTP 状态码: {response.status_code}")

            if response.status_code != 200:
                print(f"[FAIL] HTTP 错误")
                print(f"响应内容: {response.text[:500]}")
                return False

            data = response.json()
            print(f"API 响应 code: {data.get('code')}")
            print(f"API 响应 message: {data.get('message')}")

            if data.get("code") != 200:
                print(f"[FAIL] API 错误: {data.get('message')}")
                return False

            # 提取字幕内容
            subtitle_content = data.get("data")
            if not subtitle_content:
                print("[FAIL] 字幕内容为空")
                return False

            # 保存字幕文件
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            output_path = OUTPUT_DIR / f"{VIDEO_ID}.srt"
            output_path.write_text(subtitle_content, encoding="utf-8")

            print(f"[OK] 字幕下载成功")
            print(f"  文件路径: {output_path}")
            print(f"  文件大小: {len(subtitle_content)} 字节")

            # 显示前几行
            lines = subtitle_content.strip().split("\n")
            print(f"\n前 10 行内容:")
            for line in lines[:10]:
                print(f"    {line}")

            return True

    except httpx.ConnectError as e:
        print(f"[FAIL] 连接失败: {e}")
        print("  提示: 可能需要配置代理")
        return False
    except httpx.TimeoutException as e:
        print(f"[FAIL] 请求超时: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] 下载失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试流程。"""
    print("=" * 60)
    print("TikHub 字幕下载测试")
    print("=" * 60)
    print(f"测试视频: https://www.youtube.com/watch?v={VIDEO_ID}")

    # 步骤 1: 获取字幕 URL
    subtitle_url = await get_subtitle_url()
    if not subtitle_url:
        print("\n[FAIL] 无法获取字幕 URL")
        return

    # 步骤 2: 下载字幕
    success = await download_subtitle(subtitle_url)

    print("\n" + "=" * 60)
    if success:
        print("[OK] 测试完成")
    else:
        print("[FAIL] 测试失败")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
