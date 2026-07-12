"""
测试 IP 熔断状态持久化功能。

背景：IPBanCircuitBreaker（src/core/ip_ban_breaker.py）状态完全在内存中。
本仓库接入了 D3 自动部署，每次 push 到 main 都会重启容器 —— 如果重启时
正处于熔断中，服务醒来会误判为 NORMAL 全速请求 YouTube，加重封禁。

覆盖范围：
1. Database.save_ip_ban_state / load_ip_ban_state 往返一致性
2. Database.append_ip_ban_history 追加历史事件
3. IPBanCircuitBreaker.on_state_change 回调钩子（含 fail-open）
4. IPBanCircuitBreaker.restore_state 恢复状态
5. calculate_ban_recovery_time 纯函数（恢复判定与 breaker 语义一致）
6. DownloadWorker 启动时的持久化恢复流程
7. Settings 新增的 ip_ban_min_wait_before_retry / ip_ban_max_retry_interval
"""

import time
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from src.config import Settings
from src.core.ip_ban_breaker import IPBanCircuitBreaker, calculate_ban_recovery_time
from src.core.ip_ban_models import IPBanLevel, IPBanStateChangeContext
from src.core.worker import (
    FULL_BAN_AUDIO_MIN_WAIT_MULTIPLIER,
    MIN_DELAY_SECONDS_FLOOR,
    DownloadWorker,
)
from src.db.database import Database
from src.db.models import Task, TaskPriority, TaskStatus
from src.utils.logger import logger as app_logger


@pytest.fixture
def captured_logs() -> Generator[list[str], None, None]:
    """
    捕获 loguru 输出的日志正文，用于断言启动恢复流程打印的运维日志措辞。

    worker.py 里用的 `from src.utils.logger import logger` 与这里的
    `app_logger` 是同一个 loguru 单例（logger.py 只是重新导出），
    loguru 默认不会像标准库 logging 那样接入 pytest 的 caplog，
    这里用 logger.add() 挂一个临时 sink 收集消息文本，测试结束后移除，
    不影响其他测试的日志输出。
    """
    messages: list[str] = []
    sink_id = app_logger.add(
        lambda message: messages.append(message.record["message"]), level="DEBUG"
    )
    yield messages
    app_logger.remove(sink_id)


# ==================== Database 持久化测试 ====================


