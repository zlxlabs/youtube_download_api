"""
Tests for API key verification dependency.

Covers the constant-time comparison change (hmac.compare_digest) — behavior
must be identical to the previous string equality check.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.deps import verify_api_key


def _settings(api_key: str = "secret-key") -> MagicMock:
    return MagicMock(api_key=api_key)


@pytest.mark.asyncio
async def test_valid_key_passes():
    result = await verify_api_key("secret-key", _settings())
    assert result == "secret-key"


@pytest.mark.asyncio
async def test_invalid_key_rejected_403():
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key("wrong-key", _settings())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_key_rejected_401():
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(None, _settings())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_empty_key_rejected_401():
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key("", _settings())
    assert exc_info.value.status_code == 401
