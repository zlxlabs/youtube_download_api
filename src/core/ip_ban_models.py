"""
IP 熔断相关数据模型。

定义 IP 熔断级别、执行决策等基础数据结构。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Optional


class IPBanLevel(Enum):
    """IP 封禁级别"""

    NORMAL = "normal"  # 正常状态，无限制
    AUDIO_BANNED = "audio_banned"  # 音频熔断，仅允许字幕任务
    FULLY_BANNED = "fully_banned"  # 全局熔断，所有任务暂停


@dataclass
class ExecutionDecision:
    """
    任务执行决策。

    用于 Worker 判断任务是否应该执行。
    """

    action: Literal["execute", "delay", "reject"]
    """执行动作：execute=执行, delay=延迟, reject=拒绝"""

    reason: str
    """决策原因（用于日志和用户提示）"""

    delay_seconds: int = 0
    """延迟时间（秒），仅当 action=delay 时有效"""

    is_probe: bool = False
    """是否是探测尝试（用于被动探测恢复）"""


@dataclass
class IPBanState:
    """
    IP 熔断状态快照。

    用于序列化存储和 API 返回。
    """

    current_level: IPBanLevel
    """当前熔断级别"""

    banned_at: Optional[datetime] = None
    """熔断开始时间"""

    last_attempt_at: Optional[datetime] = None
    """上次尝试时间（被动探测）"""

    failed_attempts: int = 0
    """熔断期间失败尝试次数"""

    time_since_ban: Optional[int] = None
    """距离熔断开始的秒数"""

    time_since_last_attempt: Optional[int] = None
    """距离上次尝试的秒数"""

    estimated_recovery_at: Optional[datetime] = None
    """预计恢复时间"""

    def to_dict(self) -> dict:
        """转换为字典（用于 API 返回）"""
        return {
            "current_level": self.current_level.value,
            "banned_at": self.banned_at.isoformat() if self.banned_at else None,
            "last_attempt_at": (
                self.last_attempt_at.isoformat() if self.last_attempt_at else None
            ),
            "failed_attempts": self.failed_attempts,
            "time_since_ban": self.time_since_ban,
            "time_since_last_attempt": self.time_since_last_attempt,
            "estimated_recovery_at": (
                self.estimated_recovery_at.isoformat()
                if self.estimated_recovery_at
                else None
            ),
        }


@dataclass
class IPBanStateChangeContext:
    """
    熔断器状态变更事件上下文。

    IPBanCircuitBreaker 在每次状态发生变更（触发/升级/降级/恢复/从持久化
    恢复/延长/记录失败尝试）时，通过 on_state_change 回调把该上下文传递给
    外部持久化层（见 src/core/worker.py 中的回调注入），由外部决定如何写库。
    breaker 本身不感知数据库，保持纯内存、可独立单测。
    """

    event_type: str
    """
    事件类型，取值：
    - triggered: 触发熔断（NORMAL -> AUDIO_BANNED/FULLY_BANNED）
    - upgraded: 音频熔断升级为全局熔断
    - downgraded: 全局熔断降级为音频熔断
    - recovered: 恢复正常状态
    - restored: 服务启动时从持久化存储恢复状态
    - extended: 熔断时间被延长（级别不变，仅 banned_at 推后）
    - failed_attempt: 记录一次失败的探测尝试
    """

    old_level: IPBanLevel
    """变更前的熔断级别"""

    new_level: IPBanLevel
    """变更后的熔断级别"""

    old_state: IPBanState
    """变更前的完整状态快照（用于计算持续时长等，例如 recovered 事件里的原始 banned_at）"""

    new_state: IPBanState
    """变更后的完整状态快照"""

    reason: Optional[str] = None
    """触发原因（可选，通常来自触发熔断时传入的 reason 参数）"""
