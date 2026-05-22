# 内存泄漏排查与修复方案

**审计日期**: 2026-05-21
**审计范围**: Docker 长期运行（数周-数月）下的 Python 进程内存增长风险
**审计基线**: commit `4f10e0f`（已修 Playwright node driver 进程泄漏）

## 一、审计结论

校准后共发现 **1 高 + 2 中 + 1 低 + 1 运维 + 1 防御** 共 6 个真实可落地的改进点。

| ID | 文件 | 风险 | 优先级 | 修复方向 |
|---|---|---|---|---|
| H1 | `src/downloaders/cdp/downloader.py:92,1053` | 高 | P0 | `_notification_cache` 改 `cachetools.TTLCache` |
| M1 | `src/downloaders/cdp/human_behavior.py:81,86-99` | 中 | P1 | `_owned_pages` 读路径加 sweep dead pages |
| M2 | `src/downloaders/cdp/downloader.py:781` | 中 | P1 | `_background_tasks: set` 强引用 + done_callback discard |
| M3 | `src/services/task_service.py:65` | 低 | P2 | 加队列深度 metrics（不限 maxsize） |
| L3 | `docker/docker-compose.prod.yml` | 运维 | P1 | `mem_limit: 2g` + `memswap_limit: 2g` |
| D1 | `src/services/metrics.py` | 防御 | P1 | Prometheus 新增 RSS / dict size / 子进程数 |

## 二、被排除的"伪发现"（已校准）

- **`_cdp_health_status`** —— key 是 `cdp_url`，配置项个数有限，**不是泄漏**。初版审计高估。
- **`_circuit_breaker_state`** —— 单值字段，无累积可能。
- **`callback_service.py` httpx 客户端** —— 已正确使用 `async with`，无需修改。
- **DB 连接** —— SQLite WAL，无连接池累积概念。

## 三、详细发现与修复

### H1 — `_notification_cache` 无界累积 [P0]

**位置**:
- 声明: `src/downloaders/cdp/downloader.py:92`
- 写入: `src/downloaders/cdp/downloader.py:1053-1075`

**证据**:
```python
# line 92
_notification_cache: Dict[str, float] = {}  # key -> last_notify_time

# line 1053
cache_key = f"cdp_conn_fail:{hash(error)}"
# line 1075
CDPDownloader._notification_cache[cache_key] = time.time()
```

**为什么泄漏**: key 来自 `hash(error)`，error 字符串多样性高（包含端口、连接 ID、超时数值、堆栈片段等），每次新 error 模式都写入一条新 entry，**永不清理**。运行数月后可累积数千到数万 entry。

**累积估算**: 每条 entry ≈ 200 字节。10K entry ≈ 2MB（绝对量不大，但是"只增不减"模式，且是 Python dict 的话还有 rehash 开销）。

**修复方案 A（已确认）**: 使用 `cachetools.TTLCache(maxsize=512, ttl=cooldown*2)`

实施步骤：
1. `pyproject.toml` 添加依赖 `cachetools>=5.3`
2. `downloader.py` import：`from cachetools import TTLCache`
3. line 92 改为：
   ```python
   _notification_cache: TTLCache = TTLCache(
       maxsize=512,
       ttl=600,  # 取 cdp_notify_cooldown * 2 的合理上限
   )
   ```
4. 注意：TTLCache 的 `__setitem__` 会自动过期清理，无需手动逻辑
5. 验证：单元测试模拟 600 个不同 error，断言 `len(_notification_cache) <= 512`

**测试**:
- `tests/test_cdp_notification_cache.py` 新增
- 用例 1: 写入 1000 个唯一 key，断言 size 受 maxsize 限制
- 用例 2: 用例 1 后 sleep > ttl，新写入触发旧 entry 清理

---

### M1 — `_owned_pages` 中"外部关闭的尸 Page"累积 [P1]

**位置**: `src/downloaders/cdp/human_behavior.py:50, 81, 86-99`

**证据**:
```python
# line 50
self._owned_pages: set = set()

# line 81
owned = [p for p in self._owned_pages if not p.is_closed()]
```

**为什么泄漏**:
1. `p.is_closed()` 自身可能抛异常（page 对象底层 CDP session 已断），列表推导整体失败 → 后续清理逻辑被跳过
2. `keep_last` 保留的最后一个 Page 如果之后被 Chrome 外部关闭（崩溃、网络断），永远不会被 `discard()`，留在 set 中变"尸 Page"
3. 数月运行后，set 中可能有数十到上百个失效引用，每个间接持有 CDP WebSocket session 状态

**修复方案 A（已确认）**: 在所有 `_owned_pages` 读路径加 sweep dead pages 步骤

