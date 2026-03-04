"""
Tests for the per-user rate limiter in bot.py.

Covers:
  - Under the limit: requests pass through
  - At the limit: first N requests accepted, (N+1)th rejected
  - Sliding window: old requests expire, new ones are accepted again
  - Per-user isolation: user A's usage doesn't affect user B
  - Custom per-user limits from database override defaults
  - Cache invalidation when admin changes limits
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

# Import the rate-limiter symbols directly from bot.py
from bot import (
    _is_rate_limited,
    _rate_buckets,
    _rate_limit_cache,
    invalidate_rate_limit_cache,
)
import config


@pytest.fixture(autouse=True)
def clear_buckets():
    """Reset the shared rate-limiter state before every test."""
    _rate_buckets.clear()
    _rate_limit_cache.clear()
    yield
    _rate_buckets.clear()
    _rate_limit_cache.clear()


@pytest.fixture(autouse=True)
def default_limits(monkeypatch):
    """Ensure default limits are set for tests."""
    monkeypatch.setattr(config, "DEFAULT_RATE_LIMIT", 5)
    monkeypatch.setattr(config, "DEFAULT_RATE_WINDOW", 60)


class TestRateLimiterDefaults:
    """Tests using the default rate limit (no per-user override)."""

    @pytest.mark.asyncio
    async def test_first_request_allowed(self):
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            limited, max_req, window = await _is_rate_limited(user_id=1)
            assert limited is False
            assert max_req == 5
            assert window == 60

    @pytest.mark.asyncio
    async def test_up_to_limit_allowed(self):
        uid = 100
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            for _ in range(5):
                limited, _, _ = await _is_rate_limited(uid)
                assert limited is False

    @pytest.mark.asyncio
    async def test_over_limit_rejected(self):
        uid = 200
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            for _ in range(5):
                await _is_rate_limited(uid)   # consume quota
            limited, _, _ = await _is_rate_limited(uid)
            assert limited is True

    @pytest.mark.asyncio
    async def test_different_users_independent(self):
        uid_a, uid_b = 300, 400
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            for _ in range(5):
                await _is_rate_limited(uid_a)   # exhaust A's quota
            # B should still be allowed
            limited, _, _ = await _is_rate_limited(uid_b)
            assert limited is False

    @pytest.mark.asyncio
    async def test_sliding_window_expires_old_requests(self, monkeypatch):
        """
        Simulate window passing by patching time.monotonic so that
        old timestamps fall outside the window and get evicted.
        """
        uid = 500
        fake_now = [0.0]

        def mock_monotonic():
            return fake_now[0]

        monkeypatch.setattr(time, "monotonic", mock_monotonic)

        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            # Fill the bucket at t=0
            for _ in range(5):
                await _is_rate_limited(uid)
            limited, _, _ = await _is_rate_limited(uid)
            assert limited is True

            # Advance time past the window
            fake_now[0] = 61.0

            # Old timestamps should now be evicted; user can send again
            limited, _, _ = await _is_rate_limited(uid)
            assert limited is False

    @pytest.mark.asyncio
    async def test_bucket_size_stays_bounded(self):
        uid = 600
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            for _ in range(10):
                await _is_rate_limited(uid)
            assert len(_rate_buckets[uid]) <= 6


class TestRateLimiterCustomLimits:
    """Tests for per-user custom rate limits from the database."""

    @pytest.mark.asyncio
    async def test_custom_limit_higher_than_default(self):
        """User with a custom limit of 10 should be able to make more requests."""
        uid = 700
        from database import UserRateLimit
        custom = UserRateLimit(
            user_id=uid, max_requests=10, window_seconds=60,
            updated_by=1, updated_at="2025-01-01T00:00:00",
        )
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=custom):
            for _ in range(10):
                limited, max_req, window = await _is_rate_limited(uid)
                assert limited is False
                assert max_req == 10

            # 11th should be blocked
            limited, _, _ = await _is_rate_limited(uid)
            assert limited is True

    @pytest.mark.asyncio
    async def test_custom_limit_lower_than_default(self):
        """User with a custom limit of 2 should be blocked sooner."""
        uid = 800
        from database import UserRateLimit
        custom = UserRateLimit(
            user_id=uid, max_requests=2, window_seconds=30,
            updated_by=1, updated_at="2025-01-01T00:00:00",
        )
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=custom):
            for _ in range(2):
                limited, _, _ = await _is_rate_limited(uid)
                assert limited is False

            limited, max_req, window = await _is_rate_limited(uid)
            assert limited is True
            assert max_req == 2
            assert window == 30

    @pytest.mark.asyncio
    async def test_cache_invalidation(self):
        """After invalidating the cache, next check should re-query the DB."""
        uid = 900
        from database import UserRateLimit
        custom = UserRateLimit(
            user_id=uid, max_requests=10, window_seconds=120,
            updated_by=1, updated_at="2025-01-01T00:00:00",
        )
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=custom):
            await _is_rate_limited(uid)
            assert uid in _rate_limit_cache
            assert _rate_limit_cache[uid] == (10, 120)

        # Invalidate just this user
        invalidate_rate_limit_cache(uid)
        assert uid not in _rate_limit_cache

    @pytest.mark.asyncio
    async def test_cache_invalidation_all(self):
        """Invalidating without user_id clears entire cache."""
        from database import UserRateLimit
        custom1 = UserRateLimit(
            user_id=1001, max_requests=10, window_seconds=60,
            updated_by=1, updated_at="2025-01-01T00:00:00",
        )
        custom2 = UserRateLimit(
            user_id=1002, max_requests=20, window_seconds=120,
            updated_by=1, updated_at="2025-01-01T00:00:00",
        )
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, side_effect=[custom1, custom2]):
            await _is_rate_limited(1001)
            await _is_rate_limited(1002)
            assert 1001 in _rate_limit_cache
            assert 1002 in _rate_limit_cache

        invalidate_rate_limit_cache()  # clear all
        assert len(_rate_limit_cache) == 0

    @pytest.mark.asyncio
    async def test_default_fallback_when_no_custom(self, monkeypatch):
        """When DB returns None, should use config defaults."""
        uid = 1100
        monkeypatch.setattr(config, "DEFAULT_RATE_LIMIT", 3)
        monkeypatch.setattr(config, "DEFAULT_RATE_WINDOW", 30)
        with patch("bot.db.get_user_rate_limit", new_callable=AsyncMock, return_value=None):
            for _ in range(3):
                limited, max_req, window = await _is_rate_limited(uid)
                assert limited is False
                assert max_req == 3
                assert window == 30

            limited, _, _ = await _is_rate_limited(uid)
            assert limited is True
