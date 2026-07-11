"""
测试 DownloaderManager._save_metadata_to_db（precheck / get_metadata 缓存未命中触发的元数据落库路径）。

背景（外部 review 发现的 P2 bug）：TaskService._precheck_video_downloadable 复用
DownloaderManager.get_metadata()，缓存未命中时会调用 _save_metadata_to_db 落库。
该方法此前在"新建记录"分支硬编码 has_native_transcript=False——但 get_metadata()
只抓取标题/时长等基础元数据，根本没有检查过字幕可用性，写 False 等于把"没测过"
误存成"确认无字幕"，会让 task_service.py 里的音频兜底逻辑
（video_resource.has_native_transcript is False）在字幕其实存在的视频上被误触发。

这里验证 _save_metadata_to_db 的两个分支：
- 新建记录分支：必须写 has_native_transcript=None（未知），不能写 False。
- 更新已有记录分支：不应覆盖已有的 has_native_transcript 值（True/False/None 均不改变）。

另外验证 get_metadata() 降级链的"直播状态补全探测"（外部 review 第9轮问题1，P2）：
TikHub 的 fetch_metadata 成功时 live_broadcast_content 恒为 None（其 API 无直播状态
字段）。在 METADATA_PRIORITY=tikhub,ytdlp 等把 tikhub 排在前面的配置下，若第一个
成功结果直接返回而不继续探测，precheck 会拿到 live 状态未知的元数据直接放行——
直播/预约视频照常建任务，precheck 的 422 契约在该配置下不成立。修复后：链上第一个
成功结果 live_broadcast_content 为 None 时，若链上还有后续下载器，继续探测补全；
live 状态已知时立即停止（成本守护，默认 ytdlp 优先配置必须保持单次调用）。
"""

from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import Settings
from src.db.database import Database
from src.db.models import ErrorCode, VideoInfo, VideoResource
from src.downloaders.exceptions import DownloaderError
from src.downloaders.manager import DownloaderManager


# 元数据获取阶段的样例数据：只包含标题等基础信息，不包含任何字幕相关字段——
# 与生产环境中 get_metadata() 的实际产出保持一致。
SAMPLE_METADATA = {
    "title": "Sample Video",
    "author": "Sample Author",
    "channel_id": "UC_sample",
    "duration": 120,
    "description": "desc",
    "upload_date": "20260101",
    "view_count": 100,
    "thumbnail": "https://example.com/thumb.jpg",
    "live_broadcast_content": "none",
}


@pytest.fixture
def manager(test_settings: Settings, test_db: Database) -> DownloaderManager:
    """真实 DownloaderManager + 真实 test_db（aiosqlite），只验证落库语义本身。"""
    return DownloaderManager(test_settings, test_db)


class TestSaveMetadataToDbNewRecord:
    """existing 分支为 None（新视频首次落库）。"""

    @pytest.mark.asyncio
    async def test_new_record_has_native_transcript_is_none_not_false(
        self, manager: DownloaderManager, test_db: Database
    ) -> None:
        """
        核心红灯测试：precheck 触发的落库不应把"未探测字幕"误存成 False。

        get_metadata()/_save_metadata_to_db 根本没有检查字幕，has_native_transcript
        必须保持 None（未知）语义，真实探测结果留给 worker 下载后再写入。
        """
        video_id = "newvid00001"

        await manager._save_metadata_to_db(video_id, SAMPLE_METADATA)

        resource = await test_db.get_video_resource(video_id)
        assert resource is not None
        assert resource.has_native_transcript is None
        # 元数据本身应该正常落库
        assert resource.video_info is not None
        assert resource.video_info.title == "Sample Video"


