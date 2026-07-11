"""
Download worker module.

Background worker that processes download tasks from the queue.
Only downloads what's needed - reuses existing files when available.
"""

import asyncio
import json
import random
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import Settings
from src.core.downloader import (
    DownloadCancelledError,
)
from src.core.ip_ban_breaker import IPBanCircuitBreaker, calculate_ban_recovery_time
from src.core.ip_ban_models import ExecutionDecision, IPBanLevel, IPBanStateChangeContext
from src.db.database import Database
from src.downloaders.exceptions import AllDownloadersFailed, DownloaderAttempt, DownloaderError
from src.downloaders.manager import DownloaderManager
from src.downloaders.models import DownloaderResult
from src.db.models import (
    ErrorCode,
    FileType,
    FileRecord,
    RETRY_CONFIG,
    RETRY_QUEUE_PRIORITY,
    Task,
    TaskStatus,
    VideoInfo,
    is_retryable_error,
)
from src.services.callback_service import CallbackService
from src.services.file_service import FileService
from src.services.metrics import MetricsCollector
from src.services.notify import NotificationService
from src.services.task_service import TaskService
from src.utils.logger import logger


class DownloadWorker:
    """
    Background worker for processing download tasks.

    Smart downloading: only downloads what's missing, reuses existing files.
    """

    def __init__(
        self,
        db: Database,
        settings: Settings,
        task_service: TaskService,
        file_service: FileService,
        callback_service: CallbackService,
        notify_service: NotificationService,
        metrics_collector: Optional[MetricsCollector] = None,
        downloader_manager: Optional[DownloaderManager] = None,
    ):
        """
        Initialize download worker.

        Args:
            db: Database instance.
            settings: Application settings.
            task_service: Task service.
            file_service: File service.
            callback_service: Callback service.
            notify_service: Notification service.
            metrics_collector: Prometheus metrics collector (optional).
            downloader_manager: 共享的下载器管理器实例（可选）。生产环境应
                由 main.py 统一创建并注入，确保 API 路由层与 Worker 执行层
                共用同一份熔断器状态和统计数据，避免出现两套互相独立的
                下载器健康视图。未传入时（如单元测试直接构造 Worker）回退
                为自建一份独立实例，保持向后兼容。
        """
        self.db = db
        self.settings = settings
        self.task_service = task_service
        self.file_service = file_service
        self.callback_service = callback_service
        self.notify_service = notify_service
        self.metrics_collector = metrics_collector

        # 使用下载器管理器（支持多下载器降级 + 元数据缓存）
        self.downloader_manager = downloader_manager or DownloaderManager(settings, db)
        self._running = False
        self._current_task: Optional[Task] = None

        # 幂等保护：确保启动恢复动作（restore_persisted_state）只真正执行
        # 一次。main.py 的 lifespan 会在 asyncio.create_task(self.start())
        # 之前显式 await 调用一次，start() 内部又保留了兜底调用，两处调用
        # 若都真正执行会导致 IPBanCircuitBreaker.restore_state() 重复触发
        # "restored" 状态变更事件，在 ip_ban_history 表里写入重复记录。
        self._ip_ban_state_restored: bool = False

        # IP 熔断器（被动探测型）
        # 等待参数来自 settings（IP_BAN_MIN_WAIT_BEFORE_RETRY / IP_BAN_MAX_RETRY_INTERVAL），
        # 与项目其余熔断器保持一致的"全部可配置"风格。
        # on_state_change 回调把每次状态变更持久化到数据库（ip_ban_status/
        # ip_ban_history），使熔断状态在服务重启（如 D3 自动部署触发的容器
        # 重启）后可以恢复，避免误判为 NORMAL 全速请求 YouTube 加重封禁。
        # 熔断器本身不直接依赖 Database，保持纯内存、可独立单测；回调失败
        # fail-open，只记录日志，不影响熔断器本身的运行。
        self.ip_ban_breaker = IPBanCircuitBreaker(
            min_wait_before_retry=settings.ip_ban_min_wait_before_retry,
            max_retry_interval=settings.ip_ban_max_retry_interval,
            on_state_change=self._handle_ip_ban_state_change,
        )

        # 自适应间隔控制
        # interval_multiplier: 间隔倍数，限流时增大，连续成功时逐步恢复
        # consecutive_successes: 连续成功次数，用于判断是否可以降低倍数
        self._interval_multiplier: float = 1.0
        self._consecutive_successes: int = 0

        # 后台回调任务集合：强引用防止 GC 提前回收（任务完成后自动移除）
        self._callback_tasks: set = set()

    async def start(self) -> None:
        """Start the worker loop."""
        self._running = True

        # 启动时先尝试从数据库恢复上次的 IP 熔断状态，避免容器重启（如 D3
        # 自动部署触发的重启）后误判为 NORMAL 全速请求 YouTube 加重封禁。
        #
        # 这里调用的是幂等的 restore_persisted_state()，而不是直接调用
        # _restore_ip_ban_state()：正常生产路径下，main.py 的 lifespan 已经
        # 在 asyncio.create_task(self.start()) 之前显式 await 过一次
        # restore_persisted_state()（这是修复"startup 通知读到恢复前状态"
        # 这个 bug 的关键——start() 是异步调度的，它与紧随其后的
        # notify_startup() 之间如果没有任何真正让出事件循环的 await 点，
        # start() 内部的恢复动作根本没机会先跑完）。这里保留调用只是作为
        # 兜底：万一 worker 脱离 main.py 编排被单独启动（例如单测直接构造
        # DownloadWorker 后调用 start()），仍然能自我恢复。幂等保护
        # （self._ip_ban_state_restored）确保两处调用不会导致
        # ip_ban_history 表重复写入 "restored" 记录。
        await self.restore_persisted_state()

        logger.info("Download worker started")

        while self._running:
            try:
                await self._process_next_task()
            except asyncio.CancelledError:
                logger.info("Worker cancelled")
                break
            except Exception as e:
                # 添加完整的 traceback 便于调试
                logger.error(
                    f"Worker error: {e}\n"
                    f"Exception type: {type(e).__name__}\n"
                    f"Traceback:\n{traceback.format_exc()}"
                )
                await asyncio.sleep(5)

        logger.info("Download worker stopped")

    async def stop(self) -> None:
        """
        Stop the worker loop and cancel any ongoing download.

        This method:
        1. Sets _running = False to stop the main loop
        2. Calls downloader.cancel() to interrupt any active download
        """
        self._running = False
        logger.info("Stopping download worker...")

        # 触发下载取消，使正在进行的下载尽快结束
        self.downloader_manager.cancel_all()
        logger.info("Download cancellation signal sent")

        # 等待未完成的后台回调发送完毕（带上限，避免拖慢关停）
        if self._callback_tasks:
            logger.info(f"Waiting for {len(self._callback_tasks)} pending callback(s)...")
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._callback_tasks, return_exceptions=True),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for pending callbacks during shutdown")

    # 需要写入 ip_ban_history 的事件类型（触发/升级/降级/恢复正常/启动恢复）。
    # extended（延长熔断）/failed_attempt（记录失败探测）只更新 ip_ban_status，
    # 不追加历史，避免高频次要事件淹没历史表。
    _IP_BAN_HISTORY_EVENT_TYPES = frozenset(
        {"triggered", "upgraded", "downgraded", "recovered", "restored"}
    )

    async def _handle_ip_ban_state_change(
        self, context: IPBanStateChangeContext
    ) -> None:
        """
        IP 熔断器状态变更回调：持久化到数据库。

        注入给 IPBanCircuitBreaker 的 on_state_change（见 __init__），在熔断器
        每次状态变更（触发/升级/降级/恢复/延长/启动恢复/记录失败尝试）后被调用。

        Note:
            IPBanCircuitBreaker._emit_state_change 已经用 try/except 包裹了
            回调调用本身（fail-open，异常只记录日志不上抛）。这里对数据库写入
            再加一层 try/except，是为了在持久化失败时打印更贴合本层语义的日志
            （明确是"持久化失败"而不是笼统的"回调失败"），双重保险确保数据库
            异常绝不会影响熔断器状态转换本身。

        Args:
            context: 状态变更上下文（新旧级别、状态快照、事件类型、原因）
        """
        try:
            await self.db.save_ip_ban_state(
                current_level=context.new_level,
                banned_at=context.new_state.banned_at,
                last_attempt_at=context.new_state.last_attempt_at,
                failed_attempts=context.new_state.failed_attempts,
            )

            if context.event_type not in self._IP_BAN_HISTORY_EVENT_TYPES:
                return

            if context.event_type == "recovered":
                # 恢复事件：banned_at 取变更前（清空前）的原始触发时间，
                # 并记录本次熔断的持续时长，供历史审计使用。
                await self.db.append_ip_ban_history(
                    event_type=context.event_type,
                    ban_level=context.new_level.value,
                    trigger_error=context.reason,
                    banned_at=context.old_state.banned_at,
                    recovered_at=datetime.now(timezone.utc),
                    duration_seconds=context.old_state.time_since_ban,
                    recovery_method="auto_probe",
                )
            else:
                await self.db.append_ip_ban_history(
                    event_type=context.event_type,
                    ban_level=context.new_level.value,
                    trigger_error=context.reason,
                    banned_at=context.new_state.banned_at,
                    recovery_method="restored" if context.event_type == "restored" else None,
                )
        except Exception as e:
            logger.error(
                f"Failed to persist IP ban state change (event={context.event_type}): {e}"
            )

    async def restore_persisted_state(self) -> None:
        """
        对外暴露的启动恢复入口（幂等）。

        背景：_restore_ip_ban_state() 原来只在 start() 内部触发。start()
        是通过 asyncio.create_task() 异步调度的，如果调用方（main.py 的
        lifespan）在 create_task 之后、读取熔断器状态（如发送 startup 通知）
        之前没有任何真正让出事件循环的 await 点，start() 内部的恢复动作
        根本没机会先跑完——导致读到的永远是 IPBanCircuitBreaker.__init__
        的初始值 NORMAL，而不是持久化恢复后的真实状态。这是一个确定性
        bug，不是偶发竞态。

        修复方式：把恢复逻辑拆成这个公开、幂等的方法，由 main.py 在
        asyncio.create_task(download_worker.start()) 之前显式 await 调用，
        确保恢复动作严格发生在 worker 后台循环启动、以及后续读取熔断器
        状态之前，不再依赖脆弱的事件循环调度细节。

        幂等保护：用 self._ip_ban_state_restored 标志位确保恢复动作只
        真正执行一次。IPBanCircuitBreaker.restore_state() 无条件触发
        "restored" 状态变更事件（不像 reset_to_normal 那样有 old_level
        守卫），重复调用会在 ip_ban_history 表里写入重复的 restored 记录；
        start() 内部仍保留对本方法的兜底调用（应对 worker 脱离 main.py
        编排、被单独启动的场景），因此这里的幂等保护是必须的。

        flag 置位时机（P2 修复，2026-07-11）：必须等 _restore_ip_ban_state()
        确认数据库真正读取成功后才置位，不能在调用前就置位。原实现在调用前
        无条件置位——如果 lifespan 首次调用时 load_ip_ban_state() 遇到瞬时
        数据库错误，_restore_ip_ban_state() 内部 fail-open 捕获异常后直接
        返回，但 flag 已经变成 True，导致 start() 内部的兜底恢复调用被幂等
        保护挡成 no-op，进程整个生命周期停留在 NORMAL，恰好在存在持久化
        熔断时失去保护。现在 _restore_ip_ban_state() 返回一个布尔值区分
        "读取成功"（哪怕读到 None/NORMAL，即确认无需恢复）与"读取失败"，
        只有前者才置位 flag，后者保留重试机会给下一次调用（通常是 start()
        的兜底调用）。注意：main.py 是先 await 完 restore_persisted_state()
        再 create_task(start())，两次调用之间不存在真正的并发重入，因此
        这里用一个简单的布尔标志位即可，不需要引入锁。
        """
        if self._ip_ban_state_restored:
            logger.debug(
                "IP ban state already restored on this worker instance, "
                "skipping duplicate restore"
            )
            return

        if await self._restore_ip_ban_state():
            self._ip_ban_state_restored = True
        else:
            logger.warning(
                "IP ban state restore failed due to a database read error; "
                "idempotent flag left unset so a later call (e.g. start()'s "
                "fallback) can retry"
            )

    async def _restore_ip_ban_state(self) -> bool:
        """
        启动时从数据库恢复上次的 IP 熔断状态。

        背景：IPBanCircuitBreaker 状态完全在内存，D3 自动部署每次 push 到
        main 都会重启容器 —— 如果重启时正处于熔断中，服务醒来会误判为
        NORMAL 全速请求 YouTube，加重封禁。这里在 worker 启动的第一时间尝试
        从数据库恢复，是本次持久化功能修复该问题的关键路径。

        恢复判定：只要持久化状态处于熔断中（非 NORMAL）就无条件恢复，不做
        "是否已过 recovery_time" 的过期判断。

        这个熔断器（见 ip_ban_breaker.py）是被动探测型的：recovery_time
        只表示"从这一刻起 should_allow_attempt() 会放行一个探测任务"，
        并不代表 IP 已经恢复——真正的降级/恢复要等探测任务执行成功才会
        发生（见 _analyze_result_and_update_ban）。如果启动时因为
        recovery_time 已过就把持久化的熔断状态当成"过期"直接忽略，会导致
        服务重启后（FULLY_BANNED 场景下这个窗口可达小时级，而 D3 又是每次
        push 就重启）所有排队任务全速放行、完全跳过探测分析，持久化保护
        形同虚设。

        恢复之后，被动探测机制天然自洽：如果 recovery_time 已经过了，
        第一个匹配的任务就会被 should_allow_attempt() 当作探测放行，
        探测成功即自愈（reset_to_normal / downgrade_to_audio_ban），
        不需要在这里额外处理"已过期"的情况。

        数据库读取失败（如首次启动尚未建表的极端情况）fail-open：记录错误
        日志后放弃恢复，不阻塞 worker 启动。

        Returns:
            bool: 数据库是否被成功读取（即使读到 None 或 NORMAL、或数据不
            一致而放弃恢复，只要 load_ip_ban_state() 本身没有抛异常，都算
            成功，返回 True）。只有 load_ip_ban_state() 抛异常（读取失败）
            才返回 False。调用方 restore_persisted_state() 依据这个返回值
            决定是否置位幂等标志位——"确认无需恢复"和"读取失败留待重试"
            必须严格区分，否则一次瞬时数据库错误就会让幂等保护误伤后续的
            兜底恢复调用（P2 bug，见 restore_persisted_state 的 docstring）。
        """
        try:
            saved = await self.db.load_ip_ban_state()
        except Exception as e:
            logger.error(f"Failed to load persisted IP ban state, skip restore: {e}")
            return False

        if saved is None or saved["current_level"] == IPBanLevel.NORMAL:
            logger.info("No active IP ban state to restore on startup")
            return True

        banned_at = saved["banned_at"]
        if banned_at is None:
            # 数据不一致：非 NORMAL 却缺少触发时间，无法计算探测窗口，保守起见
            # 不恢复。但这属于"读取成功、数据本身有问题"，不是"读取失败"——
            # 重试并不会让数据自愈，因此仍然算成功，让幂等 flag 正常置位。
            logger.warning(
                f"Persisted IP ban state ({saved['current_level'].value}) has no "
                f"banned_at timestamp, skip restore"
            )
            return True

        # 无条件恢复：不判定是否过期（理由见上方 docstring）。
        await self.ip_ban_breaker.restore_state(
            level=saved["current_level"],
            banned_at=banned_at,
            last_attempt_at=saved["last_attempt_at"],
            failed_attempts=saved["failed_attempts"],
        )

        # 探测窗口是否已到只影响下面的日志措辞，不影响上面已经做出的恢复决策
        # 本身（P2 修复，2026-07-11：这段日志曾被一个提前 return True 挡成
        # 不可达死代码，见 git blame，这里必须放在 return 之前才能真正执行到）。
        recovery_time = calculate_ban_recovery_time(
            banned_at,
            saved["last_attempt_at"],
            self.settings.ip_ban_min_wait_before_retry,
            self.settings.ip_ban_max_retry_interval,
        )

        if datetime.now() >= recovery_time:
            logger.warning(
                f"Restored IP ban state on startup: {saved['current_level'].value} "
                f"(banned_at={banned_at}) -- probe already eligible, the next matching "
                f"task will be treated as a recovery probe instead of full-speed traffic."
            )
        else:
            remaining = int((recovery_time - datetime.now()).total_seconds())
            logger.warning(
                f"Restored IP ban state on startup: {saved['current_level'].value} "
                f"(banned_at={banned_at}, next probe eligible at {recovery_time} "
                f"in {remaining}s) -- this prevents the service from resuming "
                f"full-speed requests to YouTube right after a container restart "
                f"while still IP-banned."
            )

        return True

    def _send_callback_background(self, task: Task) -> None:
        """
        后台发送 webhook 回调。

        webhook 重试最长可阻塞约 30 秒，改为 fire-and-forget
        避免拖慢 worker 主循环处理下一个任务。
        """
        bg = asyncio.create_task(self._safe_send_callback(task))
        self._callback_tasks.add(bg)
        bg.add_done_callback(self._callback_tasks.discard)

    async def _safe_send_callback(self, task: Task) -> None:
        """发送回调并吞掉异常（回调失败不应影响任务状态）。"""
        try:
            await self.callback_service.send_callback(task)
        except Exception as e:
            logger.error(f"Failed to send callback for task {task.id}: {e}")

    def _on_task_success(self) -> None:
        """
        任务成功时调整自适应间隔。

        连续成功 3 次后开始降低间隔倍数，逐步恢复到正常水平。
        """
        self._consecutive_successes += 1

        # 连续成功 3 次后开始降低倍数
        if self._consecutive_successes >= 3 and self._interval_multiplier > 1.0:
            # 每次降低 20%，但不低于 1.0
            self._interval_multiplier = max(1.0, self._interval_multiplier * 0.8)
            logger.info(
                f"Adaptive interval: multiplier decreased to {self._interval_multiplier:.2f} "
                f"(consecutive successes: {self._consecutive_successes})"
            )

    def _on_rate_limited(self) -> None:
        """
        被限流时调整自适应间隔。

        立即增加间隔倍数，并重置连续成功计数。
        """
        self._consecutive_successes = 0

        # 倍数翻倍，但不超过 4.0（即最大间隔的 4 倍）
        old_multiplier = self._interval_multiplier
        self._interval_multiplier = min(4.0, self._interval_multiplier * 2.0)

        logger.warning(
            f"Adaptive interval: multiplier increased from {old_multiplier:.2f} "
            f"to {self._interval_multiplier:.2f} due to rate limiting"
        )

    def _get_adaptive_wait_time(self, task: Task) -> float:
        """
        计算自适应等待时间。

        根据刚完成的任务类型选择间隔策略：
        - 字幕任务（transcript_only）：短间隔（轻量级，低风控）
        - 音频/混合任务：长间隔（重量级，高风控）

        基于配置的最小/最大间隔，乘以自适应倍数。

        Args:
            task: 刚完成的任务

        Returns:
            等待时间（秒）
        """
        # 根据任务类型选择基准间隔
        if not task.include_audio and task.include_transcript:
            # 字幕任务：短间隔
            base_min = self.settings.transcript_interval_min
            base_max = self.settings.transcript_interval_max
        else:
            # 音频/混合任务：长间隔
            base_min = self.settings.audio_interval_min
            base_max = self.settings.audio_interval_max

        # 应用自适应倍数
        adjusted_min = base_min * self._interval_multiplier
        adjusted_max = base_max * self._interval_multiplier

        # 在调整后的范围内随机选择
        wait_time = random.uniform(adjusted_min, adjusted_max)

        return wait_time

    async def _get_executable_task(
        self,
    ) -> tuple[Optional[Task], Optional[ExecutionDecision]]:
        """
        从队列中获取一个可执行的任务，跳过被 IP 熔断阻止的任务。

        IP ban skip flow:
          1. Pop task from queue
          2. Check IP ban compatibility
          3. If incompatible -> re-queue task, try next one
          4. If compatible (or probe allowed) -> return task
          5. If queue exhausted -> sleep(min(ban_remaining, 60s)), return None

        Returns:
            (task, decision) tuple. task=None if no executable task found.
        """
        deferred_tasks: list[tuple[int, str]] = []
        max_attempts = 20  # 避免无限循环，最多扫描 20 个任务

        try:
            for _ in range(max_attempts):
                task = await self.task_service.get_next_task()
                if not task:
                    break

                # 检查 IP 熔断状态
                decision = await self._check_ip_ban_and_decide(task)

                if decision and decision.action == "delay":
                    # 任务被 IP 熔断阻止，重新入队并尝试下一个
                    queue_priority = self._calculate_requeue_priority(task)
                    deferred_tasks.append((queue_priority, task.id))
                    logger.debug(
                        f"Task {task.id} skipped due to IP ban "
                        f"({decision.reason}), trying next task"
                    )
                    continue

                # 找到可执行的任务
                return task, decision

            # 队列中没有可执行的任务
            if deferred_tasks:
                ban_remaining = self.ip_ban_breaker.get_remaining_time()
                sleep_time = min(ban_remaining, 60) if ban_remaining > 0 else 30
                logger.info(
                    f"All {len(deferred_tasks)} queued tasks blocked by IP ban, "
                    f"sleeping {sleep_time}s (ban remaining: {ban_remaining}s)"
                )
                await asyncio.sleep(sleep_time)

            return None, None

        finally:
            # 将所有被跳过的任务放回队列
            for priority, task_id in deferred_tasks:
                await self.task_service.task_queue.put((priority, task_id))

    def _calculate_requeue_priority(self, task: Task) -> int:
        """计算重新入队的优先级（保持原始优先级）。"""
        from src.db.models import calculate_queue_priority
        return calculate_queue_priority(
            user_priority=task.priority,
            include_audio=task.include_audio,
            include_transcript=task.include_transcript,
        )

    async def _process_next_task(self) -> None:
        """
        Process the next task from the queue.

        IP ban skip mechanism:
        When IP ban is active, incompatible tasks (e.g. audio tasks during AUDIO_BANNED)
        are re-queued and skipped. The worker tries to find a compatible task from the queue.
        If no compatible task is found, sleeps until the ban remaining time (capped at 60s).

        Handles cancellation gracefully - if cancelled during download,
        the task is reset to pending status for retry on next startup.
        """
        # 检查是否已停止
        if not self._running:
            return

        # 尝试从队列中取出一个可执行的任务
        # IP 熔断时跳过不兼容的任务（重新入队），避免阻塞 Worker
        task, decision = await self._get_executable_task()

        if not task:
            await asyncio.sleep(1)
            return

        self._current_task = task
        logger.info(f"Processing task: {task.id} ({task.video_id})")

        # 记录是否是探测尝试
        is_probe = decision.is_probe if decision else False
        ban_level_before = self.ip_ban_breaker.get_current_level()

        # 重置取消标志，为新任务准备
        self.downloader_manager.reset_cancel_all()

        try:
            await self.db.update_task_status(task.id, TaskStatus.DOWNLOADING)

            # Execute task (smart download - only what's needed, with timeout guard)
            try:
                result = await asyncio.wait_for(
                    self._execute_task(task),
                    timeout=self.settings.task_timeout
                )
            except asyncio.TimeoutError:
                logger.error(f"Task {task.id} timed out after {self.settings.task_timeout}s")
                raise DownloaderError(
                    message=f"Task timed out after {self.settings.task_timeout}s",
                    error_code=ErrorCode.TASK_TIMEOUT,
                )

            # 如果是探测尝试，分析结果并更新熔断状态
            #
            # P1 修复（2026-07-11，Codex 第 16 轮）：修复前 _execute_task /
            # _execute_download_with_manager 返回的成功结果 dict 从不携带
            # "downloader_result" 键，导致这里的 isinstance 检查恒为 False——
            # 探测任务成功执行也从来不会触发熔断降级/解除，持久化恢复出来的
            # 熔断状态因此会永久卡在熔断态（"熔断永生"）。现在
            # _execute_download_with_manager 在成功路径上会显式写入
            # downloader_result（以及 audio_requested/transcript_requested，
            # 用于区分"真正探测到仍失败"与"任务本不需要/走了缓存复用"）。
            if is_probe and isinstance(result.get("downloader_result"), DownloaderResult):
                await self._analyze_result_and_update_ban(
                    result["downloader_result"],
                    was_probe=True,
                    ban_level_before=ban_level_before,
                    audio_requested=result.get("audio_requested", True),
                    transcript_requested=result.get("transcript_requested", True),
                )

            # Update task completion with retry logic
            # 这是关键操作，如果失败会导致任务状态不一致
            await self._update_task_completed_with_retry(task.id, result)

            logger.info(f"Task {task.id} completed successfully")

            # Record metrics: task completed + lifecycle duration
            if self.metrics_collector:
                self.metrics_collector.record_task_completed("completed")
                now = datetime.now(timezone.utc)
                if task.created_at and task.started_at:
                    queue_time = (task.started_at - task.created_at).total_seconds()
                    self.metrics_collector.record_task_duration("queue", queue_time)
                if task.started_at:
                    download_time = (now - task.started_at).total_seconds()
                    self.metrics_collector.record_task_duration("download", download_time)

            # 更新自适应间隔（成功）
            self._on_task_success()

            # Send notifications (non-critical, failure won't affect task status)
            await self._send_notifications_safe(task, result)

        except DownloadCancelledError:
            # 下载被取消（通常是因为 Ctrl+C）
            # 将任务状态重置为 pending，下次启动时会自动恢复
            logger.warning(f"Task {task.id} cancelled due to shutdown")
            await self.db.update_task_status(task.id, TaskStatus.PENDING)
            # 不等待，直接返回以加快关闭速度
            return

        except (DownloaderError, AllDownloadersFailed) as e:
            await self._handle_download_error(task, e)

        except Exception as e:
            logger.error(f"Unexpected error processing task {task.id}: {e}")
            # 将未知错误转换为 DownloaderError
            downloader_error = DownloaderError(
                message=str(e),
                error_code=ErrorCode.INTERNAL_ERROR,
            )
            await self._handle_download_error(task, downloader_error)

        finally:
            # 如果已停止，不等待直接返回
            if not self._running:
                self._current_task = None
                return

            # 使用自适应间隔（基于刚完成的任务类型）
            if self._current_task:
                wait_time = self._get_adaptive_wait_time(self._current_task)
                task_type = "transcript" if (not self._current_task.include_audio and self._current_task.include_transcript) else "audio/mixed"

                if self._interval_multiplier > 1.0:
                    logger.info(
                        f"Waiting {wait_time:.1f}s before next task "
                        f"(task_type={task_type}, multiplier: {self._interval_multiplier:.2f}x)"
                    )
                else:
                    logger.debug(f"Waiting {wait_time:.1f}s before next task (task_type={task_type})")

                self._current_task = None
                await asyncio.sleep(wait_time)
            else:
                self._current_task = None

    async def _execute_task(self, task: Task) -> dict:
        """
        Execute task with smart downloading using downloader manager.

        Only downloads what's missing. Reuses existing files when available.
        Updates video_resource with metadata.

        Args:
            task: Task to execute.

        Returns:
            Dict with: audio_file_id, transcript_file_id, reused_audio, reused_transcript
        """
        # Check what's already available (double-check, may have changed)
        existing_files = await self.file_service.get_all_files_for_video(task.video_id)
        existing_audio = existing_files.get("audio")
        existing_transcript = existing_files.get("transcript")

        # Determine what we actually need to download
        need_audio = task.include_audio and existing_audio is None
        need_transcript = task.include_transcript and existing_transcript is None

        # If nothing to download, just return existing files
        if not need_audio and not need_transcript:
            logger.info(f"Task {task.id}: All resources already exist, nothing to download")
            return {
                "audio_file_id": existing_audio.id if existing_audio else None,
                "transcript_file_id": existing_transcript.id if existing_transcript else None,
                "reused_audio": existing_audio is not None,
                "reused_transcript": existing_transcript is not None,
            }

        logger.info(
            f"Task {task.id}: need_audio={need_audio}, need_transcript={need_transcript}"
        )

        # 使用下载器管理器下载（自动降级）
        return await self._execute_download_with_manager(
            task, existing_audio, existing_transcript, need_audio, need_transcript
        )

    async def _execute_download_with_manager(
        self,
        task: Task,
        existing_audio: Optional[FileRecord],
        existing_transcript: Optional[FileRecord],
        need_audio: bool,
        need_transcript: bool,
    ) -> dict:
        """
        Execute download using downloader manager (with fallback support).

        Args:
            task: Task to execute.
            existing_audio: Existing audio file (if any).
            existing_transcript: Existing transcript file (if any).
            need_audio: Whether to download audio.
            need_transcript: Whether to fetch transcript.

        Returns:
            Dict with execution result.
        """
        # 追踪首次探测结果，用于在下载失败时保存字幕可用性信息
        # 这样可以避免重复探测同一视频的字幕（节省 API 调用成本）
        first_result: Optional[DownloaderResult] = None

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                output_dir = Path(temp_dir)

                # 使用下载器管理器下载（自动降级）
                result: DownloaderResult = await self.downloader_manager.download_with_fallback(
                    video_url=task.video_url,
                    video_id=task.video_id,
                    output_dir=output_dir,
                    include_audio=need_audio,
                    include_transcript=need_transcript,
                )

                # 保存首次探测结果（包含字幕可用性信息）
                # 即使后续音频降级失败，也能保存这个探测结果
                first_result = result

                logger.info(
                    f"Task {task.id}: Download completed with {result.downloader} "
                    f"(audio={result.audio_path is not None}, "
                    f"transcript={result.transcript_path is not None})"
                )

                # Audio fallback 逻辑：仅字幕模式但无字幕时，自动下载音频
                audio_fallback = False
                if not need_audio and need_transcript and not result.has_transcript:
                    logger.info(
                        f"Task {task.id}: No transcript available, falling back to audio download"
                    )
                    audio_fallback = True

                    # 先检查数据库中是否已经存在音频文件（可能是人工上传的）
                    if existing_audio is not None:
                        logger.info(
                            f"Task {task.id}: Audio already exists in database (reusing), "
                            f"no need to download"
                        )
                        # 不需要下载，后续会从数据库读取 existing_audio
                    else:
                        # 数据库中没有音频，重新下载
                        logger.info(
                            f"Task {task.id}: Audio not found in database, downloading..."
                        )
                        result = await self.downloader_manager.download_with_fallback(
                            video_url=task.video_url,
                            video_id=task.video_id,
                            output_dir=output_dir,
                            include_audio=True,  # 强制下载音频
                            include_transcript=False,  # 已经确认没有字幕了
                        )

                        logger.info(
                            f"Task {task.id}: Audio fallback completed with {result.downloader} "
                            f"(audio={result.audio_path is not None})"
                        )

                # 将 VideoMetadata 转换为 VideoInfo（兼容现有代码）
                from src.db.models import VideoInfo as DbVideoInfo

                video_info = DbVideoInfo(
                    title=result.video_metadata.title,
                    author=result.video_metadata.author,
                    channel_id=result.video_metadata.channel_id,
                    duration=result.video_metadata.duration,
                    description=result.video_metadata.description,
                    upload_date=result.video_metadata.upload_date,
                    view_count=result.video_metadata.view_count,
                    thumbnail=result.video_metadata.thumbnail,
                )

                # Update video resource with metadata
                await self._update_video_resource(
                    task.video_id,
                    video_info,
                    result.has_transcript,
                )

                # Process audio file
                audio_file_id = existing_audio.id if existing_audio else None
                reused_audio = existing_audio is not None
                # 下载器归属：仅在本次真正新下载时记录，复用缓存时保持 None
                # （reused_audio 标志已表达来源，不写占位值）
                audio_downloader: Optional[str] = None

                # 修复：audio_fallback 场景下也需要保存音频
                if (need_audio or audio_fallback) and result.audio_path and result.audio_path.exists():
                    audio_file = await self.file_service.create_file_record(
                        video_id=task.video_id,
                        file_type=FileType.AUDIO,
                        source_path=result.audio_path,
                        quality=str(self.settings.audio_quality),
                        video_title=video_info.title,
                    )
                    audio_file_id = audio_file.id
                    reused_audio = False
                    audio_downloader = result.downloader

                # Process transcript file
                # 优化：无论用户是否请求字幕，只要下载器返回了字幕就保存
                transcript_file_id = existing_transcript.id if existing_transcript else None
                reused_transcript = existing_transcript is not None
                transcript_downloader: Optional[str] = None

                if result.transcript_path and result.transcript_path.exists():
                    if existing_transcript is None:
                        # 没有缓存，保存新字幕
                        lang = self._extract_language(result.transcript_path)
                        transcript_file = await self.file_service.create_file_record(
                            video_id=task.video_id,
                            file_type=FileType.TRANSCRIPT,
                            source_path=result.transcript_path,
                            language=lang,
                            video_title=video_info.title,
                        )
                        transcript_file_id = transcript_file.id
                        reused_transcript = False
                        transcript_downloader = result.downloader
                        logger.info(
                            f"Task {task.id}: Saved transcript (requested={need_transcript})"
                        )

                return {
                    "audio_file_id": audio_file_id,
                    "transcript_file_id": transcript_file_id,
                    "reused_audio": reused_audio,
                    "reused_transcript": reused_transcript,
                    "audio_fallback": audio_fallback,  # 标记是否触发了音频降级
                    "downloader": result.downloader,  # 下载器名称
                    "audio_downloader": audio_downloader,  # 产出音频文件的下载器（未下载则为 None）
                    "transcript_downloader": transcript_downloader,  # 产出字幕文件的下载器（未下载则为 None）
                    # 被动探测熔断分析用（见 _process_next_task -> _analyze_result_and_update_ban）。
                    # 故意使用 first_result 而不是上面可能被 audio_fallback 二次下载覆盖的
                    # result：audio_fallback 的二次下载只请求音频（include_transcript=False），
                    # 如果直接用最终 result，会把首次字幕探测的真实结果（成功/失败）抹掉，
                    # 让熔断分析看到一个"transcript_path=None"的假象，误判为字幕仍不可用。
                    "downloader_result": first_result,
                    # 本次是否真正向下载器请求了对应资源。区分"没请求"（任务本不需要，
                    # 或已有缓存复用）与"请求了但失败"——前者 first_result 对应字段恒为
                    # None，不能被熔断分析当作"探测到仍然失败"，否则会把纯粹的缓存命中
                    # 误判成风控故障，错误延长甚至升级熔断级别。
                    "audio_requested": need_audio,
                    "transcript_requested": need_transcript,
                }

        except (DownloaderError, AllDownloadersFailed) as e:
            # 即使下载失败，也尝试保存首次探测到的字幕可用性信息
            # 这样下次请求同一视频时，可以直接利用缓存的探测结果，避免重复 API 调用
            if first_result and first_result.video_metadata:
                try:
                    from src.db.models import VideoInfo as DbVideoInfo

                    video_info = DbVideoInfo(
                        title=first_result.video_metadata.title,
                        author=first_result.video_metadata.author,
                        channel_id=first_result.video_metadata.channel_id,
                        duration=first_result.video_metadata.duration,
                        description=first_result.video_metadata.description,
                        upload_date=first_result.video_metadata.upload_date,
                        view_count=first_result.video_metadata.view_count,
                        thumbnail=first_result.video_metadata.thumbnail,
                    )

                    await self._update_video_resource(
                        task.video_id,
                        video_info,
                        has_native_transcript=first_result.has_transcript,
                    )

                    logger.info(
                        f"Task {task.id}: Saved transcript detection result "
                        f"(has_transcript={first_result.has_transcript}) despite download failure"
                    )
                except Exception as save_error:
                    # 保存探测结果失败不应影响原有错误处理流程
                    logger.warning(
                        f"Task {task.id}: Failed to save transcript detection result: {save_error}"
                    )

            # 重新抛出原始异常，让上层处理
            raise

    async def _update_video_resource(
        self,
        video_id: str,
        video_info: VideoInfo,
        has_native_transcript: bool,
    ) -> None:
        """
        Update video resource with metadata.

        Args:
            video_id: YouTube video ID.
            video_info: Video metadata.
            has_native_transcript: Whether video has native subtitles.
        """
        await self.db.update_video_resource(
            video_id=video_id,
            video_info=video_info,
            has_native_transcript=has_native_transcript,
        )
        logger.debug(f"Updated video resource: {video_id}")

    # 单条失败详情 message 的截断长度，避免超长日志片段撑爆 failure_details 列
    _FAILURE_MESSAGE_MAX_LEN = 200

    def _build_failure_details(self, error: DownloaderError) -> str:
        """
        构建失败归因 JSON 字符串，写入 tasks.failure_details 列。

        - AllDownloadersFailed 且携带结构化 attempts：使用 attempts 列表，
          每个下载器一条记录（下载器名/error_code/message），完整还原整条
          降级链的尝试结果。
        - 其他情况（普通 DownloaderError，如任务级超时、未预期异常转换后的
          单次错误，或 AllDownloadersFailed 未携带 attempts 的旧路径）：
          退化为单条记录。

        Args:
            error: 失败异常（DownloaderError 或其子类 AllDownloadersFailed）。

        Returns:
            JSON 字符串，形如
            '[{"downloader": "cdp", "error_code": "CDP_NO_COOKIES", "message": "..."}]'
        """
        attempts: list[DownloaderAttempt]
        if isinstance(error, AllDownloadersFailed) and error.attempts:
            attempts = error.attempts
        else:
            attempts = [
                DownloaderAttempt(
                    downloader=error.downloader or "unknown",
                    error_code=error.error_code.value,
                    message=error.message,
                )
            ]

        records = [
            {
                "downloader": a.downloader,
                "error_code": a.error_code,
                "message": a.message[: self._FAILURE_MESSAGE_MAX_LEN],
            }
            for a in attempts
        ]
        return json.dumps(records, ensure_ascii=False)

    async def _handle_download_error(self, task: Task, error: DownloaderError) -> None:
        """
        Handle download error with retry logic.

        如果音频下载失败但用户未请求字幕，会尝试降级到字幕（因为字幕下载风控更低）。

        Args:
            task: Failed task.
            error: Download error.
        """
        logger.error(f"Task {task.id} failed: {error.error_code.value} - {error.message}")

        # 检查是否需要触发 IP 熔断
        if isinstance(error, DownloaderError):
            await self._trigger_ban_from_error(error)

        # 如果是限流错误，调整自适应间隔
        if error.error_code == ErrorCode.RATE_LIMITED:
            self._on_rate_limited()

        # 字幕降级逻辑：音频失败时尝试下载字幕作为备选
        # 条件：1) 用户请求了音频但未请求字幕 2) 音频下载失败 3) 尚未尝试过字幕降级
        if (
            task.include_audio
            and not task.include_transcript
            and isinstance(error, (DownloaderError, AllDownloadersFailed))
            and not getattr(task, "_transcript_fallback_attempted", False)
        ):
            logger.info(
                f"Task {task.id}: Audio download failed, attempting transcript fallback "
                f"(transcript download has lower risk of rate limiting)"
            )

            # 尝试字幕降级
            transcript_fallback_success = await self._try_transcript_fallback(task)

            if transcript_fallback_success:
                # 字幕降级成功，任务完成
                logger.info(
                    f"Task {task.id}: Transcript fallback succeeded, task completed with transcript only"
                )
                return
            else:
                # 字幕降级失败，继续原有的失败处理逻辑
                logger.warning(
                    f"Task {task.id}: Transcript fallback failed or video has no transcript, "
                    f"task will be marked as failed"
                )

        if is_retryable_error(error.error_code):
            config = RETRY_CONFIG.get(error.error_code, {})
            max_retries = config.get("max_retries", 0)

            if task.retry_count < max_retries:
                new_count = await self.db.increment_retry_count(task.id)

                backoff = config.get("backoff", [60])
                delay_idx = min(new_count - 1, len(backoff) - 1)
                base_delay = backoff[delay_idx]
                jitter = random.uniform(0, config.get("jitter", 0))
                retry_delay = base_delay + jitter

                logger.warning(
                    f"Task {task.id} will retry ({new_count}/{max_retries}) "
                    f"in {retry_delay:.0f}s"
                )

                await asyncio.sleep(retry_delay)
                # 重试任务使用最低优先级，新任务会优先处理
                await self.task_service.task_queue.put((RETRY_QUEUE_PRIORITY, task.id))
                return

        await self.db.update_task_status(
            task_id=task.id,
            status=TaskStatus.FAILED,
            error_code=error.error_code,
            error_message=error.message,
            failure_details=self._build_failure_details(error),
        )

        # Record metrics: task failed
        if self.metrics_collector:
            self.metrics_collector.record_task_completed("failed")

        task_updated = await self.db.get_task(task.id)
        if task_updated:
            # 提取失败的下载器列表（如果是 AllDownloadersFailed 错误）
            # errors 格式: ["ytdlp: error message", "tikhub: error message"]
            # 注意: 快速失败场景（如直播检测）errors 不含下载器名称，需过滤
            failed_downloaders = None
            if isinstance(error, AllDownloadersFailed):
                known_downloaders = {"cdp", "ytdlp", "tikhub"}
                parsed = [
                    err.split(":")[0].strip()
                    for err in error.errors
                    if ":" in err
                ]
                failed_downloaders = [
                    name for name in parsed if name in known_downloaders
                ] or None

            await self.notify_service.notify_failed(
                task_updated, error.message, failed_downloaders=failed_downloaders
            )

            if task_updated.callback_url:
                self._send_callback_background(task_updated)

    async def _try_transcript_fallback(self, task: Task) -> bool:
        """
        尝试字幕降级：音频下载失败时，尝试仅下载字幕。

        字幕下载对风控的影响更小，可以作为备选方案。

        Args:
            task: 失败的任务

        Returns:
            是否成功下载字幕并完成任务
        """
        try:
            # 标记已尝试字幕降级（避免重复尝试）
            # 注意：这个标记只在内存中，不持久化
            task._transcript_fallback_attempted = True  # type: ignore

            # 1. 检查是否已有字幕缓存
            existing_files = await self.file_service.get_all_files_for_video(task.video_id)
            existing_transcript = existing_files.get("transcript")

            if existing_transcript:
                logger.info(
                    f"Task {task.id}: Found cached transcript for video {task.video_id}, "
                    f"using cached file"
                )
                # 直接使用缓存的字幕完成任务
                # 归属列保持 None：reused_transcript=True 已表达"来自缓存"，
                # 不写占位值（如 'cache'）
                await self.db.update_task_completed(
                    task_id=task.id,
                    audio_file_id=None,
                    transcript_file_id=existing_transcript.id,
                    reused_audio=False,
                    reused_transcript=True,
                )

                # 发送完成通知
                task_updated = await self.db.get_task(task.id)
                if task_updated:
                    await self.notify_service.notify_completed(
                        task_updated, downloader="cached", transcript_fallback=True
                    )
                    if task_updated.callback_url:
                        self._send_callback_background(task_updated)

                return True

            # 2. 尝试下载字幕
            logger.info(f"Task {task.id}: Attempting to download transcript only")

            with tempfile.TemporaryDirectory() as temp_dir:
                output_dir = Path(temp_dir)

                try:
                    # 使用下载器管理器下载字幕
                    result: DownloaderResult = (
                        await self.downloader_manager.download_with_fallback(
                            video_url=task.video_url,
                            video_id=task.video_id,
                            output_dir=output_dir,
                            include_audio=False,  # 仅字幕
                            include_transcript=True,
                        )
                    )

                    # 检查是否成功获取字幕
                    if not result.has_transcript or not result.transcript_path:
                        logger.info(
                            f"Task {task.id}: Video {task.video_id} has no available transcript"
                        )
                        return False

                    logger.info(
                        f"Task {task.id}: Successfully downloaded transcript using {result.downloader}"
                    )

                    # 保存视频元数据
                    from src.db.models import VideoInfo as DbVideoInfo

                    video_info = DbVideoInfo(
                        title=result.video_metadata.title,
                        author=result.video_metadata.author,
                        channel_id=result.video_metadata.channel_id,
                        duration=result.video_metadata.duration,
                        description=result.video_metadata.description,
                        upload_date=result.video_metadata.upload_date,
                        view_count=result.video_metadata.view_count,
                        thumbnail=result.video_metadata.thumbnail,
                    )

                    await self._update_video_resource(
                        task.video_id,
                        video_info,
                        result.has_transcript,
                    )

                    # 保存字幕文件
                    lang = self._extract_language(result.transcript_path)
                    transcript_file = await self.file_service.create_file_record(
                        video_id=task.video_id,
                        file_type=FileType.TRANSCRIPT,
                        source_path=result.transcript_path,
                        language=lang,
                        video_title=video_info.title,
                    )

                    # 标记任务完成（仅字幕，音频为 None）
                    await self.db.update_task_completed(
                        task_id=task.id,
                        audio_file_id=None,
                        transcript_file_id=transcript_file.id,
                        reused_audio=False,
                        reused_transcript=False,
                        transcript_downloader=result.downloader,
                    )

                    # 发送完成通知
                    task_updated = await self.db.get_task(task.id)
                    if task_updated:
                        await self.notify_service.notify_completed(
                            task_updated,
                            downloader=result.downloader,
                            transcript_fallback=True,
                        )
                        if task_updated.callback_url:
                            self._send_callback_background(task_updated)

                    return True

                except (DownloaderError, AllDownloadersFailed) as e:
                    # 字幕下载也失败了
                    logger.warning(
                        f"Task {task.id}: Transcript download failed: {e.error_code.value} - {e.message}"
                    )
                    return False

        except Exception as e:
            # 字幕降级过程中出现未预期错误
            logger.error(
                f"Task {task.id}: Unexpected error during transcript fallback: {e}",
                exc_info=True,
            )
            return False

    def _extract_language(self, filepath: Path) -> str:
        """
        Extract language code from transcript filename.

        Args:
            filepath: Transcript file path.

        Returns:
            Language code.
        """
        parts = filepath.stem.split(".")
        if len(parts) >= 2:
            return parts[-1]
        return "unknown"

    async def _update_task_completed_with_retry(
        self,
        task_id: str,
        result: dict,
        max_retries: int = 3,
    ) -> None:
        """
        Update task completion with retry logic.

        关键操作：如果数据库更新失败，会导致任务状态不一致。
        使用重试机制确保状态更新成功。

        Args:
            task_id: Task ID.
            result: Execution result dict.
            max_retries: Maximum retry attempts.

        Raises:
            Exception: If all retries failed.
        """
        last_error: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                await self.db.update_task_completed(
                    task_id=task_id,
                    audio_file_id=result["audio_file_id"],
                    transcript_file_id=result["transcript_file_id"],
                    reused_audio=result["reused_audio"],
                    reused_transcript=result["reused_transcript"],
                    # "全部复用/无需下载"的早退路径不携带这两个 key，用 .get()
                    # 兜底为 None，与 reused=True 场景的语义保持一致
                    audio_downloader=result.get("audio_downloader"),
                    transcript_downloader=result.get("transcript_downloader"),
                )
                return  # 成功，直接返回
            except Exception as e:
                last_error = e
                logger.warning(
                    f"Failed to update task {task_id} completion "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    # 指数退避: 1s, 2s, 4s
                    await asyncio.sleep(2 ** attempt)

        # 所有重试都失败了，记录详细错误
        logger.error(
            f"All {max_retries} attempts to update task {task_id} completion failed. "
            f"Last error: {last_error}\n"
            f"Traceback:\n{traceback.format_exc()}"
        )
        raise last_error  # type: ignore[misc]

    async def _send_notifications_safe(self, task: Task, result: dict) -> None:
        """
        Send notifications safely without affecting task completion.

        通知是非关键操作，失败不应影响任务状态。

        Args:
            task: Completed task.
            result: Execution result containing downloader info.
        """
        try:
            task_updated = await self.db.get_task(task.id)
            if task_updated:
                # 发送完成通知
                try:
                    downloader = result.get("downloader")
                    await self.notify_service.notify_completed(
                        task_updated, downloader=downloader
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to send completion notification for task {task.id}: {e}"
                    )

                # 发送 webhook 回调（后台执行，异常在 _safe_send_callback 中处理）
                if task_updated.callback_url:
                    self._send_callback_background(task_updated)
        except Exception as e:
            logger.error(
                f"Failed to fetch task {task.id} for notifications: {e}"
            )

    def _get_task_type(self, task: Task) -> str:
        """
        判断任务类型。

        Args:
            task: 任务对象

        Returns:
            任务类型："transcript_only" | "audio" | "mixed"
        """
        if task.include_audio and task.include_transcript:
            return "mixed"
        elif task.include_audio:
            return "audio"
        else:
            return "transcript_only"

    async def _check_ip_ban_and_decide(self, task: Task) -> Optional[ExecutionDecision]:
        """
        检查 IP 熔断状态并决定是否执行任务。

        Args:
            task: 待执行的任务

        Returns:
            ExecutionDecision 如果需要特殊处理（延迟/拒绝），
            None 表示可以正常执行
        """
        ban_level = self.ip_ban_breaker.get_current_level()

        if ban_level == IPBanLevel.NORMAL:
            return None  # 正常执行

        task_type = self._get_task_type(task)

        # 音频熔断状态
        if ban_level == IPBanLevel.AUDIO_BANNED:
            if task_type == "transcript_only":
                # 字幕任务允许执行
                return None

            # 音频/混合任务，检查是否可以尝试
            allowed, reason = self.ip_ban_breaker.should_allow_attempt(task_type)

            if not allowed:
                # 不允许，延迟任务
                remaining = self.ip_ban_breaker.get_remaining_time()
                return ExecutionDecision(
                    action="delay",
                    reason=f"Audio ban active: {reason}",
                    delay_seconds=remaining,
                )

            # 允许尝试（作为被动探测）
            logger.info(
                f"Task {task.id} [{task_type}] allowed as recovery probe "
                f"after {self.ip_ban_breaker.get_time_since_ban() // 60} minutes"
            )
            return ExecutionDecision(
                action="execute",
                reason="Recovery probe allowed",
                is_probe=True,
            )

        # 全局熔断状态
        elif ban_level == IPBanLevel.FULLY_BANNED:
            # 全局熔断更严格
            min_wait = 3600 if task_type == "transcript_only" else 7200

            allowed, reason = self.ip_ban_breaker.should_allow_attempt(
                task_type, min_wait_override=min_wait
            )

            if not allowed:
                remaining = self.ip_ban_breaker.get_remaining_time()
                return ExecutionDecision(
                    action="delay",
                    reason=f"Full IP ban active: {reason}",
                    delay_seconds=max(remaining, min_wait),
                )

            # 允许尝试
            logger.info(
                f"Task {task.id} [{task_type}] allowed as recovery probe "
                f"during full ban"
            )
            return ExecutionDecision(
                action="execute",
                reason="Full ban recovery probe allowed",
                is_probe=True,
            )

        return None

    async def _analyze_result_and_update_ban(
        self,
        result: DownloaderResult,
        was_probe: bool,
        ban_level_before: IPBanLevel,
        audio_requested: bool = True,
        transcript_requested: bool = True,
    ) -> None:
        """
        分析下载结果，更新熔断状态（被动探测核心）。

        Args:
            result: 下载结果（探测任务本次真正请求资源后拿到的首次结果，
                见 _execute_download_with_manager 的 downloader_result 字段说明——
                必须是"本次原始请求"的结果，不能是被 audio_fallback 二次下载覆盖后
                的结果，否则会误判字幕探测状态）
            was_probe: 是否是探测尝试
            ban_level_before: 执行前的熔断级别
            audio_requested: 本次是否真正向下载器请求了音频。False 表示任务本就
                不需要音频，或音频已有缓存直接复用——这两种情况 result.audio_path
                恒为 None，不代表"探测到音频仍然失败"，必须跳过音频熔断分析，
                否则会把纯缓存命中误判为风控故障。
            transcript_requested: 本次是否真正向下载器请求了字幕，语义同上，
                用于全局熔断分析。
        """
        if ban_level_before == IPBanLevel.NORMAL or not was_probe:
            # 不是探测，或本来就是正常状态，无需分析
            return

        # 注意：audio_success 不存在和 transcript_success 对称的"成功但无资源"
        # 场景，不需要同样的修正。字幕是可选内容（视频可以没有原生字幕），三个
        # 下载器（ytdlp/tikhub/cdp）在字幕缺失时都会成功返回 has_transcript=
        # False 而不抛异常；音频则是必选内容，各下载器要么真正拿到 audio_path，
        # 要么直接抛 DownloaderError（例如 tikhub_downloader.py 找不到音频直链
        # 时的 raise），没有"请求成功但没有音频资源"的中间态，因此
        # audio_path is not None 已经是准确的探测成功判定，不需要改动。
        audio_success = result.audio_path is not None
        transcript_success = result.transcript_path is not None

        # 音频熔断期间的探测
        if ban_level_before == IPBanLevel.AUDIO_BANNED:
            if not audio_requested:
                # 本次任务没有真正向下载器请求音频（缓存复用或任务本不需要），
                # 无法据此判断音频熔断是否恢复，跳过分析（既不延长也不解除）。
                logger.debug(
                    "Probe task did not actually request audio this run "
                    "(cache reuse or not needed), skipping audio-ban analysis"
                )
                return

            if audio_success:
                # 音频恢复！
                logger.info("🎉 Audio recovery detected! Lifting audio ban.")
                await self.ip_ban_breaker.reset_to_normal()
                await self.notify_service.send_ip_recovery_notification(
                    "IP recovered: Audio downloads are now available"
                )

            elif result.partial_success and transcript_success:
                # 字幕成功但音频仍失败
                logger.info("Transcript OK but audio still banned, continuing audio ban")
                await self.ip_ban_breaker.extend_audio_ban()

            else:
                # 都失败，升级到全局熔断
                logger.error("Both failed during audio ban probe, upgrading to full ban")
                await self.ip_ban_breaker.upgrade_to_full_ban()

        # 全局熔断期间的探测
        elif ban_level_before == IPBanLevel.FULLY_BANNED:
            if not transcript_requested:
                # 本次任务没有真正向下载器请求字幕（缓存复用或任务本不需要），
                # 无法据此判断全局熔断是否恢复，跳过分析。
                #
                # 取舍说明（P2 修复顺带评估，2026-07-11）：如果这是一个纯音频
                # 探测任务（transcript_requested=False），即使 audio_success 为
                # True，这里也不会做任何降级——ip_ban_breaker 没有"FULLY_BANNED
                # 期间音频探测成功"对应的干净降级路径（现有分级语义里，
                # FULLY_BANNED 只能通过字幕探测成功降级到 AUDIO_BANNED，见
                # downgrade_to_audio_ban；音频和字幕是两条独立探测的资源，音频
                # 恢复不能反推字幕也已解封，硬造一个"音频探测成功直接降级"的
                # 路径会破坏这套语义）。保守起见维持现状：不处理，等待字幕探测
                # 覆盖这个视频。下面对 transcript_success 的判定修复后，字幕探测
                # 的成功率已经修正回真实值，这条恢复通路不再被"无字幕视频"误判
                # 堵死，因此没有为音频探测单独新增路径的必要。
                logger.debug(
                    "Probe task did not actually request transcript this run "
                    "(cache reuse or not needed), skipping full-ban analysis"
                )
                return

            # 探测"成功"不能只看 transcript_path 是否非空：视频本身没有原生
            # 字幕时，下载器会成功返回 transcript_path=None、has_transcript=
            # False（不抛异常，见 ytdlp_downloader.py 的
            # _download_simple/_download_with_partial_success 以及
            # tikhub_downloader.py 的同等分支）——这同样证明本次请求没有被
            # 封禁，是和"真正拿到字幕"同等有效的恢复信号。如果仍然只按
            # transcript_success 判定，无字幕视频（真实流量中很常见）每命中一次
            # 就会被误判为"探测仍失败"，把 banned_at 重置延长一小时，导致 IP
            # 早已恢复也走不出熔断（P2，独立 gate 审查复现）。
            transcript_probe_succeeded = transcript_success or result.has_transcript is False

            if transcript_probe_succeeded:
                # 字幕恢复，降级到音频熔断
                logger.info("📉 Transcript recovered, downgrading to audio ban")
                await self.ip_ban_breaker.downgrade_to_audio_ban()
                await self.notify_service.send_ip_recovery_notification(
                    "Partial recovery: Transcript downloads available, audio still restricted"
                )

            else:
                # 仍然失败
                logger.warning("Full ban probe failed, continuing full ban")
                await self.ip_ban_breaker.extend_full_ban()

    async def _trigger_ban_from_error(self, error: DownloaderError) -> None:
        """
        根据错误触发熔断。

        Args:
            error: 下载器错误
        """
        if error.http_status_code != 403:
            return

        operation = getattr(error, "operation", None)

        if operation == "audio" or operation == "mixed":
            # 音频相关的 403 → 音频熔断
            await self.ip_ban_breaker.trigger_audio_ban(
                reason=f"{error.downloader}: {error.message}"
            )
            await self.notify_service.send_ip_ban_notification(
                level="audio",
                reason=error.message,
            )

        elif operation == "transcript":
            # 字幕 403 → 全局熔断（更严重）
            await self.ip_ban_breaker.trigger_full_ban(
                reason=f"{error.downloader}: {error.message}"
            )
            await self.notify_service.send_ip_ban_notification(
                level="full",
                reason=error.message,
            )
        else:
            # 未知操作，保守处理 → 音频熔断
            await self.ip_ban_breaker.trigger_audio_ban(
                reason=f"{error.downloader}: {error.message}"
            )
