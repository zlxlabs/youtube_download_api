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
"""

from typing import Optional

import pytest

from src.config import Settings
from src.db.database import Database
from src.db.models import VideoResource
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
