"""
Tests for providers/manager.py.

Covers:
  - _model_enabled(): reads env var, defaults to True
  - _build_providers(): loads only enabled models
  - get_providers(): caches result
  - cheapest_provider(): returns lowest cost provider
  - analyse_image(): best / cheapest / compare / single modes
  - analyse_image(): graceful degradation when providers fail
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import providers.manager as manager_mod
from providers.base import ProviderResult, VisionProvider
from providers.manager import (
    _model_enabled,
    analyse_image,
    cheapest_provider,
    get_providers,
)


@pytest.fixture(autouse=True)
def reset_providers():
    """Each test starts with a clean provider cache."""
    manager_mod._providers = {}
    yield
    manager_mod._providers = {}


def make_provider(name: str, model: str, cost_per_image: float = 0.001,
                  cost_per_1k_input_tokens: float = 0.001) -> VisionProvider:
    p = MagicMock(spec=VisionProvider)
    p.name = name
    p.model_id = model
    p.full_name = f"{name}/{model}"
    p.cost_per_image = cost_per_image
    p.cost_per_1k_input_tokens = cost_per_1k_input_tokens
    p.cost_per_1k_output_tokens = cost_per_1k_input_tokens * 3
    return p


def make_result(provider_name: str, confidence: str = "high") -> ProviderResult:
    return ProviderResult(
        provider_name=provider_name,
        model_id="model",
        product_name="Test Product",
        brand="TestBrand",
        category="Electronics",
        key_features=["A", "B"],
        amazon_search_query="test product",
        alternative_query="test product alt",
        confidence=confidence,
        notes="",
        latency_ms=500,
        input_tokens=700,
        output_tokens=120,
        cost_usd=0.002,
    )


# ── _model_enabled ─────────────────────────────────────────────────────────────

class TestModelEnabled:
    def test_default_true_when_not_set(self, monkeypatch):
        monkeypatch.delenv("ENABLE_GPT_4O", raising=False)
        assert _model_enabled("ENABLE_GPT_4O") is True

    def test_explicit_true(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GPT_4O", "true")
        assert _model_enabled("ENABLE_GPT_4O") is True

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GPT_4O", "false")
        assert _model_enabled("ENABLE_GPT_4O") is False

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GPT_4O_MINI", "0")
        assert _model_enabled("ENABLE_GPT_4O_MINI") is False

    def test_no_disables(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GPT_4O_MINI", "no")
        assert _model_enabled("ENABLE_GPT_4O_MINI") is False

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GPT_4O", "FALSE")
        assert _model_enabled("ENABLE_GPT_4O") is False


# ── _build_providers (via get_providers with mocked key_store) ────────────────

@pytest.mark.asyncio
class TestBuildProviders:
    async def test_openai_not_loaded_when_key_missing(self, monkeypatch):
        async def mock_get(key_name):
            return None  # no keys set

        # key_store is imported lazily inside _build_providers → patch at module level
        with patch("key_store.get", side_effect=mock_get):
            with pytest.raises(RuntimeError, match="No vision providers"):
                await manager_mod._build_providers()

    async def test_openai_loaded_when_key_present(self, monkeypatch):
        async def mock_get(key_name):
            if key_name == "openai_api_key":
                return "sk-test"
            return None

        mock_provider = make_provider("openai", "gpt-4o")

        with patch("key_store.get", side_effect=mock_get):
            with patch("providers.openai_provider.OpenAIProvider", return_value=mock_provider):
                providers = await manager_mod._build_providers()

        assert len(providers) > 0

    async def test_model_skipped_when_env_disabled(self, monkeypatch):
        monkeypatch.setenv("ENABLE_GPT_4O", "false")
        monkeypatch.setenv("ENABLE_GPT_4O_MINI", "false")

        async def mock_get(key_name):
            if key_name == "openai_api_key":
                return "sk-test"
            return None

        with patch("key_store.get", side_effect=mock_get):
            with pytest.raises(RuntimeError, match="No vision providers"):
                await manager_mod._build_providers()


# ── get_providers caching ─────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestGetProviders:
    async def test_caches_on_second_call(self):
        p1 = make_provider("openai", "gpt-4o")
        p1.full_name = "openai/gpt-4o"
        fake_providers = {"openai/gpt-4o": p1}

        build_mock = AsyncMock(return_value=fake_providers)
        with patch.object(manager_mod, "_build_providers", build_mock):
            await get_providers()
            await get_providers()

        assert build_mock.call_count == 1  # Only built once (cached)

    async def test_rebuilds_after_cache_cleared(self):
        p1 = make_provider("openai", "gpt-4o")
        p1.full_name = "openai/gpt-4o"
        fake_providers = {"openai/gpt-4o": p1}

        build_mock = AsyncMock(return_value=fake_providers)
        with patch.object(manager_mod, "_build_providers", build_mock):
            await get_providers()
            manager_mod._providers = {}   # simulate key change clearing cache
            await get_providers()

        assert build_mock.call_count == 2


# ── cheapest_provider ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestCheapestProvider:
    async def test_returns_cheapest_by_image_cost(self):
        cheap  = make_provider("google", "gemini-1.5-flash", cost_per_image=0.00002,  cost_per_1k_input_tokens=0.000075)
        medium = make_provider("openai", "gpt-4o-mini",      cost_per_image=0.000023, cost_per_1k_input_tokens=0.00015)
        expensive = make_provider("openai", "gpt-4o",        cost_per_image=0.003825, cost_per_1k_input_tokens=0.005)

        manager_mod._providers = {
            cheap.full_name: cheap,
            medium.full_name: medium,
            expensive.full_name: expensive,
        }

        result = await cheapest_provider()
        assert result is cheap


# ── analyse_image ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAnalyseImage:
    def _setup_providers(self, *names_and_confidences):
        providers = {}
        for name, conf in names_and_confidences:
            p = make_provider(*name.split("/"))
            result = make_result(provider_name=name, confidence=conf)
            p.analyse = AsyncMock(return_value=result)
            p.full_name = name
            providers[name] = p
        manager_mod._providers = providers
        return providers

    async def test_best_mode_returns_highest_quality(self):
        self._setup_providers(
            ("openai/gpt-4o-mini", "low"),
            ("openai/gpt-4o",      "high"),
        )
        winner, all_results = await analyse_image(b"fake_image", mode="best")
        assert winner.confidence == "high"
        assert len(all_results) == 2

    async def test_cheapest_mode_calls_only_one_provider(self):
        providers = self._setup_providers(
            ("google/gemini-1.5-flash", "medium"),
            ("openai/gpt-4o",           "high"),
        )
        # Make gemini cheapest
        providers["google/gemini-1.5-flash"].cost_per_image = 0.00001
        providers["google/gemini-1.5-flash"].cost_per_1k_input_tokens = 0.000075
        providers["openai/gpt-4o"].cost_per_image = 0.004
        providers["openai/gpt-4o"].cost_per_1k_input_tokens = 0.005

        winner, all_results = await analyse_image(b"fake_image", mode="cheapest")
        # Only cheapest provider called
        assert len(all_results) == 1

    async def test_compare_mode_returns_all_results(self):
        self._setup_providers(
            ("openai/gpt-4o-mini", "medium"),
            ("openai/gpt-4o",      "high"),
            ("google/gemini",       "low"),
        )
        winner, all_results = await analyse_image(b"fake_image", mode="compare")
        assert len(all_results) == 3

    async def test_single_mode_calls_named_provider(self):
        self._setup_providers(
            ("openai/gpt-4o-mini", "medium"),
            ("openai/gpt-4o",      "high"),
        )
        winner, all_results = await analyse_image(b"fake", mode="single:openai/gpt-4o")
        assert len(all_results) == 1
        assert winner.provider_name == "openai/gpt-4o"

    async def test_single_mode_unknown_provider_raises(self):
        self._setup_providers(("openai/gpt-4o", "high"))
        with pytest.raises(ValueError, match="not available"):
            await analyse_image(b"fake", mode="single:nonexistent/model")

    async def test_failed_providers_excluded_from_results(self):
        self._setup_providers(
            ("openai/gpt-4o-mini", "medium"),
            ("openai/gpt-4o",      "high"),
        )
        # Make one provider fail
        manager_mod._providers["openai/gpt-4o-mini"].analyse = AsyncMock(
            side_effect=Exception("API error")
        )
        winner, all_results = await analyse_image(b"fake", mode="best")
        assert len(all_results) == 1
        assert winner.provider_name == "openai/gpt-4o"

    async def test_all_providers_fail_raises_runtime_error(self):
        self._setup_providers(("openai/gpt-4o", "high"))
        manager_mod._providers["openai/gpt-4o"].analyse = AsyncMock(
            side_effect=Exception("API down")
        )
        with pytest.raises(RuntimeError, match="All vision providers failed"):
            await analyse_image(b"fake", mode="best")
