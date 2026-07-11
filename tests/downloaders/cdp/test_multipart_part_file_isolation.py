"""
multipart 分片下载：跨尝试 part 文件路径隔离测试（外部 review 第 4 轮问题 1，P2）。

背景：403 阶段级 cookie 重试会用相同 target_path 立刻重新调用
_download_with_curl_cffi_multipart。上一轮修复（test_multipart_cancellation.py）
让函数在异常退出前 cancel + await 所有未完成的分片任务，但这只能让 **asyncio
包装层** 立即返回——分片下载的同步网络请求跑在线程池的系统线程里，
CPython 无法强制中断已经在系统线程里运行的请求本身（该线程会在自身超时后
才自然结束）。如果两次尝试共用相同的 .partN 路径，残活线程可能在清理逻辑
执行之后才真正结束，重新创建/覆写旧路径的分片文件，污染紧随其后的重试。

修复：每次调用 _download_with_curl_cffi_multipart 生成一个独立的"尝试
token"，分片路径形如 ``target.ext.a{token}.partN``，从路径层面彻底消除
跨尝试的文件竞争，不依赖线程能否及时停止。

本文件验证：
1. 两次连续调用（模拟 403 后立即发起的 cookie 重试）产生互不相同的 part
   文件路径。
2. 残活线程写脏旧路径不影响新一轮尝试的合并结果（直接向旧路径写入模拟）。
"""

import asyncio
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from src.downloaders.cdp.audio_downloader import AudioDownloader


def _closure_value(func: Callable[..., Any], name: str) -> Any:
    """
    从闭包函数中提取指定自由变量的值（测试专用小工具）。

    与 test_multipart_cancellation.py 中的同名工具函数保持一致：
    _sync_download 是 download_chunk_with_retry 内部定义的闭包，捕获了
    part_file 等分片相关的局部变量，通过 co_freevars 反查其真实值。
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
    s.cdp_multipart_chunks = 6
    s.data_dir = Path("/tmp/test_multipart_part_file_isolation")
    s.cdp_convert_to_m4a = False
    return s


@pytest.fixture
def audio_downloader(settings: MagicMock) -> AudioDownloader:
    """创建 AudioDownloader 实例（mock 所有外部依赖）。"""
    return AudioDownloader(settings=settings, downloader_name="cdp")


@pytest.mark.asyncio
async def test_two_consecutive_attempts_use_disjoint_part_paths(
    audio_downloader: AudioDownloader, tmp_path: Path
) -> None:
    """
    两次连续调用 _download_with_curl_cffi_multipart（模拟 403 后立即发起的
    cookie 重试，target_path 完全相同）必须产生互不相同的 .partN 路径。
    """
    target_path = tmp_path / "audio_itag140.webm"
    fixed_ranges = [(0, 0, 999), (1, 1000, 1999)]
    audio_downloader._calculate_dynamic_chunks = MagicMock(  # type: ignore[method-assign]
        return_value=fixed_ranges
    )

    attempt_paths: list[set[Path]] = [set(), set()]
    current_attempt = {"idx": -1}

    async def fake_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
        part_file = _closure_value(func, "part_file")
        attempt_paths[current_attempt["idx"]].add(part_file)
        part_file.write_bytes(b"x" * 1000)
        return 1000

    with patch("asyncio.to_thread", side_effect=fake_to_thread):
        for i in range(2):
            current_attempt["idx"] = i
            ok = await audio_downloader._download_with_curl_cffi_multipart(
                url="https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback",
                target_path=target_path,
                expected_size=2000,
                headers={"user-agent": "test-ua"},
            )
            assert ok is True

    assert attempt_paths[0], "第一次尝试应记录到分片路径"
    assert attempt_paths[1], "第二次尝试应记录到分片路径"
    assert attempt_paths[0].isdisjoint(attempt_paths[1]), (
        f"两次尝试的 part 文件路径不应重叠：{attempt_paths[0]} vs {attempt_paths[1]}"
    )


@pytest.mark.asyncio
async def test_stale_writer_on_old_attempt_path_does_not_corrupt_new_attempt(
    audio_downloader: AudioDownloader, tmp_path: Path
) -> None:
    """
    模拟"残活线程"场景：第一次尝试的分片任务已被取消/清理，但对应的系统线程
    在函数返回之后才真正结束，重新在旧的 .partN 路径上写入脏数据。第二次
    尝试（新的 attempt token）应完全不受这份脏数据影响：合并结果只包含本次
    尝试写入的内容。
    """
    target_path = tmp_path / "audio_itag140.webm"
    fixed_ranges = [(0, 0, 999)]
    audio_downloader._calculate_dynamic_chunks = MagicMock(  # type: ignore[method-assign]
        return_value=fixed_ranges
    )

    first_part_paths: list[Path] = []

    async def fake_to_thread_first(func: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
        part_file = _closure_value(func, "part_file")
        first_part_paths.append(part_file)
        part_file.write_bytes(b"GOOD" * 250)  # 1000 字节的"正确"内容
        return 1000

    with patch("asyncio.to_thread", side_effect=fake_to_thread_first):
        ok = await audio_downloader._download_with_curl_cffi_multipart(
            url="https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback",
            target_path=target_path,
            expected_size=1000,
            headers={"user-agent": "test-ua"},
        )
    assert ok is True
    assert target_path.read_bytes() == b"GOOD" * 250

    # 合并完成后，第一次尝试的分片文件应已被清理
    stale_path = first_part_paths[0]
    assert not stale_path.exists()

    # 模拟残活线程在第一次尝试"结束"之后才真正完成写入：用脏数据重新
    # 创建旧 attempt token 对应的分片路径
    stale_path.write_bytes(b"BAD!" * 250)

    second_part_paths: list[Path] = []

    async def fake_to_thread_second(func: Callable[..., Any], *args: Any, **kwargs: Any) -> int:
        part_file = _closure_value(func, "part_file")
        second_part_paths.append(part_file)
        part_file.write_bytes(b"NEW!" * 250)
        return 1000

    with patch("asyncio.to_thread", side_effect=fake_to_thread_second):
        ok = await audio_downloader._download_with_curl_cffi_multipart(
            url="https://rr1---sn-npoe7ndl.googlevideo.com/videoplayback",
            target_path=target_path,
            expected_size=1000,
            headers={"user-agent": "test-ua"},
        )

    assert ok is True
    # 新一轮尝试必须使用与旧路径不同的 part 文件
    assert second_part_paths[0] != stale_path
    # 合并结果只应包含新一轮尝试的数据，不受旧路径脏数据污染
    assert target_path.read_bytes() == b"NEW!" * 250

    # 脏数据文件作为孤儿文件独立存在，佐证它确实未被新一轮合并逻辑读取
    assert stale_path.exists()
    assert stale_path.read_bytes() == b"BAD!" * 250
