"""
Tests for bounded metadata lock dictionary in DownloaderManager.

Regression test: _metadata_locks previously grew unboundedly (one Lock per
video_id, never removed), a slow memory leak for a 24/7 deployment.
"""

import asyncio

import pytest

from src.downloaders.manager import DownloaderManager


def _make_manager(max_locks: int = 4) -> DownloaderManager:
    """构造仅含锁管理状态的 manager（绕过重量级 __init__）。"""
    manager = DownloaderManager.__new__(DownloaderManager)
    manager._metadata_locks = {}
    manager._METADATA_LOCKS_MAX = max_locks
    return manager


def test_same_video_id_returns_same_lock():
    manager = _make_manager()
    lock1 = manager._get_metadata_lock("video-a")
    lock2 = manager._get_metadata_lock("video-a")
    assert lock1 is lock2


def test_locks_pruned_when_exceeding_threshold():
    """超过阈值时未持有的锁应被清理，dict 不应无界增长。"""
    manager = _make_manager(max_locks=4)
    for i in range(4):
        manager._get_metadata_lock(f"video-{i}")
    assert len(manager._metadata_locks) == 4

    # 第 5 个触发清理：之前 4 个均未持有，全部被丢弃
    manager._get_metadata_lock("video-new")
    assert len(manager._metadata_locks) == 1
    assert "video-new" in manager._metadata_locks


@pytest.mark.asyncio
async def test_held_locks_survive_pruning():
    """正在持有的锁不能被清理，否则并发保护失效。"""
    manager = _make_manager(max_locks=2)

    held_lock = manager._get_metadata_lock("video-held")
    await held_lock.acquire()
    try:
        manager._get_metadata_lock("video-b")
        # 触发清理
        manager._get_metadata_lock("video-c")

        assert "video-held" in manager._metadata_locks
        assert manager._metadata_locks["video-held"] is held_lock
    finally:
        held_lock.release()
