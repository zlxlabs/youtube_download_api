"""
CDP 多客户端隔离性测试

测试场景：
5 个客户端同时连接到同一个 Chrome Server，
在不同标签页上执行操作，验证是否互不干扰。

运行前提：
1. 启动 Chrome Server：
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
     --remote-debugging-port=9222 \
     --user-data-dir=/tmp/chrome-cdp

2. 确认 Chrome 可访问：
   curl http://localhost:9222/json/version
"""

import pytest
pytestmark = pytest.mark.requires_external


import asyncio
import time
import sys
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from typing import List, Tuple

# 设置 Windows 控制台输出为 UTF-8（避免 emoji 编码错误）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass


CDP_URL = "http://localhost:9222"


async def client_task(
    client_id: int,
    cdp_url: str,
    shared_browser: Browser = None
) -> dict:
    """
    模拟单个客户端的操作

    参数：
        client_id: 客户端编号
        cdp_url: CDP 地址
        shared_browser: 如果提供，则共享 browser 实例；否则创建新实例

    返回：
        操作结果（包含隔离性验证信息）
    """
    result = {
        "client_id": client_id,
        "browser_id": None,
        "context_id": None,
        "page_title": None,
        "cookies_count": 0,
        "local_storage": None,
        "success": False,
        "error": None
    }

    playwright = None
    browser = None
    context = None
    page = None

    try:
        start_time = time.time()

        # 1. 连接到 Chrome（共享或独立）
        if shared_browser:
            browser = shared_browser
            print(f"[Client {client_id}] 使用共享 browser 实例")
        else:
            playwright = await async_playwright().start()
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            print(f"[Client {client_id}] 创建独立 browser 实例")

        result["browser_id"] = id(browser)

        # 2. 创建独立的 context（关键：每个客户端独立的浏览器上下文）
        context = await browser.new_context(
            user_agent=f"TestClient-{client_id}",
            viewport={"width": 1920, "height": 1080}
        )
        result["context_id"] = id(context)

        print(f"[Client {client_id}] 创建 context (ID: {id(context)})")

        # 3. 创建页面并访问唯一的测试 URL
        page = await context.new_page()

        # 每个客户端访问不同的页面（模拟不同的任务）
        test_urls = [
            "https://www.example.com",
            "https://www.wikipedia.org",
            "https://www.github.com",
            "https://www.stackoverflow.com",
            "https://www.reddit.com"
        ]
        test_url = test_urls[client_id % len(test_urls)]

        await page.goto(test_url, wait_until="domcontentloaded", timeout=15000)
        result["page_title"] = await page.title()

        print(f"[Client {client_id}] 已访问: {test_url} (标题: {result['page_title']})")

        # 4. 设置 localStorage（验证隔离性）
        await page.evaluate(f"""
            () => {{
                localStorage.setItem('client_id', '{client_id}');
                localStorage.setItem('timestamp', '{time.time()}');
            }}
        """)

        # 5. 读取 localStorage（确认写入成功）
        local_storage = await page.evaluate("""
            () => {
                return {
                    client_id: localStorage.getItem('client_id'),
                    timestamp: localStorage.getItem('timestamp')
                };
            }
        """)
        result["local_storage"] = local_storage

        print(f"[Client {client_id}] localStorage: {local_storage}")

        # 6. 获取 cookies
        cookies = await context.cookies()
        result["cookies_count"] = len(cookies)

        # 7. 模拟一些耗时操作（测试并发安全）
        await asyncio.sleep(2)

        # 8. 再次验证 localStorage（确认没有被其他客户端覆盖）
        final_storage = await page.evaluate("""
            () => {
                return {
                    client_id: localStorage.getItem('client_id'),
                    timestamp: localStorage.getItem('timestamp')
                };
            }
        """)

        # 验证隔离性
        if final_storage["client_id"] == str(client_id):
            print(f"[Client {client_id}] [OK] 隔离验证成功：localStorage 未被覆盖")
            result["success"] = True
        else:
            print(f"[Client {client_id}] [FAIL] 隔离验证失败：localStorage 被覆盖为 {final_storage}")
            result["success"] = False

        elapsed = time.time() - start_time
        print(f"[Client {client_id}] 完成，耗时: {elapsed:.2f}s")

    except Exception as e:
        print(f"[Client {client_id}] [ERROR] 错误: {e}")
        result["error"] = str(e)
        result["success"] = False

    finally:
        # 清理资源
        if page:
            await page.close()
        if context:
            await context.close()
        if playwright and browser:
            await browser.close()
            await playwright.stop()

    return result


