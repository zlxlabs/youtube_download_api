"""
测试 TikHub API 连接和字幕下载。

用于诊断 TikHub 下载器的连接问题。
"""

import pytest
pytestmark = pytest.mark.requires_external


import asyncio
import os
import sys
from pathlib import Path

import httpx

# 设置标准输出编码为 UTF-8（Windows）
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")

# TikHub API 配置
TIKHUB_API_KEY = "8p4MD000miJyswYg42K8nmQfwD1R0rf4rr0jmW3CjmFq/XQxdd1R/COJPg=="
TIKHUB_BASE_URL = "https://api.tikhub.io/api/v1/youtube/web"
TIKHUB_VIDEO_INFO_ENDPOINT = f"{TIKHUB_BASE_URL}/get_video_info"

# 测试视频 ID
VIDEO_ID = "ADW8IDQ-5Ws"


async def test_basic_connection():
    """测试基本的网络连接。"""
    print("\n=== 测试 1: 基本网络连接 ===")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            print(f"正在连接: {TIKHUB_BASE_URL}")
            response = await client.get(TIKHUB_BASE_URL)
            print(f"[OK] 连接成功: {response.status_code}")
            return True
    except httpx.ConnectError as e:
        print(f"[FAIL] 连接失败: {e}")
        print("  提示: TikHub API 可能需要代理访问")
        return False
    except Exception as e:
        print(f"[FAIL] 其他错误: {type(e).__name__}: {e}")
        return False


async def test_video_info_api():
    """测试视频信息 API（仅字幕）。"""
    print("\n=== 测试 2: 视频信息 API（仅字幕）===")

    params = {
        "video_id": VIDEO_ID,
        "url_access": "normal",
        "lang": "zh-CN",
        "videos": "false",
        "audios": "false",
        "subtitles": "true",  # 只请求字幕
        "related": "false",
    }

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {TIKHUB_API_KEY}",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"正在请求: {TIKHUB_VIDEO_INFO_ENDPOINT}")
            print(f"参数: {params}")

            response = await client.get(
                TIKHUB_VIDEO_INFO_ENDPOINT,
                params=params,
                headers=headers,
            )

            print(f"HTTP 状态码: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print(f"[OK] API 调用成功")
                print(f"响应 code: {data.get('code')}")
                print(f"响应 message: {data.get('message')}")

                if data.get("code") == 200:
                    video_data = data.get("data", {})
                    print(f"\n视频信息:")
                    print(f"  标题: {video_data.get('title')}")
                    print(f"  时长: {video_data.get('lengthSeconds')}秒")

                    # 检查字幕
                    subtitles = video_data.get("subtitles", {})
                    print(f"\n字幕信息:")
                    print(f"  errorId: {subtitles.get('errorId')}")

                    items = subtitles.get("items", [])
                    if items:
                        print(f"  可用字幕数: {len(items)}")
                        for item in items:
                            print(f"    - {item.get('name')} ({item.get('code')})")
                            print(f"      URL: {item.get('url')[:80]}...")
                    else:
                        print(f"  [FAIL] 无可用字幕")

                    return True
                else:
                    print(f"[FAIL] API 返回错误: {data.get('message')}")
                    return False
            else:
                print(f"[FAIL] HTTP 错误: {response.status_code}")
                print(f"响应内容: {response.text[:200]}")
                return False

    except httpx.ConnectError as e:
        print(f"[FAIL] 连接失败: {e}")
        print("  提示: 可能需要配置代理访问 TikHub API")
        return False
    except httpx.TimeoutException as e:
        print(f"[FAIL] 请求超时: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] 其他错误: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_with_proxy():
    """测试使用代理连接。"""
    print("\n=== 测试 3: 使用代理连接 ===")

    # 尝试常见的代理端口
    proxy_urls = [
        "http://127.0.0.1:7890",
        "http://127.0.0.1:1080",
        "http://127.0.0.1:10809",
    ]

    for proxy_url in proxy_urls:
        print(f"\n尝试代理: {proxy_url}")

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                proxies=proxy_url
            ) as client:
                response = await client.get(TIKHUB_BASE_URL)
                print(f"[OK] 通过代理连接成功: {response.status_code}")
                return proxy_url
        except Exception as e:
            print(f"[FAIL] 代理连接失败: {type(e).__name__}")

    print("\n[FAIL] 所有代理都无法连接")
    return None


async def main():
    """主测试函数。"""
    print("=" * 60)
    print("TikHub API 连接诊断")
    print("=" * 60)
    print(f"测试视频: https://www.youtube.com/watch?v={VIDEO_ID}")
    print(f"API Key: {TIKHUB_API_KEY[:20]}...")

    # 测试 1: 基本连接
    basic_ok = await test_basic_connection()

    if not basic_ok:
        # 如果基本连接失败，尝试代理
        proxy_url = await test_with_proxy()

        if proxy_url:
            print(f"\n建议在 .env.development 中配置:")
            print(f"HTTP_PROXY={proxy_url}")
            print(f"HTTPS_PROXY={proxy_url}")
        else:
            print("\n无法连接到 TikHub API，请检查:")
            print("1. 网络连接是否正常")
            print("2. 是否需要配置代理")
            print("3. 防火墙是否阻止了连接")
    else:
        # 测试 2: API 调用
        await test_video_info_api()

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
