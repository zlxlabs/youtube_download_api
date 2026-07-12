"""
multipart 分片下载：单分片 403 时其余分片任务的取消行为测试。

背景（外部 review 第 3 轮问题 1，P1）：
_download_with_curl_cffi_multipart 用 asyncio.gather(*tasks) 等待所有分片
任务。gather() 在默认（非 return_exceptions）模式下，一旦某个分片任务抛出
异常（如 403）就会立即向上传播，但**不会自动取消**其余仍在运行的分片任务
——它们可能仍在读写 .partN 文件。

紧接着的 403 阶段级 cookie 重试会用**相同的 target_path**（因此相同的
.partN 路径）立刻重新调用 _download_with_curl_cffi_multipart，如果旧任务
还没结束，两批任务会竞争同一批分片文件，可能损坏合并结果或让本可成功的
重试失败。

本文件验证修复：函数因任一分片异常退出前，必须先取消所有未完成的分片任务
并 await 其真正结束，保证函数返回/抛出时不存在任何仍在运行的后台任务。
"""

import asyncio
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from src.db.models import ErrorCode
from src.downloaders.cdp.audio_downloader import AudioDownloader
from src.downloaders.exceptions import DownloaderError


def _closure_value(func: Callable[..., Any], name: str) -> Any:
    """
    从闭包函数中提取指定自由变量的值（测试专用小工具）。

    _sync_download 是 download_chunk_with_retry 内部定义的闭包，捕获了
    chunk_idx 等分片相关的局部变量。通过检查 co_freevars 找到对应的
    cell，反查其在测试 mock 里代表哪个分片，从而模拟"某个分片 403、
    其余分片长时间挂起"的场景。
    """
    names = func.__code__.co_freevars
    for freevar_name, cell in zip(names, func.__closure__ or ()):
        if freevar_name == name:
            return cell.cell_contents
    raise KeyError(f"{name} not found in closure of {func!r}")


@pytest.fixture
def settings() -> MagicMock:
    """构造 AudioDownloader 所需的最小配置（mock）。"""
    s = MagicMock()
    s.cdp_use_curl_cffi = True
    s.cdp_enable_multipart = True
    s.cdp_multipart_min_size = 1
    s.cdp_multipart_chunks = 6  # 最大并发数，需 >= 测试分片数保证不被信号量卡住
    s.data_dir = Path("/tmp/test_multipart_cancellation")
    s.cdp_convert_to_m4a = False
    return s


@pytest.fixture
def audio_downloader(settings: MagicMock) -> AudioDownloader:
    """创建 AudioDownloader 实例（mock 所有外部依赖）。"""
    return AudioDownloader(settings=settings, downloader_name="cdp")


@pytest.mark.asyncio
async def test_multipart_403_cancels_and_awaits_pending_chunk_tasks(
    audio_downloader: AudioDownloader, tmp_path: Path
) -> None:
    """
    场景：3 个分片并发下载，分片 0 立即 403，分片 1/2 长时间挂起（模拟仍在
    进行的网络 I/O）。

    期望：
    - 函数以 DownloaderError(403) 退出（原有行为不变）。
    - 分片 1/2 对应的 asyncio 任务已被取消（cancelled）且已经真正结束
      （task.done() 为 True），不存在悬挂的后台任务。
    - 分片 1/2 的"挂起"点确实收到了 CancelledError（证明取消真实发生，
      而不是任务从未被观察）。
    """
    target_path = tmp_path / "audio_itag140.webm"
    # 固定为 3 个小分片，避免依赖真实分片大小计算逻辑，保证测试确定性
    fixed_ranges = [(0, 0, 999), (1, 1000, 1999), (2, 2000, 2999)]
    audio_downloader._calculate_dynamic_chunks = MagicMock(  # type: ignore[method-assign]
        return_value=fixed_ranges
    )

    fail_idx = 0
    hang_started = {1: asyncio.Event(), 2: asyncio.Event()}
    cancelled_chunks: set[int] = set()

    async def fake_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
        """
        替换 asyncio.to_thread：不真正起线程，用纯 asyncio 语义模拟
        "分片 0 立即 403 / 其它分片挂起直到被 cancel"，确保挂起点能被
        asyncio 级别的取消正确打断（真实网络线程无法被 Python 强制中断，
        不在本测试验证范围内）。

        分片 0 显式等待分片 1/2 都已经进入"挂起"状态后才触发 403——这样
        断言不依赖真实的随机调度时序，可靠复现"某分片失败退出时，其它
        分片仍在进行中"的场景。
        """
        idx = _closure_value(func, "chunk_idx")
        if idx == fail_idx:
            await hang_started[1].wait()
            await hang_started[2].wait()
            raise DownloaderError(
                message=f"HTTP 403 for chunk {idx}",
                error_code=ErrorCode.CDP_DOWNLOAD_403,
                downloader="cdp",
                http_status_code=403,
                stop_fallback=True,
            )
        hang_started[idx].set()
        try:
            # 永远不会被 set 的 Event：只能通过任务取消（CancelledError）打断
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled_chunks.add(idx)
            raise
        return 0  # pragma: no cover - 不会执行到这里

    created_tasks: list["asyncio.Task[int]"] = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro: Any, *args: Any, **kwargs: Any) -> "asyncio.Task[Any]":
        task = real_create_task(coro, *args, **kwargs)
        created_tasks.append(task)
        return task

    with (
        patch("asyncio.to_thread", side_effect=fake_to_thread),
        patch("asyncio.create_task", side_effect=tracking_create_task),
    ):
        with pytest.raises(DownloaderError) as exc_info:
            await audio_downloader._download_with_curl_cffi_multipart(
                url="https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback",
                target_path=target_path,
                expected_size=3000,
                headers={"user-agent": "test-ua"},
            )

    assert exc_info.value.error_code == ErrorCode.CDP_DOWNLOAD_403

    # 3 个分片任务都应该被创建
    assert len(created_tasks) == 3
    failing_task, hung_task_1, hung_task_2 = created_tasks

    # 挂起点确实被进入过，证明取消发生前任务已经在运行（而非从未调度）
    assert hang_started[1].is_set()
    assert hang_started[2].is_set()

    # 函数返回时，所有分片任务都必须已经结束（无悬挂后台任务）
    assert failing_task.done()
    assert hung_task_1.done()
    assert hung_task_2.done()

    # 挂起的两个分片任务应处于"已取消"状态；403 的分片是正常异常退出，非取消
    assert not failing_task.cancelled()
    assert hung_task_1.cancelled()
    assert hung_task_2.cancelled()

    # 挂起点确实收到了 CancelledError（不是被静默丢弃/未观察的任务）
    assert cancelled_chunks == {1, 2}


