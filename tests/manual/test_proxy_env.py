"""
检查代理环境变量配置。

诊断 httpx 客户端的代理使用情况。
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx

# 设置标准输出编码为 UTF-8（Windows）
if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")


def check_env_proxies():
    """检查环境变量中的代理配置。"""
    print("=" * 60)
    print("环境变量代理检查")
    print("=" * 60)

    proxy_vars = [
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ]

    print("\n系统环境变量:")
    has_proxy = False
    for var in proxy_vars:
        value = os.environ.get(var)
        if value:
            print(f"  {var}={value}")
            has_proxy = True

    if not has_proxy:
        print("  (未设置代理)")

    print("\n.env.development 配置:")
    env_file = Path(".env.development")
    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
        for line in content.split("\n"):
            if "PROXY" in line.upper() and not line.strip().startswith("#"):
                print(f"  {line.strip()}")


async def test_httpx_client_with_env():
    """测试 httpx 客户端在当前环境下的行为。"""
    print("\n=" * 60)
    print("httpx 客户端测试（使用环境变量）")
    print("=" * 60)

    test_url = "https://api.tikhub.io/api/v1/youtube/web"

    try:
        # 默认客户端（会自动使用环境变量代理）
        async with httpx.AsyncClient(timeout=10.0) as client:
            print(f"\n测试 1: 默认配置（自动使用环境变量）")
            print(f"正在连接: {test_url}")
            response = await client.get(test_url)
            print(f"[OK] 连接成功: {response.status_code}")
    except httpx.ConnectError as e:
        print(f"[FAIL] 连接失败: {e}")
    except Exception as e:
        print(f"[FAIL] 其他错误: {type(e).__name__}: {e}")


async def test_httpx_client_no_proxy():
    """测试 httpx 客户端明确禁用代理。"""
    print("\n测试 2: 明确禁用代理（trust_env=False）")

    test_url = "https://api.tikhub.io/api/v1/youtube/web"

    try:
        # 禁用环境变量代理
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            print(f"正在连接: {test_url}")
            response = await client.get(test_url)
            print(f"[OK] 连接成功: {response.status_code}")
    except httpx.ConnectError as e:
        print(f"[FAIL] 连接失败: {e}")
    except Exception as e:
        print(f"[FAIL] 其他错误: {type(e).__name__}: {e}")


async def test_httpx_client_explicit_proxy():
    """测试 httpx 客户端使用明确的代理配置。"""
    print("\n测试 3: 明确指定代理（proxies={}）")

    test_url = "https://api.tikhub.io/api/v1/youtube/web"

    try:
        # 明确指定空代理（覆盖环境变量）
        async with httpx.AsyncClient(
            timeout=10.0,
            proxies={}  # 空字典：覆盖环境变量，不使用代理
        ) as client:
            print(f"正在连接: {test_url}")
            response = await client.get(test_url)
            print(f"[OK] 连接成功: {response.status_code}")
    except httpx.ConnectError as e:
        print(f"[FAIL] 连接失败: {e}")
    except Exception as e:
        print(f"[FAIL] 其他错误: {type(e).__name__}: {e}")


async def main():
    """主测试流程。"""
    # 检查环境变量
    check_env_proxies()

    # 测试不同的 httpx 客户端配置
    await test_httpx_client_with_env()
    await test_httpx_client_no_proxy()
    await test_httpx_client_explicit_proxy()

    print("\n=" * 60)
    print("测试完成")
    print("=" * 60)

    print("\n结论:")
    print("如果 '测试 1' 失败但 '测试 2/3' 成功，说明环境变量代理配置有问题")
    print("建议在 TikHubDownloader.__init__ 中:")
    print("  1. 如果 settings.http_proxy 为空，使用 trust_env=False")
    print("  2. 或者使用 proxies={} 明确禁用代理")


if __name__ == "__main__":
    asyncio.run(main())