实施步骤：
1. 新增私有方法 `_safe_is_closed(page) -> bool`：
   ```python
   @staticmethod
   def _safe_is_closed(page) -> bool:
       try:
           return page.is_closed()
       except Exception:
           # 底层 CDP session 已失效，视为已关闭
           return True
   ```
2. 在 `cleanup_old_pages` 顶部加 sweep：
   ```python
   # sweep dead pages（防止外部关闭的 Page 累积）
   self._owned_pages = {
       p for p in self._owned_pages if not self._safe_is_closed(p)
   }
   ```
3. 在 `background_human_behavior` 入口同样加 sweep
4. 其他读 `_owned_pages` 的位置（如 audio_downloader）一并加

**测试**:
- mock 一个 Page 对象，让 `is_closed()` 抛 `ConnectionClosed` 异常
- 断言：`cleanup_old_pages` 不会因此抛错，且该 Page 被从 set 中移除

---

### M2 — `background_human_behavior` task 无强引用 [P1]

**位置**: `src/downloaders/cdp/downloader.py:781-798`

**证据**:
```python
# line 781
task = asyncio.create_task(
    self._behavior_simulator.background_human_behavior(...)
)
# task 是局部变量，函数返回后没有任何强引用持有它
task.add_done_callback(handle_task_exception)
return True
```

**为什么是 bug**: 这不是内存泄漏，是**反向问题**。Python 官方 asyncio 文档明确警告："Important: Save a reference to the result of this function, to avoid a task disappearing mid-execution." `add_done_callback` 不构成强引用。任务可能在 finally 块跑完前被 GC 提前回收，导致 human behavior 模拟中断且无日志。

**修复方案 A（已确认）**: 加 `_background_tasks: set` 强引用 + done_callback 中 discard

实施步骤：
1. `CDPDownloader.__init__` 中添加：
   ```python
   self._background_tasks: set[asyncio.Task] = set()
   ```
2. line 781 改为：
   ```python
   task = asyncio.create_task(
       self._behavior_simulator.background_human_behavior(...)
   )
   self._background_tasks.add(task)
   task.add_done_callback(self._background_tasks.discard)
   task.add_done_callback(handle_task_exception)
   ```
3. `close()` 方法中（如果尚未有）添加：
   ```python
   for t in list(self._background_tasks):
       t.cancel()
   # 可选：await asyncio.gather(*self._background_tasks, return_exceptions=True)
   ```

**测试**:
- 单元测试：创建多个 background task，验证 `_background_tasks` 在任务完成后自动 discard，最终 size 为 0
- 验证 `close()` 时未完成的任务被 cancel

---

### M3 — `asyncio.PriorityQueue` 无 maxsize [P2]

**位置**: `src/services/task_service.py:65`

**风险评估**: 个人/小团队规模服务，任务速率受 YouTube 下载速度天然限制，队列深度通常 < 100。理论风险但实际不会爆。

**修复方案 C（已确认）**: 不限 maxsize，仅加队列深度 metrics（合并到 D1 实施）。

---

### L3 — Docker 缺 mem_limit [P1]

**位置**: `docker/docker-compose.prod.yml`

**证据**: 当前配置只有 `ulimits.nofile`，无任何 memory 限制。

**修复方案 A（已确认）**: `mem_limit: 2g` + `memswap_limit: 2g`（禁 swap）

实施步骤：
```yaml
services:
  youtube-api:
    # ... existing fields
    mem_limit: 2g
    memswap_limit: 2g     # 等于 mem_limit → 禁用 swap
    mem_reservation: 512m # 软限制，调度参考
```

**为什么禁 swap**: 如果允许 swap，泄漏发生时进程会先吃 swap，宿主机磁盘 IO 飙升、服务响应严重劣化但又不会被 OOMKill，反而拖累其他服务。禁 swap 后内存超 2g 立即 OOMKill，`restart: unless-stopped` 自动拉起，故障恢复快。

**注意**: 你的 docker compose 是 v2 standalone 模式（不是 Swarm），`mem_limit` 字段在 compose v3.8 spec 下依然生效。如果未来迁移到 Swarm，需改用 `deploy.resources.limits.memory`。

**联动**: Uptime Kuma 应已监控容器存活，OOMKill 后重启会触发告警（验证一下）。

---

### D1 — 可观测性补强 [P1]

**位置**: `src/services/metrics.py`（已有 Prometheus 注册中心）

**新增指标（A + C + D 已确认）**:

| 指标名 | 类型 | 来源 | 用途 |
|---|---|---|---|
| `ytdl_process_rss_bytes` | Gauge | `psutil.Process().memory_info().rss` | 总内存上限告警（> 1.5g） |
| `ytdl_dict_size{name}` | Gauge labeled | `len(_notification_cache)`, `len(_owned_pages)`, `task_queue.qsize()` | 量化可疑结构 |
| `ytdl_child_process_count{type}` | Gauge labeled | `psutil.Process().children(recursive=True)` 分类计数 yt-dlp / node / chrome | 防 node driver 泄漏回归 |

