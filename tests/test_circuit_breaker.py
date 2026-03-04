"""Tests for circuit_breaker.py — circuit breaker pattern implementation."""
from __future__ import annotations

import asyncio
import time

import pytest

from circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitOpenError,
    CircuitState,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _ok():
    """A coroutine that succeeds."""
    return "ok"


async def _fail():
    """A coroutine that raises."""
    raise RuntimeError("service unavailable")


# ── CircuitBreaker basic tests ────────────────────────────────────────────────

async def test_initial_state():
    cb = CircuitBreaker("test-init")
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0


async def test_successful_call():
    cb = CircuitBreaker("test-ok")
    result = await cb.call(_ok())
    assert result == "ok"
    assert cb._failure_count == 0
    assert cb._total_calls == 1


async def test_failed_call_increments_count():
    cb = CircuitBreaker("test-fail-count", failure_threshold=5)
    with pytest.raises(RuntimeError, match="service unavailable"):
        await cb.call(_fail())
    assert cb._failure_count == 1
    assert cb._total_failures == 1
    assert cb.state == CircuitState.CLOSED


async def test_circuit_opens_after_threshold():
    cb = CircuitBreaker("test-open", failure_threshold=3, recovery_timeout=60.0)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())
    assert cb.state == CircuitState.OPEN
    assert cb._failure_count == 3


async def test_open_circuit_raises_circuit_open_error():
    cb = CircuitBreaker("test-open-err", failure_threshold=2, recovery_timeout=60.0)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())
    assert cb.state == CircuitState.OPEN

    with pytest.raises(CircuitOpenError) as exc_info:
        await cb.call(_ok())
    assert "test-open-err" in str(exc_info.value)
    assert cb._total_short_circuits == 1


async def test_success_resets_failure_count():
    cb = CircuitBreaker("test-reset", failure_threshold=5)
    # Two failures, then a success
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())
    assert cb._failure_count == 2

    await cb.call(_ok())
    assert cb._failure_count == 0


async def test_half_open_after_recovery_timeout():
    cb = CircuitBreaker("test-half-open", failure_threshold=2, recovery_timeout=0.1)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())
    assert cb._state == CircuitState.OPEN

    # Wait for recovery timeout
    await asyncio.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN


async def test_half_open_to_closed_on_success():
    cb = CircuitBreaker(
        "test-ho-close",
        failure_threshold=2,
        recovery_timeout=0.05,
        success_threshold=2,
    )
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())

    await asyncio.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN

    # Two successful probes to close
    await cb.call(_ok())
    assert cb.state == CircuitState.HALF_OPEN  # not yet — need 2

    await cb.call(_ok())
    assert cb.state == CircuitState.CLOSED


async def test_half_open_to_open_on_failure():
    cb = CircuitBreaker(
        "test-ho-fail",
        failure_threshold=2,
        recovery_timeout=0.05,
        success_threshold=2,
    )
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())

    await asyncio.sleep(0.1)
    assert cb.state == CircuitState.HALF_OPEN

    with pytest.raises(RuntimeError):
        await cb.call(_fail())
    assert cb.state == CircuitState.OPEN


async def test_manual_reset():
    cb = CircuitBreaker("test-manual-reset", failure_threshold=2)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(_fail())
    assert cb.state == CircuitState.OPEN

    cb.reset()
    assert cb.state == CircuitState.CLOSED
    assert cb._failure_count == 0
    assert cb._last_failure_time is None


async def test_stats_property():
    cb = CircuitBreaker("test-stats", failure_threshold=3, recovery_timeout=30.0)
    await cb.call(_ok())
    stats = cb.stats
    assert stats["name"] == "test-stats"
    assert stats["state"] == "CLOSED"
    assert stats["total_calls"] == 1
    assert stats["failure_threshold"] == 3
    assert stats["recovery_timeout"] == 30.0


# ── CircuitBreakerRegistry tests ──────────────────────────────────────────────

async def test_registry_get_creates_and_caches():
    reg = CircuitBreakerRegistry()
    cb1 = reg.get("svc-a", failure_threshold=10)
    cb2 = reg.get("svc-a", failure_threshold=99)  # params ignored on second call
    assert cb1 is cb2
    assert cb1.failure_threshold == 10


async def test_registry_get_all_stats():
    reg = CircuitBreakerRegistry()
    reg.get("svc-x")
    reg.get("svc-y")
    stats = reg.get_all_stats()
    assert len(stats) == 2
    names = {s["name"] for s in stats}
    assert names == {"svc-x", "svc-y"}


async def test_registry_reset_specific():
    reg = CircuitBreakerRegistry()
    cb = reg.get("svc-reset", failure_threshold=1, recovery_timeout=60.0)
    with pytest.raises(RuntimeError):
        await cb.call(_fail())
    assert cb.state == CircuitState.OPEN

    assert reg.reset("svc-reset") is True
    assert cb.state == CircuitState.CLOSED

    assert reg.reset("nonexistent") is False


async def test_registry_reset_all():
    reg = CircuitBreakerRegistry()
    cb1 = reg.get("svc-r1", failure_threshold=1, recovery_timeout=60.0)
    cb2 = reg.get("svc-r2", failure_threshold=1, recovery_timeout=60.0)
    cb3 = reg.get("svc-r3")  # stays closed

    with pytest.raises(RuntimeError):
        await cb1.call(_fail())
    with pytest.raises(RuntimeError):
        await cb2.call(_fail())

    count = reg.reset_all()
    assert count == 2
    assert cb1.state == CircuitState.CLOSED
    assert cb2.state == CircuitState.CLOSED
    assert cb3.state == CircuitState.CLOSED


async def test_registry_names():
    reg = CircuitBreakerRegistry()
    reg.get("alpha")
    reg.get("beta")
    assert set(reg.names) == {"alpha", "beta"}


# ── Edge cases ────────────────────────────────────────────────────────────────

async def test_coroutine_closed_on_open_circuit():
    """When circuit is open, the passed coroutine should be properly closed."""
    cb = CircuitBreaker("test-close-coro", failure_threshold=1, recovery_timeout=60.0)
    with pytest.raises(RuntimeError):
        await cb.call(_fail())

    # Create a coroutine — it should be closed (not awaited) when circuit is open
    coro = _ok()
    with pytest.raises(CircuitOpenError):
        await cb.call(coro)
    # If we get here without "coroutine was never awaited" warning, coro was properly closed


async def test_circuit_breaker_with_different_exception_types():
    cb = CircuitBreaker("test-multi-exc", failure_threshold=3)
    exceptions = [ValueError("bad"), TypeError("type"), ConnectionError("conn")]
    for exc_cls in exceptions:
        async def _raise_specific(e=exc_cls):
            raise e
        with pytest.raises(type(exc_cls)):
            await cb.call(_raise_specific())
    assert cb.state == CircuitState.OPEN
    assert "ConnectionError" in cb._last_failure_error