class TestSaveMetadataToDbExistingRecord:
    """existing 分支已有记录时，不应覆盖已有的 has_native_transcript 值（回归测试）。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("existing_value", [True, False, None])
    async def test_update_does_not_overwrite_has_native_transcript(
        self,
        manager: DownloaderManager,
        test_db: Database,
        existing_value: Optional[bool],
    ) -> None:
        video_id = f"existvid-{existing_value}"
        await test_db.create_video_resource(
            VideoResource(video_id=video_id, has_native_transcript=existing_value)
        )

        await manager._save_metadata_to_db(video_id, SAMPLE_METADATA)

        resource = await test_db.get_video_resource(video_id)
        assert resource is not None
        assert resource.has_native_transcript is existing_value
        # 更新分支应该正常刷新元数据字段本身
        assert resource.video_info is not None
        assert resource.video_info.title == "Sample Video"


class TestGetMetadataForceRefreshOverwritesStaleLiveStatus:
    """
    Codex 第6轮问题1(P1) 关联验证：get_metadata(force_refresh=True) 拿到新鲜数据后，
    必须覆盖 DB 里过期的 live_broadcast_content 缓存。

    precheck（task_service.py）在缓存显示 live/upcoming 时会调用一次
    force_refresh=True 的 get_metadata 重新确认状态；如果新鲜数据没有真正落库，
    下一次请求会再次读到过期的 live/upcoming 缓存，永远 422。
    """

    @pytest.mark.asyncio
    async def test_force_refresh_overwrites_stale_live_status(
        self, manager: DownloaderManager, test_db: Database
    ) -> None:
        video_id = "stalelive001"
        # 预置一份陈旧缓存：曾经是 upcoming（直播尚未开始时探测并落库的）
        stale_info = VideoInfo(title="Stale upcoming", live_broadcast_content="upcoming")
        await test_db.create_video_resource(
            VideoResource(video_id=video_id, video_info=stale_info)
        )

        # 强制刷新命中的下载器返回新鲜数据：直播已结束，不再是 upcoming/live
        fresh_downloader = MagicMock()
        fresh_downloader.name = "ytdlp"
        fresh_downloader.fetch_metadata = AsyncMock(
            return_value={"title": "Now a VOD", "live_broadcast_content": None}
        )
        manager.downloaders = [fresh_downloader]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=stalelive001",
            video_id,
            force_refresh=True,
        )

        assert result is not None
        assert result.get("live_broadcast_content") is None

        # 关键断言：DB 缓存必须被新鲜数据覆盖，不能停留在旧的 upcoming
        resource = await test_db.get_video_resource(video_id)
        assert resource is not None
        assert resource.video_info is not None
        assert resource.video_info.live_broadcast_content is None
        assert resource.video_info.title == "Now a VOD"


def _make_downloader(name: str, fetch_metadata_mock: AsyncMock) -> MagicMock:
    """构造一个只桩出 name / fetch_metadata 的下载器 MagicMock，供降级链测试直接注入。"""
    downloader = MagicMock()
    downloader.name = name
    downloader.fetch_metadata = fetch_metadata_mock
    return downloader


# 样例元数据：TikHub 成功但拿不到直播状态（其 API 无该字段，恒为 None）
TIKHUB_META_UNKNOWN_LIVE = {
    "title": "TikHub Title",
    "author": "TikHub Author",
    "live_broadcast_content": None,
}


class TestGetMetadataLiveStatusProbe:
    """
    Codex 第9轮问题1(P2)：get_metadata 降级链在第一个成功结果 live 状态未知时，
    应继续向后探测补全，而不是直接停链返回未知状态。
    """

    @pytest.mark.asyncio
    async def test_probe_merges_live_status_when_second_downloader_resolves_live(
        self, manager: DownloaderManager
    ) -> None:
        """tikhub live=None + ytdlp live="live" -> 合并结果 live="live"，主体字段来自 tikhub。"""
        tikhub = _make_downloader(
            "tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE))
        )
        ytdlp = _make_downloader(
            "ytdlp",
            AsyncMock(return_value={"title": "Ytdlp Title", "live_broadcast_content": "live"}),
        )
        manager.downloaders = [tikhub, ytdlp]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=abc", "abc", priority="tikhub,ytdlp"
        )

        assert result is not None
        assert result["live_broadcast_content"] == "live"
        # 主体字段以第一个成功结果（tikhub）为准，不被 ytdlp 覆盖
        assert result["title"] == "TikHub Title"
        assert result["author"] == "TikHub Author"
        tikhub.fetch_metadata.assert_awaited_once()
        ytdlp.fetch_metadata.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_probe_merges_live_none_keeps_primary_fields(
        self, manager: DownloaderManager
    ) -> None:
        """tikhub live=None + ytdlp live="none" -> 合并 live="none"，title 等主体字段仍来自 tikhub。"""
        tikhub = _make_downloader(
            "tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE))
        )
        ytdlp = _make_downloader(
            "ytdlp",
            AsyncMock(return_value={"title": "Ytdlp Title", "live_broadcast_content": "none"}),
        )
        manager.downloaders = [tikhub, ytdlp]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=def", "def", priority="tikhub,ytdlp"
        )

        assert result is not None
        assert result["live_broadcast_content"] == "none"
        assert result["title"] == "TikHub Title"

    @pytest.mark.asyncio
    async def test_probe_all_unknown_returns_first_result_without_error(
        self, manager: DownloaderManager
    ) -> None:
        """tikhub live=None，ytdlp 探测失败（抛异常）-> 返回 tikhub 结果，live=None，无异常。"""
        tikhub = _make_downloader(
            "tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE))
        )
        ytdlp = _make_downloader("ytdlp", AsyncMock(side_effect=RuntimeError("network boom")))
        manager.downloaders = [tikhub, ytdlp]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=ghi", "ghi", priority="tikhub,ytdlp"
        )

        assert result is not None
        assert result["live_broadcast_content"] is None
        assert result["title"] == "TikHub Title"
        ytdlp.fetch_metadata.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_probe_all_unknown_when_second_downloader_returns_nothing(
        self, manager: DownloaderManager
    ) -> None:
        """tikhub live=None，ytdlp 返回空结果（非异常）-> 同样返回 tikhub 结果，live=None。"""
        tikhub = _make_downloader(
            "tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE))
        )
        ytdlp = _make_downloader("ytdlp", AsyncMock(return_value=None))
        manager.downloaders = [tikhub, ytdlp]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=jkl", "jkl", priority="tikhub,ytdlp"
        )

        assert result is not None
        assert result["live_broadcast_content"] is None
        assert result["title"] == "TikHub Title"

    @pytest.mark.asyncio
    async def test_cost_guard_default_priority_stops_after_single_call(
        self, manager: DownloaderManager
    ) -> None:
        """
        成本守护：默认 ytdlp 优先配置下，ytdlp 直接返回已知 live 状态时，
        tikhub 绝不能被调用 —— 用调用计数断言锁死单次调用。
        """
        ytdlp = _make_downloader(
            "ytdlp",
            AsyncMock(return_value={"title": "Ytdlp Title", "live_broadcast_content": "none"}),
        )
        tikhub = _make_downloader("tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE)))
        manager.downloaders = [ytdlp, tikhub]

        # 不传 priority，走 test_settings 的默认 metadata_priority="ytdlp,tikhub"
        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=mno", "mno"
        )

        assert result is not None
        assert result["live_broadcast_content"] == "none"
        ytdlp.fetch_metadata.assert_awaited_once()
        tikhub.fetch_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_merged_metadata_is_persisted_to_db(
        self, manager: DownloaderManager, test_db: Database
    ) -> None:
        """合并后的元数据（而非 tikhub 的原始未知 live 状态）应落库。"""
        tikhub = _make_downloader(
            "tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE))
        )
        ytdlp = _make_downloader(
            "ytdlp",
            AsyncMock(return_value={"title": "Ytdlp Title", "live_broadcast_content": "live"}),
        )
        manager.downloaders = [tikhub, ytdlp]
        video_id = "persistlive1"

        await manager.get_metadata(
            "https://www.youtube.com/watch?v=persistlive1", video_id, priority="tikhub,ytdlp"
        )

        resource = await test_db.get_video_resource(video_id)
        assert resource is not None
        assert resource.video_info is not None
        # 落库的是合并结果：live 状态来自 ytdlp，主体字段来自 tikhub
        assert resource.video_info.live_broadcast_content == "live"
        assert resource.video_info.title == "TikHub Title"

    @pytest.mark.asyncio
    async def test_probe_content_level_error_raised_when_raise_content_errors_true(
        self, manager: DownloaderManager
    ) -> None:
        """
        补全探测阶段若后续下载器抛出内容级终态错误（如 VIDEO_PRIVATE），
        raise_content_errors=True 时应照常上抛 —— 这本身就是有效的终态信号。
        """
        tikhub = _make_downloader(
            "tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE))
        )
        ytdlp = _make_downloader(
            "ytdlp",
            AsyncMock(
                side_effect=DownloaderError(
                    message="Video is private",
                    error_code=ErrorCode.VIDEO_PRIVATE,
                    downloader="ytdlp",
                )
            ),
        )
        manager.downloaders = [tikhub, ytdlp]

        with pytest.raises(DownloaderError) as exc_info:
            await manager.get_metadata(
                "https://www.youtube.com/watch?v=pqr",
                "pqr",
                priority="tikhub,ytdlp",
                raise_content_errors=True,
            )

        assert exc_info.value.error_code == ErrorCode.VIDEO_PRIVATE

    @pytest.mark.asyncio
    async def test_probe_region_blocked_not_raised_keeps_primary_result(
        self, manager: DownloaderManager
    ) -> None:
        """
        外部 review 第13轮问题2(P2)：VIDEO_REGION_BLOCKED 是下载器/出口位置相关的
        错误，不是视频客观状态——本地部署的 ytdlp 被地区封锁，不代表 TikHub（远端
        服务）也下载不了同一视频。补全探测阶段若后续下载器（ytdlp）抛出
        VIDEO_REGION_BLOCKED，即使 raise_content_errors=True 也不应上抛丢弃前面
        已经成功的 TikHub 结果，而应像普通失败一样吞掉，返回 TikHub 的主体结果
        （live 状态维持未知，语义与探测前一致）。
        """
        tikhub = _make_downloader(
            "tikhub", AsyncMock(return_value=dict(TIKHUB_META_UNKNOWN_LIVE))
        )
        ytdlp = _make_downloader(
            "ytdlp",
            AsyncMock(
                side_effect=DownloaderError(
                    message="Blocked in your region",
                    error_code=ErrorCode.VIDEO_REGION_BLOCKED,
                    downloader="ytdlp",
                )
            ),
        )
        manager.downloaders = [tikhub, ytdlp]

        result = await manager.get_metadata(
            "https://www.youtube.com/watch?v=stu",
            "stu",
            priority="tikhub,ytdlp",
            raise_content_errors=True,
        )

        assert result is not None
        assert result["title"] == "TikHub Title"
        assert result["live_broadcast_content"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
