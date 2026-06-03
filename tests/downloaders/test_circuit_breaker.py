"""
熔断器 (CircuitBreaker) 单元测试。

覆盖完整状态机 (CLOSED/OPEN/HALF_OPEN) 转换，以及关键回归用例：
HALF_OPEN 状态下被取消 (asyncio.CancelledError) 时必须归还名额，
否则 half_open_calls 只增不减会导致熔断器永久卡死。

不依赖真实下载器/Chrome，全部使用 mock 的同步/异步 func。

状态机:
    CLOSED --(连续失败达阈值)--> OPEN
    OPEN   --(等待 timeout)----> HALF_OPEN
    HALF_OPEN --(成功达 success_threshold)--> CLOSED
    HALF_OPEN --(失败)----------> OPEN
    HALF_OPEN --(取消)----------> 归还名额, 状态不变
"""

import asyncio

import pytest

from src.downloaders.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
)


# ---------------------------------------------------------------------------
# 测试辅助：构造 mock func
# ---------------------------------------------------------------------------

def _ok_sync(result="ok"):
    """返回一个成功的同步 func。"""
    def f():
        return result
    return f


def _fail_sync(exc):
    """返回一个抛指定异常的同步 func。"""
    def f():
        raise exc
    return f


def _ok_async(result="ok"):
    """返回一个成功的异步 func（call_async 会 await func()）。"""
    async def f():
        return result
    return f


def _fail_async(exc):
    """返回一个抛指定异常的异步 func。"""
    async def f():
        raise exc
    return f


# ---------------------------------------------------------------------------
# CLOSED 状态
# ---------------------------------------------------------------------------

async def test_closed_success_resets_failure_count():
    """CLOSED 下成功调用应清零失败计数并保持 CLOSED。"""
    cb = CircuitBreaker("t", failure_threshold=3)
    # 先累积 2 次失败（未达阈值 3）
    for _ in range(2):
        with pytest.raises(ValueError):
            await cb.call_async(_fail_async(ValueError()))
    assert cb.failure_count == 2
    # 一次成功应清零
    result = await cb.call_async(_ok_async("done"))
    assert result == "done"
    assert cb.failure_count == 0
    assert cb.is_closed


async def test_closed_failures_reach_threshold_opens():
    """CLOSED 下连续失败达阈值应转 OPEN。"""
    cb = CircuitBreaker("t", failure_threshold=3)
    for _ in range(3):
        with pytest.raises(ValueError):
            await cb.call_async(_fail_async(ValueError()))
    assert cb.is_open
    assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# OPEN 状态
# ---------------------------------------------------------------------------

async def test_open_rejects_before_timeout():
    """OPEN 且未超时应直接抛 CircuitBreakerOpen，不执行 func。"""
    cb = CircuitBreaker("t", failure_threshold=1, timeout=1800)
    with pytest.raises(ValueError):
        await cb.call_async(_fail_async(ValueError()))
    assert cb.is_open
    # 未超时：拒绝
    with pytest.raises(CircuitBreakerOpen):
        await cb.call_async(_ok_async())


async def test_open_transitions_to_half_open_after_timeout():
    """OPEN 超时后下一次调用应进入 HALF_OPEN。"""
    cb = CircuitBreaker(
        "t", failure_threshold=1, timeout=0, half_open_max_calls=3, success_threshold=5
    )
    with pytest.raises(ValueError):
        await cb.call_async(_fail_async(ValueError()))
    assert cb.is_open
    # timeout=0 → 立即可半开；用成功调用触发转换（success_threshold=5 不会立即关闭）
    await cb.call_async(_ok_async())
    assert cb.state == CircuitState.HALF_OPEN


# ---------------------------------------------------------------------------
# HALF_OPEN 状态
# ---------------------------------------------------------------------------