class TestDatabaseIPBanPersistence:
    """测试 Database.save_ip_ban_state / load_ip_ban_state / append_ip_ban_history。"""

    @pytest.mark.asyncio
    async def test_load_returns_normal_state_by_default(self, test_db: Database) -> None:
        """新建数据库默认应该是 NORMAL 状态（种子行已由建表逻辑写入）。"""
        state = await test_db.load_ip_ban_state()

        assert state is not None
        assert state["current_level"] == IPBanLevel.NORMAL
        assert state["banned_at"] is None

    @pytest.mark.asyncio
    async def test_load_returns_none_when_row_missing(self, test_db: Database) -> None:
        """显式删除单行表记录后，读回应返回 None。"""
        await test_db.execute("DELETE FROM ip_ban_status WHERE id = 1")

        state = await test_db.load_ip_ban_state()

        assert state is None

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip_audio_banned(self, test_db: Database) -> None:
        """保存音频熔断状态后应该能原样读回（各字段一致）。"""
        banned_at = datetime.now().replace(microsecond=0)
        last_attempt_at = banned_at + timedelta(minutes=10)

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=banned_at,
            last_attempt_at=last_attempt_at,
            failed_attempts=3,
        )

        state = await test_db.load_ip_ban_state()

        assert state is not None
        assert state["current_level"] == IPBanLevel.AUDIO_BANNED
        assert state["banned_at"] == banned_at
        assert state["last_attempt_at"] == last_attempt_at
        assert state["failed_attempts"] == 3
        # 读回的时间戳必须是 naive（与 breaker 内部 datetime.now() 保持一致），
        # 否则 breaker 做时间运算时会因 naive/aware 混用而抛异常。
        assert state["banned_at"].tzinfo is None
        assert state["last_attempt_at"].tzinfo is None

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip_fully_banned(self, test_db: Database) -> None:
        """保存全局熔断状态后应该能原样读回。"""
        banned_at = datetime.now().replace(microsecond=0)

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.FULLY_BANNED,
            banned_at=banned_at,
            last_attempt_at=None,
            failed_attempts=0,
        )

        state = await test_db.load_ip_ban_state()

        assert state is not None
        assert state["current_level"] == IPBanLevel.FULLY_BANNED
        assert state["banned_at"] == banned_at
        assert state["last_attempt_at"] is None
        assert state["failed_attempts"] == 0

    @pytest.mark.asyncio
    async def test_save_overwrites_previous_state(self, test_db: Database) -> None:
        """多次 save 应该是 upsert 语义，最新一次覆盖旧值。"""
        first = datetime.now().replace(microsecond=0)
        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=first,
            last_attempt_at=None,
            failed_attempts=1,
        )

        second = first + timedelta(hours=1)
        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.FULLY_BANNED,
            banned_at=second,
            last_attempt_at=second,
            failed_attempts=2,
        )

        state = await test_db.load_ip_ban_state()
        assert state is not None
        assert state["current_level"] == IPBanLevel.FULLY_BANNED
        assert state["banned_at"] == second
        assert state["failed_attempts"] == 2

    @pytest.mark.asyncio
    async def test_save_reset_to_normal_clears_timestamps(self, test_db: Database) -> None:
        """恢复正常状态后应该能保存 banned_at=None。"""
        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=datetime.now(),
            last_attempt_at=datetime.now(),
            failed_attempts=1,
        )

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.NORMAL,
            banned_at=None,
            last_attempt_at=None,
            failed_attempts=0,
        )

        state = await test_db.load_ip_ban_state()
        assert state is not None
        assert state["current_level"] == IPBanLevel.NORMAL
        assert state["banned_at"] is None
        assert state["last_attempt_at"] is None

    @pytest.mark.asyncio
    async def test_append_ip_ban_history_triggered(self, test_db: Database) -> None:
        """触发事件应写入一条历史记录，字段正确。"""
        banned_at = datetime.now(timezone.utc)

        await test_db.append_ip_ban_history(
            event_type="triggered",
            ban_level=IPBanLevel.AUDIO_BANNED.value,
            trigger_error="cdp: HTTP 403 Forbidden",
            banned_at=banned_at,
        )

        cursor = await test_db.execute(
            "SELECT * FROM ip_ban_history ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row["event_type"] == "triggered"
        assert row["ban_level"] == "audio_banned"
        assert row["trigger_error"] == "cdp: HTTP 403 Forbidden"
        assert row["recovered_at"] is None

    @pytest.mark.asyncio
    async def test_append_ip_ban_history_recovered(self, test_db: Database) -> None:
        """恢复事件应写入 recovered_at 与 duration_seconds。"""
        banned_at = datetime.now(timezone.utc) - timedelta(hours=1)
        recovered_at = datetime.now(timezone.utc)

        await test_db.append_ip_ban_history(
            event_type="recovered",
            ban_level=IPBanLevel.NORMAL.value,
            banned_at=banned_at,
            recovered_at=recovered_at,
            duration_seconds=3600,
        )

        cursor = await test_db.execute(
            "SELECT * FROM ip_ban_history ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()

        assert row is not None
        assert row["event_type"] == "recovered"
        assert row["ban_level"] == "normal"
        assert row["recovered_at"] is not None
        assert row["duration_seconds"] == 3600

    @pytest.mark.asyncio
    async def test_append_ip_ban_history_multiple_events_preserve_order(
        self, test_db: Database
    ) -> None:
        """多个事件按插入顺序追加，互不覆盖。"""
        await test_db.append_ip_ban_history(
            event_type="triggered", ban_level="audio_banned"
        )
        await test_db.append_ip_ban_history(
            event_type="upgraded", ban_level="fully_banned"
        )
        await test_db.append_ip_ban_history(
            event_type="downgraded", ban_level="audio_banned"
        )
        await test_db.append_ip_ban_history(
            event_type="recovered", ban_level="normal"
        )

        cursor = await test_db.execute(
            "SELECT event_type FROM ip_ban_history ORDER BY id ASC"
        )
        rows = await cursor.fetchall()
        event_types = [row["event_type"] for row in rows]

        assert event_types == ["triggered", "upgraded", "downgraded", "recovered"]


# ==================== 时区正确性测试（外部 review 第13轮问题3，P2） ====================


class TestIPBanPersistenceTimezoneCorrectness:
    """
    IPBanCircuitBreaker 全程使用 naive 本地时间做时间运算（既有约定，本次
    修复不改动 breaker 内部实现）。save_ip_ban_state / append_ip_ban_history
    落库前必须把 naive 本地时间正确转换成 aware UTC，而不是被全局 sqlite
    适配器"naive 视为 UTC"的默认规则误当 UTC 直接写入——否则生产环境
    TZ=Asia/Shanghai 下，18:00 本地触发的熔断会被存成 18:00Z（实际应为
    10:00Z），status/history 的绝对时间全部偏 8 小时；一旦部署时区变化，
    历史数据的偏移量还会继续漂移。

    这里通过 monkeypatch TZ 环境变量 + time.tzset() 模拟非 UTC 部署时区。
    注意：光测"save 再 load 一致"不够——旧的 bug 实现（naive 直接透传给
    全局适配器）在同一进程内 save/load 全程使用同一个系统时区，误转换会
    自我抵消，往返结果照样一致，掩盖了绝对时间存错的问题。所以下面同时
    用 CAST(... AS TEXT) 绕过 PARSE_DECLTYPES 自动转换，直接断言数据库里
    的原始存储字符串是真正的 UTC，才能真正锁死这个 bug。
    """

    @pytest.fixture(autouse=True)
    def _shanghai_timezone(self, monkeypatch: Any) -> Generator[None, None, None]:
        """把进程时区强制设为 Asia/Shanghai（UTC+8），模拟生产 TZ 配置。"""
        monkeypatch.setenv("TZ", "Asia/Shanghai")
        time.tzset()
        yield
        # monkeypatch 会在测试结束时自动撤销 TZ 环境变量，这里同步调用
        # time.tzset() 让进程时区设置也一起还原，避免污染后续测试。
        time.tzset()

    @pytest.mark.asyncio
    async def test_save_ip_ban_status_stores_true_utc_not_local_mislabeled(
        self, test_db: Database
    ) -> None:
        """save_ip_ban_state 存储的 banned_at 原始值应是真正的 UTC，而非本地时间。"""
        # 2026-01-01 18:00:00 本地时间（Asia/Shanghai, UTC+8）-> 应存为 10:00:00Z
        banned_at_local = datetime(2026, 1, 1, 18, 0, 0)

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=banned_at_local,
            last_attempt_at=None,
            failed_attempts=1,
        )

        cursor = await test_db.execute(
            "SELECT CAST(banned_at AS TEXT) AS raw FROM ip_ban_status WHERE id = 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        raw_value = row["raw"]
        assert raw_value is not None
        assert raw_value.startswith("2026-01-01T10:00:00"), (
            f"18:00 本地时间（UTC+8）应存为 10:00:00Z，实际存储值：{raw_value}"
        )

    @pytest.mark.asyncio
    async def test_append_ip_ban_history_stores_true_utc_not_local_mislabeled(
        self, test_db: Database
    ) -> None:
        """append_ip_ban_history 存储的 banned_at 原始值应是真正的 UTC。"""
        # 23:30 本地时间（Asia/Shanghai, UTC+8）-> 应存为 15:30:00Z
        banned_at_local = datetime(2026, 6, 15, 23, 30, 0)

        await test_db.append_ip_ban_history(
            event_type="triggered",
            ban_level=IPBanLevel.FULLY_BANNED.value,
            banned_at=banned_at_local,
        )

        cursor = await test_db.execute(
            "SELECT CAST(banned_at AS TEXT) AS raw FROM ip_ban_history "
            "ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        raw_value = row["raw"]
        assert raw_value is not None
        assert raw_value.startswith("2026-06-15T15:30:00"), (
            f"23:30 本地时间（UTC+8）应存为 15:30:00Z，实际存储值：{raw_value}"
        )

    @pytest.mark.asyncio
    async def test_save_and_load_round_trip_naive_local_under_non_utc_tz(
        self, test_db: Database
    ) -> None:
        """
        往返一致性：非 UTC 时区下，save 一个 naive 本地时间，load 回来应该
        还原成同一个 naive 本地时间（与 breaker 内部时间运算兼容），即使
        底层存储的已经是正确转换后的 UTC。
        """
        banned_at_local = datetime(2026, 3, 10, 9, 15, 30)
        last_attempt_local = banned_at_local + timedelta(minutes=20)

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=banned_at_local,
            last_attempt_at=last_attempt_local,
            failed_attempts=2,
        )

        state = await test_db.load_ip_ban_state()

        assert state is not None
        assert state["banned_at"] == banned_at_local
        assert state["last_attempt_at"] == last_attempt_local
        # 读回的时间戳必须是 naive（与 breaker 内部 datetime.now() 保持一致）
        assert state["banned_at"].tzinfo is None
        assert state["last_attempt_at"].tzinfo is None


# ==================== IPBanCircuitBreaker 回调钩子测试 ====================


class TestIPBanBreakerOnStateChangeCallback:
    """测试 IPBanCircuitBreaker 的 on_state_change 回调钩子。"""

    @pytest.mark.asyncio
    async def test_trigger_audio_ban_invokes_callback_with_correct_levels(self) -> None:
        """触发音频熔断应调用回调，携带正确的新旧级别。"""
        callback = AsyncMock()
        breaker = IPBanCircuitBreaker(on_state_change=callback)

        await breaker.trigger_audio_ban(reason="HTTP 403")

        callback.assert_awaited_once()
        context: IPBanStateChangeContext = callback.await_args.args[0]
        assert context.event_type == "triggered"
        assert context.old_level == IPBanLevel.NORMAL
        assert context.new_level == IPBanLevel.AUDIO_BANNED
        assert context.reason == "HTTP 403"

    @pytest.mark.asyncio
    async def test_upgrade_to_full_ban_invokes_callback_with_upgraded_event(self) -> None:
        """从音频熔断升级到全局熔断应产生 upgraded 事件（而非 triggered）。"""
        callback = AsyncMock()
        breaker = IPBanCircuitBreaker(on_state_change=callback)

        await breaker.trigger_audio_ban()
        callback.reset_mock()

        await breaker.upgrade_to_full_ban()

        callback.assert_awaited_once()
        context: IPBanStateChangeContext = callback.await_args.args[0]
        assert context.event_type == "upgraded"
        assert context.old_level == IPBanLevel.AUDIO_BANNED
        assert context.new_level == IPBanLevel.FULLY_BANNED

    @pytest.mark.asyncio
    async def test_downgrade_to_audio_ban_invokes_callback_with_downgraded_event(
        self,
    ) -> None:
        """从全局熔断降级到音频熔断应产生 downgraded 事件。"""
        callback = AsyncMock()
        breaker = IPBanCircuitBreaker(on_state_change=callback)

        await breaker.trigger_full_ban()
        callback.reset_mock()

        await breaker.downgrade_to_audio_ban()

        callback.assert_awaited_once()
        context: IPBanStateChangeContext = callback.await_args.args[0]
        assert context.event_type == "downgraded"
        assert context.old_level == IPBanLevel.FULLY_BANNED
        assert context.new_level == IPBanLevel.AUDIO_BANNED

    @pytest.mark.asyncio
    async def test_reset_to_normal_invokes_callback_with_recovered_event(self) -> None:
        """恢复正常状态应产生 recovered 事件，且旧状态快照中仍带原始 banned_at。"""
        callback = AsyncMock()
        breaker = IPBanCircuitBreaker(on_state_change=callback)

        await breaker.trigger_audio_ban()
        callback.reset_mock()

        await breaker.reset_to_normal()

        callback.assert_awaited_once()
        context: IPBanStateChangeContext = callback.await_args.args[0]
        assert context.event_type == "recovered"
        assert context.old_level == IPBanLevel.AUDIO_BANNED
        assert context.new_level == IPBanLevel.NORMAL
        # old_state 应保留恢复前（清空前）的 banned_at，供历史记录计算持续时长
        assert context.old_state.banned_at is not None
        assert context.new_state.banned_at is None

    @pytest.mark.asyncio
    async def test_reset_to_normal_when_already_normal_does_not_invoke_callback(
        self,
    ) -> None:
        """本来就是 NORMAL 时调用 reset_to_normal 不应产生多余回调。"""
        callback = AsyncMock()
        breaker = IPBanCircuitBreaker(on_state_change=callback)

        await breaker.reset_to_normal()

        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_affect_transition_result(self) -> None:
        """回调抛异常时，熔断器状态转换本身必须正常完成（fail-open）。"""
        callback = AsyncMock(side_effect=RuntimeError("db write failed"))
        breaker = IPBanCircuitBreaker(on_state_change=callback)

        # 不应该向上抛出异常
        await breaker.trigger_audio_ban(reason="HTTP 403")

        assert breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_callback_configured_does_not_raise(self) -> None:
        """未配置回调时（默认 None），状态转换应正常工作，不报错。"""
        breaker = IPBanCircuitBreaker()

        await breaker.trigger_full_ban(reason="transcript 403")

        assert breaker.get_current_level() == IPBanLevel.FULLY_BANNED


# ==================== IPBanCircuitBreaker.restore_state 测试 ====================


class TestIPBanBreakerRestoreState:
    """测试 IPBanCircuitBreaker.restore_state。"""

    @pytest.mark.asyncio
    async def test_restore_state_sets_fields_without_resetting_timestamps(self) -> None:
        """restore_state 应直接写入传入的历史时间戳,不重置为当前时间。"""
        breaker = IPBanCircuitBreaker()
        banned_at = datetime.now() - timedelta(minutes=30)
        last_attempt_at = datetime.now() - timedelta(minutes=5)

        await breaker.restore_state(
            level=IPBanLevel.AUDIO_BANNED,
            banned_at=banned_at,
            last_attempt_at=last_attempt_at,
            failed_attempts=2,
        )

        assert breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        assert breaker.banned_at == banned_at
        assert breaker.last_attempt_at == last_attempt_at
        assert breaker.failed_attempts == 2

    @pytest.mark.asyncio
    async def test_restore_state_affects_should_allow_attempt(self) -> None:
        """恢复后的状态应该真实影响拦截行为（should_allow_attempt）。"""
        breaker = IPBanCircuitBreaker(
            min_wait_before_retry=3600, max_retry_interval=1800
        )

        # 恢复一个 5 分钟前触发、远未到最小等待时间的熔断状态
        await breaker.restore_state(
            level=IPBanLevel.AUDIO_BANNED,
            banned_at=datetime.now() - timedelta(minutes=5),
            last_attempt_at=None,
            failed_attempts=0,
        )

        allowed, reason = breaker.should_allow_attempt("audio")
        assert not allowed

    @pytest.mark.asyncio
    async def test_restore_state_invokes_callback_with_restored_event(self) -> None:
        """restore_state 应通过同一回调机制产生 restored 事件。"""
        callback = AsyncMock()
        breaker = IPBanCircuitBreaker(on_state_change=callback)

        await breaker.restore_state(
            level=IPBanLevel.FULLY_BANNED,
            banned_at=datetime.now() - timedelta(minutes=10),
            last_attempt_at=None,
            failed_attempts=1,
        )

        callback.assert_awaited_once()
        context: IPBanStateChangeContext = callback.await_args.args[0]
        assert context.event_type == "restored"
        assert context.old_level == IPBanLevel.NORMAL
        assert context.new_level == IPBanLevel.FULLY_BANNED


# ==================== calculate_ban_recovery_time 纯函数测试 ====================


class TestCalculateBanRecoveryTime:
    """测试恢复时间计算的纯函数（供启动恢复流程复用，避免与实例方法重复实现）。"""

    def test_recovery_time_uses_min_wait_when_no_last_attempt(self) -> None:
        banned_at = datetime(2026, 1, 1, 0, 0, 0)

        recovery_time = calculate_ban_recovery_time(
            banned_at, None, min_wait_before_retry=3600, max_retry_interval=1800
        )

        assert recovery_time == banned_at + timedelta(seconds=3600)

    def test_recovery_time_uses_later_of_min_wait_and_retry_interval(self) -> None:
        banned_at = datetime(2026, 1, 1, 0, 0, 0)
        last_attempt_at = banned_at + timedelta(minutes=55)

        recovery_time = calculate_ban_recovery_time(
            banned_at,
            last_attempt_at,
            min_wait_before_retry=3600,
            max_retry_interval=1800,
        )

        # last_attempt + max_retry = 00:55 + 30min = 01:25，晚于 banned_at + 60min = 01:00
        assert recovery_time == last_attempt_at + timedelta(seconds=1800)

    def test_matches_breaker_instance_method(self) -> None:
        """纯函数结果应与 breaker 实例方法 get_estimated_recovery_time 完全一致。"""
        breaker = IPBanCircuitBreaker(min_wait_before_retry=3600, max_retry_interval=1800)
        breaker.current_level = IPBanLevel.AUDIO_BANNED
        breaker.banned_at = datetime.now()
        breaker.last_attempt_at = datetime.now() - timedelta(minutes=1)

        expected = breaker.get_estimated_recovery_time()
        actual = calculate_ban_recovery_time(
            breaker.banned_at, breaker.last_attempt_at, 3600, 1800
        )

        assert expected == actual


# ==================== DownloadWorker 启动恢复流程测试 ====================


def _make_worker(db: Database, settings: Settings) -> DownloadWorker:
    """构造一个仅用于测试启动恢复逻辑的 DownloadWorker（其余依赖全部 mock）。"""
    return DownloadWorker(
        db=db,
        settings=settings,
        task_service=MagicMock(),
        file_service=MagicMock(),
        callback_service=MagicMock(),
        notify_service=AsyncMock(),
        downloader_manager=MagicMock(),
    )


class TestWorkerIPBanStartupRestore:
    """测试 DownloadWorker 启动时从数据库恢复 IP 熔断状态。"""

    @pytest.mark.asyncio
    async def test_restore_when_no_persisted_ban(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """数据库里没有有效熔断记录（NORMAL）时，保持 NORMAL。"""
        worker = _make_worker(test_db, test_settings)

        await worker._restore_ip_ban_state()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.NORMAL

    @pytest.mark.asyncio
    async def test_restore_when_row_missing(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """数据库连记录都没有（load 返回 None）时，保持 NORMAL。"""
        await test_db.execute("DELETE FROM ip_ban_status WHERE id = 1")
        worker = _make_worker(test_db, test_settings)

        await worker._restore_ip_ban_state()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.NORMAL

    @pytest.mark.asyncio
    async def test_restore_active_unexpired_ban(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """熔断中且未过期时，应恢复 breaker 状态并生效拦截。"""
        test_settings.ip_ban_min_wait_before_retry = 3600
        test_settings.ip_ban_max_retry_interval = 1800

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=datetime.now() - timedelta(minutes=5),
            last_attempt_at=None,
            failed_attempts=1,
        )

        worker = _make_worker(test_db, test_settings)
        await worker._restore_ip_ban_state()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        allowed, _ = worker.ip_ban_breaker.should_allow_attempt("audio")
        assert not allowed

    @pytest.mark.asyncio
    async def test_restore_probe_window_elapsed_still_restores_ban(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        熔断的探测窗口已到（早已超过最小等待时间与重试间隔）时，
        熔断状态本身仍必须无条件恢复——这个熔断器是被动探测型的，
        recovery_time 只表示"允许下一次尝试作为探测"，不代表 IP 已恢复；
        真正的恢复要等探测任务执行成功。

        如果这里因为"看起来已过期"就忽略持久化状态直接回到 NORMAL，
        服务重启后所有排队任务会全速放行、完全跳过探测分析，持久化
        保护形同虚设（尤其 FULLY_BANNED 场景，加上 D3 每次 push 都
        重启，这个窗口经常是小时级）。
        """
        test_settings.ip_ban_min_wait_before_retry = 60
        test_settings.ip_ban_max_retry_interval = 60

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=datetime.now() - timedelta(hours=5),
            last_attempt_at=datetime.now() - timedelta(hours=5),
            failed_attempts=1,
        )

        worker = _make_worker(test_db, test_settings)
        await worker._restore_ip_ban_state()

        # 熔断级别必须原样恢复，不能因为探测窗口已到就被当成过期忽略。
        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        assert worker.ip_ban_breaker.failed_attempts == 1

        # 被动探测机制天然自洽：探测窗口已到，对应任务类型应被
        # should_allow_attempt() 放行（当作探测），而不是被拦截。
        allowed, reason = worker.ip_ban_breaker.should_allow_attempt("audio")
        assert allowed
        assert "Allowed" in reason

    @pytest.mark.asyncio
    async def test_restore_probe_window_not_reached_blocks_attempt(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """熔断的探测窗口尚未到时，恢复后应继续拦截尝试（回归覆盖旧的未过期分支）。"""
        test_settings.ip_ban_min_wait_before_retry = 3600
        test_settings.ip_ban_max_retry_interval = 1800

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=datetime.now() - timedelta(minutes=5),
            last_attempt_at=None,
            failed_attempts=1,
        )

        worker = _make_worker(test_db, test_settings)
        await worker._restore_ip_ban_state()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        allowed, _ = worker.ip_ban_breaker.should_allow_attempt("audio")
        assert not allowed

    @pytest.mark.asyncio
    async def test_restore_probe_window_elapsed_logs_probe_already_eligible(
        self,
        test_db: Database,
        test_settings: Settings,
        captured_logs: list[str],
    ) -> None:
        """
        P2 回归测试（Codex 第12轮）：探测窗口已到时，恢复流程必须真正
        打印出"probe already eligible"措辞的启动日志。

        背景：第6轮修复曾为这两条日志分支补上运维可见性，但第7轮引入
        幂等 flag 时在 `await self.ip_ban_breaker.restore_state(...)` 之后
        插入了一个提前 `return True`，导致这两条日志分支和它们前面的
        recovery_time 计算彻底沦为不可达死代码——恢复场景下启动日志完全
        不输出探测窗口信息。这里直接断言日志正文，锁死"必须真正执行到"
        这一行为，而不仅仅是熔断级别恢复正确。
        """
        test_settings.ip_ban_min_wait_before_retry = 60
        test_settings.ip_ban_max_retry_interval = 60

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=datetime.now() - timedelta(hours=5),
            last_attempt_at=datetime.now() - timedelta(hours=5),
            failed_attempts=1,
        )

        worker = _make_worker(test_db, test_settings)
        result = await worker._restore_ip_ban_state()

        assert result is True
        assert any("probe already eligible" in msg for msg in captured_logs), (
            f"探测窗口已到场景下未输出预期的启动日志，实际捕获日志：{captured_logs}"
        )

    @pytest.mark.asyncio
    async def test_restore_probe_window_not_reached_logs_next_probe_eligible_time(
        self,
        test_db: Database,
        test_settings: Settings,
        captured_logs: list[str],
    ) -> None:
        """
        P2 回归测试（Codex 第12轮）：探测窗口尚未到时，恢复流程必须真正
        打印出预计探测时间（"next probe eligible at"措辞）的启动日志。

        与上一条测试互补，锁死死代码修复后的两个分支都被真正执行到。
        """
        test_settings.ip_ban_min_wait_before_retry = 3600
        test_settings.ip_ban_max_retry_interval = 1800

        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.AUDIO_BANNED,
            banned_at=datetime.now() - timedelta(minutes=5),
            last_attempt_at=None,
            failed_attempts=1,
        )

        worker = _make_worker(test_db, test_settings)
        result = await worker._restore_ip_ban_state()

        assert result is True
        assert any("next probe eligible at" in msg for msg in captured_logs), (
            f"探测窗口未到场景下未输出预期的启动日志，实际捕获日志：{captured_logs}"
        )

    @pytest.mark.asyncio
    async def test_state_change_after_restore_persists_to_db(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """restore 之后如果 breaker 再次发生状态变更，应该继续正常写库（回调未被破坏）。"""
        worker = _make_worker(test_db, test_settings)

        await worker.ip_ban_breaker.trigger_audio_ban(reason="test")

        state = await test_db.load_ip_ban_state()
        assert state is not None
        assert state["current_level"] == IPBanLevel.AUDIO_BANNED

        cursor = await test_db.execute(
            "SELECT event_type FROM ip_ban_history ORDER BY id DESC LIMIT 1"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["event_type"] == "triggered"

    @pytest.mark.asyncio
    async def test_fully_banned_restart_first_task_is_probe_not_full_speed(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        端到端语义测试：FULLY_BANNED 状态持久化后，服务重启（探测窗口已到）
        恢复出的第一个任务必须走"探测"分支（ExecutionDecision.is_probe=True），
        而不是被当成 NORMAL 直接全速放行（decision is None）。

        对应 P1 缺陷场景：FULLY_BANNED 的探测窗口可达小时级，D3 又是每次
        push 就重启，如果重启时把"探测窗口已到"误判为"IP 已恢复"，
        所有排队任务会跳过 _check_ip_ban_and_decide 的探测判定全速放行，
        完全绕过 _analyze_result_and_update_ban 的探测分析。
        """
        test_settings.ip_ban_min_wait_before_retry = 60
        test_settings.ip_ban_max_retry_interval = 60

        # 模拟 5 小时前触发的全局熔断，早已超过探测窗口
        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.FULLY_BANNED,
            banned_at=datetime.now() - timedelta(hours=5),
            last_attempt_at=datetime.now() - timedelta(hours=5),
            failed_attempts=2,
        )

        worker = _make_worker(test_db, test_settings)
        await worker._restore_ip_ban_state()

        # 熔断状态必须原样恢复为 FULLY_BANNED，不能被当成过期回到 NORMAL
        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.FULLY_BANNED

        task = Task(
            id="task-1",
            video_id="video-1",
            video_url="https://youtube.com/watch?v=video-1",
            status=TaskStatus.PENDING,
            include_audio=True,
            include_transcript=True,
            priority=TaskPriority.NORMAL,
            created_at=datetime.now(timezone.utc),
        )

        decision = await worker._check_ip_ban_and_decide(task)

        # 必须走探测分支：decision 不为 None，且 is_probe=True。
        # 如果重启时把探测窗口已到误判为"已恢复"，这里会是 None（全速放行）。
        assert decision is not None
        assert decision.action == "execute"
        assert decision.is_probe is True

    @pytest.mark.asyncio
    async def test_restore_persisted_state_is_idempotent(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        P2 回归测试：restore_persisted_state() 必须幂等。

        背景：main.py 的 lifespan 现在会在 asyncio.create_task(start())
        之前显式 await 调用一次 restore_persisted_state()（修复 startup
        通知读到恢复前状态的 bug）；start() 内部又保留了同一方法的兜底
        调用（应对 worker 脱离 main.py 编排被单独启动的场景）。如果这两次
        调用都真正执行恢复动作，IPBanCircuitBreaker.restore_state() 会
        无条件触发 "restored" 状态变更事件（不像 reset_to_normal 那样有
        old_level 守卫），导致 ip_ban_history 表里多出一条重复记录。

        这里直接模拟两次调用（对应"main.py 显式调用" + "start() 内部兜底
        调用"），断言恢复后的熔断级别正确、且 ip_ban_history 里
        event_type='restored' 的记录只有一条。
        """
        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.FULLY_BANNED,
            banned_at=datetime.now() - timedelta(minutes=5),
            last_attempt_at=None,
            failed_attempts=1,
        )

        worker = _make_worker(test_db, test_settings)

        # 第一次调用：模拟 main.py 在 create_task(start()) 之前的显式调用。
        await worker.restore_persisted_state()
        # 第二次调用：模拟 start() 内部保留的兜底调用，理应是幂等 no-op。
        await worker.restore_persisted_state()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.FULLY_BANNED

        cursor = await test_db.execute(
            "SELECT COUNT(*) as cnt FROM ip_ban_history WHERE event_type = 'restored'"
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["cnt"] == 1, (
            "restore_persisted_state() 重复调用不应重复写入 ip_ban_history "
            "的 restored 记录（幂等保护失效）"
        )

    @pytest.mark.asyncio
    async def test_restore_persisted_state_retries_after_transient_db_error(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        P2 回归测试：幂等 flag 只能在 load_ip_ban_state() 真正读取成功后置位。

        背景：如果 flag 在调用 load_ip_ban_state() 之前（或不区分成功/失败）
        就置位，一旦 lifespan 首次恢复时数据库发生瞬时错误，
        _restore_ip_ban_state() 捕获异常后 fail-open 直接返回，但 flag
        已经变成 True —— start() 内部的兜底恢复调用会被幂等保护挡掉，
        变成 no-op，进程整个生命周期停留在 NORMAL，恰好在存在持久化熔断
        时失去保护。

        这里模拟：第一次 load 抛数据库异常，第二次 load 正常返回
        FULLY_BANNED，断言第二次调用（对应 start() 的兜底调用）真正
        执行了恢复。
        """
        # 预先写入一条 FULLY_BANNED 状态，供“第二次真正读取”时恢复。
        await test_db.save_ip_ban_state(
            current_level=IPBanLevel.FULLY_BANNED,
            banned_at=datetime.now() - timedelta(minutes=5),
            last_attempt_at=None,
            failed_attempts=1,
        )

        worker = _make_worker(test_db, test_settings)

        original_load = test_db.load_ip_ban_state
        call_count = 0

        async def flaky_load() -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated transient db error")
            return await original_load()

        worker.db.load_ip_ban_state = flaky_load  # type: ignore[method-assign]

        # 第一次调用：模拟 main.py 在 create_task(start()) 之前的显式调用，
        # 数据库读取失败，fail-open：不应恢复，也不应置位幂等 flag。
        await worker.restore_persisted_state()
        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.NORMAL
        assert worker._ip_ban_state_restored is False

        # 第二次调用：模拟 start() 内部的兜底调用，此时数据库读取应该
        # 成功并真正恢复出持久化的熔断状态（而不是被幂等保护挡成 no-op）。
        await worker.restore_persisted_state()

        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.FULLY_BANNED
        assert worker._ip_ban_state_restored is True
        assert call_count == 2, "第二次调用应该真正触发了一次新的数据库读取"

    @pytest.mark.asyncio
    async def test_restore_persisted_state_no_retry_after_successful_none_read(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        读到 None（数据库连状态行都没有）属于“确认无需恢复”的成功读取，
        与“读取失败”必须区分：flag 应该照常置位，第二次调用不应该
        再次查库（幂等保持）。
        """
        await test_db.execute("DELETE FROM ip_ban_status WHERE id = 1")
        worker = _make_worker(test_db, test_settings)

        original_load = test_db.load_ip_ban_state
        call_count = 0

        async def counting_load() -> Any:
            nonlocal call_count
            call_count += 1
            return await original_load()

        worker.db.load_ip_ban_state = counting_load  # type: ignore[method-assign]

        await worker.restore_persisted_state()
        assert call_count == 1
        assert worker._ip_ban_state_restored is True
        assert worker.ip_ban_breaker.get_current_level() == IPBanLevel.NORMAL

        # 第二次调用应被幂等 flag 拦截，不应再次触发数据库读取。
        await worker.restore_persisted_state()
        assert call_count == 1


# ==================== FULLY_BANNED delay_seconds 配置化与口径修正测试 ====================
# 背景（PR #6 CI gate / Codex review 发现的 major 问题）：
# _check_ip_ban_and_decide() 的 FULLY_BANNED 分支曾经硬编码
# min_wait = 3600 if task_type == "transcript_only" else 7200，运维调整
# IP_BAN_MIN_WAIT_BEFORE_RETRY 对全局熔断的探测放行完全不生效；同时
# delay_seconds=max(remaining, min_wait) 是一个掩盖 remaining 与放行判定口径
# 错位的补丁——get_remaining_time() 原先按熔断器基础 min_wait 计算剩余时间，
# 与 should_allow_attempt 用的 min_wait_override 不一致，即使探测窗口只剩几
# 分钟，任务也会被推迟整整一个 min_wait 窗口。下面的测试锁死修复后的行为：
# min_wait 来自 settings.ip_ban_min_wait_before_retry（音频/混合为其
# FULL_BAN_AUDIO_MIN_WAIT_MULTIPLIER 倍），delay_seconds 是与放行判定同一
# 口径的 remaining（带 MIN_DELAY_SECONDS_FLOOR 下限）。


def _make_probe_task(task_id: str, *, include_audio: bool, include_transcript: bool) -> Task:
    """构造一个用于探测判定测试的最小任务。"""
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


class TestFullyBannedDelaySeconds:
    """测试 FULLY_BANNED 分支的 min_wait 配置化与 delay_seconds 口径修正。"""

    @pytest.mark.asyncio
    async def test_custom_min_wait_gates_transcript_and_audio_probes(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        自定义 IP_BAN_MIN_WAIT_BEFORE_RETRY=600 时：字幕任务应在等待满 600s 后
        被放行作为探测，音频任务需要等满 2 倍（1200s）才被放行——修复前
        FULLY_BANNED 分支硬编码 3600/7200，调整这个配置对全局熔断完全不生效。
        """
        test_settings.ip_ban_min_wait_before_retry = 600
        test_settings.ip_ban_max_retry_interval = 60

        worker = _make_worker(test_db, test_settings)
        await worker.ip_ban_breaker.trigger_full_ban(reason="test")

        transcript_task = _make_probe_task(
            "transcript", include_audio=False, include_transcript=True
        )
        audio_task = _make_probe_task("audio", include_audio=True, include_transcript=False)

        # 刚触发熔断：字幕、音频都还没到各自的等待窗口，应该被延迟。
        decision = await worker._check_ip_ban_and_decide(transcript_task)
        assert decision is not None and decision.action == "delay"
        decision = await worker._check_ip_ban_and_decide(audio_task)
        assert decision is not None and decision.action == "delay"

        # 回拨到刚好等满 600s（字幕的 min_wait）：字幕应被放行，音频仍需等待
        # （音频门槛是 600 * FULL_BAN_AUDIO_MIN_WAIT_MULTIPLIER = 1200s）。
        worker.ip_ban_breaker.banned_at = datetime.now() - timedelta(seconds=600)

        decision = await worker._check_ip_ban_and_decide(transcript_task)
        assert decision is not None
        assert decision.action == "execute"
        assert decision.is_probe is True

        decision = await worker._check_ip_ban_and_decide(audio_task)
        assert decision is not None and decision.action == "delay"

        # 回拨到等满 1200s（600 * 2）：音频任务现在也应该被放行作为探测。
        worker.ip_ban_breaker.banned_at = datetime.now() - timedelta(seconds=600 * 2)

        decision = await worker._check_ip_ban_and_decide(audio_task)
        assert decision is not None
        assert decision.action == "execute"
        assert decision.is_probe is True

    @pytest.mark.asyncio
    async def test_audio_delay_seconds_reflects_remaining_not_full_window(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        FULLY_BANNED + 音频任务，已经等待 7100s（距 2×3600=7200s 的音频探测
        窗口还差 100s）时，delay_seconds 应该约等于 100s（允许几秒抖动/下限），
        而不是被拉回整个 min_wait（3600s）或 2×min_wait（7200s）窗口——旧代码
        的 max(remaining, min_wait) 补丁会让这里的 delay_seconds 至少是 7200。
        """
        test_settings.ip_ban_min_wait_before_retry = 3600
        test_settings.ip_ban_max_retry_interval = 60

        worker = _make_worker(test_db, test_settings)
        await worker.ip_ban_breaker.trigger_full_ban(reason="test")
        worker.ip_ban_breaker.banned_at = datetime.now() - timedelta(seconds=7100)

        audio_task = _make_probe_task("audio", include_audio=True, include_transcript=False)
        decision = await worker._check_ip_ban_and_decide(audio_task)

        assert decision is not None
        assert decision.action == "delay"
        # 允许调用耗时带来的几秒抖动，但必须远小于旧行为的 3600/7200 整窗口。
        assert 0 < decision.delay_seconds <= 110, (
            f"delay_seconds={decision.delay_seconds} 明显偏离剩余的约 100s，"
            "疑似又回退到 max(remaining, min_wait) 的整窗口补丁"
        )

    @pytest.mark.asyncio
    async def test_delay_seconds_floor_when_remaining_computed_zero_but_not_allowed(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        边界场景：get_remaining_time() 与 should_allow_attempt() 是两次独立
        调用，中间隔着几毫秒的执行耗时，理论上可能出现 remaining 已经被算成
        0，但 should_allow_attempt() 仍判定为不允许。直接 mock 这两个方法制造
        该边界，断言 delay_seconds 落到 MIN_DELAY_SECONDS_FLOOR 而不是 0——
        返回 0 会让任务立刻重新入队，形成没有实际退避的紧循环。
        """
        test_settings.ip_ban_min_wait_before_retry = 3600

        worker = _make_worker(test_db, test_settings)
        await worker.ip_ban_breaker.trigger_full_ban(reason="test")

        worker.ip_ban_breaker.should_allow_attempt = MagicMock(  # type: ignore[method-assign]
            return_value=(False, "Minimum wait not reached: 0 minutes remaining")
        )
        worker.ip_ban_breaker.get_remaining_time = MagicMock(return_value=0)  # type: ignore[method-assign]

        audio_task = _make_probe_task(
            "audio-edge", include_audio=True, include_transcript=False
        )
        decision = await worker._check_ip_ban_and_decide(audio_task)

        assert decision is not None
        assert decision.action == "delay"
        assert decision.delay_seconds == MIN_DELAY_SECONDS_FLOOR

    @pytest.mark.asyncio
    async def test_transcript_task_uses_base_min_wait_not_multiplied(
        self, test_db: Database, test_settings: Settings
    ) -> None:
        """
        字幕任务（transcript_only）不应被乘以 FULL_BAN_AUDIO_MIN_WAIT_MULTIPLIER，
        只有音频/混合任务才使用 2 倍等待时间——回归覆盖两个分支没有被写反。
        """
        test_settings.ip_ban_min_wait_before_retry = 3600
        assert FULL_BAN_AUDIO_MIN_WAIT_MULTIPLIER == 2

        worker = _make_worker(test_db, test_settings)
        await worker.ip_ban_breaker.trigger_full_ban(reason="test")
        worker.ip_ban_breaker.banned_at = datetime.now() - timedelta(seconds=3500)

        transcript_task = _make_probe_task(
            "transcript-only", include_audio=False, include_transcript=True
        )
        decision = await worker._check_ip_ban_and_decide(transcript_task)

        assert decision is not None
        assert decision.action == "delay"
        # 距字幕自己的 min_wait(3600) 只差 100s，不是距 2 倍窗口(7200)差 3700s。
        assert 0 < decision.delay_seconds <= 110


# ==================== Settings 配置项测试 ====================


class TestIPBanSettings:
    """测试 Settings 新增的 IP 熔断等待参数配置。"""

    def test_defaults(self, tmp_path: Path) -> None:
        settings = Settings(
            _env_file=None,
            api_key="test-key",
            data_dir=tmp_path,
        )

        assert settings.ip_ban_min_wait_before_retry == 3600
        assert settings.ip_ban_max_retry_interval == 1800

    def test_env_override(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setenv("IP_BAN_MIN_WAIT_BEFORE_RETRY", "7200")
        monkeypatch.setenv("IP_BAN_MAX_RETRY_INTERVAL", "900")

        settings = Settings(
            _env_file=None,
            api_key="test-key",
            data_dir=tmp_path,
        )

        assert settings.ip_ban_min_wait_before_retry == 7200
        assert settings.ip_ban_max_retry_interval == 900

    def test_worker_uses_settings_values_for_breaker(
        self, tmp_path: Path
    ) -> None:
        """worker 构造熔断器时应使用 settings 里的等待参数，而非硬编码。"""
        settings = Settings(
            _env_file=None,
            api_key="test-key",
            data_dir=tmp_path,
            ip_ban_min_wait_before_retry=111,
            ip_ban_max_retry_interval=222,
        )

        db = MagicMock()
        worker = DownloadWorker(
            db=db,
            settings=settings,
            task_service=MagicMock(),
            file_service=MagicMock(),
            callback_service=MagicMock(),
            notify_service=AsyncMock(),
            downloader_manager=MagicMock(),
        )

        assert worker.ip_ban_breaker.MIN_WAIT_BEFORE_RETRY == 111
        assert worker.ip_ban_breaker.MAX_RETRY_INTERVAL == 222


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
