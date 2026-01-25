"""
测试任务优先级功能。

验证紧急任务优先处理、普通任务正常排队、重试任务最低优先级。
"""

import pytest
from src.db.models import TaskPriority, PRIORITY_MAPPING, RETRY_QUEUE_PRIORITY


def test_priority_enum_values():
    """测试优先级枚举值定义正确。"""
    assert TaskPriority.URGENT.value == "urgent"
    assert TaskPriority.NORMAL.value == "normal"


def test_priority_mapping():
    """测试优先级映射到队列优先级数字。"""
    # 紧急任务应该是最高优先级（数字最小）
    assert PRIORITY_MAPPING[TaskPriority.URGENT] == 0
    # 普通任务应该是中等优先级
    assert PRIORITY_MAPPING[TaskPriority.NORMAL] == 1
    # 重试任务应该是最低优先级
    assert RETRY_QUEUE_PRIORITY == 2


def test_to_queue_priority():
    """测试优先级枚举转换为队列优先级。"""
    assert TaskPriority.URGENT.to_queue_priority() == 0
    assert TaskPriority.NORMAL.to_queue_priority() == 1


def test_priority_ordering():
    """测试优先级排序符合预期。"""
    # 数字越小，优先级越高
    urgent_priority = TaskPriority.URGENT.to_queue_priority()
    normal_priority = TaskPriority.NORMAL.to_queue_priority()
    retry_priority = RETRY_QUEUE_PRIORITY

    # 验证顺序：urgent < normal < retry
    assert urgent_priority < normal_priority < retry_priority


@pytest.mark.asyncio
async def test_priority_queue_ordering():
    """测试优先级队列正确排序。"""
    import asyncio

    # 创建优先级队列
    queue = asyncio.PriorityQueue()

    # 按非顺序添加任务
    await queue.put((1, "task_normal"))  # 普通任务
    await queue.put((2, "task_retry"))   # 重试任务
    await queue.put((0, "task_urgent"))  # 紧急任务
    await queue.put((1, "task_normal2")) # 另一个普通任务

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
