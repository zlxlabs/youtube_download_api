# 任务优先级功能设计文档

## 概述

本文档描述任务优先级功能的设计与实现，该功能允许客户端指定任务的紧急程度，系统根据优先级智能调度任务处理顺序。

## 业务需求

1. **紧急任务立即处理**：某些场景下用户需要实时等待结果（如在线转换、VIP 服务）
2. **普通任务正常排队**：大部分常规请求按提交顺序处理
3. **重试任务避让新请求**：失败重试的任务不应阻塞新用户请求

## 优先级层级

系统采用三级优先级机制：

| 优先级 | API 枚举 | 队列优先级 | 说明 |
|--------|----------|-----------|------|
| **高** | `urgent` | 0 | 紧急任务，立即处理 |
| **中** | `normal` | 1 | 普通任务，正常排队（默认） |
| **低** | _(内部)_ | 2 | 重试任务，最低优先级 |

### 优先级规则

1. **数字越小，优先级越高**：使用 Python 的 `asyncio.PriorityQueue`，按数字升序排列
2. **相同优先级 FIFO**：同一优先级内按提交时间先进先出
3. **重试自动降级**：失败重试的任务自动使用最低优先级（priority=2）

## 技术实现

### 1. 数据模型

#### 枚举定义（src/db/models.py）

```python
class TaskPriority(str, Enum):
    """任务优先级枚举。"""

    URGENT = "urgent"  # 紧急任务
    NORMAL = "normal"  # 普通任务（默认）

    def to_queue_priority(self) -> int:
        """转换为队列优先级数字。"""
        return PRIORITY_MAPPING[self]

# 优先级映射
PRIORITY_MAPPING: dict[TaskPriority, int] = {
    TaskPriority.URGENT: 0,  # 最高优先级
    TaskPriority.NORMAL: 1,  # 中等优先级
}

# 重试任务的队列优先级（最低）
RETRY_QUEUE_PRIORITY = 2
```

#### Task 模型扩展

```python
@dataclass
class Task:
    # ... 其他字段
    priority: TaskPriority = TaskPriority.NORMAL
```

### 2. API 接口

#### 请求 Schema（src/api/schemas.py）

```python
class CreateTaskRequest(BaseModel):
    video_url: str
    priority: TaskPriority = Field(
        default=TaskPriority.NORMAL,
        description="任务优先级：urgent（紧急）或 normal（普通）"
    )
    # ... 其他字段
```

#### 响应 Schema

```python
class TaskResponse(BaseModel):
    task_id: Optional[str]
    priority: Optional[TaskPriority]  # 返回任务优先级
    # ... 其他字段
```

### 3. 任务队列

#### 队列结构（src/services/task_service.py）

```python
# 优先级队列：(priority, task_id)
self._task_queue: asyncio.PriorityQueue[tuple[int, str]]
```

#### 入队逻辑

**新任务创建**：
```python
# 使用用户指定的优先级
queue_priority = task.priority.to_queue_priority()  # 0 或 1
await self._task_queue.put((queue_priority, task.id))
```

**任务恢复**（重启后）：
```python
# 保持原有优先级
queue_priority = task.priority.to_queue_priority()
await self._task_queue.put((queue_priority, task.id))
```

**重试任务**（src/core/worker.py）：
```python
# 重试任务使用最低优先级
await self.task_service.task_queue.put((RETRY_QUEUE_PRIORITY, task.id))  # priority=2
```

### 4. 数据库

#### 表结构变更

```sql
ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal';
CREATE INDEX idx_tasks_priority ON tasks(priority);
```

#### 自动迁移

系统启动时自动检测并添加 `priority` 字段（src/db/database.py）：

```python
async def _run_migrations(self) -> None:
    """运行数据库迁移。"""
    # 检查 tasks 表是否存在 priority 列
    # 如果不存在，则添加该列
```

## 使用示例

### 创建紧急任务

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "priority": "urgent",
    "include_audio": true
  }'
```

### 创建普通任务（默认）

```bash
curl -X POST http://localhost:8000/api/v1/tasks \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "include_audio": true
  }'
```

## 调度示例

假设队列中有以下任务：

| 提交顺序 | 任务类型 | 优先级 | 队列优先级 |
|---------|---------|-------|-----------|
| 1 | 普通任务 A | normal | 1 |
| 2 | 重试任务 B | _(retry)_ | 2 |
| 3 | 紧急任务 C | urgent | 0 |
| 4 | 普通任务 D | normal | 1 |

**实际处理顺序**：C (urgent, 0) → A (normal, 1) → D (normal, 1) → B (retry, 2)

## 性能考虑

1. **队列索引**：PriorityQueue 内部使用堆结构，插入和删除的时间复杂度为 O(log n)
2. **数据库索引**：为 `priority` 字段创建索引，加快按优先级查询
3. **内存占用**：队列仅存储 `(priority, task_id)` 元组，内存占用很小

## 兼容性

### 向后兼容

- 默认值为 `normal`，现有客户端无需修改
- 数据库迁移自动为旧记录设置 `priority='normal'`
- API 响应对于缓存命中返回 `priority=null`

### 升级步骤

1. 部署新代码（包含自动迁移）
2. 系统启动时自动添加 `priority` 字段
3. 客户端按需选择是否使用 `priority` 参数

## 监控与日志

### 日志示例

**任务创建**：
```
Created task abc-123 for video dQw4w9WgXcQ (priority=urgent, need_audio=true, ...)
```

**队列处理**：
```
Processing task: abc-123 (dQw4w9WgXcQ)
```

### 建议监控指标

- 各优先级任务的数量分布
- 各优先级任务的平均等待时间
- 紧急任务的处理延迟（P99）

## 未来扩展

### 可能的增强功能

1. **更多优先级层级**：如 `critical`、`low` 等
2. **动态优先级调整**：根据等待时间自动提升优先级
3. **优先级配额**：限制每个 API Key 的紧急任务配额
4. **优先级队列统计**：暴露队列状态 API

## 测试

参见 `tests/test_priority.py`：
- 优先级枚举值测试
- 优先级映射测试
- 队列排序测试
- 端到端集成测试

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/db/models.py` | 优先级枚举和常量定义 |
| `src/api/schemas.py` | API 请求/响应 Schema |
| `src/services/task_service.py` | 任务创建和队列管理 |
| `src/core/worker.py` | 任务处理和重试逻辑 |
| `src/db/database.py` | 数据库表结构和迁移 |
| `tests/test_priority.py` | 优先级功能测试 |
| `docs/PRIORITY_DESIGN.md` | 本设计文档 |

## 总结

优先级功能通过三级队列机制，实现了：
- ✅ 紧急任务优先处理
- ✅ 普通任务正常排队
- ✅ 重试任务避让新请求
- ✅ 向后兼容，平滑升级
- ✅ 代码清晰，易于维护
