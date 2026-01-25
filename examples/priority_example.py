"""
任务优先级使用示例。

演示如何创建不同优先级的任务，以及队列的处理顺序。
"""

import asyncio
import httpx
from datetime import datetime


API_BASE_URL = "http://localhost:8000"
API_KEY = "your-api-key"  # 替换为实际的 API Key


async def create_task(video_url: str, priority: str = "normal") -> dict:
    """
    创建下载任务。

    Args:
        video_url: YouTube 视频 URL
        priority: 任务优先级（urgent 或 normal）

    Returns:
        任务响应
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE_URL}/api/v1/tasks",
            json={
                "video_url": video_url,
                "priority": priority,
                "include_audio": True,
                "include_transcript": False,
            },
            headers={
                "X-API-Key": API_KEY,
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()


async def get_task(task_id: str) -> dict:
    """
    查询任务状态。

    Args:
        task_id: 任务 ID

    Returns:
        任务详情
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE_URL}/api/v1/tasks/{task_id}",
            headers={"X-API-Key": API_KEY},
        )
        response.raise_for_status()
        return response.json()


async def demo_priority_ordering():
    """演示优先级队列的处理顺序。"""
    print("=" * 60)
    print("任务优先级演示")
    print("=" * 60)

    # 1. 创建普通任务
    print("\n1️⃣  创建普通任务 A")
    task_a = await create_task(
        "https://www.youtube.com/watch?v=normal_task_a",
        priority="normal"
    )
    print(f"   任务 ID: {task_a['task_id']}")
    print(f"   优先级: {task_a['priority']}")
    print(f"   状态: {task_a['status']}")
    if task_a.get('position'):
        print(f"   队列位置: {task_a['position']}")

    # 2. 创建另一个普通任务
    print("\n2️⃣  创建普通任务 B")
    task_b = await create_task(
        "https://www.youtube.com/watch?v=normal_task_b",
        priority="normal"
    )
    print(f"   任务 ID: {task_b['task_id']}")
    print(f"   优先级: {task_b['priority']}")
    print(f"   状态: {task_b['status']}")
    if task_b.get('position'):
        print(f"   队列位置: {task_b['position']}")

    # 3. 创建紧急任务（应该插队到最前面）
    print("\n3️⃣  创建紧急任务 C（应该优先处理）")
    task_c = await create_task(
        "https://www.youtube.com/watch?v=urgent_task_c",
        priority="urgent"
    )
    print(f"   任务 ID: {task_c['task_id']}")
    print(f"   优先级: {task_c['priority']} ⚡")
    print(f"   状态: {task_c['status']}")
    if task_c.get('position'):
        print(f"   队列位置: {task_c['position']}")

    # 4. 预期处理顺序
    print("\n" + "=" * 60)
    print("预期处理顺序：")
    print("=" * 60)
    print(f"1. 任务 C (urgent)  - {task_c['task_id'][:8]}...")
    print(f"2. 任务 A (normal)  - {task_a['task_id'][:8]}...")
    print(f"3. 任务 B (normal)  - {task_b['task_id'][:8]}...")
    print("\n说明：紧急任务 C 虽然最后提交，但会优先处理")


async def demo_cache_hit():
    """演示缓存命中的情况。"""
    print("\n" + "=" * 60)
    print("缓存命中演示")
    print("=" * 60)

    video_url = "https://www.youtube.com/watch?v=cache_test"

    # 第一次请求（正常任务）
    print("\n1️⃣  第一次请求（创建任务）")
    task1 = await create_task(video_url, priority="normal")
    print(f"   任务 ID: {task1['task_id']}")
    print(f"   缓存命中: {task1['cache_hit']}")
    print(f"   状态: {task1['status']}")

    # 模拟等待任务完成...
    print("\n   ⏳ 等待任务完成...")
    await asyncio.sleep(2)

    # 第二次请求（缓存命中，应该立即返回）
    print("\n2️⃣  第二次请求相同视频（应该命中缓存）")
    task2 = await create_task(video_url, priority="urgent")
    print(f"   任务 ID: {task2['task_id']}")
    print(f"   缓存命中: {task2['cache_hit']}")
    print(f"   状态: {task2['status']}")
    print(f"   优先级: {task2.get('priority', 'N/A')} (缓存命中时为 null)")

    if task2['cache_hit']:
        print("\n   ✅ 缓存命中！资源立即返回，无需排队")
        print("   说明：缓存命中时优先级不影响结果，因为无需下载")


async def main():
    """主函数。"""
    try:
        print("\n🚀 YouTube 音频下载 API - 优先级功能演示")
        print(f"📅 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🌐 API: {API_BASE_URL}")

        # 演示 1: 优先级排序
        await demo_priority_ordering()

        # 演示 2: 缓存命中
        # await demo_cache_hit()  # 取消注释以测试缓存功能

        print("\n" + "=" * 60)
        print("✅ 演示完成！")
        print("=" * 60)
        print("\n提示：")
        print("  - 使用 'urgent' 优先级处理需要立即响应的任务")
        print("  - 使用 'normal' 优先级（默认）处理常规任务")
        print("  - 重试任务自动使用最低优先级，不影响新请求")

    except httpx.HTTPStatusError as e:
        print(f"\n❌ API 错误: {e.response.status_code}")
        print(f"   响应: {e.response.text}")
    except Exception as e:
        print(f"\n❌ 错误: {e}")


if __name__ == "__main__":
    asyncio.run(main())
