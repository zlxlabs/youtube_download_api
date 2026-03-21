"""
Prometheus metrics collector module.

Aggregates metrics from various components (DownloaderManager, IPBanCircuitBreaker,
TaskService) and exposes them via prometheus-client for /metrics endpoint scraping.
"""

from typing import Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from src.utils.logger import logger


class MetricsCollector:
    """
    Prometheus metrics aggregator.

    Collects metrics from:
    - DownloaderManager: downloader success/failure rates
    - IPBanCircuitBreaker: ban state
    - Database: queue depth, task counts
    - Worker: task lifecycle timing

    All metrics use the 'ytdl_' prefix for namespace isolation.
    """

    def __init__(self) -> None:
        """Initialize Prometheus metrics registry and metric definitions."""
        self.registry = CollectorRegistry()

        # -- Task metrics --
        self.tasks_total = Counter(
            "ytdl_tasks_total",
            "Total tasks by final status",
            ["status"],
            registry=self.registry,
        )

        self.task_queue_depth = Gauge(
            "ytdl_task_queue_depth",
            "Current number of pending tasks in queue",
            ["priority"],
            registry=self.registry,
        )

        self.task_duration_seconds = Histogram(
            "ytdl_task_duration_seconds",
            "Task processing duration by phase",
            ["phase"],
            buckets=[5, 15, 30, 60, 120, 300, 600, 1200],
            registry=self.registry,
        )

        # -- Downloader metrics --
        self.downloader_requests_total = Counter(
            "ytdl_downloader_requests_total",
            "Total downloader requests by downloader and result",
            ["downloader", "result"],
            registry=self.registry,
        )

        # -- Circuit breaker metrics --
        self.circuit_breaker_state = Gauge(
            "ytdl_circuit_breaker_state",
            "Downloader circuit breaker state (0=closed, 1=half_open, 2=open)",
            ["downloader"],
            registry=self.registry,
        )

        # -- IP ban metrics --
        self.ip_ban_level = Gauge(
            "ytdl_ip_ban_level",
            "IP ban level (0=normal, 1=audio_banned, 2=fully_banned)",
            registry=self.registry,
        )

        self.ip_ban_duration_seconds = Gauge(
            "ytdl_ip_ban_duration_seconds",
            "Seconds since IP ban started (0 if not banned)",
            registry=self.registry,
        )

        # -- Callback metrics --
        self.callback_total = Counter(
            "ytdl_callback_total",
            "Total webhook callbacks by status",
            ["status"],
            registry=self.registry,
        )

        # -- Config warnings --
        self.config_warnings_total = Gauge(
            "ytdl_config_warnings_total",
            "Number of configuration warnings detected at startup",
            registry=self.registry,
        )

        # Internal tracking: last synced downloader stats snapshot
        # Used to compute deltas for counter increments
        self._last_downloader_stats: dict[str, dict[str, int]] = {}

        logger.info("MetricsCollector initialized")

    def sync_downloader_stats(self, stats_summary: dict[str, dict]) -> None:
        """
        Sync downloader stats from DownloaderManager.get_stats_summary().

        Computes deltas from last sync to increment Prometheus counters correctly.
        Called periodically (e.g. before /metrics scrape).

        Args:
            stats_summary: { downloader_name: { success, failure, total, success_rate } }
        """
        for downloader, data in stats_summary.items():
            prev = self._last_downloader_stats.get(downloader, {"success": 0, "failure": 0})

            success_delta = data.get("success", 0) - prev.get("success", 0)
            failure_delta = data.get("failure", 0) - prev.get("failure", 0)

            if success_delta > 0:
                self.downloader_requests_total.labels(
                    downloader=downloader, result="success"
                ).inc(success_delta)
            if failure_delta > 0:
                self.downloader_requests_total.labels(
                    downloader=downloader, result="failure"
                ).inc(failure_delta)

            self._last_downloader_stats[downloader] = {
                "success": data.get("success", 0),
                "failure": data.get("failure", 0),
            }

    def sync_circuit_breaker_states(self, cb_states: dict[str, dict]) -> None:
        """
        Sync circuit breaker states from DownloaderManager.get_circuit_breaker_states().

        Args:
            cb_states: { downloader_name: { state: "closed"|"half_open"|"open", ... } }
        """
        state_map = {"closed": 0, "half_open": 1, "open": 2}
        for name, state_info in cb_states.items():
            state_str = state_info.get("state", "closed")
            self.circuit_breaker_state.labels(downloader=name).set(
                state_map.get(state_str, 0)
            )

    def sync_ip_ban_state(self, ban_state_dict: dict) -> None:
        """
        Sync IP ban state from IPBanCircuitBreaker.get_state().to_dict().

        Args:
            ban_state_dict: Serialized IPBanState dict
        """
        level_map = {"normal": 0, "audio_banned": 1, "fully_banned": 2}
        level_str = ban_state_dict.get("current_level", "normal")
        self.ip_ban_level.set(level_map.get(level_str, 0))

        time_since_ban = ban_state_dict.get("time_since_ban")
        self.ip_ban_duration_seconds.set(time_since_ban if time_since_ban else 0)

    def sync_queue_stats(self, queue_stats: dict) -> None:
        """
        Sync queue statistics from Database.get_queue_stats().

        Args:
            queue_stats: { pending: int, downloading: int }
        """
        self.task_queue_depth.labels(priority="pending").set(
            queue_stats.get("pending", 0)
        )
        self.task_queue_depth.labels(priority="downloading").set(
            queue_stats.get("downloading", 0)
        )

    def record_task_completed(self, status: str) -> None:
        """Record a task completion with its final status."""
        self.tasks_total.labels(status=status).inc()

    def record_task_duration(self, phase: str, duration_seconds: float) -> None:
        """
        Record task duration for a specific phase.

        Args:
            phase: "queue" (time in queue) or "download" (download time)
            duration_seconds: Duration in seconds
        """
        if duration_seconds >= 0:
            self.task_duration_seconds.labels(phase=phase).observe(duration_seconds)

    def record_callback(self, status: str) -> None:
        """Record a webhook callback result."""
        self.callback_total.labels(status=status).inc()

    def set_config_warnings(self, count: int) -> None:
        """Set the number of config warnings."""
        self.config_warnings_total.set(count)

    def generate_metrics(self) -> bytes:
        """
        Generate Prometheus text format metrics output.

        Returns:
            bytes: Prometheus exposition format text
        """
        return generate_latest(self.registry)
