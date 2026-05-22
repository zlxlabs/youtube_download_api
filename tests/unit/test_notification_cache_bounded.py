"""H1: 验证 _notification_cache 是有界的 TTLCache，不会无界累积。

背景：原实现是 Dict[str, float]，按 hash(error) 索引，长期运行下条目只增不减。
修复：替换为 cachetools.TTLCache(maxsize=512, ttl=...)。
"""

from __future__ import annotations

import time

import pytest
from cachetools import TTLCache

from src.downloaders.cdp.downloader import CDPDownloader


def test_notification_cache_is_ttl_cache_instance():
    """通知缓存必须是 TTLCache，不能再是普通 dict。"""
    assert isinstance(
        CDPDownloader._notification_cache, TTLCache
    ), "应使用 cachetools.TTLCache 以确保自动过期与大小限制"


def test_notification_cache_has_size_cap():
    """maxsize 必须有限且 ≤ 1024，避免无界增长。"""
    maxsize = CDPDownloader._notification_cache.maxsize
    assert 0 < maxsize <= 1024, f"maxsize 应在 (0, 1024]，当前为 {maxsize}"


def test_notification_cache_has_positive_ttl():
    """TTL 必须为正数。"""
    ttl = CDPDownloader._notification_cache.ttl
    assert ttl > 0, f"ttl 应为正数，当前为 {ttl}"


def test_notification_cache_rejects_overflow():
    """写入超过 maxsize 的条目后，size 不会超过 maxsize。"""
    cache = CDPDownloader._notification_cache
    cache.clear()
    maxsize = cache.maxsize

    # 写入 maxsize + 200 个不同 key
    for i in range(maxsize + 200):
        cache[f"overflow_key_{i}"] = time.time()

    assert len(cache) <= maxsize, (
        f"写入 {maxsize + 200} 条后 size 应受 maxsize={maxsize} 限制，"
        f"实际 size={len(cache)}"
    )
    cache.clear()


def test_notification_cache_ttl_expires_entries():
    """超过 ttl 的条目应被自动过期清理。"""
    # 用独立实例避免污染全局
    short_cache: TTLCache = TTLCache(maxsize=10, ttl=0.05)
    short_cache["k1"] = time.time()
    assert "k1" in short_cache

    time.sleep(0.1)
    # cachetools 在访问时触发清理
    assert "k1" not in short_cache, "TTL 过期后 key 应消失"
