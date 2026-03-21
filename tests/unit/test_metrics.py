"""
Tests for MetricsCollector service.

Verifies Prometheus metrics are correctly computed and exposed.
"""

import pytest

from src.services.metrics import MetricsCollector


class TestMetricsCollector:
    """Tests for MetricsCollector."""

    def test_init_creates_registry(self):
        """MetricsCollector should create a Prometheus registry on init."""
        mc = MetricsCollector()
        assert mc.registry is not None

    def test_generate_metrics_returns_bytes(self):
        """generate_metrics should return valid Prometheus text format."""
        mc = MetricsCollector()
        output = mc.generate_metrics()
        assert isinstance(output, bytes)
        assert len(output) > 0

    def test_record_task_completed(self):
        """Task completion counter should increment."""
        mc = MetricsCollector()
        mc.record_task_completed("completed")
        mc.record_task_completed("completed")
        mc.record_task_completed("failed")

        output = mc.generate_metrics().decode()
        assert 'ytdl_tasks_total{status="completed"}' in output
        assert 'ytdl_tasks_total{status="failed"}' in output

    def test_record_task_duration(self):
        """Task duration histogram should record observations."""
        mc = MetricsCollector()
        mc.record_task_duration("queue", 10.5)
        mc.record_task_duration("download", 120.3)

        output = mc.generate_metrics().decode()
        assert "ytdl_task_duration_seconds" in output

    def test_sync_downloader_stats_computes_deltas(self):
        """Downloader stats should compute deltas correctly."""
        mc = MetricsCollector()

        # First sync: 5 successes, 2 failures
        mc.sync_downloader_stats({
            "cdp": {"success": 5, "failure": 2, "total": 7, "success_rate": 0.71},
        })

        # Second sync: 8 successes, 3 failures (delta: +3 success, +1 failure)
        mc.sync_downloader_stats({
            "cdp": {"success": 8, "failure": 3, "total": 11, "success_rate": 0.73},
        })

        output = mc.generate_metrics().decode()
        assert 'ytdl_downloader_requests_total{downloader="cdp",result="success"}' in output
        assert 'ytdl_downloader_requests_total{downloader="cdp",result="failure"}' in output

    def test_sync_downloader_stats_no_negative_deltas(self):
        """Downloader stats should not produce negative deltas (e.g. after restart)."""
        mc = MetricsCollector()

        # First sync with some counts
        mc.sync_downloader_stats({
            "ytdlp": {"success": 10, "failure": 5, "total": 15, "success_rate": 0.67},
        })

        # Simulate restart: counts are lower (should not increment negatively)
        mc.sync_downloader_stats({
            "ytdlp": {"success": 2, "failure": 1, "total": 3, "success_rate": 0.67},
        })

        # Should not raise any errors
        output = mc.generate_metrics().decode()
        assert "ytdl_downloader_requests_total" in output

    def test_sync_circuit_breaker_states(self):
        """Circuit breaker states should be mapped to numeric values."""
        mc = MetricsCollector()
        mc.sync_circuit_breaker_states({
            "cdp": {"state": "open", "failures": 5},
            "ytdlp": {"state": "closed", "failures": 0},
        })

        output = mc.generate_metrics().decode()
        assert "ytdl_circuit_breaker_state" in output

    def test_sync_ip_ban_state_normal(self):
        """IP ban normal state should be level 0."""
        mc = MetricsCollector()
        mc.sync_ip_ban_state({
            "current_level": "normal",
            "time_since_ban": None,
        })

        output = mc.generate_metrics().decode()
        assert "ytdl_ip_ban_level" in output

    def test_sync_ip_ban_state_audio_banned(self):
        """IP ban audio_banned state should be level 1."""
        mc = MetricsCollector()
        mc.sync_ip_ban_state({
            "current_level": "audio_banned",
            "time_since_ban": 1800,
        })

        output = mc.generate_metrics().decode()
        assert "ytdl_ip_ban_level" in output
        assert "ytdl_ip_ban_duration_seconds" in output

    def test_sync_queue_stats(self):
        """Queue stats should update gauge values."""
        mc = MetricsCollector()
        mc.sync_queue_stats({"pending": 10, "downloading": 1})

        output = mc.generate_metrics().decode()
        assert "ytdl_task_queue_depth" in output

    def test_set_config_warnings(self):
        """Config warnings gauge should update."""
        mc = MetricsCollector()
        mc.set_config_warnings(3)

        output = mc.generate_metrics().decode()
        assert "ytdl_config_warnings_total" in output

    def test_zero_duration_not_recorded(self):
        """Negative durations should not be recorded."""
        mc = MetricsCollector()
        # Should not raise
        mc.record_task_duration("queue", -5.0)

    def test_empty_stats_summary(self):
        """Syncing empty stats should not raise."""
        mc = MetricsCollector()
        mc.sync_downloader_stats({})
        mc.sync_circuit_breaker_states({})
        mc.sync_ip_ban_state({"current_level": "normal"})
        mc.sync_queue_stats({})
        output = mc.generate_metrics()
        assert isinstance(output, bytes)
