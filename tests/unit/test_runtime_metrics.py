"""D1+M3: 长期运行可观测性指标。

新增三组 gauge 指标：
- ytdl_process_rss_bytes: 进程 RSS（整体内存上限告警）
- ytdl_dict_size{name}: 关键追踪 dict 的大小（防累积）
- ytdl_child_process_count{type}: 子进程计数（防 node driver / yt-dlp 泄漏回归）
"""

from __future__ import annotations

import pytest

from src.services.metrics import MetricsCollector


@pytest.fixture
def collector() -> MetricsCollector:
    return MetricsCollector()


def _metric_present(output: bytes, metric_name: str) -> bool:
    return any(
        line.startswith(metric_name) and not line.startswith(f"# ")
        for line in output.decode("utf-8").splitlines()
    )


def _metric_value(output: bytes, metric_name: str) -> float | None:
    """从 Prometheus 文本格式中提取首个匹配指标的数值。"""
    for line in output.decode("utf-8").splitlines():
        if line.startswith("#"):
            continue
        if line.startswith(metric_name):
            return float(line.rsplit(" ", 1)[-1])
    return None


def test_process_rss_metric_registered(collector: MetricsCollector):
    """ytdl_process_rss_bytes 必须存在并可设置。"""
    assert hasattr(collector, "process_rss_bytes"), "需要 process_rss_bytes Gauge"
    collector.sync_runtime_state()
    output = collector.generate_metrics()
    assert _metric_present(output, "ytdl_process_rss_bytes"), (
        "ytdl_process_rss_bytes 应在 /metrics 输出中"
    )


def test_process_rss_value_positive(collector: MetricsCollector):
    """RSS 实际值应为正数（当前进程肯定占用内存）。"""
    collector.sync_runtime_state()
    output = collector.generate_metrics()
    rss = _metric_value(output, "ytdl_process_rss_bytes")
    assert rss is not None
    assert rss > 0, f"进程 RSS 应大于 0，当前={rss}"


def test_dict_size_metric_registered(collector: MetricsCollector):
    """ytdl_dict_size{name="..."} 必须存在。"""
    assert hasattr(collector, "dict_size"), "需要 dict_size 带 name label 的 Gauge"
    collector.sync_runtime_state()
    output = collector.generate_metrics()
    assert _metric_present(output, "ytdl_dict_size"), "ytdl_dict_size 必须输出"


def test_dict_size_tracks_notification_cache(collector: MetricsCollector):
    """notification_cache 标签必须存在，反映 CDPDownloader._notification_cache 长度。"""
    from src.downloaders.cdp.downloader import CDPDownloader

    CDPDownloader._notification_cache.clear()
    CDPDownloader._notification_cache["k1"] = 1.0
    CDPDownloader._notification_cache["k2"] = 2.0

    collector.sync_runtime_state()
    output = collector.generate_metrics()

    # 找带 name="notification_cache" 的行
    decoded = output.decode("utf-8")
    matches = [
        line for line in decoded.splitlines()
        if 'ytdl_dict_size{name="notification_cache"}' in line and not line.startswith("#")
    ]
    assert matches, "应输出 name=notification_cache 的 dict_size"
    value = float(matches[0].rsplit(" ", 1)[-1])
    assert value == 2.0, f"应反映 _notification_cache 的当前长度，当前={value}"

    CDPDownloader._notification_cache.clear()


def test_dict_size_tracks_task_queue(collector: MetricsCollector):
    """task_queue 标签必须存在（M3 队列深度可观测性）。"""
    collector.sync_runtime_state(task_queue_size=42)
    output = collector.generate_metrics()
    decoded = output.decode("utf-8")
    matches = [
        line for line in decoded.splitlines()
        if 'ytdl_dict_size{name="task_queue"}' in line and not line.startswith("#")
    ]
    assert matches
    assert float(matches[0].rsplit(" ", 1)[-1]) == 42.0


def test_child_process_count_metric_registered(collector: MetricsCollector):
    """ytdl_child_process_count{type="..."} 必须存在。"""
    assert hasattr(collector, "child_process_count"), (
        "需要 child_process_count 带 type label 的 Gauge"
    )
    collector.sync_runtime_state()
    output = collector.generate_metrics()
    assert _metric_present(output, "ytdl_child_process_count"), (
        "ytdl_child_process_count 必须输出"
    )


def test_child_process_count_has_known_types(collector: MetricsCollector):
    """至少包含 yt-dlp / node / chrome / other 四个 type label。"""
    collector.sync_runtime_state()
    decoded = collector.generate_metrics().decode("utf-8")
    for typ in ("yt-dlp", "node", "chrome", "other"):
        assert f'type="{typ}"' in decoded, f"缺少 type={typ} 的 child_process_count"


def test_sync_runtime_state_does_not_raise(collector: MetricsCollector):
    """sync_runtime_state 不能抛异常（psutil 异常应内部吞掉）。"""
    # 多次调用应该幂等
    collector.sync_runtime_state()
    collector.sync_runtime_state()
    collector.sync_runtime_state(task_queue_size=0)
