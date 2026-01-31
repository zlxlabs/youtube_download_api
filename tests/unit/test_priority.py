"""
测试任务优先级功能。

验证紧急任务优先处理、普通任务正常排队、重试任务最低优先级。
"""

import pytest
from src.db.models import TaskPriority, RETRY_QUEUE_PRIORITY, calculate_queue_priority


def test_priority_enum_values():
    """测试优先级枚举值定义正确。"""
    assert TaskPriority.URGENT.value == "urgent"
    assert TaskPriority.NORMAL.value == "normal"


def test_calculate_queue_priority():
    """测试优先级计算函数。"""
    # urgent 任务全局最高优先级（0）
    assert calculate_queue_priority(TaskPriority.URGENT, True, True) == 0
    assert calculate_queue_priority(TaskPriority.URGENT, True, False) == 0
    assert calculate_queue_priority(TaskPriority.URGENT, False, True) == 0

    # normal 任务根据类型分级
    # 仅字幕任务：高优先级（1）
    assert calculate_queue_priority(TaskPriority.NORMAL, False, True) == 1
    # 音频/混合任务：中等优先级（2）
    assert calculate_queue_priority(TaskPriority.NORMAL, True, True) == 2
    assert calculate_queue_priority(TaskPriority.NORMAL, True, False) == 2


def test_retry_queue_priority():
    """测试重试任务优先级是最低。"""
    assert RETRY_QUEUE_PRIORITY == 3


def test_priority_ordering():
    """测试优先级排序符合预期。"""
    # 数字越小，优先级越高
    urgent_priority = calculate_queue_priority(TaskPriority.URGENT, True, True)
    transcript_only_priority = calculate_queue_priority(
        TaskPriority.NORMAL, False, True
    )
    audio_priority = calculate_queue_priority(TaskPriority.NORMAL, True, False)
    retry_priority = RETRY_QUEUE_PRIORITY

    # 验证顺序：urgent (0) < transcript_only (1) < audio (2) < retry (3)
    assert urgent_priority == 0
    assert transcript_only_priority == 1
    assert audio_priority == 2
    assert retry_priority == 3

    # 验证大小关系
    assert urgent_priority < transcript_only_priority < audio_priority < retry_priority


@pytest.mark.asyncio
async def test_priority_queue_ordering():
    """测试优先级队列正确排序。"""
    import asyncio

    # 创建优先级队列
    queue = asyncio.PriorityQueue()

    # 按非顺序添加任务
    await queue.put((1, "task_normal"))  # 普通任务
    await queue.put((2, "task_retry"))  # 重试任务
    await queue.put((0, "task_urgent"))  # 紧急任务
    await queue.put((1, "task_normal2"))  # 另一个普通任务

    # 验证出队顺序：紧急 -> 普通 -> 普通 -> 重试
    priority1, task1 = await queue.get()
    assert priority1 == 0
    assert task1 == "task_urgent"

    priority2, task2 = await queue.get()
    assert priority2 == 1
    assert task2 == "task_normal"

    priority3, task3 = await queue.get()
    assert priority3 == 1
    assert task3 == "task_normal2"

    priority4, task4 = await queue.get()
    assert priority4 == 2
    assert task4 == "task_retry"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
