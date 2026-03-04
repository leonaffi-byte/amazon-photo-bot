"""
Tests for analytics export functions in database.py.

Covers:
  - export_search_logs: CSV and JSON formats, date filtering
  - export_api_costs: CSV and JSON formats, date filtering
  - export_user_activity: CSV and JSON formats, aggregation, date filtering
  - Empty dataset handling for all export types
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

import database as db


@pytest_asyncio.fixture(autouse=True)
async def init(tmp_data_dir):
    """Initialise the DB schema before every test."""
    await db.init_db()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_csv(csv_str: str) -> list[dict]:
    """Parse a CSV string into a list of dicts (one per row)."""
    reader = csv.DictReader(io.StringIO(csv_str))
    return list(reader)


async def _seed_search_logs():
    """Insert a few search log entries with known data."""
    await db.log_search(
        user_id=100, product_name="Keyboard",
        tag_used="tag-20", provider_used="openai/gpt-4o",
        result_count=10, israel_filter=True, search_type="photo",
    )
    await db.log_search(
        user_id=100, product_name="Mouse",
        tag_used="tag-20", provider_used="anthropic/claude-3",
        result_count=5, israel_filter=False, search_type="text",
    )
    await db.log_search(
        user_id=200, product_name="Monitor",
        tag_used="none", provider_used="openai/gpt-4o",
        result_count=8, israel_filter=False, search_type="photo",
    )


async def _seed_api_costs():
    """Insert a few API cost log entries."""
    await db.log_api_cost(
        provider_name="openai/gpt-4o", cost_usd=0.015,
        input_tokens=1000, output_tokens=200, user_id=100,
    )
    await db.log_api_cost(
        provider_name="anthropic/claude-3", cost_usd=0.008,
        input_tokens=800, output_tokens=150, user_id=100,
    )
    await db.log_api_cost(
        provider_name="openai/gpt-4o", cost_usd=0.012,
        input_tokens=900, output_tokens=180, user_id=200,
    )


# ── export_search_logs ────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestExportSearchLogs:
    async def test_csv_format_returns_string(self):
        await _seed_search_logs()
        result = await db.export_search_logs(fmt="csv")
        assert isinstance(result, str)
        rows = _parse_csv(result)
        assert len(rows) == 3

    async def test_csv_has_correct_headers(self):
        await _seed_search_logs()
        result = await db.export_search_logs(fmt="csv")
        rows = _parse_csv(result)
        expected_headers = {
            "id", "user_id", "product_name", "tag_used", "provider_used",
            "result_count", "israel_filter", "searched_at", "search_type",
        }
        assert set(rows[0].keys()) == expected_headers

    async def test_csv_content_matches_inserted_data(self):
        await _seed_search_logs()
        result = await db.export_search_logs(fmt="csv")
        rows = _parse_csv(result)
        product_names = {r["product_name"] for r in rows}
        assert product_names == {"Keyboard", "Mouse", "Monitor"}

    async def test_json_format_returns_list(self):
        await _seed_search_logs()
        result = await db.export_search_logs(fmt="json")
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(r, dict) for r in result)

    async def test_json_contains_all_fields(self):
        await _seed_search_logs()
        result = await db.export_search_logs(fmt="json")
        expected_keys = {
            "id", "user_id", "product_name", "tag_used", "provider_used",
            "result_count", "israel_filter", "searched_at", "search_type",
        }
        assert set(result[0].keys()) == expected_keys

    async def test_empty_table_csv(self):
        result = await db.export_search_logs(fmt="csv")
        assert isinstance(result, str)
        rows = _parse_csv(result)
        assert len(rows) == 0

    async def test_empty_table_json(self):
        result = await db.export_search_logs(fmt="json")
        assert isinstance(result, list)
        assert len(result) == 0

    async def test_date_filter_future_returns_empty(self):
        await _seed_search_logs()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        result = await db.export_search_logs(fmt="json", start_date=future)
        assert len(result) == 0

    async def test_date_filter_past_returns_all(self):
        await _seed_search_logs()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = await db.export_search_logs(fmt="json", start_date=past)
        assert len(result) == 3


# ── export_api_costs ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestExportApiCosts:
    async def test_csv_format_returns_string(self):
        await _seed_api_costs()
        result = await db.export_api_costs(fmt="csv")
        assert isinstance(result, str)
        rows = _parse_csv(result)
        assert len(rows) == 3

    async def test_csv_has_correct_headers(self):
        await _seed_api_costs()
        result = await db.export_api_costs(fmt="csv")
        rows = _parse_csv(result)
        expected_headers = {
            "id", "ts", "user_id", "provider_name",
            "cost_usd", "input_tokens", "output_tokens",
        }
        assert set(rows[0].keys()) == expected_headers

    async def test_json_format_returns_list(self):
        await _seed_api_costs()
        result = await db.export_api_costs(fmt="json")
        assert isinstance(result, list)
        assert len(result) == 3

    async def test_json_cost_values(self):
        await _seed_api_costs()
        result = await db.export_api_costs(fmt="json")
        costs = {r["cost_usd"] for r in result}
        assert 0.015 in costs
        assert 0.008 in costs
        assert 0.012 in costs

    async def test_empty_table_csv(self):
        result = await db.export_api_costs(fmt="csv")
        rows = _parse_csv(result)
        assert len(rows) == 0

    async def test_empty_table_json(self):
        result = await db.export_api_costs(fmt="json")
        assert len(result) == 0

    async def test_date_filter(self):
        await _seed_api_costs()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        result = await db.export_api_costs(fmt="json", start_date=future)
        assert len(result) == 0


# ── export_user_activity ──────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestExportUserActivity:
    async def test_csv_format_returns_string(self):
        await _seed_search_logs()
        result = await db.export_user_activity(fmt="csv")
        assert isinstance(result, str)
        rows = _parse_csv(result)
        # Two distinct users: 100, 200
        assert len(rows) == 2

    async def test_csv_has_correct_headers(self):
        await _seed_search_logs()
        result = await db.export_user_activity(fmt="csv")
        rows = _parse_csv(result)
        expected_headers = {
            "user_id", "total_searches", "photo_searches", "text_searches",
            "providers_used", "first_search", "last_search",
        }
        assert set(rows[0].keys()) == expected_headers

    async def test_json_aggregation_correct(self):
        await _seed_search_logs()
        result = await db.export_user_activity(fmt="json")
        assert isinstance(result, list)
        # User 100 should have 2 searches
        user_100 = next(r for r in result if r["user_id"] == 100)
        assert user_100["total_searches"] == 2
        assert user_100["photo_searches"] == 1
        assert user_100["text_searches"] == 1

    async def test_json_user_200_has_one_search(self):
        await _seed_search_logs()
        result = await db.export_user_activity(fmt="json")
        user_200 = next(r for r in result if r["user_id"] == 200)
        assert user_200["total_searches"] == 1
        assert user_200["photo_searches"] == 1
        assert user_200["text_searches"] == 0

    async def test_providers_used_aggregation(self):
        await _seed_search_logs()
        result = await db.export_user_activity(fmt="json")
        user_100 = next(r for r in result if r["user_id"] == 100)
        # Should contain both providers
        providers = set(user_100["providers_used"].split(","))
        assert "openai/gpt-4o" in providers
        assert "anthropic/claude-3" in providers

    async def test_ordered_by_total_searches_desc(self):
        await _seed_search_logs()
        result = await db.export_user_activity(fmt="json")
        # User 100 (2 searches) should come before User 200 (1 search)
        assert result[0]["user_id"] == 100
        assert result[1]["user_id"] == 200

    async def test_empty_table(self):
        result = await db.export_user_activity(fmt="json")
        assert len(result) == 0

    async def test_date_filter(self):
        await _seed_search_logs()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        result = await db.export_user_activity(fmt="json", start_date=future)
        assert len(result) == 0


# ── Combined export (JSON) ────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCombinedExport:
    """Test that all three export functions work together for the All-JSON case."""

    async def test_all_exports_produce_valid_json(self):
        await _seed_search_logs()
        await _seed_api_costs()

        search_logs = await db.export_search_logs(fmt="json")
        api_costs = await db.export_api_costs(fmt="json")
        user_activity = await db.export_user_activity(fmt="json")

        combined = {
            "search_logs": search_logs,
            "api_costs": api_costs,
            "user_activity": user_activity,
        }
        # Verify it serializes cleanly
        json_str = json.dumps(combined, indent=2, default=str)
        parsed = json.loads(json_str)
        assert len(parsed["search_logs"]) == 3
        assert len(parsed["api_costs"]) == 3
        assert len(parsed["user_activity"]) == 2
