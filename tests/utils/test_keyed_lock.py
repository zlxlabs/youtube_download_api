"""
KeyedLockManager 单元测试。

Codex 第10轮问题2(P2)：TaskService 原先按 "超过 256 上限时按 locked() 判断清理"
的策略维护 _video_locks，但 asyncio.Lock 在"持有者 release 后、排队 waiter 尚未
被事件循环恢复"的窗口里 locked() 返回 False——此时清理会把还有 waiter 在排队的
锁从 dict 里删掉，后来者拿到一把全新的锁，与旧锁上的 waiter 并发穿过临界区，
锁失去意义。

KeyedLockManager 改用引用计数管理生命周期：进入临界区前登记（计数 +1），退出后
计数 -1，减到 0 才真正从字典删除该 key 对应的锁——从根本上保证只要还有人在排队
或持有锁，条目就不会被误删，因此也不再需要任何"超过阈值清理"的兜底逻辑。
"""

import asyncio

import pytest

from src.utils.keyed_lock import KeyedLockManager


class TestBasicMutualExclusion:
    """基本互斥语义：同一个 key 的临界区应该被串行化。"""

    @pytest.mark.asyncio
    async def test_concurrent_same_key_serialized(self) -> None:
        """N 个并发协程用同一个 key 争抢锁：临界区内任意时刻只能有一个协程在执行。"""
        manager = KeyedLockManager()
        in_critical_section = 0
        max_concurrent = 0
        order: list[int] = []

        async def worker(i: int) -> None:
            nonlocal in_critical_section, max_concurrent
            async with manager.acquire("same-key"):
                in_critical_section += 1
                max_concurrent = max(max_concurrent, in_critical_section)
                await asyncio.sleep(0.01)
                order.append(i)
                in_critical_section -= 1

        await asyncio.gather(*(worker(i) for i in range(20)))

        assert max_concurrent == 1
        assert len(order) == 20

    @pytest.mark.asyncio
    async def test_different_keys_do_not_block_each_other(self) -> None:
        """不同 key 的锁互相独立，不应互相阻塞。"""
        manager = KeyedLockManager()

        async with manager.acquire("key-a"):
            # 持有 key-a 的锁期间，key-b 应该能立即获取，不被阻塞
            async with asyncio.timeout(0.1):
                async with manager.acquire("key-b"):
                    pass


class TestNoLeakAfterCompletion:
    """临界区全部完成后，字典条目应被清理，不留下引用计数为 0 的僵尸条目。"""

    @pytest.mark.asyncio
    async def test_dict_empty_after_sequential_use(self) -> None:
        manager = KeyedLockManager()
        for i in range(50):
            async with manager.acquire(f"video-{i}"):
                pass
        assert len(manager) == 0

    @pytest.mark.asyncio
    async def test_dict_empty_after_concurrent_use(self) -> None:
        manager = KeyedLockManager()

        async def worker(i: int) -> None:
            async with manager.acquire(f"video-{i % 5}"):
                await asyncio.sleep(0.005)

        await asyncio.gather(*(worker(i) for i in range(30)))
        assert len(manager) == 0


class TestWaiterSurvivesPruningWindow:
    """
    复现 Codex 第10轮问题2 描述的竞态本质：旧实现按"超过阈值时 lock.locked()==False
    就清理"的策略，会在"A release() 之后、排队中的 B 尚未被事件循环真正唤醒"这个
    瞬时窗口里把仍有 waiter 排队的条目误删，导致后来者 C 创建一把全新的锁、绕开
    B 的排队直接执行，锁失去互斥意义。

    KeyedLockManager 已经不存在"外部按阈值定期清理"这个机制——删除条目只发生在
    "最后一个使用者的 acquire() 正常退出、引用计数回落到 0"这一原子操作内。因此
    这里改为直接验证由此保证的不变式：只要还有 waiter 在排队（引用计数 > 0），
    条目和其中的锁对象就必须保持不变、可被后来者安全复用；A 释放后，B、C 严格按
    FIFO 顺序串行拿到锁；全部完成后条目清理干净，不留僵尸引用计数。
    """

    @pytest.mark.asyncio
    async def test_entry_and_lock_identity_preserved_across_queued_waiters(self) -> None:
        manager = KeyedLockManager()
        video_id = "racey-video-01"

        # A 持有锁
        cm_a = manager.acquire(video_id)
        await cm_a.__aenter__()
        assert video_id in manager
        assert manager.ref_count(video_id) == 1
        lock_ref = manager._locks[video_id].lock

        entered_order: list[str] = []

        async def waiter(name: str) -> None:
            async with manager.acquire(video_id):
                entered_order.append(name)

        # B 登记并开始排队等待（A 仍持有锁，B 必然阻塞在 lock.acquire()）
        task_b = asyncio.create_task(waiter("B"))
        await asyncio.sleep(0)
        assert entered_order == []  # B 尚未进入临界区
        assert manager.ref_count(video_id) == 2  # A + B
        # 条目未被替换：B 排队等待用的是与 A 相同的锁对象
        assert manager._locks[video_id].lock is lock_ref

        # C 也在 A 释放之前进来排队
        task_c = asyncio.create_task(waiter("C"))
        await asyncio.sleep(0)
        assert entered_order == []
        assert manager.ref_count(video_id) == 3  # A + B + C
        # 条目依然是同一个，C 排在 B 之后，而不是拿到一把新锁抢先执行
        assert manager._locks[video_id].lock is lock_ref

        # A 释放，B、C 依次被唤醒
        await cm_a.__aexit__(None, None, None)
        await asyncio.wait_for(task_b, timeout=1)
        await asyncio.wait_for(task_c, timeout=1)

        # 严格 FIFO 串行：B 在 C 之前进入临界区，两者不会并发穿过临界区
        assert entered_order == ["B", "C"]

        # 全部完成后条目清理干净，不留下引用计数为 0 的僵尸条目
        assert video_id not in manager
        assert len(manager) == 0


class TestCancellationSafety:
    """等待锁期间被取消时，引用计数必须正确回退，不能泄漏条目。"""

    @pytest.mark.asyncio
    async def test_cancelled_while_waiting_refcount_rolls_back(self) -> None:
        manager = KeyedLockManager()
        video_id = "cancel-me"

        cm_holder = manager.acquire(video_id)
        await cm_holder.__aenter__()

        async def waiter() -> None:
            async with manager.acquire(video_id):
                pass  # pragma: no cover - 应该在拿到锁之前被取消

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert manager.ref_count(video_id) == 2

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # 取消后引用计数应该回退，只剩持有者自己那一份
        assert manager.ref_count(video_id) == 1

        await cm_holder.__aexit__(None, None, None)
        assert video_id not in manager
        assert len(manager) == 0