async def test_isolated_browsers():
    """
    测试方案 1：每个客户端创建独立的 browser 实例

    预期结果：
    ✅ 完全隔离（每个客户端有独立的 CDP session）
    """
    print("\n" + "="*80)
    print("测试方案 1：独立 Browser 实例（5 个客户端，各自连接 CDP）")
    print("="*80 + "\n")

    # 并发执行 5 个客户端任务
    tasks = [
        client_task(client_id=i, cdp_url=CDP_URL, shared_browser=None)
        for i in range(5)
    ]

    results = await asyncio.gather(*tasks)

    # 分析结果
    print("\n" + "-"*80)
    print("隔离性分析：")
    print("-"*80)

    browser_ids = set(r["browser_id"] for r in results)
    context_ids = set(r["context_id"] for r in results)

    print(f"Browser 实例数: {len(browser_ids)} (预期: 5)")
    print(f"Context 实例数: {len(context_ids)} (预期: 5)")
    print(f"成功的客户端: {sum(1 for r in results if r['success'])}/5")

    for r in results:
        status = "[OK]" if r["success"] else "[FAIL]"
        print(f"  {status} Client {r['client_id']}: "
              f"localStorage.client_id = {r['local_storage']['client_id'] if r['local_storage'] else 'N/A'}, "
              f"Title = {r['page_title'][:30] if r['page_title'] else 'N/A'}...")

    # 验证隔离性
    all_success = all(r["success"] for r in results)
    print(f"\n{'[PASS]' if all_success else '[FAIL]'} 隔离性测试: {'通过' if all_success else '失败'}")

    return results


async def test_shared_browser():
    """
    测试方案 2：所有客户端共享同一个 browser 实例

    预期结果：
    ✅ 依然隔离（虽然共享 browser，但 context 是独立的）
    """
    print("\n" + "="*80)
    print("测试方案 2：共享 Browser 实例（5 个客户端，共享同一个 browser）")
    print("="*80 + "\n")

    playwright = await async_playwright().start()
    shared_browser = await playwright.chromium.connect_over_cdp(CDP_URL)

    print(f"共享 Browser 实例 ID: {id(shared_browser)}\n")

    try:
        # 并发执行 5 个客户端任务（共享 browser）
        tasks = [
            client_task(client_id=i, cdp_url=CDP_URL, shared_browser=shared_browser)
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        # 分析结果
        print("\n" + "-"*80)
        print("隔离性分析：")
        print("-"*80)

        browser_ids = set(r["browser_id"] for r in results)
        context_ids = set(r["context_id"] for r in results)

        print(f"Browser 实例数: {len(browser_ids)} (预期: 1，因为共享)")
        print(f"Context 实例数: {len(context_ids)} (预期: 5)")
        print(f"成功的客户端: {sum(1 for r in results if r['success'])}/5")

        for r in results:
            status = "✅" if r["success"] else "❌"
            print(f"  {status} Client {r['client_id']}: "
                  f"localStorage.client_id = {r['local_storage']['client_id'] if r['local_storage'] else 'N/A'}, "
                  f"Title = {r['page_title'][:30] if r['page_title'] else 'N/A'}...")

        # 验证隔离性
        all_success = all(r["success"] for r in results)
        print(f"\n{'[PASS]' if all_success else '[FAIL]'} 隔离性测试: {'通过' if all_success else '失败'}")

        return results

    finally:
        await shared_browser.close()
        await playwright.stop()


async def main():
    """运行所有测试"""
    print("\n[TEST] CDP 多客户端隔离性测试")
    print("="*80)

    # 测试 1：独立 browser 实例
    results1 = await test_isolated_browsers()

    # 等待一下
    await asyncio.sleep(2)

    # 测试 2：共享 browser 实例
    results2 = await test_shared_browser()

    # 最终结论
    print("\n" + "="*80)
    print("[RESULT] 测试结论")
    print("="*80)

    test1_pass = all(r["success"] for r in results1)
    test2_pass = all(r["success"] for r in results2)

    if test1_pass and test2_pass:
        print("[PASS] 两种方案都能实现完全隔离！")
        print("\n推荐方案：")
        print("  - 如果客户端是独立的服务/进程：方案 1（独立 browser）")
        print("  - 如果客户端是同一进程内的并发任务：方案 2（共享 browser）")
    elif test2_pass:
        print("[PASS] 方案 2（共享 browser）隔离性良好")
        print("[WARN] 方案 1（独立 browser）存在问题，需进一步调查")
    elif test1_pass:
        print("[PASS] 方案 1（独立 browser）隔离性良好")
        print("[WARN] 方案 2（共享 browser）存在问题，需进一步调查")
    else:
        print("[FAIL] 两种方案都存在隔离性问题，需要使用 CDP Target API")


if __name__ == "__main__":
    asyncio.run(main())