实施步骤：
1. `pyproject.toml` 确认依赖 `psutil`（应已存在，yt-dlp 间接依赖）
2. `src/services/metrics.py` 添加：
   ```python
   from prometheus_client import Gauge
   import psutil

   process_rss_bytes = Gauge('ytdl_process_rss_bytes', 'Process RSS memory')
   dict_size = Gauge('ytdl_dict_size', 'Tracked dict size', ['name'])
   child_process_count = Gauge('ytdl_child_process_count', 'Child process count', ['type'])

   def collect_runtime_metrics():
       """在 /metrics 端点请求时同步收集"""
       proc = psutil.Process()
       process_rss_bytes.set(proc.memory_info().rss)

       # dict sizes（从各模块导入引用）
       from src.downloaders.cdp.downloader import CDPDownloader
       dict_size.labels(name='notification_cache').set(len(CDPDownloader._notification_cache))

       # child processes by name
       children = proc.children(recursive=True)
       counts = {'yt-dlp': 0, 'node': 0, 'chrome': 0, 'other': 0}
       for c in children:
           try:
               name = c.name().lower()
               if 'yt-dlp' in name: counts['yt-dlp'] += 1
               elif 'node' in name: counts['node'] += 1
               elif 'chrome' in name: counts['chrome'] += 1
               else: counts['other'] += 1
           except (psutil.NoSuchProcess, psutil.AccessDenied):
               pass
       for k, v in counts.items():
           child_process_count.labels(type=k).set(v)
   ```
3. `/metrics` endpoint handler 调用 `collect_runtime_metrics()` 后再渲染
4. 配合 Uptime Kuma 或 Prometheus alertmanager：
   - `ytdl_process_rss_bytes > 1.5e9` 持续 5min → 告警
   - `ytdl_dict_size{name="notification_cache"} > 600` → 告警（理论上 TTLCache 后不应触发，触发说明 H1 修复失效）
   - `ytdl_child_process_count{type="node"} > 5` → 告警（防 4f10e0f 回归）

**测试**:
- 集成测试：调用 `/metrics`，断言三类指标都存在且数值合理

---

## 四、实施顺序与并行化

按 worktree 拆分为 3 个独立 lane，可完全并行：

```
Lane A  (cdp/downloader.py):    H1  →  M2     [顺序，同文件]
Lane B  (cdp/human_behavior.py): M1            [独立]
Lane C  (docker + metrics):     L3 + D1 + M3  [独立]

合并顺序: A, B, C 任意 → 最后一起 build & deploy
```

预计 CC 总工时：~75min（不含构建部署）。

## 五、验收清单

- [ ] H1: `_notification_cache` 在持续运行 1h（mock 100 个不同 error）后 size ≤ 512
- [ ] M1: mock `is_closed()` 抛异常的 Page，`cleanup_old_pages` 不阻塞且死 Page 被移除
- [ ] M2: 创建 10 个 background task，全部完成后 `_background_tasks` size 为 0；`close()` 取消未完成 task
- [ ] M3: `/metrics` 包含 `ytdl_dict_size{name="task_queue"}`
- [ ] L3: `docker inspect youtube-api | grep Memory` 返回 2147483648 (2g)
- [ ] D1: `/metrics` 返回 RSS、dict size、child count 三类新 gauge
- [ ] 完整 docker-compose 部署到 n305，运行 3 天，RSS 稳定，告警未触发

## 六、长期烧测建议

修复部署后，开 7 天烧测窗口：

```bash
# 每 30 分钟采样
*/30 * * * * curl -s http://n305:8000/metrics | grep -E 'ytdl_(process_rss|dict_size|child_process)' >> /var/log/ytdl-memstat.log
```

期望曲线：RSS 稳定在 300-800MB 之间小幅波动，**不应有持续单调上升趋势**。

## 七、未来如需深度诊断

如果上述修复后仍有 RSS 缓慢增长，按需启用：

1. **tracemalloc 临时启用**:
   ```python
   import tracemalloc; tracemalloc.start(25)
   # 1 小时后
   snapshot = tracemalloc.take_snapshot()
   for stat in snapshot.statistics('lineno')[:20]:
       logger.info(str(stat))
   ```
2. **objgraph 对象计数对比**:
   ```python
   import objgraph
   objgraph.show_growth(limit=20)  # 每小时调用一次，看哪类对象在增长
   ```
3. **生成 heap dump**: `py-spy dump --pid <pid>` 或 `pyrasite-shell`