async def test_half_open_success_threshold_closes():
    """HALF_OPEN 下成功达 success_threshold 应恢复 CLOSED。"""
    cb = CircuitBreaker(
        "t", failure_threshold=1, timeout=0, half_open_max_calls=3, success_threshold=2
    )
    with pytest.raises(ValueError):
        await cb.call_async(_fail_async(ValueError()))
    # 两次成功（达 success_threshold=2）应关闭
    await cb.call_async(_ok_async())
    assert cb.state == CircuitState.HALF_OPEN
    await cb.call_async(_ok_async())
    assert cb.is_closed


async def test_half_open_failure_reopens():
    """HALF_OPEN 下失败应重新 OPEN。"""
    cb = CircuitBreaker(
        "t", failure_threshold=1, timeout=0, half_open_max_calls=3, success_threshold=2
    )
    with pytest.raises(ValueError):
        await cb.call_async(_fail_async(ValueError()))
    # 进入半开并失败
    with pytest.raises(ValueError):
        await cb.call_async(_fail_async(ValueError()))
    assert cb.is_open


async def test_half_open_max_calls_rejects():
    """HALF_OPEN 下名额耗尽应抛 'max calls reached'。"""
    cb = CircuitBreaker(
        "t", failure_threshold=1, timeout=0, half_open_max_calls=2, success_threshold=5
    )
    with pytest.raises(ValueError):
        await cb.call_async(_fail_async(ValueError()))
    # 两次成功占满名额（success_threshold=5 不会关闭）
    await cb.call_async(_ok_async())
    await cb.call_async(_ok_async())
    assert cb.half_open_calls == 2
    # 第三次：名额已满 → 拒绝
    with pytest.raises(CircuitBreakerOpen, match="max calls reached"):
        await cb.call_async(_ok_async())


# ---------------------------------------------------------------------------
# 关键回归：取消时归还半开名额（修复前会永久卡死）
# ---------------------------------------------------------------------------

async def test_half_open_releases_slot_on_cancellation():
    """
    回归：HALF_OPEN 中 func 被取消 (CancelledError) 时必须归还名额。

    修复前：half_open_calls += 1 后被取消，CancelledError 属于 BaseException，
    绕过 except Exception，名额不回退；累计到 max_calls 后永久卡死 HALF_OPEN
    （"max calls reached"），只能重启进程。
    """
    cb = CircuitBreaker(
        "t", failure_threshold=1, timeout=0, half_open_max_calls=1, success_threshold=1
    )
    # 触发 OPEN
    with pytest.raises(ValueError):
        await cb.call_async(_fail_async(ValueError()))
    assert cb.is_open

    # 进入 HALF_OPEN 的探测被取消
    with pytest.raises(asyncio.CancelledError):
        await cb.call_async(_fail_async(asyncio.CancelledError()))

    # 名额必须归还，否则下一次会撞 "max calls reached" 永久卡死
    assert cb.half_open_calls == 0
    assert cb.state == CircuitState.HALF_OPEN

    # 后续成功探测应能恢复 CLOSED（证明未卡死）
    await cb.call_async(_ok_async())
    assert cb.is_closed


def test_half_open_releases_slot_on_cancellation_sync():
    """同步 call() 在 HALF_OPEN 被 BaseException 取消时同样应归还名额。"""
    cb = CircuitBreaker(
        "t", failure_threshold=1, timeout=0, half_open_max_calls=1, success_threshold=1
    )
    with pytest.raises(ValueError):
        cb.call(_fail_sync(ValueError()))
    assert cb.is_open

    with pytest.raises(KeyboardInterrupt):
        cb.call(_fail_sync(KeyboardInterrupt()))

    assert cb.half_open_calls == 0
    cb.call(_ok_sync())
    assert cb.is_closed


# ---------------------------------------------------------------------------
# force_open
# ---------------------------------------------------------------------------

async def test_force_open_immediately_opens():
    """force_open 应立即转 OPEN 并拒绝后续请求。"""
    cb = CircuitBreaker("t", failure_threshold=5, timeout=1800)
    assert cb.is_closed
    cb.force_open(reason="nsig failed")
    assert cb.is_open
    with pytest.raises(CircuitBreakerOpen):
        await cb.call_async(_ok_async())
