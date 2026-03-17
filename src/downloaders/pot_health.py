"""
PO Token Provider 健康状态追踪器。

追踪 pot-provider 服务的可用性，防止在 pot-provider 不可用时
将 bgutil 配置注入 yt-dlp（否则 yt-dlp 的 bgutil 插件会无限重试）。

使用模块级单例，在 CDPDownloader 和 YtdlpDownloader 之间共享状态。
"""

import threading
import time
from typing import Optional

from src.utils.logger import logger


class PotProviderHealthTracker:
    """
    PO Token Provider 健康状态追踪器。

    当 pot-provider 连续失败达到阈值时，标记为不可用。
    经过冷却期后允许重新探测。

    线程安全：所有状态修改通过锁保护。
    """

    _instance: Optional["PotProviderHealthTracker"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        failure_threshold: int = 2,
        cooldown_seconds: int = 300,
    ):
        """
        初始化健康追踪器。

        Args:
            failure_threshold: 连续失败次数阈值，达到后标记不可用
            cooldown_seconds: 不可用后的冷却时间（秒），过后允许重新探测
        """
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._last_failure_time: Optional[float] = None
        self._state_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "PotProviderHealthTracker":
        """获取全局单例实例。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）。"""
        with cls._lock:
            cls._instance = None

    def record_success(self) -> None:
        """记录一次成功请求，重置失败计数。"""
        with self._state_lock:
            if self._consecutive_failures > 0:
                logger.info(
                    f"[pot-health] Provider recovered after {self._consecutive_failures} failures"
                )
            self._consecutive_failures = 0
            self._last_failure_time = None

    def record_failure(self) -> None:
        """记录一次失败请求。达到阈值后标记为不可用。"""
        with self._state_lock:
            prev_failures = self._consecutive_failures
            self._consecutive_failures += 1
            self._last_failure_time = time.monotonic()

            if prev_failures < self._failure_threshold <= self._consecutive_failures:
                logger.warning(
                    f"[pot-health] Provider marked unavailable "
                    f"(consecutive failures: {self._consecutive_failures}, "
                    f"cooldown: {self._cooldown_seconds}s)"
                )

    def is_available(self) -> bool:
        """
        检查 pot-provider 是否可用。

        Returns:
            True 表示可用（或冷却期已过，允许探测）
        """
        with self._state_lock:
            if self._consecutive_failures < self._failure_threshold:
                return True

            # 检查冷却期是否已过
            if self._last_failure_time is not None:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self._cooldown_seconds:
                    logger.info(
                        f"[pot-health] Cooldown expired ({elapsed:.0f}s >= "
                        f"{self._cooldown_seconds}s), allowing probe"
                    )
                    return True

            return False

    @property
    def consecutive_failures(self) -> int:
        """当前连续失败次数。"""
        return self._consecutive_failures
