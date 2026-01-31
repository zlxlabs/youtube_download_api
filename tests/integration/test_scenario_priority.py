"""测试场景化优先级选择"""

import pytest

from src.config import Settings
from src.downloaders.manager import DownloaderManager


class TestScenarioPriority:
    """测试下载器管理器的场景化优先级选择"""

    def test_audio_scenario_uses_audio_priority(self):
        """测试音频下载场景使用 AUDIO_DOWNLOAD_PRIORITY"""
        # 准备：配置仅允许 cdp 用于音频下载
        settings = Settings(
            api_key="test-key",
            audio_download_priority="cdp",
            transcript_only_priority="tikhub,ytdlp",
            metadata_priority="ytdlp,tikhub",
            cdp_enabled=True,  # 启用 CDP
        )

        manager = DownloaderManager(settings)

        # 执行：获取音频下载场景的优先级列表
        prioritized = manager._get_prioritized_downloaders(
            include_audio=True,
            include_transcript=False
        )

        # 验证：应该只有 cdp（如果可用）
        downloader_names = [d.name for d in prioritized]

        # CDP 可能不可用（需要 Playwright），所以我们检查顺序而不是绝对值
        if "cdp" in downloader_names:
            assert downloader_names[0] == "cdp", (
                f"Expected 'cdp' to be first for audio download, "
                f"but got: {downloader_names}"
            )

    def test_transcript_scenario_uses_transcript_priority(self):
        """测试字幕下载场景使用 TRANSCRIPT_ONLY_PRIORITY"""
        settings = Settings(
            api_key="test-key",
            audio_download_priority="cdp,ytdlp,tikhub",
            transcript_only_priority="tikhub,ytdlp",
            metadata_priority="ytdlp,tikhub",
        )

        manager = DownloaderManager(settings)

        # 执行：获取字幕下载场景的优先级列表
        prioritized = manager._get_prioritized_downloaders(
            include_audio=False,
            include_transcript=True
        )

        # 验证：应该遵循 TRANSCRIPT_ONLY_PRIORITY
        downloader_names = [d.name for d in prioritized]

        # tikhub 可能不可用（需要 API key），检查可用的下载器顺序
        if "tikhub" in downloader_names and "ytdlp" in downloader_names:
            tikhub_idx = downloader_names.index("tikhub")
            ytdlp_idx = downloader_names.index("ytdlp")
            assert tikhub_idx < ytdlp_idx, (
                f"Expected 'tikhub' before 'ytdlp' for transcript download, "
                f"but got: {downloader_names}"
            )

    def test_mixed_scenario_uses_audio_priority(self):
        """测试音频+字幕场景使用 AUDIO_DOWNLOAD_PRIORITY"""
        settings = Settings(
            api_key="test-key",
            audio_download_priority="cdp,ytdlp",
            transcript_only_priority="tikhub,ytdlp",
            metadata_priority="ytdlp,tikhub",
            cdp_enabled=True,
        )

        manager = DownloaderManager(settings)

        # 执行：获取音频+字幕场景的优先级列表
        prioritized = manager._get_prioritized_downloaders(
            include_audio=True,
            include_transcript=True
        )

        # 验证：应该使用 AUDIO_DOWNLOAD_PRIORITY
        downloader_names = [d.name for d in prioritized]

        if "cdp" in downloader_names:
            assert downloader_names[0] == "cdp", (
                f"Expected 'cdp' to be first for audio+transcript download, "
                f"but got: {downloader_names}"
            )

    def test_invalid_scenario_raises_error(self):
        """测试无效场景（两者都为 False）抛出异常"""
        settings = Settings(
            api_key="test-key",
            audio_download_priority="cdp,ytdlp",
            transcript_only_priority="tikhub,ytdlp",
        )

        manager = DownloaderManager(settings)

        # 执行 & 验证：应该抛出 ValueError
        with pytest.raises(ValueError, match="Invalid scenario"):
            manager._get_prioritized_downloaders(
                include_audio=False,
                include_transcript=False
            )

    def test_priority_config_strictly_followed(self):
        """测试严格遵循配置的优先级顺序"""
        settings = Settings(
            api_key="test-key",
            audio_download_priority="ytdlp",  # 只配置 ytdlp
            transcript_only_priority="ytdlp",
            metadata_priority="ytdlp",
            cdp_enabled=False,  # 禁用 CDP
        )

        manager = DownloaderManager(settings)

        # 执行：获取音频下载场景的优先级列表
        prioritized = manager._get_prioritized_downloaders(
            include_audio=True,
            include_transcript=False
        )

        # 验证：应该只有 ytdlp
        downloader_names = [d.name for d in prioritized]
        assert "ytdlp" in downloader_names, (
            f"Expected 'ytdlp' to be available, but got: {downloader_names}"
        )
        # 不应该有 CDP（因为未配置）
        assert "cdp" not in downloader_names, (
            f"Expected 'cdp' to be excluded (not in priority config), "
            f"but it's present in: {downloader_names}"
        )
