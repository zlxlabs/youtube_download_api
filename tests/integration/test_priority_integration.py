"""
优先级功能集成测试。

测试从 API 到数据库的完整流程。
"""

import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.db.database import Database
from src.db.models import Task, TaskPriority, TaskStatus
from src.services.file_service import FileService
from src.services.task_service import TaskService
from src.api.schemas import CreateTaskRequest


@pytest.fixture
async def test_db():
    """创建临时测试数据库。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        await db.connect()
        yield db
        await db.disconnect()


@pytest.fixture
async def test_settings():
    """创建测试配置。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            api_key="test-key",
            data_dir=Path(tmpdir),
            task_interval_min=5,  # 最小值要求 >= 5
            task_interval_max=10,  # 最小值要求 >= 10
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        yield settings


@pytest.fixture
async def task_service(test_db, test_settings):
    """创建任务服务。"""
    file_service = FileService(test_db, test_settings)
    service = TaskService(test_db, test_settings, file_service)
    yield service


@pytest.mark.asyncio
async def test_database_migration_adds_priority_column(test_db):
    """测试数据库迁移自动添加 priority 字段。"""
    # 验证 tasks 表存在 priority 列
    cursor = await test_db.execute("PRAGMA table_info(tasks)")
    columns = await cursor.fetchall()
    column_names = [col["name"] for col in columns]

    assert "priority" in column_names, "priority 列应该存在于 tasks 表中"


@pytest.mark.asyncio
async def test_task_creation_with_urgent_priority(test_db, task_service):
    """测试创建紧急任务。"""
    request = CreateTaskRequest(
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        priority=TaskPriority.URGENT,
        include_audio=True,
        include_transcript=False,
    )

    response = await task_service.create_task(request)

    # 验证响应包含优先级
    assert response.priority == TaskPriority.URGENT

    # 验证数据库中保存了正确的优先级
    task = await test_db.get_task(response.task_id)
    assert task is not None
    assert task.priority == TaskPriority.URGENT


@pytest.mark.asyncio
async def test_task_creation_with_default_priority(test_db, task_service):
    """测试创建默认优先级任务。"""
    request = CreateTaskRequest(
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        # 不指定 priority，应该使用默认值 normal
        include_audio=True,
        include_transcript=False,
    )

    response = await task_service.create_task(request)

    # 验证默认优先级为 normal
    assert response.priority == TaskPriority.NORMAL

    # 验证数据库中保存了正确的优先级
    task = await test_db.get_task(response.task_id)
    assert task is not None
    assert task.priority == TaskPriority.NORMAL


@pytest.mark.asyncio
async def test_priority_queue_ordering(test_db, task_service):
    """测试优先级队列的排序。"""
    # 创建不同优先级的任务
    normal_request = CreateTaskRequest(
        video_url="https://www.youtube.com/watch?v=normal1",
        priority=TaskPriority.NORMAL,
        include_audio=True,
        include_transcript=False,
    )

    urgent_request = CreateTaskRequest(
        video_url="https://www.youtube.com/watch?v=urgent1",
        priority=TaskPriority.URGENT,
        include_audio=True,
        include_transcript=False,
    )

    # 先创建普通任务，再创建紧急任务
    normal_response = await task_service.create_task(normal_request)
    urgent_response = await task_service.create_task(urgent_request)

    # 从队列中获取任务，紧急任务应该先出队
    task1 = await task_service.get_next_task()
    task2 = await task_service.get_next_task()

    # 验证紧急任务优先处理
    assert task1.id == urgent_response.task_id
    assert task1.priority == TaskPriority.URGENT

    # 验证普通任务其次处理
    assert task2.id == normal_response.task_id
    assert task2.priority == TaskPriority.NORMAL


@pytest.mark.asyncio
async def test_task_restore_maintains_priority(test_db, task_service):
    """测试任务恢复时保持原有优先级。"""
    # 创建紧急任务
    request = CreateTaskRequest(
        video_url="https://www.youtube.com/watch?v=test123",
        priority=TaskPriority.URGENT,
        include_audio=True,
        include_transcript=False,
    )

    response = await task_service.create_task(request)

    # 清空队列（模拟重启前的状态）
    while not task_service.task_queue.empty():
        await task_service.task_queue.get()

    # 恢复待处理任务
    restored_count = await task_service.restore_pending_tasks()

    # 验证恢复了 1 个任务
    assert restored_count == 1

    # 验证恢复的任务保持了原有优先级
    task = await task_service.get_next_task()
    assert task is not None
    assert task.id == response.task_id
    assert task.priority == TaskPriority.URGENT


@pytest.mark.asyncio
async def test_database_stores_priority_correctly(test_db):
    """测试数据库正确存储和读取优先级。"""
    # 创建测试任务
    task = Task(
        id="test-task-urgent",
        video_id="dQw4w9WgXcQ",
        video_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        status=TaskStatus.PENDING,
        priority=TaskPriority.URGENT,
        include_audio=True,
        include_transcript=False,
        created_at=datetime.now(timezone.utc),
    )

    # 保存到数据库
    await test_db.create_task(task)

    # 从数据库读取
    retrieved_task = await test_db.get_task(task.id)

    # 验证优先级正确保存和读取
    assert retrieved_task is not None
    assert retrieved_task.priority == TaskPriority.URGENT
    assert isinstance(retrieved_task.priority, TaskPriority)


@pytest.mark.asyncio
async def test_task_restore_handles_more_than_single_batch(test_db, task_service):
    """
    回归测试：restore_pending_tasks 不应有静默上限。

    历史 bug：曾经硬编码 get_pending_tasks(limit=100)，服务重启时堆积超过
    100 条 pending 任务会导致超出部分永远恢复不到队列里，任务静默卡死。
    这里直接造 150 条 pending 任务（超过恢复批次大小），验证：
    1. 全部 150 条都被恢复计数、全部进队列（不多不少）；
    2. 出队顺序语义正确——相同优先级下，按创建时间（恢复批次遍历顺序）先进先出。
    """
    total = 150
    for i in range(total):
        task = Task(
            id=f"restore-task-{i:04d}",
            video_id=f"restore-video-{i:04d}",
            video_url=f"https://www.youtube.com/watch?v=restore-video-{i:04d}",
            status=TaskStatus.PENDING,
            priority=TaskPriority.NORMAL,
            include_audio=True,
            include_transcript=False,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=i),
        )
        await test_db.create_task(task)

    # 清空队列（模拟重启前的状态，保证从空队列开始恢复）
    while not task_service.task_queue.empty():
        await task_service.task_queue.get()

    restored_count = await task_service.restore_pending_tasks()

    # 不再受硬编码 100 条上限影响，150 条应全部恢复
    assert restored_count == total
    assert task_service.task_queue.qsize() == total

    # 相同优先级下，任务 id 按零填充编号与创建时间同序递增，
    # 出队顺序应与创建顺序一致，验证恢复过程未打乱/遗漏/重复任何任务。
    dequeued_ids = []
    while not task_service.task_queue.empty():
        _, task_id = await task_service.task_queue.get()
        dequeued_ids.append(task_id)

    expected_ids = [f"restore-task-{i:04d}" for i in range(total)]
    assert dequeued_ids == expected_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
