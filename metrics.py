"""
metrics.py — Lightweight Prometheus-compatible metrics.

Exposes request counts, latency histograms, error rates, and per-provider costs
without depending on the heavyweight prometheus_client library.

All metric types (Counter, Histogram, Gauge) are thread/async-safe via a simple
threading lock (asyncio runs on one thread, but the lock also protects against
any unexpected concurrent access).

Usage:
    from metrics import REQUESTS_TOTAL, VISION_LATENCY, registry
    REQUESTS_TOTAL.inc(labels={"type": "photo"})
    VISION_LATENCY.observe(1.23, labels={"provider": "openai", "model": "gpt-4o"})
    text = registry.format_prometheus()
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Metric types ──────────────────────────────────────────────────────────────


class Counter:
    """Monotonically increasing counter with optional labels."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1, labels: dict[str, str] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if not labels:
            return ()
        return tuple(labels.get(n, "") for n in self.label_names)

    def format_prometheus(self) -> str:
        lines: list[str] = []
        lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} counter")
        with self._lock:
            if not self._values:
                # Emit a zero-value line so the metric is always visible
                lines.append(f"{self.name} 0")
            else:
                for key, val in sorted(self._values.items()):
                    label_str = self._format_labels(key)
                    lines.append(f"{self.name}{label_str} {_fmt(val)}")
        return "\n".join(lines)

    def _format_labels(self, key: tuple[str, ...]) -> str:
        if not key or not self.label_names:
            return ""
        pairs = ",".join(
            f'{n}="{v}"' for n, v in zip(self.label_names, key)
        )
        return "{" + pairs + "}"


class Gauge:
    """Value that can go up and down."""

    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1, labels: dict[str, str] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def dec(self, value: float = 1, labels: dict[str, str] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) - value

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if not labels:
            return ()
        return tuple(labels.get(n, "") for n in self.label_names)

    def format_prometheus(self) -> str:
        lines: list[str] = []
        lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} gauge")
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            else:
                for key, val in sorted(self._values.items()):
                    label_str = self._format_labels(key)
                    lines.append(f"{self.name}{label_str} {_fmt(val)}")
        return "\n".join(lines)

    def _format_labels(self, key: tuple[str, ...]) -> str:
        if not key or not self.label_names:
            return ""
        pairs = ",".join(
            f'{n}="{v}"' for n, v in zip(self.label_names, key)
        )
        return "{" + pairs + "}"


# Default histogram buckets (latency in seconds)
DEFAULT_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


class Histogram:
    """Tracks value distributions with configurable buckets."""

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self.buckets = tuple(sorted(buckets))
        # Per label-key: {bucket_upper_bound: count}, plus _sum and _count
        self._bucket_counts: dict[tuple[str, ...], dict[float, int]] = {}
        self._sums: dict[tuple[str, ...], float] = {}
        self._counts: dict[tuple[str, ...], int] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(labels)
        with self._lock:
            if key not in self._bucket_counts:
                self._bucket_counts[key] = {b: 0 for b in self.buckets}
                self._sums[key] = 0.0
                self._counts[key] = 0
            # Increment only the first (smallest) bucket that contains this value.
            # The cumulative sum is computed at format time.
            for b in self.buckets:
                if value <= b:
                    self._bucket_counts[key][b] += 1
                    break
            self._sums[key] += value
            self._counts[key] += 1

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if not labels:
            return ()
        return tuple(labels.get(n, "") for n in self.label_names)

    def format_prometheus(self) -> str:
        lines: list[str] = []
        lines.append(f"# HELP {self.name} {self.help_text}")
        lines.append(f"# TYPE {self.name} histogram")
        with self._lock:
            for key in sorted(self._bucket_counts):
                label_str = self._format_labels(key)
                # Cumulative buckets
                cumulative = 0
                for b in self.buckets:
                    cumulative += self._bucket_counts[key][b]
                    le_label = self._format_labels_with_le(key, b)
                    lines.append(f"{self.name}_bucket{le_label} {cumulative}")
                # +Inf bucket = total count
                le_inf = self._format_labels_with_le(key, "+Inf")
                lines.append(f"{self.name}_bucket{le_inf} {self._counts[key]}")
                lines.append(f"{self.name}_sum{label_str} {_fmt(self._sums[key])}")
                lines.append(f"{self.name}_count{label_str} {self._counts[key]}")
        return "\n".join(lines)

    def _format_labels(self, key: tuple[str, ...]) -> str:
        if not key or not self.label_names:
            return ""
        pairs = ",".join(
            f'{n}="{v}"' for n, v in zip(self.label_names, key)
        )
        return "{" + pairs + "}"

    def _format_labels_with_le(self, key: tuple[str, ...], le: float | str) -> str:
        pairs: list[str] = []
        for n, v in zip(self.label_names, key):
            pairs.append(f'{n}="{v}"')
        pairs.append(f'le="{le}"')
        return "{" + ",".join(pairs) + "}"


