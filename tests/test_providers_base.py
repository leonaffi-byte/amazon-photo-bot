"""
Tests for providers/base.py — ProviderResult and parse_json_response.

Covers:
  - quality_score calculation across confidence levels
  - cost_str formatting (milli-dollar vs dollar)
  - to_product_info() field mapping
  - parse_json_response: plain JSON, markdown-fenced JSON, invalid JSON
"""
from __future__ import annotations

import pytest

from providers.base import ProviderResult, parse_json_response


def make_result(**kwargs) -> ProviderResult:
    defaults = dict(
        provider_name="test/model",
        model_id="model",
        product_name="Test Keyboard",
        brand="TestBrand",
        category="Electronics",
        key_features=["Wireless", "Backlit", "USB-C"],
        amazon_search_query="TestBrand wireless keyboard backlit",
        alternative_query="wireless keyboard backlit",
        confidence="high",
        notes="Identified from clear photo",
        latency_ms=1200,
        input_tokens=800,
        output_tokens=150,
        cost_usd=0.005,
    )
    defaults.update(kwargs)
    return ProviderResult(**defaults)


# ── quality_score ─────────────────────────────────────────────────────────────

class TestQualityScore:
    def test_high_confidence_full_data_best_score(self):
        r = make_result(confidence="high")
        # Full completeness: product_name + brand + 3 features + good query = > 2 points
        assert r.quality_score > 2.0

    def test_low_confidence_same_data_lower_score(self):
        high = make_result(confidence="high")
        low  = make_result(confidence="low")
        assert high.quality_score > low.quality_score

    def test_medium_confidence_between_high_and_low(self):
        high   = make_result(confidence="high")
        medium = make_result(confidence="medium")
        low    = make_result(confidence="low")
        assert high.quality_score > medium.quality_score > low.quality_score

    def test_unknown_confidence_gives_low_score(self):
        r = make_result(confidence="unknown")
        assert r.quality_score < make_result(confidence="high").quality_score

    def test_no_features_reduces_score(self):
        with_features    = make_result(confidence="high", key_features=["A", "B", "C"])
        without_features = make_result(confidence="high", key_features=[])
        assert with_features.quality_score > without_features.quality_score

    def test_empty_search_query_reduces_score(self):
        good  = make_result(amazon_search_query="test keyboard")
        empty = make_result(amazon_search_query="")
        assert good.quality_score > empty.quality_score


# ── cost_str ──────────────────────────────────────────────────────────────────

class TestCostStr:
    def test_sub_millidollar_shows_m_notation(self):
        r = make_result(cost_usd=0.0005)
        assert "m" in r.cost_str   # milli-dollar notation

    def test_over_millidollar_shows_dollar(self):
        r = make_result(cost_usd=0.01)
        assert r.cost_str.startswith("$")
        assert "m" not in r.cost_str

    def test_zero_cost(self):
        r = make_result(cost_usd=0.0)
        # Should not crash; shows something
        assert "$" in r.cost_str or "m" in r.cost_str


# ── to_product_info ───────────────────────────────────────────────────────────

class TestToProductInfo:
    def test_all_fields_mapped(self):
        r    = make_result()
        info = r.to_product_info()
        assert info.product_name          == r.product_name
        assert info.brand                 == r.brand
        assert info.category              == r.category
        assert info.key_features          == r.key_features
        assert info.amazon_search_query   == r.amazon_search_query
        assert info.alternative_query     == r.alternative_query
        assert info.confidence            == r.confidence
        assert r.provider_name in info.notes

    def test_no_brand(self):
        r    = make_result(brand=None)
        info = r.to_product_info()
        assert info.brand is None


# ── parse_json_response ───────────────────────────────────────────────────────

class TestParseJsonResponse:
    def test_plain_json(self):
        raw = '{"product_name": "Widget", "confidence": "high"}'
        data = parse_json_response(raw, "testprovider")
        assert data["product_name"] == "Widget"
        assert data["confidence"] == "high"

    def test_json_fenced_with_backticks(self):
        raw = "```json\n{\"product_name\": \"Widget\"}\n```"
        data = parse_json_response(raw, "testprovider")
        assert data["product_name"] == "Widget"

    def test_json_fenced_without_language_hint(self):
        raw = "```\n{\"product_name\": \"Widget\"}\n```"
        data = parse_json_response(raw, "testprovider")
        assert data["product_name"] == "Widget"

    def test_leading_trailing_whitespace(self):
        raw = '  \n  {"product_name": "Widget"}  \n  '
        data = parse_json_response(raw, "testprovider")
        assert data["product_name"] == "Widget"

    def test_invalid_json_raises_value_error(self):
        raw = "This is not JSON at all."
        with pytest.raises(ValueError, match="JSON parse error"):
            parse_json_response(raw, "testprovider")

    def test_truncated_json_raises_value_error(self):
        raw = '{"product_name": "Wid'
        with pytest.raises(ValueError):
            parse_json_response(raw, "testprovider")
