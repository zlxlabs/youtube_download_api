"""
测试探测任务执行结果与 IP 熔断器状态迁移之间的接线（worker 层端到端）。

背景（P1，Codex 第 16 轮 review 发现）：`_process_next_task()` 里判断是否要把
下载结果送去做熔断分析的条件是：

    if is_probe and isinstance(result.get("downloader_result"), DownloaderResult):
        await self._analyze_result_and_update_ban(...)

但修复前，`_execute_task()` / `_execute_download_with_manager()` 返回的成功结果
dict 从来不带 "downloader_result" 键——上面的 isinstance 检查恒为 False，导致
探测任务无论成功还是失败，都不会触发 `_analyze_result_and_update_ban()`。叠加
本仓库另一个已完成的修复（服务重启后从数据库恢复持久化的熔断状态），效果是：
一旦熔断状态被持久化恢复出来，就再也没有代码路径能把它降级/解除——熔断永生。

这里的测试全部走真实调用链（`_process_next_task` -> `_execute_task` ->
`_execute_download_with_manager` -> `_analyze_result_and_update_ban`），只 mock
下载器管理器/task_service/file_service/db/notify_service，刻意不直接调用
`_analyze_result_and_update_ban()`——否则测试会绕过这次真正断线的接线层，
和 test_ip_ban_passive_probe.py 里已经存在的、只测熔断器自身方法的单测重复。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings
from src.core.ip_ban_models import IPBanLevel
from src.core.worker import DownloadWorker
from src.db.database import Database
from src.db.models import ErrorCode, FileRecord, FileType, Task, TaskPriority, TaskStatus
from src.downloaders.exceptions import DownloaderError
from src.downloaders.models import DownloaderResult, VideoMetadata


def _make_task(
    task_id: str,
    include_audio: bool,
    include_transcript: bool,
) -> Task:
    """构造一个最小可用的测试任务（callback_url 留空，避免触发后台回调发送）。"""
    return Task(
        id=task_id,
        video_id=f"video-{task_id}",
        video_url=f"https://youtube.com/watch?v=video-{task_id}",
        status=TaskStatus.PENDING,
        include_audio=include_audio,
        include_transcript=include_transcript,
        priority=TaskPriority.NORMAL,
        created_at=datetime.now(timezone.utc),
    )


def _make_worker(db: AsyncMock, settings: Settings) -> DownloadWorker:
    """
    构造一个用于端到端测试的 DownloadWorker。

    db 用 AsyncMock（不落真实 sqlite）：本文件关注的是 worker 内部的调用链接线
    是否正确（探测成功/失败是否真的驱动了熔断器状态迁移），持久化层本身的
    读写正确性已经由 test_ip_ban_persistence.py 的 TestDatabaseIPBanPersistence
    覆盖，这里用 mock 断言"调用参数是否正确"即可，不需要重复验证 SQL 往返。
    """
    # 探测失败场景会经过 _handle_download_error 的重试计数逻辑
    # （min(new_count - 1, ...)），必须返回真实 int，否则 AsyncMock 默认返回值
    # 是 MagicMock，会在整数运算处炸掉，和本文件要测的熔断接线无关。
    db.increment_retry_count = AsyncMock(return_value=1)

    task_service = MagicMock()
    task_service.task_queue = MagicMock()
    task_service.task_queue.put = AsyncMock()

    file_service = MagicMock()
    file_service.get_all_files_for_video = AsyncMock(
        return_value={"audio": None, "transcript": None}
    )
    file_service.create_file_record = AsyncMock(return_value=MagicMock(id="file-id"))

    downloader_manager = MagicMock()
    downloader_manager.download_with_fallback = AsyncMock()

    worker = DownloadWorker(
        db=db,
        settings=settings,
        task_service=task_service,
        file_service=file_service,
        callback_service=MagicMock(),
        notify_service=AsyncMock(),
        downloader_manager=downloader_manager,
        metrics_collector=None,
    )
    return worker


@pytest.fixture
def ban_settings(test_settings: Settings) -> Settings:
    """把等待参数调小，方便测试里手动回拨时间戳模拟"已等待足够久"。"""
    test_settings.ip_ban_min_wait_before_retry = 60
    test_settings.ip_ban_max_retry_interval = 60
    return test_settings


class TestProbeSuccessDrivesBanRecovery:
    """核心 P1 场景：探测任务成功执行必须驱动熔断器状态迁移。"""

    @pytest.mark.asyncio
    async def test_fully_banned_restored_probe_chain_recovers_to_normal(
        self, ban_settings: Settings
    ) -> None:
        """
        端到端场景：服务重启后从数据库恢复出 FULLY_BANNED 状态 -> 第一个放行的
        探测任务（字幕）成功 -> 熔断器应该降级到 AUDIO_BANNED，且持久化更新、
        恢复通知都要触发 -> 紧接着第二个放行的探测任务（音频）也成功 ->
        熔断器应该最终回到 NORMAL。

        分两步而不是一步到位：现有 _analyze_result_and_update_ban 的
        FULLY_BANNED 分支只看字幕探测结果（决定 FULLY_BANNED -> AUDIO_BANNED），
        不存在 FULLY_BANNED 直接一次性回 NORMAL 的路径，回到 NORMAL 需要
        AUDIO_BANNED 状态下音频探测再成功一次（这是本仓库分级熔断的既有设计，
        不是本次修复引入的行为）。
        """
        db = AsyncMock()
        db.load_ip_ban_state = AsyncMock(
            return_value={
                "current_level": IPBanLevel.FULLY_BANNED,
                "banned_at": datetime.now() - timedelta(hours=8),
                "last_attempt_at": None,
                "failed_attempts": 2,
            }
        )

        worker = _make_worker(db, ban_settings)

        # 步骤 0：模拟服务重启后的持久化恢复（走真实的 _restore_ip_ban_state，
        # 不是直接摆弄 breaker 内部字段）。
        restored_ok = await worker._restore_ip_ban_state()
        assert restored_ok is True
        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.FULLY_BANNED

        # 恢复动作本身也会写一条 "restored" 历史，重置掉，只关注下面两次探测
        # 各自触发的持久化调用。
        db.save_ip_ban_state.reset_mock()
        db.append_ip_ban_history.reset_mock()

        worker._running = True

        # ---------- 步骤 1：字幕探测任务成功 -> 降级到 AUDIO_BANNED ----------
        task1 = _make_task("probe-transcript", include_audio=False, include_transcript=True)
        worker.task_service.get_next_task = AsyncMock(return_value=task1)
        worker.db.get_task = AsyncMock(return_value=task1)
        worker.downloader_manager.download_with_fallback = AsyncMock(
            return_value=DownloaderResult(
                success=True,
                downloader="tikhub",
                video_metadata=VideoMetadata(video_id=task1.video_id, title="T1"),
                audio_path=None,
                transcript_path=Path("/tmp/fake-transcript-1.srt"),
                has_transcript=True,
            )
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await worker._process_next_task()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.AUDIO_BANNED, (
            "字幕探测成功后必须降级到 AUDIO_BANNED——如果这里仍是 FULLY_BANNED，"
            "说明 _analyze_result_and_update_ban 根本没被调用（P1 断线复现）"
        )
        db.save_ip_ban_state.assert_called_once()
        assert (
            db.save_ip_ban_state.call_args.kwargs["current_level"]
            == IPBanLevel.AUDIO_BANNED
        )
        db.append_ip_ban_history.assert_called_once()
        assert db.append_ip_ban_history.call_args.kwargs["event_type"] == "downgraded"
        worker.notify_service.send_ip_recovery_notification.assert_called_once()

        # 手动回拨触发时间，模拟"已经等待超过最小间隔"，避免测试真的 sleep。
        worker.ip_ban_breaker.banned_at -= timedelta(seconds=ban_settings.ip_ban_min_wait_before_retry + 10)

        # ---------- 步骤 2：音频探测任务成功 -> 解除熔断回到 NORMAL ----------
        task2 = _make_task("probe-audio", include_audio=True, include_transcript=False)
        worker.task_service.get_next_task = AsyncMock(return_value=task2)
        worker.db.get_task = AsyncMock(return_value=task2)
        worker.downloader_manager.download_with_fallback = AsyncMock(
            return_value=DownloaderResult(
                success=True,
                downloader="cdp",
                video_metadata=VideoMetadata(video_id=task2.video_id, title="T2"),
                audio_path=Path("/tmp/fake-audio-2.m4a"),
                transcript_path=None,
                has_transcript=False,
            )
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await worker._process_next_task()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.NORMAL, (
            "音频探测成功后必须解除熔断回到 NORMAL"
        )
        assert db.save_ip_ban_state.call_count == 2
        assert db.save_ip_ban_state.call_args.kwargs["current_level"] == IPBanLevel.NORMAL
        assert db.append_ip_ban_history.call_count == 2
        assert db.append_ip_ban_history.call_args.kwargs["event_type"] == "recovered"
        assert worker.notify_service.send_ip_recovery_notification.call_count == 2


class TestProbeFailureExistingSemantics(object):
    """探测任务失败：回归测试，确认既有的 403 触发机制没有被本次修复破坏。"""

    @pytest.mark.asyncio
    async def test_probe_failure_via_403_keeps_ban_active(
        self, ban_settings: Settings
    ) -> None:
        """
        探测任务失败（HTTP 403）时，走的是既有的 _trigger_ban_from_error 机制
        （不是 _analyze_result_and_update_ban 的失败分支——后者在下载器抛异常
        时根本不会被调用，见 _process_next_task 里 try 块的结构）。这里验证：
        熔断状态在探测失败后仍然保持"熔断中"，不会被误判为已恢复清回 NORMAL。
        """
        db = AsyncMock()
        worker = _make_worker(db, ban_settings)

        await worker.ip_ban_breaker.trigger_audio_ban(reason="seed")
        worker.ip_ban_breaker.banned_at -= timedelta(
            seconds=ban_settings.ip_ban_min_wait_before_retry + 10
        )
        db.save_ip_ban_state.reset_mock()
        db.append_ip_ban_history.reset_mock()

        task = _make_task("probe-fail", include_audio=True, include_transcript=False)
        worker.task_service.get_next_task = AsyncMock(return_value=task)
        worker.db.get_task = AsyncMock(return_value=task)
        worker.downloader_manager.download_with_fallback = AsyncMock(
            side_effect=DownloaderError(
                message="HTTP 403 Forbidden",
                error_code=ErrorCode.RATE_LIMITED,
                downloader="ytdlp",
                http_status_code=403,
                operation="audio",
            )
        )

        worker._running = True
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await worker._process_next_task()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        worker.notify_service.send_ip_ban_notification.assert_called_once()


class TestNonProbeSuccessRegression:
    """普通（非探测）成功任务在熔断期间的既有语义回归。"""

    @pytest.mark.asyncio
    async def test_non_probe_success_during_audio_ban_does_not_touch_breaker(
        self, ban_settings: Settings
    ) -> None:
        """
        AUDIO_BANNED 期间，字幕类任务被 _check_ip_ban_and_decide 判定为"正常
        放行"（不是探测，见该方法 AUDIO_BANNED + transcript_only 分支直接
        return None）。这类任务成功执行不应该触发任何熔断状态迁移——修复
        "探测成功不触发分析" 的同时，不能误把这类非探测流量也送进分析。
        """
        db = AsyncMock()
        worker = _make_worker(db, ban_settings)

        await worker.ip_ban_breaker.trigger_audio_ban(reason="seed")
        db.save_ip_ban_state.reset_mock()
        db.append_ip_ban_history.reset_mock()

        task = _make_task("normal-transcript", include_audio=False, include_transcript=True)
        worker.task_service.get_next_task = AsyncMock(return_value=task)
        worker.db.get_task = AsyncMock(return_value=task)
        worker.downloader_manager.download_with_fallback = AsyncMock(
            return_value=DownloaderResult(
                success=True,
                downloader="tikhub",
                video_metadata=VideoMetadata(video_id=task.video_id, title="T3"),
                transcript_path=Path("/tmp/fake-transcript-3.srt"),
                has_transcript=True,
            )
        )

        worker._running = True
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await worker._process_next_task()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        db.save_ip_ban_state.assert_not_called()
        db.append_ip_ban_history.assert_not_called()
        worker.notify_service.send_ip_recovery_notification.assert_not_called()


class TestCachedResourceDoesNotMisleadAnalysis:
    """
    防止修复本身引入新的误判：探测任务命中缓存的资源不能被当成"探测失败"。

    这是本次修复顺带发现并堵上的一个衍生风险（不是 Codex 报告的原始 P1，见
    worker.py 里 audio_requested/transcript_requested 的注释）：is_probe 的
    判定只看任务类型（_check_ip_ban_and_decide），不看资源是否已缓存；如果
    直接把 DownloaderResult.audio_path/transcript_path 是否为 None 当作"这次
    探测到的真实结果"，缓存命中（根本没有真正请求下载器）会被误读成"探测到
    仍然失败"，错误延长甚至升级熔断级别。
    """

    @pytest.mark.asyncio
    async def test_audio_banned_probe_with_cached_audio_does_not_misfire_upgrade(
        self, ban_settings: Settings
    ) -> None:
        db = AsyncMock()
        worker = _make_worker(db, ban_settings)

        await worker.ip_ban_breaker.trigger_audio_ban(reason="seed")
        worker.ip_ban_breaker.banned_at -= timedelta(
            seconds=ban_settings.ip_ban_min_wait_before_retry + 10
        )
        db.save_ip_ban_state.reset_mock()
        db.append_ip_ban_history.reset_mock()

        task = _make_task("mixed-cached-audio", include_audio=True, include_transcript=True)
        cached_audio = FileRecord(
            id="cached-audio-1",
            video_id=task.video_id,
            file_type=FileType.AUDIO,
            filename="a.m4a",
            filepath="a.m4a",
        )
        worker.task_service.get_next_task = AsyncMock(return_value=task)
        worker.file_service.get_all_files_for_video = AsyncMock(
            return_value={"audio": cached_audio, "transcript": None}
        )
        worker.db.get_task = AsyncMock(return_value=task)
        worker.downloader_manager.download_with_fallback = AsyncMock(
            return_value=DownloaderResult(
                success=True,
                downloader="tikhub",
                video_metadata=VideoMetadata(video_id=task.video_id, title="T4"),
                audio_path=None,  # 本次没有真正请求音频（need_audio=False，走缓存）
                transcript_path=Path("/tmp/fake-transcript-4.srt"),
                has_transcript=True,
            )
        )

        worker._running = True
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await worker._process_next_task()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.AUDIO_BANNED, (
            "缓存命中不应该被误判为音频探测仍然失败，不能升级到 FULLY_BANNED"
        )
        db.save_ip_ban_state.assert_not_called()
        db.append_ip_ban_history.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
