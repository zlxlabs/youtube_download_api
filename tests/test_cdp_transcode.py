"""
CDP 下载器转码功能测试。

测试 webm → m4a 自动转换功能。
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.db.models import ErrorCode
from src.downloaders.cdp.audio_downloader import AudioDownloader
from src.downloaders.exceptions import DownloaderError


@pytest.fixture
def settings():
    """创建测试配置。"""
    return Settings(
        api_key="test-key",
        audio_quality=128,
        data_dir=Path("./test_data"),
    )


@pytest.fixture
def audio_downloader(settings):
    """创建 AudioDownloader 实例。"""
    return AudioDownloader(settings, downloader_name="cdp")


@pytest.mark.asyncio
async def test_convert_m4a_to_m4a_no_conversion(audio_downloader, tmp_path):
    """测试：m4a 文件不需要转换。"""
    # 创建 m4a 文件
    m4a_file = tmp_path / "test.m4a"
    m4a_file.write_text("fake m4a content")

    # 调用转换
    result = await audio_downloader._convert_to_m4a_if_needed(m4a_file, tmp_path)

    # 验证：返回原文件，未调用转码服务
    assert result == m4a_file
    assert m4a_file.exists()


@pytest.mark.asyncio
async def test_convert_webm_to_m4a(audio_downloader, tmp_path):
    """测试：webm 文件转换为 m4a。"""
    # 创建 webm 文件
    webm_file = tmp_path / "test.webm"
    webm_file.write_text("fake webm content")

    # 创建转码后的 m4a 文件
    m4a_file = tmp_path / "test.m4a"

    # Mock 转码服务
    with patch.object(
        audio_downloader._transcode_service,
        "transcode_to_m4a",
        new_callable=AsyncMock,
    ) as mock_transcode:
        # 设置 mock 返回值
        mock_transcode.return_value = m4a_file
        m4a_file.write_text("fake m4a content")

        # 调用转换
        result = await audio_downloader._convert_to_m4a_if_needed(webm_file, tmp_path)

        # 验证：返回 m4a 文件
        assert result == m4a_file
        assert m4a_file.exists()

        # 验证：调用了转码服务
        mock_transcode.assert_called_once_with(
            input_file=webm_file,
            output_dir=tmp_path,
            target_bitrate=128,
            output_filename=webm_file.stem,
        )


@pytest.mark.asyncio
async def test_convert_webm_to_m4a_deletes_original(audio_downloader, tmp_path):
    """测试：转换成功后删除原始 webm 文件。"""
    # 创建 webm 文件
    webm_file = tmp_path / "test.webm"
    webm_file.write_text("fake webm content")

    # 创建转码后的 m4a 文件
    m4a_file = tmp_path / "test.m4a"

    # Mock 转码服务
    with patch.object(
        audio_downloader._transcode_service,
        "transcode_to_m4a",
        new_callable=AsyncMock,
    ) as mock_transcode:
        mock_transcode.return_value = m4a_file
        m4a_file.write_text("fake m4a content")

        # 调用转换
        await audio_downloader._convert_to_m4a_if_needed(webm_file, tmp_path)

        # 验证：原始文件已删除
        assert not webm_file.exists()
        assert m4a_file.exists()


@pytest.mark.asyncio
async def test_convert_transcode_error_raises(audio_downloader, tmp_path):
    """测试：转码失败抛出异常。"""
    from src.services.transcode_service import TranscodeError

    # 创建 webm 文件
    webm_file = tmp_path / "test.webm"
    webm_file.write_text("fake webm content")

    # Mock 转码服务（抛出异常）
    with patch.object(
        audio_downloader._transcode_service,
        "transcode_to_m4a",
        new_callable=AsyncMock,
    ) as mock_transcode:
        mock_transcode.side_effect = TranscodeError("ffmpeg failed")

        # 调用转换，预期抛出异常
        with pytest.raises(DownloaderError) as exc_info:
            await audio_downloader._convert_to_m4a_if_needed(webm_file, tmp_path)

        # 验证：异常信息正确
        assert exc_info.value.error_code == ErrorCode.CDP_TRANSCODE_FAILED
        assert "Failed to convert .webm to m4a" in exc_info.value.message


@pytest.mark.asyncio
async def test_download_audio_converts_webm(audio_downloader, tmp_path):
    """测试：download_audio 方法自动转换 webm。"""
    from src.downloaders.cdp.models import AudioInfo

    # 创建 AudioInfo（webm 格式）
    audio_info = AudioInfo(
        url="http://example.com/audio.webm",
        itag=251,
        mime_type="webm",
        title="Test Video",
        filesize=1024 * 1024,
        ext="webm",
    )

    # Mock _download_with_curl_cffi（返回 webm 文件）
    webm_file = tmp_path / "Test_Video_itag251.webm"
    webm_file.write_text("fake webm content")

    m4a_file = tmp_path / "Test_Video_itag251.m4a"
    m4a_file.write_text("fake m4a content")

    with patch.object(
        audio_downloader,
        "_download_with_curl_cffi",
        new_callable=AsyncMock,
    ) as mock_download:
        mock_download.return_value = True

        with patch.object(
            audio_downloader._transcode_service,
            "transcode_to_m4a",
            new_callable=AsyncMock,
        ) as mock_transcode:
            mock_transcode.return_value = m4a_file

            # 启用 curl_cffi
            audio_downloader.settings.cdp_use_curl_cffi = True

            # 调用下载
            result = await audio_downloader.download_audio(
                audio_info=audio_info,
                video_id="test_video",
                task_id="test_task",
                output_dir=tmp_path,
                headers={"User-Agent": "test"},
            )

            # 验证：返回 m4a 文件
            assert result == m4a_file
            assert result.suffix == ".m4a"

            # 验证：调用了转码
            mock_transcode.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
