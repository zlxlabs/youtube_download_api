from datetime import datetime, timedelta

import pytest

from src.core.ip_ban_breaker import IPBanCircuitBreaker
from src.core.ip_ban_models import IPBanLevel


@pytest.mark.asyncio
async def test_probe_lease_allows_only_one_attempt_per_cooldown_window():
    breaker = IPBanCircuitBreaker(min_wait_before_retry=1, max_retry_interval=60)
    await breaker.restore_state(
        IPBanLevel.FULLY_BANNED, datetime.now() - timedelta(seconds=2), None, 0
    )

    acquired, _ = await breaker.try_acquire_probe("audio", min_wait_override=1)
    rejected, reason = await breaker.try_acquire_probe("audio", min_wait_override=1)

    assert acquired is True
    assert breaker.last_attempt_at is not None
    assert rejected is False
    assert "Too soon" in reason


@pytest.mark.asyncio
async def test_audio_trigger_cannot_downgrade_full_ban():
    breaker = IPBanCircuitBreaker()
    await breaker.trigger_full_ban()

    await breaker.trigger_audio_ban(reason="audio 403")

    assert breaker.get_current_level() == IPBanLevel.FULLY_BANNED


@pytest.mark.asyncio
@pytest.mark.parametrize("transition", ["downgrade", "upgrade", "extend"])
async def test_ban_window_transition_clears_probe_lease(transition):
    breaker = IPBanCircuitBreaker()
    breaker.last_attempt_at = datetime.now()
    if transition == "downgrade":
        await breaker.trigger_full_ban()
        breaker.last_attempt_at = datetime.now()
        await breaker.downgrade_to_audio_ban()
    elif transition == "upgrade":
        await breaker.trigger_audio_ban()
        breaker.last_attempt_at = datetime.now()
        await breaker.upgrade_to_full_ban()
    else:
        await breaker.trigger_full_ban()
        breaker.last_attempt_at = datetime.now()
        await breaker.extend_ban(60)

    assert breaker.last_attempt_at is None


@pytest.mark.asyncio
async def test_ban_triggers_clear_probe_lease():
    breaker = IPBanCircuitBreaker()
    breaker.last_attempt_at = datetime.now()
    await breaker.trigger_audio_ban()
    assert breaker.last_attempt_at is None

    breaker.last_attempt_at = datetime.now()
    await breaker.trigger_full_ban()
    assert breaker.last_attempt_at is None