@pytest.mark.asyncio
async def test_multipart_generic_exception_also_cancels_pending_chunk_tasks(
    audio_downloader: AudioDownloader, tmp_path: Path
) -> None:
    """
    场景：分片任务本身仍在正常"进行中"（模拟网络 I/O 尚未返回），但编排逻辑
    的 asyncio.gather(*tasks) 本身抛出一个与分片下载无关的普通异常（防御性
    的 except Exception 分支，覆盖分片重试链路之外的意外情况）。

    期望：即使异常不是 DownloaderError，也要先取消并等待所有已创建的分片
    任务结束，再返回 False（原有行为不变），不留下悬挂任务。
    """
    target_path = tmp_path / "audio_itag140.webm"
    fixed_ranges = [(0, 0, 999), (1, 1000, 1999)]
    audio_downloader._calculate_dynamic_chunks = MagicMock(  # type: ignore[method-assign]
        return_value=fixed_ranges
    )

    hang_started = {0: asyncio.Event(), 1: asyncio.Event()}

    async def fake_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
        idx = _closure_value(func, "chunk_idx")
        hang_started[idx].set()
        await asyncio.Event().wait()  # 永远不会被 set，只能被 cancel 打断
        return 0  # pragma: no cover

    created_tasks: list["asyncio.Task[int]"] = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro: Any, *args: Any, **kwargs: Any) -> "asyncio.Task[Any]":
        task = real_create_task(coro, *args, **kwargs)
        created_tasks.append(task)
        return task

    real_gather = asyncio.gather

    async def controlled_gather(*aws: Any, **kwargs: Any) -> Any:
        """
        区分两处 gather 调用：
        - 主编排的 gather(*tasks)（无 return_exceptions）：等两个分片任务都
          真正开始运行后，模拟一个与分片下载无关的普通异常。
        - 修复代码里清理阶段的 gather(*tasks, return_exceptions=True)：
          必须走真实 gather，否则无法验证取消是否真正被等待完成。
        """
        if kwargs.get("return_exceptions"):
            return await real_gather(*aws, **kwargs)
        await hang_started[0].wait()
        await hang_started[1].wait()
        raise RuntimeError("unexpected orchestration bug")

    with (
        patch("asyncio.to_thread", side_effect=fake_to_thread),
        patch("asyncio.create_task", side_effect=tracking_create_task),
        patch("asyncio.gather", side_effect=controlled_gather),
    ):
        result = await audio_downloader._download_with_curl_cffi_multipart(
            url="https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback",
            target_path=target_path,
            expected_size=2000,
            headers={"user-agent": "test-ua"},
        )

    assert result is False
    assert len(created_tasks) == 2
    for task in created_tasks:
        assert task.done()
        assert task.cancelled()
