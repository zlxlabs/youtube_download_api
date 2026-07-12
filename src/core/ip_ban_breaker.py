"""
被动探测型 IP 熔断器。

实现分级 IP 熔断机制，通过用户任务被动探测恢复，避免主动探测请求。
"""

from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from src.core.ip_ban_models import IPBanLevel, IPBanState, IPBanStateChangeContext
from src.utils.logger import logger

# 状态变更回调类型：接收一次状态变更的完整上下文，异步执行（通常用于持久化到数据库）。
OnStateChangeCallback = Callable[[IPBanStateChangeContext], Awaitable[None]]


def calculate_ban_recovery_time(
    banned_at: datetime,
    last_attempt_at: Optional[datetime],
    min_wait_before_retry: int,
    max_retry_interval: int,
) -> datetime:
    """
    计算预计恢复时间（允许下一次尝试的最早时间）。

    独立于 IPBanCircuitBreaker 实例的纯函数，供服务启动时的持久化恢复流程
    在决定是否需要 restore_state 之前进行只读判定，避免与实例方法
    get_estimated_recovery_time 重复实现同一套公式（保持单一数据源）。

    Args:
        banned_at: 熔断触发时间
        last_attempt_at: 最近一次尝试时间（可选）
        min_wait_before_retry: 最小等待时间（秒）
        max_retry_interval: 重试间隔（秒）

    Returns:
        预计恢复时间（可以尝试的最早时间）
    """
    earliest = banned_at + timedelta(seconds=min_wait_before_retry)

    if last_attempt_at:
        retry_after = last_attempt_at + timedelta(seconds=max_retry_interval)
        earliest = max(earliest, retry_after)

    return earliest


