"""M2: 验证 CDPDownloader 维护后台任务的强引用集合。

背景：asyncio.create_task 局部变量在函数返回后被 GC，任务可能在完成前被回收。
修复：实例上维护 _background_tasks: set[asyncio.Task]，task done 时自动 discard。

仅测试集合行为，不实际跑 Playwright。
"""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest

from src.config import Settings
from src.downloaders.cdp.downloader import CDPDownloader


@pytest.fixture
def downloader() -> CDPDownloader:
    """构造一个最小可用的 CDPDownloader。Settings 用默认值。"""
    settings = Settings(api_key="test-key")
    return CDPDownloader(settings)


def test_background_tasks_set_exists(downloader: CDPDownloader):
    """实例必须暴露 _background_tasks 强引用集合。"""
    assert hasattr(downloader, "_background_tasks"), (
        "CDPDownloader 必须维护 _background_tasks 集合以避免 task 被 GC"
    )
    assert isinstance(downloader._background_tasks, set)


def test_track_background_task_adds_to_set(downloader: CDPDownloader):
    """_track_background_task 接收 task，加入集合并在 done 时 discard。"""
    assert hasattr(downloader, "_track_background_task"), (
        "需要 _track_background_task 辅助方法统一管理"
    )

    async def runner():
        async def noop():
            await asyncio.sleep(0)

        task = asyncio.create_task(noop())
        downloader._track_background_task(task)

        # 加入后 size = 1
        assert len(downloader._background_tasks) == 1
        assert task in downloader._background_tasks

        # 等待完成
        await task

        # done callback 异步触发，给一次 loop 机会
        await asyncio.sleep(0)
        assert task not in downloader._background_tasks, (
            "task 完成后应从集合中 discard"
        )
        assert len(downloader._background_tasks) == 0

    asyncio.run(runner())


def test_track_handles_exception_without_leaking(downloader: CDPDownloader):
    """任务抛异常时也要从集合中 discard，不能泄漏。"""

    async def runner():
        async def boom():
            raise RuntimeError("boom")

        task = asyncio.create_task(boom())
        downloader._track_background_task(task)

        # 异常吞掉
        try:
            await task
        except RuntimeError:
            pass

        await asyncio.sleep(0)
        assert task not in downloader._background_tasks
        assert len(downloader._background_tasks) == 0

    asyncio.run(runner())


def test_close_cancels_pending_background_tasks(downloader: CDPDownloader):
    """close() 时应取消所有未完成的后台任务。"""

    async def runner():
        async def long_running():
            await asyncio.sleep(60)

        task = asyncio.create_task(long_running())
        downloader._track_background_task(task)

        # 注意 close() 会尝试关 Playwright，这里只测 task cancel 部分
        # 验证 close 后任务被取消
        try:
            await asyncio.wait_for(downloader.close(), timeout=5)
        except Exception:
            # 允许 Playwright/Browser 清理报错（环境无浏览器）
            pass

        # done callback 异步触发
        await asyncio.sleep(0)
        assert task.cancelled() or task.done(), (
            "close() 必须取消或等待未完成的后台任务"
        )

    asyncio.run(runner())
