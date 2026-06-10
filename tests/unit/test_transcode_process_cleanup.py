"""
Tests for transcode subprocess lifecycle and partial output cleanup.

Regression tests for:
1. ffmpeg/ffprobe subprocess leak: asyncio.wait_for only cancels communicate(),
   the subprocess must be explicitly killed on timeout/cancellation.
2. Partial output files left behind when transcode fails.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.services.transcode_service import TranscodeError, TranscodeService


class FakeProcess:
    """Fake asyncio subprocess whose communicate() hangs forever."""

    def __init__(self) -> None:
        self.killed = False
        self.returncode = None
        self.pid = 99999

    async def communicate(self):
        await asyncio.sleep(3600)

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def service():
    with patch.object(TranscodeService, "_check_ffmpeg", return_value=True):
        yield TranscodeService()


@pytest.mark.asyncio
async def test_run_command_kills_subprocess_on_timeout(service):
    """超时后子进程必须被 kill，否则 ffmpeg 会泄漏并持续占用 CPU。"""
    fake = FakeProcess()

    async def fake_create(*args, **kwargs):
        return fake

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        with pytest.raises(asyncio.TimeoutError):
            await service._run_command(
                ["ffmpeg", "-i", "input"], timeout=0, log_fallback="fallback"
            )

    assert fake.killed is True


@pytest.mark.asyncio
async def test_run_command_kills_subprocess_on_cancel(service):
    """外部取消（如任务取消/服务关停）时子进程同样必须被 kill。"""
    fake = FakeProcess()

    async def fake_create(*args, **kwargs):
        return fake

    with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
        run_task = asyncio.create_task(
            service._run_command(["ffmpeg"], timeout=3600, log_fallback="fallback")
        )
        await asyncio.sleep(0.01)
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

    assert fake.killed is True


@pytest.mark.asyncio
async def test_kill_process_skips_already_exited(service):
    """已退出的进程不应重复 kill。"""
    fake = FakeProcess()
    fake.returncode = 0
    await service._kill_process(fake)
    assert fake.killed is False


def test_cleanup_partial_output_removes_file(tmp_path):
    output = tmp_path / "partial.m4a"
    output.write_bytes(b"partial data")
    TranscodeService._cleanup_partial_output(output)
    assert not output.exists()


def test_cleanup_partial_output_missing_file_is_noop(tmp_path):
    TranscodeService._cleanup_partial_output(tmp_path / "missing.m4a")


@pytest.mark.asyncio
async def test_transcode_failure_removes_partial_output(service, tmp_path):
    """ffmpeg 失败时，已部分写入的输出文件必须被删除，避免磁盘残留。"""
    input_file = tmp_path / "in.mp3"
    input_file.write_bytes(b"audio data")
    output_file = tmp_path / "in.m4a"

    async def fake_run(cmd, timeout, log_fallback):
        # 模拟 ffmpeg 写出半成品后以非零码退出
        output_file.write_bytes(b"partial output")
        return 1, b"", b"boom"

    with patch.object(service, "validate_file", AsyncMock(return_value=True)), \
            patch.object(service, "_get_audio_codec", AsyncMock(return_value="mp3")), \
            patch.object(service, "_run_command", side_effect=fake_run):
        with pytest.raises(TranscodeError):
            await service.transcode_to_m4a(input_file, tmp_path)

    assert not output_file.exists()
