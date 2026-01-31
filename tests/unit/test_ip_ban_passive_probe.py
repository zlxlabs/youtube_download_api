"""
测试被动探测型 IP 熔断机制。

验证核心功能：
1. 分级熔断（音频/全局）
2. 被动探测（利用用户任务）
3. 自动恢复
4. 部分成功
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.ip_ban_breaker import IPBanCircuitBreaker
from src.core.ip_ban_models import ExecutionDecision, IPBanLevel
from src.db.models import ErrorCode
from src.downloaders.exceptions import DownloaderError
from src.downloaders.models import DownloaderResult, VideoMetadata


class TestIPBanCircuitBreaker:
    """测试 IP 熔断器核心功能"""

    def test_initial_state_is_normal(self):
        """测试初始状态为正常"""
        breaker = IPBanCircuitBreaker()
        assert breaker.get_current_level() == IPBanLevel.NORMAL
        assert breaker.get_time_since_ban() == 0
        assert breaker.get_time_since_last_attempt() is None

    @pytest.mark.asyncio
    async def test_trigger_audio_ban(self):
        """测试触发音频熔断"""
        breaker = IPBanCircuitBreaker()

        await breaker.trigger_audio_ban(reason="HTTP 403 on audio download")

        assert breaker.get_current_level() == IPBanLevel.AUDIO_BANNED
        assert breaker.banned_at is not None
        assert breaker.get_time_since_ban() >= 0

    @pytest.mark.asyncio
    async def test_trigger_full_ban(self):
        """测试触发全局熔断"""
        breaker = IPBanCircuitBreaker()

        await breaker.trigger_full_ban(reason="HTTP 403 on transcript")

        assert breaker.get_current_level() == IPBanLevel.FULLY_BANNED
        assert breaker.banned_at is not None

    @pytest.mark.asyncio
    async def test_upgrade_to_full_ban(self):
        """测试从音频熔断升级到全局熔断"""
        breaker = IPBanCircuitBreaker()

        # 先触发音频熔断
        await breaker.trigger_audio_ban()
        assert breaker.get_current_level() == IPBanLevel.AUDIO_BANNED

        # 升级到全局熔断
        await breaker.upgrade_to_full_ban()
        assert breaker.get_current_level() == IPBanLevel.FULLY_BANNED

    @pytest.mark.asyncio
    async def test_downgrade_to_audio_ban(self):
        """测试从全局熔断降级到音频熔断"""
        breaker = IPBanCircuitBreaker()

        # 先触发全局熔断
        await breaker.trigger_full_ban()
        assert breaker.get_current_level() == IPBanLevel.FULLY_BANNED

        # 降级到音频熔断
        await breaker.downgrade_to_audio_ban()
        assert breaker.get_current_level() == IPBanLevel.AUDIO_BANNED

    @pytest.mark.asyncio
    async def test_reset_to_normal(self):
        """测试恢复正常状态"""
        breaker = IPBanCircuitBreaker()

        # 触发熔断
        await breaker.trigger_audio_ban()
        assert breaker.get_current_level() == IPBanLevel.AUDIO_BANNED

        # 恢复
        await breaker.reset_to_normal()
        assert breaker.get_current_level() == IPBanLevel.NORMAL
        assert breaker.banned_at is None

    def test_should_allow_attempt_too_soon(self):
        """测试等待时间不足时不允许尝试"""
        breaker = IPBanCircuitBreaker(min_wait_before_retry=3600)  # 60 分钟

        # 模拟刚刚熔断（5 分钟前）
        breaker.current_level = IPBanLevel.AUDIO_BANNED
        breaker.banned_at = datetime.now() - timedelta(minutes=5)

        allowed, reason = breaker.should_allow_attempt("audio")
        assert not allowed
        assert "remaining" in reason

    def test_should_allow_attempt_after_min_wait(self):
        """测试达到最小等待时间后允许尝试"""
        breaker = IPBanCircuitBreaker(min_wait_before_retry=60)  # 1 分钟

        # 模拟 2 分钟前熔断
        breaker.current_level = IPBanLevel.AUDIO_BANNED
        breaker.banned_at = datetime.now() - timedelta(minutes=2)

        allowed, reason = breaker.should_allow_attempt("audio")
        assert allowed

    def test_get_estimated_recovery_time(self):
        """测试预计恢复时间计算"""
        breaker = IPBanCircuitBreaker(min_wait_before_retry=3600)

        # 触发熔断
        breaker.current_level = IPBanLevel.AUDIO_BANNED
        breaker.banned_at = datetime.now()

        recovery_time = breaker.get_estimated_recovery_time()
        assert recovery_time is not None

        # 应该是大约 60 分钟后
        expected = datetime.now() + timedelta(seconds=3600)
        assert abs((recovery_time - expected).total_seconds()) < 10


class TestPartialSuccess:
    """测试部分成功功能"""

    def test_downloader_result_partial_success(self):
        """测试 DownloaderResult 部分成功标记"""
        metadata = VideoMetadata(video_id="test123", title="Test Video")

        result = DownloaderResult(
            success=True,
            partial_success=True,
            downloader="ytdlp",
            video_metadata=metadata,
            audio_path=None,  # 音频失败
            transcript_path="/tmp/test.srt",  # 字幕成功
            has_transcript=True,
            audio_error="IP_BANNED_403: HTTP 403 Forbidden",
            audio_error_code="RATE_LIMITED",
            failure_details={
                "audio": {
                    "requested": True,
                    "success": False,
                    "error": "IP_BANNED_403: HTTP 403 Forbidden",
                },
                "transcript": {
                    "requested": True,
                    "success": True,
                    "error": None,
                },
            },
        )

        assert result.partial_success is True
        assert result.audio_path is None
        assert result.transcript_path is not None
        assert result.audio_error is not None
        assert result.failure_details is not None


class TestExecutionDecision:
    """测试执行决策"""

    def test_execution_decision_execute(self):
        """测试执行决策"""
        decision = ExecutionDecision(
            action="execute",
            reason="Normal state",
            is_probe=False,
        )

        assert decision.action == "execute"
        assert not decision.is_probe

    def test_execution_decision_delay(self):
        """测试延迟决策"""
        decision = ExecutionDecision(
            action="delay",
            reason="IP ban active",
            delay_seconds=1800,
        )

        assert decision.action == "delay"
        assert decision.delay_seconds == 1800

    def test_execution_decision_probe(self):
        """测试探测决策"""
        decision = ExecutionDecision(
            action="execute",
            reason="Recovery probe allowed",
            is_probe=True,
        )

        assert decision.action == "execute"
        assert decision.is_probe is True


class TestDownloaderErrorOperation:
    """测试 DownloaderError 的 operation 字段"""

    def test_error_with_audio_operation(self):
        """测试音频操作错误"""
        error = DownloaderError(
            message="HTTP 403 Forbidden",
            error_code=ErrorCode.RATE_LIMITED,
            downloader="ytdlp",
            http_status_code=403,
            operation="audio",
        )

        assert error.operation == "audio"
        assert error.http_status_code == 403

    def test_error_with_transcript_operation(self):
        """测试字幕操作错误"""
        error = DownloaderError(
            message="HTTP 403 Forbidden",
            error_code=ErrorCode.RATE_LIMITED,
            downloader="ytdlp",
            http_status_code=403,
            operation="transcript",
        )

        assert error.operation == "transcript"


class TestIPBanState:
    """测试 IP 熔断状态快照"""

    @pytest.mark.asyncio
    async def test_get_state_snapshot(self):
        """测试获取状态快照"""
        breaker = IPBanCircuitBreaker()

        # 触发熔断
        await breaker.trigger_audio_ban()

        # 获取状态快照
        state = breaker.get_state()

        assert state.current_level == IPBanLevel.AUDIO_BANNED
        assert state.banned_at is not None
        assert state.time_since_ban is not None
        assert state.time_since_ban >= 0

    @pytest.mark.asyncio
    async def test_state_to_dict(self):
        """测试状态快照转换为字典"""
        breaker = IPBanCircuitBreaker()

        await breaker.trigger_full_ban()
        state = breaker.get_state()

        state_dict = state.to_dict()

        assert state_dict["current_level"] == "fully_banned"
        assert "banned_at" in state_dict
        assert "time_since_ban" in state_dict


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
