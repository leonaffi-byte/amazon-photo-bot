"""
Tests for translator.py.

Covers:
  - detect_language() for English, Hebrew, Russian
  - translate_and_refine() with mocked LLM for English input (refinement)
  - translate_and_refine() with mocked LLM for Hebrew input (translation)
  - _call_llm() tries providers in cheapest-first order, falls back on failure
  - Timeout handling (asyncio.wait_for wraps the calls)
  - Edge cases: empty LLM response, single-line LLM response
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import translator


@pytest.fixture(autouse=True)
def clear_llm_cache():
    """Clear the LLM cache between tests to prevent interference."""
    translator._LLM_CACHE.clear()
    yield
    translator._LLM_CACHE.clear()


# ── detect_language() ─────────────────────────────────────────────────────────

class TestDetectLanguage:
    def test_english(self):
        assert translator.detect_language("hello") == "en"

    def test_hebrew(self):
        assert translator.detect_language("שלום") == "he"

    def test_russian(self):
        assert translator.detect_language("привет") == "ru"

    def test_mixed_hebrew_english(self):
        """Hebrew characters should be detected even when mixed with English."""
        assert translator.detect_language("buy שולחן table") == "he"

    def test_empty_string(self):
        assert translator.detect_language("") == "en"

    def test_numbers_only(self):
        assert translator.detect_language("12345") == "en"


# ── translate_and_refine() — English input ────────────────────────────────────

class TestTranslateAndRefineEnglish:
    async def test_english_returns_original_and_refined(self):
        """For English input, the original text is returned alongside a refined query."""
        with patch("translator._call_llm", new_callable=AsyncMock, return_value="wireless keyboard bluetooth"):
            english_text, query = await translator.translate_and_refine("I need a wireless keyboard")

        assert english_text == "I need a wireless keyboard"
        assert query == "wireless keyboard bluetooth"

    async def test_english_fallback_on_llm_none(self):
        """If _call_llm returns None, the original text is used as the query."""
        with patch("translator._call_llm", new_callable=AsyncMock, return_value=None):
            english_text, query = await translator.translate_and_refine("red shoes")

        assert english_text == "red shoes"
        assert query == "red shoes"


# ── translate_and_refine() — Hebrew input ─────────────────────────────────────

class TestTranslateAndRefineHebrew:
    async def test_hebrew_returns_translation_and_query(self):
        """For Hebrew input, _call_llm is invoked and two lines are parsed."""
        llm_response = "Hello\nHello greeting card"
        with patch("translator._call_llm", new_callable=AsyncMock, return_value=llm_response):
            english_text, query = await translator.translate_and_refine("שלום")

        assert english_text == "Hello"
        assert query == "Hello greeting card"

    async def test_hebrew_single_line_response(self):
        """If LLM returns only one line, both fields use it."""
        with patch("translator._call_llm", new_callable=AsyncMock, return_value="wireless mouse"):
            english_text, query = await translator.translate_and_refine("עכבר אלחוטי")

        assert english_text == "wireless mouse"
        assert query == "wireless mouse"

    async def test_hebrew_empty_response_falls_back(self):
        """If LLM returns None for Hebrew, the original text is returned as-is."""
        with patch("translator._call_llm", new_callable=AsyncMock, return_value=None):
            english_text, query = await translator.translate_and_refine("שלום")

        assert english_text == "שלום"
        assert query == "שלום"


# ── _call_llm() — provider fallback chain ─────────────────────────────────────

class TestCallLlm:
    async def test_uses_gemini_when_google_key_available(self):
        """When google_api_key is set, Gemini is tried and its result returned."""
        mock_resp = MagicMock()
        mock_resp.text = "gemini result"

        mock_generate = AsyncMock(return_value=mock_resp)
        mock_aio_models = MagicMock()
        mock_aio_models.generate_content = mock_generate
        mock_aio = MagicMock()
        mock_aio.models = mock_aio_models
        mock_client = MagicMock()
        mock_client.aio = mock_aio

        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        async def fake_get(key):
            if key == "google_api_key":
                return "fake-google-key"
            return None

        with patch("key_store.get", side_effect=fake_get):
            with patch.dict("sys.modules", {"google": MagicMock(genai=mock_genai), "google.genai": mock_genai}):
                result = await translator._call_llm("test prompt")

        assert result == "gemini result"

    async def test_falls_back_to_openai_when_no_google_key(self):
        """If google_api_key is not set, OpenAI is tried next."""
        mock_message = MagicMock()
        mock_message.content = "openai result"
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        mock_create = AsyncMock(return_value=mock_resp)
        mock_completions = MagicMock()
        mock_completions.create = mock_create
        mock_chat = MagicMock()
        mock_chat.completions = mock_completions
        mock_client = MagicMock()
        mock_client.chat = mock_chat

        async def fake_get(key):
            if key == "openai_api_key":
                return "fake-openai-key"
            return None

        with patch("key_store.get", side_effect=fake_get):
            with patch("openai.AsyncOpenAI", return_value=mock_client):
                result = await translator._call_llm("test prompt")

        assert result == "openai result"

    async def test_falls_back_through_all_providers(self):
        """If Gemini and OpenAI keys are missing, Anthropic is tried."""
        mock_text_block = MagicMock()
        mock_text_block.text = "anthropic result"
        mock_msg = MagicMock()
        mock_msg.content = [mock_text_block]

        mock_create = AsyncMock(return_value=mock_msg)
        mock_messages = MagicMock()
        mock_messages.create = mock_create
        mock_client = MagicMock()
        mock_client.messages = mock_messages

        async def fake_get(key):
            if key == "anthropic_api_key":
                return "fake-anthropic-key"
            return None

        with patch("key_store.get", side_effect=fake_get):
            with patch("anthropic.AsyncAnthropic", return_value=mock_client):
                result = await translator._call_llm("test prompt")

        assert result == "anthropic result"

    async def test_returns_none_when_no_providers_available(self):
        """If no API keys are set, _call_llm returns None."""
        async def fake_get(key):
            return None

        with patch("key_store.get", side_effect=fake_get):
            result = await translator._call_llm("test prompt")

        assert result is None


# ── Timeout handling ──────────────────────────────────────────────────────────

class TestTimeout:
    async def test_timeout_on_english_refinement(self):
        """If _call_llm takes too long for English input, original text is used."""
        async def slow_llm(prompt):
            await asyncio.sleep(60)
            return "should not reach"

        with patch("translator._call_llm", side_effect=slow_llm):
            english_text, query = await translator.translate_and_refine("fast shoes")

        # On timeout, refined = original text
        assert english_text == "fast shoes"
        assert query == "fast shoes"

    async def test_timeout_on_hebrew_translation(self):
        """If _call_llm times out for Hebrew input, original text is returned."""
        async def slow_llm(prompt):
            await asyncio.sleep(60)
            return "should not reach"

        with patch("translator._call_llm", side_effect=slow_llm):
            english_text, query = await translator.translate_and_refine("שלום")

        # On timeout, falls back to original text
        assert english_text == "שלום"
        assert query == "שלום"
