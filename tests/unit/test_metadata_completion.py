"""测试元数据补充机制"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Settings
from src.downloaders.manager import DownloaderManager
from src.downloaders.models import DownloaderResult, VideoMetadata


class TestMetadataCompletion:
    """测试下载器元数据补充机制"""

    def test_merge_metadata_prefers_downloader_values(self):
        """测试元数据合并优先使用下载器返回的值"""
        settings = Settings(
            api_key="test-key",
            audio_download_priority="cdp",
        )

        manager = DownloaderManager(settings)

        # 基础元数据（完整）
        base = VideoMetadata(
            video_id="test123",
            title="Base Title",
            author="Base Author",
            channel_id="BaseChannel",
            duration=100,
            description="Base description",
            source_downloader="ytdlp",
        )

        # 下载器元数据（部分字段，但 title 不同）
        downloader = VideoMetadata(
            video_id="test123",
            title="Downloader Title",  # 不同的值
            author=None,  # 缺失
            channel_id=None,  # 缺失
            duration=None,  # 缺失
            source_downloader="cdp",
        )

        # 合并
        merged = manager._merge_metadata(base, downloader)

        # 验证：优先使用下载器的值
        assert merged.title == "Downloader Title"  # 下载器的值
        assert merged.author == "Base Author"  # 补充基础值
        assert merged.channel_id == "BaseChannel"  # 补充基础值
        assert merged.duration == 100  # 补充基础值
        assert merged.description == "Base description"  # 补充基础值
        assert merged.source_downloader == "cdp+ytdlp"  # 合并来源

    def test_merge_metadata_handles_all_none(self):
        """测试元数据合并处理全部为 None 的情况"""
        settings = Settings(api_key="test-key")
        manager = DownloaderManager(settings)

        base = VideoMetadata(
            video_id="test123",
            title="Title",
            author="Author",
        )

        downloader = VideoMetadata(
            video_id="test123",
            title=None,
            author=None,
        )

        merged = manager._merge_metadata(base, downloader)

        # 全部使用基础值
        assert merged.title == "Title"
        assert merged.author == "Author"

    @pytest.mark.asyncio
    async def test_download_with_fallback_fetches_metadata_first(self):
        """测试 download_with_fallback 先获取元数据"""
        settings = Settings(
            api_key="test-key",
            audio_download_priority="cdp",
            metadata_priority="ytdlp",
        )

        manager = DownloaderManager(settings)

        # Mock get_metadata 返回完整元数据
        # 包含 live_broadcast_content 以避免触发二次刷新
        manager.get_metadata = AsyncMock(return_value={
            "title": "Complete Title",
            "author": "Complete Author",
            "channel_id": "CompleteChannel",
            "duration": 200,
            "description": "Complete description",
            "live_broadcast_content": "none",
        })

        # Mock 下载器返回不完整元数据
        mock_result = DownloaderResult(
            success=True,
            downloader="cdp",
            video_metadata=VideoMetadata(
                video_id="test123",
                title="CDP Title",  # 只有 title
                source_downloader="cdp",
            ),
            audio_path=Path("/tmp/audio.m4a"),
        )

        # Mock _download_with_downloader
        manager._download_with_downloader = AsyncMock(return_value=mock_result)

        # 执行下载
        result = await manager.download_with_fallback(
            video_url="https://www.youtube.com/watch?v=test123",
            video_id="test123",
            output_dir=Path("/tmp"),
            include_audio=True,
            include_transcript=False,
        )

        # 验证 get_metadata 被调用
        manager.get_metadata.assert_called_once()

        # 验证返回的元数据是完整的（合并后的）
        assert result.video_metadata.title == "CDP Title"  # 优先下载器
        assert result.video_metadata.author == "Complete Author"  # 补充
        assert result.video_metadata.channel_id == "CompleteChannel"  # 补充
        assert result.video_metadata.duration == 200  # 补充
        assert result.video_metadata.description == "Complete description"  # 补充
        assert "cdp" in result.video_metadata.source_downloader  # 包含下载器来源

    @pytest.mark.asyncio
    async def test_download_handles_metadata_fetch_failure(self):
        """测试元数据获取失败时的降级处理"""
        settings = Settings(
            api_key="test-key",
            audio_download_priority="cdp",
        )

        manager = DownloaderManager(settings)

        # Mock get_metadata 返回 None（失败）
        manager.get_metadata = AsyncMock(return_value=None)

        # Mock 下载器返回部分元数据
        mock_result = DownloaderResult(
            success=True,
            downloader="cdp",
            video_metadata=VideoMetadata(
                video_id="test123",
                title="CDP Title",
                source_downloader="cdp",
            ),
            audio_path=Path("/tmp/audio.m4a"),
        )

        manager._download_with_downloader = AsyncMock(return_value=mock_result)

        # 执行下载
        result = await manager.download_with_fallback(
            video_url="https://www.youtube.com/watch?v=test123",
            video_id="test123",
            output_dir=Path("/tmp"),
            include_audio=True,
            include_transcript=False,
        )

        # 验证：即使元数据获取失败，下载仍然成功
        assert result.success
        assert result.video_metadata.title == "CDP Title"
        # 其他字段是 None（没有补充）
        assert result.video_metadata.author is None
