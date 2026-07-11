"""
键控互斥锁管理器（KeyedLockManager）。

按任意 key（如 video_id）提供进程内 asyncio.Lock，用于把"同一个 key 的多步
临界区操作"串行化。典型用法::

    keyed_locks = KeyedLockManager()

    async def handle(video_id: str) -> None:
        async with keyed_locks.acquire(video_id):
            ...  # 临界区，同一 video_id 的并发调用会在此排队

生命周期管理：使用引用计数而非"超过阈值后按 locked() 判断清理"。

背景（Codex 第10轮问题2）：旧实现在锁字典超过上限时，用 `lock.locked()` 判断
是否可以清理——但 asyncio.Lock 在"持有者 release() 之后、排队中的 waiter 尚未
被事件循环真正唤醒"这段窗口期里，locked() 已经返回 False 了（release() 只是
唤醒 waiter 的 future，真正把 _locked 置回 True 要等 waiter 自己的协程恢复执行）。
这个窗口里如果清理了字典条目，后来者会创建一把全新的 Lock 并立即拿到手
（因为新锁是空闲的），与仍在旧锁队列里排队的 waiter 并发穿过临界区，锁就失去
了互斥意义。

引用计数从根本上避免了这个问题：进入 acquire() 时先登记引用计数（在真正
`await lock.acquire()` 之前完成），只要还有协程持有或排队等待某个 key 的锁，
它的引用计数就大于 0，字典条目就不会被删除；只有当最后一个使用者退出临界区、
计数归零时才删除。因此不再需要任何"超过阈值批量清理"的兜底逻辑——字典大小
天然有界于"当前正在被使用的 key 数量"。
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict

from src.utils.logger import logger


@dataclass
class _RefCountedLock:
    """单个 key 对应的锁及其当前使用者数量（持有者 + 排队等待者）。"""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ref_count: int = 0


class KeyedLockManager:
    """
    按 key 提供互斥锁的管理器，基于引用计数自动回收不再使用的条目。

    非线程安全，仅设计用于单个事件循环内的协程间协调（与 asyncio.Lock 本身
    的适用范围一致）。
    """

    def __init__(self) -> None:
        self._locks: Dict[str, _RefCountedLock] = {}

    def __len__(self) -> int:
        """当前字典中的条目数（等价于正被使用中的 key 数量）。"""
        return len(self._locks)

    def __contains__(self, key: str) -> bool:
        return key in self._locks

    def ref_count(self, key: str) -> int:
        """
        查询某个 key 当前的引用计数（持有者 + 排队等待者之和）。

        仅用于测试/调试观测内部状态，业务代码不应依赖这个数字做决策。
        key 不存在时返回 0。
        """
        entry = self._locks.get(key)
        return entry.ref_count if entry is not None else 0

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[None]:
        """
        获取指定 key 的互斥锁，返回一个异步上下文管理器。

        引用计数在真正 await 锁之前就完成登记，保证"排队等待锁"这段时间内，
        该 key 对应的字典条目不会被并发的清理逻辑误删（因为不再有这类清理逻辑，
        但登记顺序本身也保证了 key 在整个排队+持有期间全程可见、可复用）。

        异常安全：无论是等待锁时被取消（CancelledError），还是临界区内部抛出
        任何异常，引用计数都会在 finally 块中正确回退，不会泄漏。
        """
        entry = self._locks.get(key)
        if entry is None:
            entry = _RefCountedLock()
            self._locks[key] = entry
        entry.ref_count += 1
        try:
            await entry.lock.acquire()
            try:
                yield
            finally:
                entry.lock.release()
        finally:
            entry.ref_count -= 1
            if entry.ref_count == 0:
                # 只有当字典里当前这个 key 对应的仍然是同一个 entry 时才删除，
                # 防御性检查：正常流程下不会出现不一致，但避免任何意外情况下
                # 误删了别的协程刚刚创建的新条目。
                if self._locks.get(key) is entry:
                    del self._locks[key]
                    logger.debug(f"KeyedLockManager: released and dropped entry for key={key!r}")
