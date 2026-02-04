"""
熔断器实现。

基于 Netflix Hystrix 和 Resilience4j 的熔断器模式，
用于保护下载器免受持续性故障的影响。
"""

from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional, TypeVar

from src.utils.logger import logger

T = TypeVar("T")


class CircuitState(str, Enum):
    """熔断器状态枚举。"""

    CLOSED = "closed"  # 正常工作状态
    OPEN = "open"  # 熔断开启，拒绝请求
    HALF_OPEN = "half_open"  # 半开状态，尝试恢复


class CircuitBreakerOpen(Exception):
    """
    熔断器开启异常。

    当熔断器处于 OPEN 状态时抛出，指示应该跳过当前下载器。
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class CircuitBreaker:
    """
    熔断器实现。

    工作原理：
    1. CLOSED（正常）：允许所有请求，统计失败次数
    2. 失败次数达到阈值 -> OPEN（熔断）
    3. OPEN（熔断）：拒绝所有请求，等待超时
    4. 超时后 -> HALF_OPEN（半开）
    5. HALF_OPEN（半开）：允许少量请求测试
       - 成功 -> CLOSED（恢复）
       - 失败 -> OPEN（重新熔断）
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        timeout: int = 1800,  # 30 分钟
        half_open_max_calls: int = 3,
        success_threshold: int = 2,  # 半开状态需要连续成功 2 次才恢复
    ):
        """
        初始化熔断器。

        Args:
            name: 熔断器名称（用于日志）
            failure_threshold: 连续失败阈值（达到后触发熔断）
            timeout: 熔断超时时间（秒）
            half_open_max_calls: 半开状态最大允许调用次数
            success_threshold: 半开状态恢复所需的连续成功次数
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold

        # 状态
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.half_open_calls = 0

        logger.debug(
            f"[CircuitBreaker:{name}] Initialized: "
            f"threshold={failure_threshold}, timeout={timeout}s"
        )

    def call(self, func: Callable[[], T]) -> T:
        """
        通过熔断器调用函数。

        Args:
            func: 要调用的函数（无参数）

        Returns:
            函数返回值

        Raises:
            CircuitBreakerOpen: 熔断器开启时抛出
            Exception: 函数执行失败时抛出原始异常
        """
        # 1. 检查是否应该尝试恢复
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self._transition_to_half_open()
            else:
                raise CircuitBreakerOpen(
                    f"Circuit breaker is OPEN, remaining timeout: {self._remaining_timeout()}s"
                )

        # 2. 半开状态：检查调用次数限制
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerOpen(
                    "Circuit breaker is HALF_OPEN and max calls reached"
                )
            self.half_open_calls += 1

        # 3. 执行调用
        try:
            result = func()
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    async def call_async(self, func: Callable[[], T]) -> T:
        """
        通过熔断器调用异步函数。

        Args:
            func: 要调用的异步函数（无参数）

        Returns:
            函数返回值

        Raises:
            CircuitBreakerOpen: 熔断器开启时抛出
            Exception: 函数执行失败时抛出原始异常
        """
        # 1. 检查是否应该尝试恢复
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self._transition_to_half_open()
            else:
                raise CircuitBreakerOpen(
                    f"Circuit breaker is OPEN, remaining timeout: {self._remaining_timeout()}s"
                )

        # 2. 半开状态：检查调用次数限制
        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.half_open_max_calls:
                raise CircuitBreakerOpen(
                    "Circuit breaker is HALF_OPEN and max calls reached"
                )
            self.half_open_calls += 1

        # 3. 执行调用
        try:
            result = await func()
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        """处理调用成功。"""
        self.failure_count = 0

        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            logger.debug(
                f"[CircuitBreaker:{self.name}] Half-open success count: "
                f"{self.success_count}/{self.success_threshold}"
            )

            # 半开状态连续成功足够次数 -> 关闭熔断器
            if self.success_count >= self.success_threshold:
                self._transition_to_closed()

    def _on_failure(self) -> None:
        """处理调用失败。"""
        self.failure_count += 1
        self.last_failure_time = datetime.now()

        if self.state == CircuitState.HALF_OPEN:
            # 半开状态失败 -> 重新打开熔断器
            logger.warning(
                f"[CircuitBreaker:{self.name}] Half-open test failed, reopening circuit"
            )
            self._transition_to_open()

        elif self.state == CircuitState.CLOSED:
            # 检查是否达到失败阈值
            if self.failure_count >= self.failure_threshold:
                logger.warning(
                    f"[CircuitBreaker:{self.name}] Failure threshold reached "
                    f"({self.failure_count}/{self.failure_threshold}), opening circuit"
                )
                self._transition_to_open()
            else:
                logger.debug(
                    f"[CircuitBreaker:{self.name}] Failure count: "
                    f"{self.failure_count}/{self.failure_threshold}"
                )

    def force_open(self, reason: str = "") -> None:
        """
        强制打开熔断器（用于全局性错误）。

        对于全局性错误（如 nsig 失败），不需要等待失败计数达到阈值，
        直接打开熔断器，避免后续请求继续尝试必然失败的下载器。

        Args:
            reason: 强制打开的原因（用于日志）
        """
        if self.state == CircuitState.OPEN:
            # 已经打开，刷新超时时间
            self.last_failure_time = datetime.now()
            logger.debug(
                f"[CircuitBreaker:{self.name}] Already OPEN, refreshed timeout"
            )
            return

        self.last_failure_time = datetime.now()
        self._transition_to_open()
        logger.warning(
            f"[CircuitBreaker:{self.name}] Force opened: {reason or 'global error detected'}"
        )

    def _transition_to_open(self) -> None:
        """转换到 OPEN 状态。"""
        self.state = CircuitState.OPEN
        self.success_count = 0
        logger.warning(
            f"[CircuitBreaker:{self.name}] State: CLOSED -> OPEN "
            f"(timeout: {self.timeout}s)"
        )

    def _transition_to_half_open(self) -> None:
        """转换到 HALF_OPEN 状态。"""
        self.state = CircuitState.HALF_OPEN
        self.half_open_calls = 0
        self.success_count = 0
        logger.info(
            f"[CircuitBreaker:{self.name}] State: OPEN -> HALF_OPEN "
            f"(max calls: {self.half_open_max_calls})"
        )

    def _transition_to_closed(self) -> None:
        """转换到 CLOSED 状态。"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        logger.info(f"[CircuitBreaker:{self.name}] State: HALF_OPEN -> CLOSED (recovered)")

    def _should_attempt_reset(self) -> bool:
        """检查是否应该尝试恢复（从 OPEN 到 HALF_OPEN）。"""
        if not self.last_failure_time:
            return False

        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.timeout

    def _remaining_timeout(self) -> int:
        """计算剩余熔断时间（秒）。"""
        if not self.last_failure_time:
            return 0

        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return max(0, int(self.timeout - elapsed))

    @property
    def is_closed(self) -> bool:
        """检查熔断器是否关闭（正常工作）。"""
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """检查熔断器是否开启（拒绝请求）。"""
        return self.state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        """检查熔断器是否半开（测试恢复）。"""
        return self.state == CircuitState.HALF_OPEN

    def get_state_summary(self) -> dict:
        """
        获取熔断器状态摘要。

        Returns:
            包含状态信息的字典
        """
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "remaining_timeout": self._remaining_timeout() if self.is_open else None,
        }
