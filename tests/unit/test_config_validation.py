"""
Tests for Settings.validate_consistency() method.

Verifies that cross-field configuration validation correctly
detects contradictory or suspicious configurations.
"""

import tempfile
from pathlib import Path

import pytest

from src.config import Settings


def _make_settings(**overrides) -> Settings:
    """Create Settings with test defaults and optional overrides."""
    defaults = {
        "api_key": "test-key",
        "wecom_webhook_url": "https://example.com/webhook",
        "data_dir": Path(tempfile.mkdtemp()),
        "debug": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestConfigValidation:
    """Tests for validate_consistency."""

    def test_valid_config_no_warnings(self):
        """Valid configuration should produce no warnings."""
        settings = _make_settings()
        warnings = settings.validate_consistency()
        assert len(warnings) == 0

    def test_cdp_enabled_no_urls(self):
        """CDP enabled with empty URLs should warn."""
        settings = _make_settings(cdp_enabled=True, cdp_urls="")
        warnings = settings.validate_consistency()
        assert any("CDP_ENABLED=true" in w and "CDP_URLS" in w for w in warnings)

    def test_cdp_enabled_with_urls_no_warning(self):
        """CDP enabled with valid URLs should not warn about URLs."""
        settings = _make_settings(
            cdp_enabled=True, cdp_urls="http://localhost:9222"
        )
        warnings = settings.validate_consistency()
        assert not any("CDP_URLS" in w for w in warnings)

    def test_human_behavior_with_high_concurrency(self):
        """Human behavior enabled with concurrency > 1 should warn."""
        settings = _make_settings(
            cdp_human_behavior_enabled=True, download_concurrency=2
        )
        warnings = settings.validate_consistency()
        assert any("HUMAN_BEHAVIOR" in w and "CONCURRENCY" in w for w in warnings)

    def test_human_behavior_with_single_concurrency_no_warning(self):
        """Human behavior with concurrency=1 should not warn."""
        settings = _make_settings(
            cdp_human_behavior_enabled=True, download_concurrency=1
        )
        warnings = settings.validate_consistency()
        assert not any("CONCURRENCY" in w for w in warnings)

    def test_cdp_in_priority_but_disabled(self):
        """CDP in audio priority but CDP disabled should warn."""
        settings = _make_settings(
            cdp_enabled=False, audio_download_priority="cdp,ytdlp,tikhub"
        )
        warnings = settings.validate_consistency()
        assert any("AUDIO_DOWNLOAD_PRIORITY" in w and "CDP_ENABLED=false" in w for w in warnings)

    def test_pot_token_without_cdp(self):
        """PO Token enabled without CDP should warn."""
        settings = _make_settings(
            cdp_enable_pot_token=True, cdp_enabled=False
        )
        warnings = settings.validate_consistency()
        assert any("POT_TOKEN" in w and "CDP_ENABLED=false" in w for w in warnings)

    def test_no_wecom_webhook(self):
        """Empty WeChat webhook should warn about no notifications."""
        settings = _make_settings(wecom_webhook_url="")
        warnings = settings.validate_consistency()
        assert any("WECOM_WEBHOOK_URL" in w for w in warnings)

    def test_deprecated_interval_config(self):
        """Non-default deprecated interval config should warn."""
        settings = _make_settings(task_interval_min=30, task_interval_max=300)
        warnings = settings.validate_consistency()
        assert any("deprecated" in w.lower() for w in warnings)

    def test_empty_audio_priority(self):
        """Empty audio download priority should warn."""
        settings = _make_settings(audio_download_priority="")
        warnings = settings.validate_consistency()
        assert any("AUDIO_DOWNLOAD_PRIORITY" in w and "empty" in w for w in warnings)

    def test_multiple_warnings_detected(self):
        """Multiple issues should produce multiple warnings."""
        settings = _make_settings(
            cdp_enabled=True,
            cdp_urls="",
            cdp_enable_pot_token=True,
            wecom_webhook_url="",
        )
        warnings = settings.validate_consistency()
        # Should have at least: CDP_URLS empty, POT_TOKEN without CDP URL, no webhook
        assert len(warnings) >= 2
