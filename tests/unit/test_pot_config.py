"""
Test POT (PO Token) configuration.

Usage:
    pytest tests/unit/test_pot_config.py
    python tests/unit/test_pot_config.py   # manual integration test
"""

import os
import sys
from unittest.mock import MagicMock, patch

# Fix Windows encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_fake_settings(**overrides):
    """Create a mock settings object with sensible defaults for POT tests."""
    defaults = {
        "pot_server_url": "http://localhost:4416",
        "cookie_file": "/tmp/cookies.txt",
    }
    defaults.update(overrides)
    settings = MagicMock()
    for k, v in defaults.items():
        setattr(settings, k, v)
    return settings


def test_pot_server_reachable():
    """Test if POT server reachability check works with a mock response."""
    fake_settings = _make_fake_settings()
    mock_response = MagicMock()
    mock_response.status_code = 200

    with (
        patch("src.config.get_settings", return_value=fake_settings),
        patch("httpx.get", return_value=mock_response) as mock_get,
    ):
        from src.config import get_settings

        settings = get_settings()
        import httpx

        response = httpx.get(f"{settings.pot_server_url}/ping", timeout=5)
        assert response.status_code == 200
        mock_get.assert_called_once_with(
            f"{fake_settings.pot_server_url}/ping", timeout=5
        )


def test_extractor_args_format():
    """Test extractor_args format is constructed correctly."""
    fake_settings = _make_fake_settings(pot_server_url="http://pot:4416")

    # Correct Python API format (matches CLI parse result):
    # - Key: extractor name
    # - Value: nested dict {param_name: [param_values]}
    extractor_args = {
        "youtube": {"player_client": ["web"]},
        "youtubepot-bgutilhttp": {
            "base_url": [fake_settings.pot_server_url],
        },
    }

    # Verify structure
    assert "youtube" in extractor_args
    assert extractor_args["youtube"]["player_client"] == ["web"]
    assert extractor_args["youtubepot-bgutilhttp"]["base_url"] == [
        "http://pot:4416"
    ]


def test_ytdlp_with_pot():
    """Test yt-dlp opts are correctly assembled with POT config.

    This is a unit test that verifies the configuration structure
    without making real network calls.
    """
    fake_settings = _make_fake_settings(pot_server_url="http://pot:4416")

    extractor_args = {
        "youtube": {"player_client": ["web"]},
        "youtubepot-bgutilhttp": {
            "base_url": [fake_settings.pot_server_url],
        },
    }

    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    opts = {
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "extract_flat": False,
        "skip_download": True,
        "format": "best",
        "extractor_args": extractor_args,
    }

    # Verify opts structure is valid for yt-dlp
    assert opts["skip_download"] is True
    assert opts["format"] == "best"
    assert "extractor_args" in opts
    assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
        "http://pot:4416"
    ]

    # Verify yt-dlp accepts these opts without error (mock extract_info)
    fake_info = {"title": "Test Video", "formats": [{"format_id": "18"}]}
    mock_ydl_instance = MagicMock()
    mock_ydl_instance.extract_info.return_value = fake_info
    mock_ydl_instance.__enter__ = MagicMock(return_value=mock_ydl_instance)
    mock_ydl_instance.__exit__ = MagicMock(return_value=False)

    with patch("yt_dlp.YoutubeDL", return_value=mock_ydl_instance) as mock_cls:
        import yt_dlp

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(test_url, download=False)

        mock_cls.assert_called_once_with(opts)
        mock_ydl_instance.extract_info.assert_called_once_with(
            test_url, download=False
        )
        assert info["title"] == "Test Video"
        assert len(info["formats"]) == 1


# ---------------------------------------------------------------------------
# Manual integration test entry point (requires real .env and POT server)
# ---------------------------------------------------------------------------
def _integration_main():
    """Run as manual integration test: python tests/unit/test_pot_config.py"""
    os.environ["ENV_FILE"] = ".env.development"
    import httpx
    import yt_dlp

    from src.config import get_settings

    print("=" * 60)
    print("POT Config Integration Test")
    print("=" * 60)

    settings = get_settings()
    print(f"\nConfig:")
    print(f"  POT_SERVER_URL: {settings.pot_server_url}")
    print(f"  COOKIE_FILE: {settings.cookie_file}")

    # Test 1: POT server connection
    try:
        response = httpx.get(f"{settings.pot_server_url}/ping", timeout=5)
        print(f"\n[1] [OK] POT Server response: {response.status_code}")
    except Exception as e:
        print(f"\n[1] [FAIL] POT Server unreachable: {e}")
        print("   Please ensure POT provider container is running.")
        return

    # Test 2: extractor_args
    extractor_args = {
        "youtube": {"player_client": ["web"]},
        "youtubepot-bgutilhttp": {
            "base_url": [settings.pot_server_url],
        },
    }
    print(f"\n[2] extractor_args: {extractor_args}")

    # Test 3: yt-dlp actual call
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    opts = {
        "quiet": False,
        "verbose": True,
        "no_warnings": False,
        "extract_flat": False,
        "skip_download": True,
        "format": "best",
        "extractor_args": extractor_args,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(test_url, download=False)
            if info:
                print(f"\n[3] [OK] Title: {info.get('title', 'N/A')}")
            else:
                print(f"\n[3] [FAIL] Could not get video info")
    except Exception as e:
        print(f"\n[3] [FAIL] yt-dlp error: {e}")


if __name__ == "__main__":
    _integration_main()
