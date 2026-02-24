"""
Tests for the per-user rate limiter in bot.py.

Covers:
  - Under the limit: requests pass through
  - At the limit: first N requests accepted, (N+1)th rejected
  - Sliding window: old requests expire, new ones are accepted again
  - Per-user isolation: user A's usage doesn't affect user B
"""
from __future__ import annotations

import time

import pytest

# Import the rate-limiter symbols directly from bot.py
from bot import _is_rate_limited, _rate_buckets, RATE_MAX_REQUESTS, RATE_WINDOW_SECS


@pytest.fixture(autouse=True)
def clear_buckets():
    """Reset the shared rate-limiter state before every test."""
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


class TestRateLimiter:
    def test_first_request_allowed(self):
        assert _is_rate_limited(user_id=1) is False

    def test_up_to_limit_allowed(self):
        uid = 100
        for _ in range(RATE_MAX_REQUESTS):
            assert _is_rate_limited(uid) is False

    def test_over_limit_rejected(self):
        uid = 200
        for _ in range(RATE_MAX_REQUESTS):
            _is_rate_limited(uid)   # consume quota
        assert _is_rate_limited(uid) is True

    def test_different_users_independent(self):
        uid_a, uid_b = 300, 400
        for _ in range(RATE_MAX_REQUESTS):
            _is_rate_limited(uid_a)   # exhaust A's quota
        # B should still be allowed
        assert _is_rate_limited(uid_b) is False

    def test_sliding_window_expires_old_requests(self, monkeypatch):
        """
        Simulate RATE_WINDOW_SECS passing by patching time.monotonic so that
        old timestamps fall outside the window and get evicted.
        """
        uid = 500
        fake_now = [0.0]   # mutable so the inner function can modify it

        def mock_monotonic():
            return fake_now[0]

        monkeypatch.setattr(time, "monotonic", mock_monotonic)

        # Fill the bucket at t=0
        for _ in range(RATE_MAX_REQUESTS):
            _is_rate_limited(uid)
        assert _is_rate_limited(uid) is True   # limit hit

        # Advance time past the window
        fake_now[0] = RATE_WINDOW_SECS + 1.0

        # Old timestamps should now be evicted; user can send again
        assert _is_rate_limited(uid) is False

    def test_bucket_size_stays_bounded(self):
        """After the window expires, the bucket must not grow unboundedly."""
        uid = 600
        for _ in range(RATE_MAX_REQUESTS * 2):
            _is_rate_limited(uid)
        # Even after many attempts, bucket len <= RATE_MAX_REQUESTS + 1
        assert len(_rate_buckets[uid]) <= RATE_MAX_REQUESTS + 1
