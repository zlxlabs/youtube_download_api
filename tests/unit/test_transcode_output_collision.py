"""
Regression tests: 转码输出路径与输入路径撞名。

线上事故（2026-07-25）：人工上传 .m4a 文件时，临时文件名为 <uuid>.m4a，
transcode_to_m4a 的 output_filename 默认取 input_file.stem，输出目录又是同一个
临时目录，于是 output_file == input_file。ffmpeg 直接拒绝
（"Output ... same as Input #0 - exiting"），失败清理逻辑还会把用户刚上传的
原文件删掉，导致回退的重编码分支报 "No such file or directory"，
最终 API 返回 422。

这些用例锁死：无论走 remux 还是重编码分支，输出路径都不得等于输入路径，
且输入文件在任何失败路径下都不能被删除。
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.services.transcode_service import TranscodeError, TranscodeService


@pytest.fixture
def service():
    with patch.object(TranscodeService, "_check_ffmpeg", return_value=True):
        yield TranscodeService()


def _cmd_paths(cmd: list[str]) -> tuple[Path, Path]:
    """从 ffmpeg 命令里取出输入与输出路径。"""
    return Path(cmd[cmd.index("-i") + 1]), Path(cmd[-1])


def _is_remux(cmd: list[str]) -> bool:
    return "copy" in cmd


def _make_ffmpeg_stub(fail_remux: bool = False):
    """
    模拟真实 ffmpeg 行为：

    - 输入输出同路径时拒绝执行并删除该文件（复刻线上观察到的破坏性行为）
    - 否则写出输出文件并成功返回
    """

    async def fake_run(cmd, timeout, log_fallback):
        input_file, output_file = _cmd_paths(cmd)
        if input_file == output_file:
            output_file.unlink(missing_ok=True)
            return 254, b"", b"Output same as Input #0 - exiting"
        if fail_remux and _is_remux(cmd):
            return 1, b"", b"remux failed"
        output_file.write_bytes(b"transcoded")
        return 0, b"", b""

    return fake_run


@pytest.mark.asyncio
async def test_m4a_input_does_not_collide_with_output(service, tmp_path: Path):
    """.m4a 输入时输出路径必须避开输入路径，否则 ffmpeg 直接失败。"""
    input_file = tmp_path / "9bd6d59434c446db9d91bb7759c97dca.m4a"
    input_file.write_bytes(b"aac audio data")

    with patch.object(service, "validate_file", AsyncMock(return_value=True)), \
            patch.object(service, "_get_audio_codec", AsyncMock(return_value="aac")), \
            patch.object(service, "_run_command", side_effect=_make_ffmpeg_stub()):
        output_file = await service.transcode_to_m4a(input_file, tmp_path)

    assert output_file != input_file
    assert output_file.exists()
    assert input_file.exists(), "输入文件不得被转码流程删除"


@pytest.mark.asyncio
async def test_m4a_input_survives_remux_failure(service, tmp_path: Path):
    """remux 失败回退到重编码时，输入文件仍必须存在。"""
    input_file = tmp_path / "collision.m4a"
    input_file.write_bytes(b"aac audio data")

    with patch.object(service, "validate_file", AsyncMock(return_value=True)), \
            patch.object(service, "_get_audio_codec", AsyncMock(return_value="aac")), \
            patch.object(
                service, "_run_command", side_effect=_make_ffmpeg_stub(fail_remux=True)
            ):
        output_file = await service.transcode_to_m4a(input_file, tmp_path)

    assert output_file != input_file
    assert output_file.exists()
    assert input_file.exists(), "remux 失败清理不得误删输入文件"


@pytest.mark.asyncio
async def test_explicit_output_filename_collision_is_avoided(service, tmp_path: Path):
    """显式传入与输入同名的 output_filename 时同样不得撞名。"""
    input_file = tmp_path / "same-name.m4a"
    input_file.write_bytes(b"aac audio data")

    with patch.object(service, "validate_file", AsyncMock(return_value=True)), \
            patch.object(service, "_get_audio_codec", AsyncMock(return_value="aac")), \
            patch.object(service, "_run_command", side_effect=_make_ffmpeg_stub()):
        output_file = await service.transcode_to_m4a(
            input_file, tmp_path, output_filename="same-name"
        )

    assert output_file != input_file
    assert output_file.exists()
    assert input_file.exists()


@pytest.mark.asyncio
async def test_non_m4a_input_keeps_plain_output_name(service, tmp_path: Path):
    """非 .m4a 输入不存在撞名问题，输出名保持原样，避免无谓改名。"""
    input_file = tmp_path / "audio.mp3"
    input_file.write_bytes(b"mp3 audio data")

    with patch.object(service, "validate_file", AsyncMock(return_value=True)), \
            patch.object(service, "_get_audio_codec", AsyncMock(return_value="mp3")), \
            patch.object(service, "_run_command", side_effect=_make_ffmpeg_stub()):
        output_file = await service.transcode_to_m4a(input_file, tmp_path)

    assert output_file == tmp_path / "audio.m4a"
    assert input_file.exists()


@pytest.mark.asyncio
async def test_failure_cleanup_never_deletes_input(service, tmp_path: Path):
    """转码彻底失败时，清理逻辑只能删输出，不能碰输入。"""
    input_file = tmp_path / "boom.m4a"
    input_file.write_bytes(b"aac audio data")

    async def always_fail(cmd, timeout, log_fallback):
        _, output_file = _cmd_paths(cmd)
        output_file.write_bytes(b"partial")
        return 1, b"", b"boom"

    with patch.object(service, "validate_file", AsyncMock(return_value=True)), \
            patch.object(service, "_get_audio_codec", AsyncMock(return_value="aac")), \
            patch.object(service, "_run_command", side_effect=always_fail):
        with pytest.raises(TranscodeError):
            await service.transcode_to_m4a(input_file, tmp_path)

    assert input_file.exists(), "失败清理误删了输入文件"
