# 重试策略调整说明

## 修改内容

### 1. 重试次数调整（从 5 次 → 1 次）

**修改文件**：`src/db/models.py`

**调整前**：
- RATE_LIMITED: 最多重试 5 次，退避时间 5-60 分钟
- NETWORK_ERROR: 最多重试 3 次
- POT_TOKEN_FAILED: 最多重试 5 次
- DOWNLOAD_FAILED: 最多重试 3 次

**调整后**：
- **所有可重试错误统一改为最多重试 1 次**
- 退避时间调整：
  - RATE_LIMITED: 10 分钟（+ 0-120 秒随机抖动）
  - NETWORK_ERROR: 5 分钟（+ 0-60 秒随机抖动）
  - POT_TOKEN_FAILED: 3 分钟（+ 0-60 秒随机抖动）
  - DOWNLOAD_FAILED: 5 分钟（+ 0-60 秒随机抖动）

**调整原因**：
- 避免重试队列积压
- 失败任务快速失败，不占用队列资源
- 用户可以根据实际情况手动重新提交失败任务

### 2. 任务优先级队列（新任务优先）

**修改文件**：
- `src/services/task_service.py`
- `src/core/worker.py`

**调整前**：
- 使用普通队列 `asyncio.Queue[str]`
- 任务按 FIFO（先进先出）顺序处理
- 新任务和重试任务无优先级区别

**调整后**：
- 使用优先级队列 `asyncio.PriorityQueue[tuple[int, str]]`
- 任务优先级规则：
  - **priority=0**：新任务（高优先级，优先处理）✅
  - **priority=1**：重试任务（低优先级，后处理）⬇️

**调整效果**：
- ✅ 新提交的任务会立即处理，不会被重试任务阻塞
- ✅ 重试任务在队列末尾等待，不影响新任务
- ✅ 提升用户体验，新请求响应更快

## 任务处理流程

### 场景 1：新任务提交

```
1. 用户提交新任务 A
   ↓
2. 加入队列 (priority=0, task_id=A)
   ↓
3. Worker 立即从队列取出（优先级 0 最高）
   ↓
4. 开始下载
```

### 场景 2：任务失败重试

```
1. 任务 B 失败（RATE_LIMITED）
   ↓
2. 检查重试次数（0 < 1）✓
   ↓
3. 等待 10 分钟
   ↓
4. 重新加入队列 (priority=1, task_id=B)
   ↓
5. Worker 从队列取任务时，如果有新任务（priority=0）会先处理
   ↓
6. 新任务处理完后，才处理重试任务 B
```

### 场景 3：混合场景

```
队列状态：
  - (priority=1, task_id=B)  # 重试任务
  - (priority=1, task_id=C)  # 重试任务

用户提交新任务 D：
  - (priority=0, task_id=D)  # 新任务
  - (priority=1, task_id=B)  # 重试任务
  - (priority=1, task_id=C)  # 重试任务

Worker 处理顺序：
  1. 先处理 D（priority=0）✅
  2. 再处理 B（priority=1）
  3. 最后处理 C（priority=1）
```

## 任务状态流转

```
新任务
  ↓
pending (priority=0 加入队列)
  ↓
downloading
  ├─ 成功 → completed ✅
  │
  └─ 失败
      ├─ 可重试（retry_count < 1）
      │   ↓
      │   等待退避时间（3-10 分钟）
      │   ↓
      │   pending (priority=1 重新加入队列)
      │   ↓
      │   downloading
      │   ├─ 成功 → completed ✅
      │   └─ 失败 → failed ❌（放弃，不再重试）
      │
      └─ 不可重试 → failed ❌
```

## 影响分析

### 对现有任务的影响

1. **待处理任务（pending）**
   - ✅ 不受影响，继续按优先级处理

2. **失败任务（已重试多次）**
   - ⚠️ 之前可能重试 2-5 次的任务，现在只能重试 1 次
   - 💡 如果这些任务再次失败，会直接标记为 FAILED
   - 💡 用户需要手动重新提交这些任务

3. **重试队列中的任务**
   - ✅ 不会阻塞新任务
   - ⬇️ 会在所有新任务之后处理

### 对成功率的影响

**预期效果**：
- ✅ 成功率应该保持不变（因为配合了更长的任务间隔）
- ✅ 重试 1 次足以应对临时性错误
- ✅ 持续失败的任务快速失败，不浪费资源

**建议**：
- 监控失败率，如果 > 20%，考虑进一步增加任务间隔
- 对于失败的任务，检查失败原因后手动重新提交

## 配置建议

配合这次修改，建议配置：

```bash
# docker/.env
TASK_INTERVAL_MIN=900    # 15 分钟
TASK_INTERVAL_MAX=2700   # 45 分钟
```

**原因**：
- 重试次数减少，需要提高首次成功率
- 更长的间隔可以避免触发限流
- 配合自适应频控机制，限流时会自动延长

## 监控建议

### 1. 查看任务统计

```bash
# 统计各状态任务数量
curl -H "X-API-Key: YOUR_KEY" http://192.168.31.218:8300/api/v1/tasks \
  | jq '.[] | .status' | sort | uniq -c

# 输出示例：
#  45 "completed"
#   3 "pending"
#   2 "failed"
```

### 2. 查看失败任务详情

```bash
# 查看所有失败任务
curl -H "X-API-Key: YOUR_KEY" http://192.168.31.218:8300/api/v1/tasks \
  | jq '.[] | select(.status=="failed") | {id, video_id, error_code, retry_count}'
```

### 3. 监控重试情况

```bash
# 查看日志中的重试记录
docker logs youtube-api --tail 200 | grep "will retry"

# 输出示例：
# Task abc123 will retry (1/1) in 620s
```

## 回滚方案

如果需要回滚到之前的配置：

```bash
# 1. 恢复 src/db/models.py 中的 RETRY_CONFIG
# 2. 恢复 task_queue 为普通队列
git revert <commit_id>

# 3. 重启服务
docker-compose -f docker/docker-compose.prod.yml restart youtube-api
```

## 常见问题

### Q: 为什么只重试 1 次？

**A**:
- 避免重试队列积压，影响新任务处理
- 配合更长的任务间隔（15-45 分钟），首次成功率应该很高
- 失败任务可以快速识别，用户可以根据错误类型决定是否重新提交

### Q: 重试任务会被饿死吗？

**A**:
- 不会。只是优先级较低，但仍然会被处理
- 当没有新任务时，重试任务会被立即处理
- 即使有新任务，重试任务也只是延迟处理，不会被丢弃

### Q: 如何处理失败的任务？

**A**:
1. 查看失败原因（error_code）
2. 如果是 RATE_LIMITED：等待 1-2 小时后重新提交
3. 如果是 VIDEO_UNAVAILABLE：确认视频是否存在
4. 如果是 POT_TOKEN_FAILED：检查 pot-provider 服务

---

**修改日期**：2026-01-25
**版本**：v2.0
**状态**：已实施