class IPBanCircuitBreaker:
    """
    被动探测型 IP 熔断器。

    核心特性：
    1. 分级熔断：NORMAL → AUDIO_BANNED → FULLY_BANNED
    2. 被动探测：不主动发起测试请求，利用实际任务探测恢复
    3. 自动升降级：根据任务执行结果自动调整熔断级别
    4. 智能延迟：根据等待时间判断是否允许尝试

    配置参数：
    - MIN_WAIT_BEFORE_RETRY: 最小等待时间（秒），触发熔断后必须等待这么久才允许重试
    - MAX_RETRY_INTERVAL: 重试间隔（秒），失败后至少等待这么久才允许下次尝试
    """

    def __init__(
        self,
        min_wait_before_retry: int = 3600,  # 60 分钟
        max_retry_interval: int = 1800,  # 30 分钟
        on_state_change: Optional[OnStateChangeCallback] = None,
    ):
        """
        初始化 IP 熔断器。

        Args:
            min_wait_before_retry: 最小等待时间（秒），默认 3600（60 分钟）
            max_retry_interval: 重试间隔（秒），默认 1800（30 分钟）
            on_state_change: 状态变更回调（可选）。每次状态发生变更（触发/升级/
                降级/恢复/从持久化恢复等）时异步调用，用于外部持久化到数据库。
                回调执行失败只记录 error 日志，不会向上抛出、不影响熔断器本身
                的运行（fail-open）。熔断器本身不直接依赖数据库，保持纯内存、
                可独立单测。
        """
        # 状态
        self.current_level: IPBanLevel = IPBanLevel.NORMAL
        self.banned_at: Optional[datetime] = None
        self.last_attempt_at: Optional[datetime] = None
        self.failed_attempts: int = 0

        # 配置
        self.MIN_WAIT_BEFORE_RETRY = min_wait_before_retry
        self.MAX_RETRY_INTERVAL = max_retry_interval

        # 状态变更回调（可选，用于持久化）
        self.on_state_change = on_state_change

        logger.info(
            f"IPBanCircuitBreaker initialized: "
            f"min_wait={min_wait_before_retry}s, "
            f"retry_interval={max_retry_interval}s"
        )

    async def _emit_state_change(
        self, event_type: str, old_state: IPBanState, reason: Optional[str] = None
    ) -> None:
        """
        触发状态变更回调（fail-open：回调异常只记录日志，绝不影响熔断器本身状态）。

        Args:
            event_type: 事件类型（详见 IPBanStateChangeContext.event_type 说明）
            old_state: 变更前的完整状态快照（在调用方完成状态变更之前捕获）
            reason: 触发原因（可选，用于日志与历史记录）
        """
        if self.on_state_change is None:
            return

        try:
            context = IPBanStateChangeContext(
                event_type=event_type,
                old_level=old_state.current_level,
                new_level=self.current_level,
                old_state=old_state,
                new_state=self.get_state(),
                reason=reason,
            )
            await self.on_state_change(context)
        except Exception as e:
            logger.error(
                f"IP ban on_state_change callback failed (event={event_type}): {e}"
            )

    def get_current_level(self) -> IPBanLevel:
        """
        获取当前熔断级别。

        Returns:
            当前熔断级别
        """
        return self.current_level

    def get_time_since_ban(self) -> int:
        """
        获取距离熔断开始的时间（秒）。

        Returns:
            距离熔断开始的秒数，如果未熔断返回 0
        """
        if not self.banned_at:
            return 0
        return int((datetime.now() - self.banned_at).total_seconds())

    def get_time_since_last_attempt(self) -> Optional[int]:
        """
        获取距离上次尝试的时间（秒）。

        Returns:
            距离上次尝试的秒数，如果没有尝试过返回 None
        """
        if not self.last_attempt_at:
            return None
        return int((datetime.now() - self.last_attempt_at).total_seconds())

    def get_estimated_recovery_time(
        self, min_wait_override: Optional[int] = None
    ) -> Optional[datetime]:
        """
        获取预计恢复时间。

        Args:
            min_wait_override: 最小等待时间覆盖（秒），None 则使用熔断器默认的
                MIN_WAIT_BEFORE_RETRY。与 should_allow_attempt() 的同名参数
                保持同一套语义——调用方（如 worker 的全局熔断分支，音频/混合
                任务需要比默认值更长的等待时间）需要用同一个 min_wait 计算
                "是否允许尝试"和"还要等多久"，两者口径必须一致，否则会出现
                剩余时间与放行判定互相矛盾。

        Returns:
            预计恢复时间（可以尝试的最早时间），如果未熔断返回 None
        """
        if self.current_level == IPBanLevel.NORMAL:
            return None

        if not self.banned_at:
            return None

        min_wait = (
            min_wait_override if min_wait_override is not None else self.MIN_WAIT_BEFORE_RETRY
        )

        # 复用模块级纯函数，避免与 calculate_ban_recovery_time 重复实现同一套公式
        return calculate_ban_recovery_time(
            self.banned_at,
            self.last_attempt_at,
            min_wait,
            self.MAX_RETRY_INTERVAL,
        )

    def get_remaining_time(self, min_wait_override: Optional[int] = None) -> int:
        """
        获取距离可以尝试的剩余时间（秒）。

        Args:
            min_wait_override: 最小等待时间覆盖（秒），透传给
                get_estimated_recovery_time()。调用方在用 min_wait_override
                调用 should_allow_attempt() 做放行判定时，必须用同一个值调用
                这里，否则剩余时间会按熔断器默认的 MIN_WAIT_BEFORE_RETRY 计算，
                与真正决定是否放行的 min_wait 不一致。

        Returns:
            剩余等待秒数，0 表示可以立即尝试
        """
        recovery_time = self.get_estimated_recovery_time(min_wait_override)
        if not recovery_time:
            return 0

        remaining = (recovery_time - datetime.now()).total_seconds()
        return max(0, int(remaining))

    def get_state(self) -> IPBanState:
        """
        获取当前状态快照。

        Returns:
            IPBanState 状态快照
        """
        return IPBanState(
            current_level=self.current_level,
            banned_at=self.banned_at,
            last_attempt_at=self.last_attempt_at,
            failed_attempts=self.failed_attempts,
            time_since_ban=self.get_time_since_ban(),
            time_since_last_attempt=self.get_time_since_last_attempt(),
            estimated_recovery_at=self.get_estimated_recovery_time(),
        )

    async def trigger_audio_ban(self, reason: Optional[str] = None) -> None:
        """
        触发音频熔断。

        当音频下载遇到 403 错误时调用。
        只禁止音频/混合任务，字幕任务仍可执行。

        Args:
            reason: 触发原因（可选，用于日志）
        """
        old_state = self.get_state()
        self.current_level = IPBanLevel.AUDIO_BANNED
        self.banned_at = datetime.now()
        self.failed_attempts = 0

        reason_msg = f" - {reason}" if reason else ""
        logger.warning(f"🔒 Audio ban activated{reason_msg}")
        logger.info(
            f"Audio/mixed tasks suspended. Transcript-only tasks still available. "
            f"Will allow retry probe after {self.MIN_WAIT_BEFORE_RETRY // 60} minutes."
        )

        await self._emit_state_change("triggered", old_state, reason=reason)

    async def trigger_full_ban(self, reason: Optional[str] = None) -> None:
        """
        触发全局熔断。

        当字幕也遇到 403 错误时调用。
        禁止所有任务执行。

        Args:
            reason: 触发原因（可选，用于日志）
        """
        old_state = self.get_state()
        self.current_level = IPBanLevel.FULLY_BANNED
        self.banned_at = datetime.now()
        self.failed_attempts = 0

        reason_msg = f" - {reason}" if reason else ""
        logger.error(f"🔒🔒 Full IP ban activated{reason_msg}")
        logger.info(
            f"All tasks suspended. "
            f"Will allow retry probe after {self.MIN_WAIT_BEFORE_RETRY // 60} minutes."
        )

        await self._emit_state_change("triggered", old_state, reason=reason)

    async def upgrade_to_full_ban(self) -> None:
        """
        从音频熔断升级到全局熔断。

        当音频熔断期间字幕也失败时调用。

        注意：不直接调用 trigger_full_ban，而是内联实现相同的状态变更，
        以便产生语义正确的 "upgraded" 事件（而不是 "triggered"），
        供持久化层区分历史事件类型。
        """
        old_state = self.get_state()
        reason = "Transcript also blocked"
        self.current_level = IPBanLevel.FULLY_BANNED
        self.banned_at = datetime.now()
        self.failed_attempts = 0

        logger.error(f"⬆️ Upgrading from audio ban to full ban - {reason}")
        logger.info(
            f"All tasks suspended. "
            f"Will allow retry probe after {self.MIN_WAIT_BEFORE_RETRY // 60} minutes."
        )

        await self._emit_state_change("upgraded", old_state, reason=reason)

    async def downgrade_to_audio_ban(self) -> None:
        """
        从全局熔断降级到音频熔断。

        当全局熔断期间字幕恢复时调用。
        """
        old_state = self.get_state()
        self.current_level = IPBanLevel.AUDIO_BANNED
        self.banned_at = datetime.now()  # 重置时间
        self.failed_attempts = 0

        logger.info("⬇️ Downgraded from full ban to audio ban")
        logger.info("Transcript downloads available again, audio still restricted")

        await self._emit_state_change("downgraded", old_state)

    async def reset_to_normal(self) -> None:
        """
        恢复正常状态。

        当探测成功时调用。
        """
        old_state = self.get_state()
        old_level = old_state.current_level
        self.current_level = IPBanLevel.NORMAL
        self.banned_at = None
        self.last_attempt_at = None
        self.failed_attempts = 0

        if old_level != IPBanLevel.NORMAL:
            logger.info(f"✅ IP ban lifted - recovered from {old_level.value}")
            logger.info("All services restored to normal operation")
            await self._emit_state_change("recovered", old_state)

    async def restore_state(
        self,
        level: IPBanLevel,
        banned_at: Optional[datetime],
        last_attempt_at: Optional[datetime],
        failed_attempts: int,
    ) -> None:
        """
        从持久化存储恢复熔断器状态（服务重启后调用）。

        背景：熔断器状态完全在内存，容器重启（如 D3 自动部署每次 push 触发的
        重启）会丢失熔断状态；如果重启时正处于熔断中，服务醒来会误判为
        NORMAL 全速请求 YouTube，加重封禁。本方法用于在启动阶段把之前持久化
        的状态写回内存。

        与 trigger_*/downgrade_to_audio_ban 等方法不同，本方法直接写入传入的
        历史时间戳，不会把 banned_at 重置为当前时间，确保重启前后的等待时间
        计算语义保持一致（不会因为重启而"免费"重置等待窗口）。

        Args:
            level: 待恢复的熔断级别
            banned_at: 熔断触发时间（来自持久化记录）
            last_attempt_at: 最近一次尝试时间（来自持久化记录，可能为 None）
            failed_attempts: 熔断期间失败尝试次数（来自持久化记录）
        """
        old_state = self.get_state()
        self.current_level = level
        self.banned_at = banned_at
        self.last_attempt_at = last_attempt_at
        self.failed_attempts = failed_attempts

        logger.info(
            f"IP ban state restored from persistence: {level.value} "
            f"(banned_at={banned_at}, last_attempt_at={last_attempt_at}, "
            f"failed_attempts={failed_attempts})"
        )

        await self._emit_state_change("restored", old_state)

    async def record_failed_attempt(self) -> None:
        """
        记录失败尝试。

        在探测失败时调用，用于统计和避免过于频繁的重试。
        """
        old_state = self.get_state()
        self.last_attempt_at = datetime.now()
        self.failed_attempts += 1

        logger.debug(
            f"Failed probe attempt recorded "
            f"(total: {self.failed_attempts} since ban started)"
        )

        await self._emit_state_change("failed_attempt", old_state)

    async def extend_ban(self, additional_duration: int) -> None:
        """
        延长当前熔断时间。

        通过重置 banned_at 来实现延长效果。

        Args:
            additional_duration: 额外延长的秒数
        """
        if self.current_level == IPBanLevel.NORMAL:
            return

        old_state = self.get_state()
        # 简单实现：重置开始时间（相当于延长）
        self.banned_at = datetime.now()

        logger.info(
            f"Extending {self.current_level.value} by "
            f"{additional_duration // 60} minutes"
        )

        await self._emit_state_change("extended", old_state)

    async def extend_audio_ban(self, additional_duration: int = 1800) -> None:
        """
        延长音频熔断。

        Args:
            additional_duration: 额外延长的秒数，默认 1800（30 分钟）
        """
        if self.current_level != IPBanLevel.AUDIO_BANNED:
            return

        await self.extend_ban(additional_duration)

    async def extend_full_ban(self, additional_duration: int = 3600) -> None:
        """
        延长全局熔断。

        Args:
            additional_duration: 额外延长的秒数，默认 3600（60 分钟）
        """
        if self.current_level != IPBanLevel.FULLY_BANNED:
            return

        await self.extend_ban(additional_duration)

    def should_allow_attempt(
        self, task_type: str, min_wait_override: Optional[int] = None
    ) -> tuple[bool, str]:
        """
        判断是否应该允许尝试执行任务（辅助方法）。

        Args:
            task_type: 任务类型（"transcript_only", "audio", "mixed"）
            min_wait_override: 最小等待时间覆盖（秒），None 则使用默认值

        Returns:
            (是否允许, 原因说明) 元组
        """
        if self.current_level == IPBanLevel.NORMAL:
            return True, "No ban active"

        # 使用覆盖值或默认值
        min_wait = min_wait_override or self.MIN_WAIT_BEFORE_RETRY

        # 检查最小等待时间
        time_since_ban = self.get_time_since_ban()
        if time_since_ban < min_wait:
            remaining = min_wait - time_since_ban
            return (
                False,
                f"Minimum wait not reached: {remaining // 60} minutes remaining",
            )

        # 检查重试间隔
        time_since_last = self.get_time_since_last_attempt()
        if time_since_last is not None and time_since_last < self.MAX_RETRY_INTERVAL:
            remaining = self.MAX_RETRY_INTERVAL - time_since_last
            return (
                False,
                f"Too soon after last attempt: {remaining // 60} minutes remaining",
            )

        return True, f"Allowed after {time_since_ban // 60} minutes of ban"
