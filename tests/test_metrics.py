"""
Tests for the lightweight Prometheus metrics module.
"""
from __future__ import annotations

import pytest

from metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    _fmt,
)


# ── Counter ───────────────────────────────────────────────────────────────────


class TestCounter:
    def test_inc_no_labels(self):
        c = Counter("test_total", "A test counter")
        c.inc()
        c.inc(5)
        output = c.format_prometheus()
        assert "test_total 6" in output
        assert "# TYPE test_total counter" in output
        assert "# HELP test_total A test counter" in output

    def test_inc_with_labels(self):
        c = Counter("req_total", "Requests", label_names=("method", "status"))
        c.inc(labels={"method": "GET", "status": "200"})
        c.inc(labels={"method": "GET", "status": "200"})
        c.inc(labels={"method": "POST", "status": "500"})
        output = c.format_prometheus()
        assert 'req_total{method="GET",status="200"} 2' in output
        assert 'req_total{method="POST",status="500"} 1' in output

    def test_empty_counter_shows_zero(self):
        c = Counter("empty_total", "Empty counter")
        output = c.format_prometheus()
        assert "empty_total 0" in output

    def test_missing_label_defaults_to_empty_string(self):
        c = Counter("x_total", "X", label_names=("a", "b"))
        c.inc(labels={"a": "foo"})  # "b" is missing
        output = c.format_prometheus()
        assert 'x_total{a="foo",b=""} 1' in output


# ── Gauge ─────────────────────────────────────────────────────────────────────


class TestGauge:
    def test_set(self):
        g = Gauge("temperature", "Current temp")
        g.set(42.5)
        output = g.format_prometheus()
        assert "temperature 42.5" in output
        assert "# TYPE temperature gauge" in output

    def test_inc_dec(self):
        g = Gauge("connections", "Active connections")
        g.inc()
        g.inc()
        g.dec()
        output = g.format_prometheus()
        assert "connections 1" in output

    def test_labeled_gauge(self):
        g = Gauge("state", "State", label_names=("name",))
        g.set(0, labels={"name": "circuit_a"})
        g.set(1, labels={"name": "circuit_b"})
        output = g.format_prometheus()
        assert 'state{name="circuit_a"} 0' in output
        assert 'state{name="circuit_b"} 1' in output

    def test_empty_gauge_shows_zero(self):
        g = Gauge("empty_gauge", "Empty")
        assert "empty_gauge 0" in g.format_prometheus()


# ── Histogram ─────────────────────────────────────────────────────────────────


class TestHistogram:
    def test_single_observation(self):
        h = Histogram("duration", "Duration", buckets=(0.5, 1.0, 5.0))
        h.observe(0.3)
        output = h.format_prometheus()
        assert "# TYPE duration histogram" in output
        # 0.3 falls in the 0.5 and 1.0 and 5.0 buckets (cumulative)
        assert 'duration_bucket{le="0.5"} 1' in output
        assert 'duration_bucket{le="1.0"} 1' in output
        assert 'duration_bucket{le="5.0"} 1' in output
        assert 'duration_bucket{le="+Inf"} 1' in output
        assert "duration_sum" in output
        assert "duration_count 1" in output

    def test_multiple_observations(self):
        h = Histogram("lat", "Latency", buckets=(1.0, 5.0, 10.0))
        h.observe(0.5)
        h.observe(3.0)
        h.observe(7.0)
        output = h.format_prometheus()
        # bucket<=1.0 has 1 (0.5), bucket<=5.0 has 2 (0.5, 3.0), bucket<=10.0 has 3
        assert 'lat_bucket{le="1.0"} 1' in output
        assert 'lat_bucket{le="5.0"} 2' in output
        assert 'lat_bucket{le="10.0"} 3' in output
        assert 'lat_bucket{le="+Inf"} 3' in output
        assert "lat_count 3" in output

    def test_labeled_histogram(self):
        h = Histogram("req_dur", "Request duration", label_names=("method",), buckets=(1.0,))
        h.observe(0.5, labels={"method": "GET"})
        h.observe(0.8, labels={"method": "POST"})
        output = h.format_prometheus()
        assert 'req_dur_bucket{method="GET",le="1.0"} 1' in output
        assert 'req_dur_bucket{method="POST",le="1.0"} 1' in output

    def test_value_above_all_buckets(self):
        h = Histogram("big", "Big values", buckets=(1.0, 2.0))
        h.observe(100.0)
        output = h.format_prometheus()
        # None of the finite buckets should contain it
        assert 'big_bucket{le="1.0"} 0' in output
        assert 'big_bucket{le="2.0"} 0' in output
        # +Inf always gets it
        assert 'big_bucket{le="+Inf"} 1' in output


# ── Registry ──────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_format_prometheus_combines_all(self):
        r = MetricsRegistry()
        c = Counter("r_total", "Test counter")
        g = Gauge("r_gauge", "Test gauge")
        r.register(c)
        r.register(g)
        c.inc(3)
        g.set(99)
        output = r.format_prometheus()
        assert "r_total 3" in output
        assert "r_gauge 99" in output
        # Ends with newline
        assert output.endswith("\n")

    def test_empty_registry(self):
        r = MetricsRegistry()
        output = r.format_prometheus()
        assert output == "\n"


# ── Helper ────────────────────────────────────────────────────────────────────


class TestFmt:
    def test_integer_values(self):
        assert _fmt(0.0) == "0"
        assert _fmt(42.0) == "42"

    def test_float_values(self):
        assert _fmt(1.5) == "1.5"
        assert _fmt(0.001234) == "0.001234"

    def test_large_float(self):
        # Should use significant digits
        result = _fmt(123456.789)
        assert "123457" in result or "1.23457e+05" in result