# ── Registry ──────────────────────────────────────────────────────────────────


class MetricsRegistry:
    """Singleton that holds all metrics and renders them in Prometheus format."""

    def __init__(self) -> None:
        self._metrics: list[Counter | Gauge | Histogram] = []
        self._lock = threading.Lock()

    def register(self, metric: Counter | Gauge | Histogram) -> None:
        with self._lock:
            self._metrics.append(metric)

    def format_prometheus(self) -> str:
        """Return all registered metrics in Prometheus text exposition format."""
        with self._lock:
            metrics = list(self._metrics)
        parts = [m.format_prometheus() for m in metrics]
        return "\n".join(parts) + "\n"


# Module-level singleton
registry = MetricsRegistry()


def _fmt(v: float) -> str:
    """Format a float for Prometheus output (integers without decimal)."""
    if v == int(v) and not math.isinf(v):
        return str(int(v))
    return f"{v:.6g}"


# ── Pre-defined metrics ──────────────────────────────────────────────────────

REQUESTS_TOTAL = Counter(
    "bot_requests_total",
    "Total bot requests by type",
    label_names=("type",),
)
registry.register(REQUESTS_TOTAL)

VISION_REQUESTS_TOTAL = Counter(
    "bot_vision_requests_total",
    "Total vision API requests by provider, model, and status",
    label_names=("provider", "model", "status"),
)
registry.register(VISION_REQUESTS_TOTAL)

VISION_LATENCY = Histogram(
    "bot_vision_latency_seconds",
    "Vision API latency in seconds",
    label_names=("provider", "model"),
)
registry.register(VISION_LATENCY)

SEARCH_REQUESTS_TOTAL = Counter(
    "bot_search_requests_total",
    "Total Amazon search requests by backend and status",
    label_names=("backend", "status"),
)
registry.register(SEARCH_REQUESTS_TOTAL)

SEARCH_LATENCY = Histogram(
    "bot_search_latency_seconds",
    "Amazon search latency in seconds",
    label_names=("backend",),
)
registry.register(SEARCH_LATENCY)

API_COST_DOLLARS = Counter(
    "bot_api_cost_dollars",
    "Cumulative API cost in US dollars by provider and model",
    label_names=("provider", "model"),
)
registry.register(API_COST_DOLLARS)

ACTIVE_USERS = Gauge(
    "bot_active_users",
    "Number of users with active sessions",
)
registry.register(ACTIVE_USERS)

ERRORS_TOTAL = Counter(
    "bot_errors_total",
    "Total errors by module and error type",
    label_names=("module", "error_type"),
)
registry.register(ERRORS_TOTAL)

CIRCUIT_BREAKER_STATE = Gauge(
    "bot_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    label_names=("name",),
)
registry.register(CIRCUIT_BREAKER_STATE)
