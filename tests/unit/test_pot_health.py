"""PotProviderHealthTracker 单元测试。"""

import time
from unittest.mock import patch

from src.downloaders.pot_health import PotProviderHealthTracker


class TestPotProviderHealthTracker:
    """PotProviderHealthTracker 基本功能测试。"""

    def setup_method(self):
        """每个测试前重置单例。"""
        PotProviderHealthTracker.reset_instance()

    def test_initial_state_is_available(self):
        """初始状态应为可用。"""
        tracker = PotProviderHealthTracker(failure_threshold=2)
        assert tracker.is_available() is True
        assert tracker.consecutive_failures == 0

    def test_single_failure_still_available(self):
        """单次失败不应标记为不可用（未达阈值）。"""
        tracker = PotProviderHealthTracker(failure_threshold=2)
        tracker.record_failure()
        assert tracker.is_available() is True

    def test_threshold_failures_marks_unavailable(self):
        """连续失败达到阈值后应标记为不可用。"""
        tracker = PotProviderHealthTracker(failure_threshold=2, cooldown_seconds=300)
        tracker.record_failure()
        tracker.record_failure()
        assert tracker.is_available() is False

    def test_success_resets_failures(self):
        """成功请求应重置失败计数。"""
        tracker = PotProviderHealthTracker(failure_threshold=2)
        tracker.record_failure()
        tracker.record_success()
        assert tracker.consecutive_failures == 0
        assert tracker.is_available() is True

    def test_success_after_unavailable_recovers(self):
        """不可用后成功应恢复为可用。"""
        tracker = PotProviderHealthTracker(failure_threshold=2, cooldown_seconds=0)
        tracker.record_failure()
        tracker.record_failure()
        # 冷却期为 0，立即允许探测
        assert tracker.is_available() is True
        tracker.record_success()
        assert tracker.is_available() is True
        assert tracker.consecutive_failures == 0

    def test_cooldown_expiry_allows_probe(self):
        """冷却期过后应允许重新探测。"""
        tracker = PotProviderHealthTracker(failure_threshold=2, cooldown_seconds=1)
        tracker.record_failure()
        tracker.record_failure()
        assert tracker.is_available() is False

        # 模拟时间推进
        with patch("src.downloaders.pot_health.time") as mock_time:
            # 设置 monotonic 返回值：当前时间 + 冷却期
            mock_time.monotonic.return_value = time.monotonic() + 2
            assert tracker.is_available() is True

    def test_singleton_returns_same_instance(self):
        """单例模式应返回相同实例。"""
        instance1 = PotProviderHealthTracker.get_instance()
        instance2 = PotProviderHealthTracker.get_instance()
        assert instance1 is instance2

    def test_reset_instance_creates_new(self):
        """reset_instance 后应创建新实例。"""
        instance1 = PotProviderHealthTracker.get_instance()
        PotProviderHealthTracker.reset_instance()
        instance2 = PotProviderHealthTracker.get_instance()
        assert instance1 is not instance2

    def test_failure_after_cooldown_resets_timer(self):
        """冷却期后再次失败应重置冷却计时器。"""
        tracker = PotProviderHealthTracker(failure_threshold=1, cooldown_seconds=300)
        tracker.record_failure()
        assert tracker.is_available() is False
        # 再记录一次失败（探测失败），仍然不可用
        tracker.record_failure()
        assert tracker.is_available() is False
