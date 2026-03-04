"""
circuit_breaker.py — Circuit breaker pattern for external service calls.

Wraps async coroutines with open/half-open/closed state management to
prevent cascading failures when external APIs (vision providers, search
backends, proxy endpoints) are unavailable.

States:
  CLOSED    — Normal operation; requests pass through. Failures are counted.
  OPEN      — Service is considered down; requests fail immediately with
              CircuitOpenError (no network call made). After recovery_timeout
              seconds, transitions to HALF_OPEN.
  HALF_OPEN — Testing recovery; allows one request through at a time.
              If it succeeds (success_threshold times), transitions back to
              CLOSED. If it fails, transitions back to OPEN.

Usage:
    from circuit_breaker import registry

    cb = registry.get("openai", failure_threshold=5, recovery_timeout=60)
    result = await cb.call(some_async_function())
"""
from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Any, Coroutine

logger = logging.getLogger(__name__)


class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when a call is attempted on an open circuit breaker."""

    def __init__(self, name: str, recovery_in: float):
        self.name = name
        self.recovery_in = recovery_in
        super().__init__(
            f"Circuit '{name}' is OPEN — failing fast. "
            f"Recovery attempt in {recovery_in:.0f}s."
        )


class CircuitBreaker:
    """Circuit breaker for a single external service.

    Parameters:
        name:              Identifier (e.g. "openai", "rapidapi").
        failure_threshold: Consecutive failures before opening the circuit.
        recovery_timeout:  Seconds to wait in OPEN before trying HALF_OPEN.
        success_threshold: Consecutive successes in HALF_OPEN before closing.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float | None = None
        self._last_failure_error: str | None = None
        self._total_calls: int = 0
        self._total_failures: int = 0
        self._total_short_circuits: int = 0

        # Lock to serialize HALF_OPEN probes so only one request gets through
        self._half_open_lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state, accounting for recovery timeout transitions."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info(
                    "Circuit '%s' transitioned OPEN -> HALF_OPEN after %.0fs",
                    self.name, elapsed,
                )
        return self._state

    @property
    def stats(self) -> dict[str, Any]:
        """Return a dict of circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "total_calls": self._total_calls,
            "total_failures": self._total_failures,
            "total_short_circuits": self._total_short_circuits,
            "last_failure_time": self._last_failure_time,
            "last_failure_error": self._last_failure_error,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "success_threshold": self.success_threshold,
        }

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        prev = self._state
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._last_failure_error = None
        if prev != CircuitState.CLOSED:
            logger.info("Circuit '%s' manually reset: %s -> CLOSED", self.name, prev.value)

    async def call(self, coro: Coroutine) -> Any:
        """Execute a coroutine through the circuit breaker.

        Args:
            coro: An awaitable coroutine to execute.

        Returns:
            The result of the coroutine.

        Raises:
            CircuitOpenError: If the circuit is OPEN and recovery timeout
                has not yet elapsed.
            Exception: Any exception raised by the coroutine (after recording
                the failure).
        """
        current_state = self.state   # triggers OPEN -> HALF_OPEN check
        self._total_calls += 1

        if current_state == CircuitState.OPEN:
            self._total_short_circuits += 1
            remaining = self.recovery_timeout
            if self._last_failure_time is not None:
                remaining = max(
                    0.0,
                    self.recovery_timeout - (time.monotonic() - self._last_failure_time),
                )
            logger.debug(
                "Circuit '%s' is OPEN — short-circuiting (recovery in %.0fs)",
                self.name, remaining,
            )
            # We must close the coroutine to avoid "coroutine was never awaited" warning
            coro.close()
            raise CircuitOpenError(self.name, remaining)

        if current_state == CircuitState.HALF_OPEN:
            # Serialize HALF_OPEN probes: only one request at a time
            async with self._half_open_lock:
                return await self._execute(coro)

        # CLOSED — normal execution
        return await self._execute(coro)

    async def _execute(self, coro: Coroutine) -> Any:
        """Run the coroutine, tracking success/failure."""
        try:
            result = await coro
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            raise

    def _on_success(self) -> None:
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                logger.info(
                    "Circuit '%s' recovered: HALF_OPEN -> CLOSED "
                    "(after %d successful probes)",
                    self.name, self.success_threshold,
                )
            else:
                logger.debug(
                    "Circuit '%s' HALF_OPEN probe succeeded (%d/%d)",
                    self.name, self._success_count, self.success_threshold,
                )
        else:
            # CLOSED — reset failure counter on any success
            if self._failure_count > 0:
                logger.debug(
                    "Circuit '%s' failure counter reset (was %d)",
                    self.name, self._failure_count,
                )
            self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        """Record a failed call."""
        self._failure_count += 1
        self._total_failures += 1
        self._last_failure_time = time.monotonic()
        self._last_failure_error = f"{type(exc).__name__}: {str(exc)[:200]}"

        if self._state == CircuitState.HALF_OPEN:
            # Probe failed — go back to OPEN
            self._state = CircuitState.OPEN
            self._success_count = 0
            logger.warning(
                "Circuit '%s' probe failed: HALF_OPEN -> OPEN (%s)",
                self.name, self._last_failure_error,
            )
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._success_count = 0
            logger.warning(
                "Circuit '%s' opened: %d consecutive failures (threshold=%d). "
                "Last error: %s",
                self.name, self._failure_count,
                self.failure_threshold, self._last_failure_error,
            )


class CircuitBreakerRegistry:
    """Singleton registry that manages all circuit breakers."""

    def __init__(self) -> None:
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ) -> CircuitBreaker:
        """Get or create a circuit breaker by name.

        If a breaker with this name already exists, it is returned as-is
        (parameters are only used on first creation).
        """
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                success_threshold=success_threshold,
            )
            logger.debug(
                "Created circuit breaker '%s' (threshold=%d, timeout=%.0fs)",
                name, failure_threshold, recovery_timeout,
            )
        return self._breakers[name]

    def get_all_stats(self) -> list[dict[str, Any]]:
        """Return stats for all registered circuit breakers."""
        return [cb.stats for cb in self._breakers.values()]

    def reset(self, name: str) -> bool:
        """Reset a specific circuit breaker. Returns True if found."""
        if name in self._breakers:
            self._breakers[name].reset()
            return True
        return False

    def reset_all(self) -> int:
        """Reset all circuit breakers. Returns count of breakers reset."""
        count = 0
        for cb in self._breakers.values():
            if cb.state != CircuitState.CLOSED:
                cb.reset()
                count += 1
        return count

    @property
    def names(self) -> list[str]:
        """Return all registered breaker names."""
        return list(self._breakers.keys())


# ── Module-level singleton ────────────────────────────────────────────────────
registry = CircuitBreakerRegistry()
