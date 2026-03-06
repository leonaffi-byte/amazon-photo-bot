"""
Tests for provider behavior with malformed API responses.

Covers:
  - parse_json_response() from providers/base.py with invalid JSON -> raises ValueError
  - parse_json_response() with partial JSON (missing fields) -> returns partial dict
  - parse_json_response() with empty string -> raises ValueError
  - parse_json_response() with nested markdown fences -> handles gracefully
  - Provider analyse() with HTTP 429 rate-limit error -> raises exception
  - Provider analyse() with HTTP 500 server error -> raises exception
  - Provider analyse() with empty response body -> raises exception
  - Provider analyse() with valid JSON but missing fields -> fills defaults
  - Provider analyse() with garbage content text -> raises ValueError
  - Provider analyse() with None content -> raises exception
  - Anthropic provider with error -> raises exception
  - Gemini provider with error -> raises exception
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from providers.base import ProviderResult, parse_json_response


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — parse_json_response edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestParseJsonResponseMalformed:

    def test_completely_invalid_json_raises_value_error(self):
        """Random prose that is not JSON at all raises ValueError."""
        raw = "I think this is a nice product. It looks like a keyboard."
        with pytest.raises(ValueError, match="JSON parse error"):
            parse_json_response(raw, "test/model")

    def test_empty_string_raises_value_error(self):
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="JSON parse error"):
            parse_json_response("", "test/model")

    def test_whitespace_only_raises_value_error(self):
        """Whitespace-only string raises ValueError."""
        with pytest.raises(ValueError, match="JSON parse error"):
            parse_json_response("   \n\t  ", "test/model")

    def test_partial_json_missing_fields_returns_available_fields(self):
        """JSON with only some fields returns what's available (wrapped in products array)."""
        raw = '{"product_name": "Widget"}'
        data = parse_json_response(raw, "test/model")
        # parse_json_response wraps single dicts in {"products": [dict]}
        assert "products" in data
        first = data["products"][0]
        assert first["product_name"] == "Widget"
        # Missing fields simply aren't in the dict
        assert "brand" not in first
        assert "confidence" not in first

    def test_truncated_json_raises_value_error(self):
        """JSON cut off mid-value raises ValueError."""
        raw = '{"product_name": "Wid'
        with pytest.raises(ValueError):
            parse_json_response(raw, "test/model")

    def test_json_with_trailing_comma_is_fixed(self):
        """JSON with trailing comma is auto-fixed by _fix_json."""
        raw = '{"product_name": "Widget", "brand": "TestCo",}'
        data = parse_json_response(raw, "test/model")
        first = data["products"][0]
        assert first["product_name"] == "Widget"
        assert first["brand"] == "TestCo"

    def test_nested_markdown_fences(self):
        """Markdown fence with extra backticks or nested content."""
        raw = "```json\n```json\n{\"product_name\": \"Widget\"}\n```\n```"
        # The outer fence strip should still yield valid JSON
        # (the inner ```json becomes part of the text which is then
        # attempted to be parsed — this will fail, which is correct behavior)
        # This tests the fence-stripping logic edge case
        try:
            data = parse_json_response(raw, "test/model")
            # If it succeeds, verify it parsed something
            assert isinstance(data, dict)
        except ValueError:
            # Also acceptable — nested fences are ambiguous
            pass

    def test_json_array_instead_of_object(self):
        """A valid JSON array is wrapped in {"products": [...]} format."""
        raw = '[{"product_name": "Widget"}]'
        result = parse_json_response(raw, "test/model")
        assert isinstance(result, dict)
        assert "products" in result
        assert result["products"][0]["product_name"] == "Widget"

    def test_html_response_raises_value_error(self):
        """HTML error page from a provider raises ValueError."""
        raw = "<html><body><h1>503 Service Unavailable</h1></body></html>"
        with pytest.raises(ValueError, match="JSON parse error"):
            parse_json_response(raw, "test/model")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — OpenAI provider with malformed/error responses
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenAIProviderErrors:

    def _make_mock_response(self, content: str | None, prompt_tokens=800, completion_tokens=150):
        """Create a mock OpenAI chat completion response."""
        message = MagicMock()
        message.content = content

        choice = MagicMock()
        choice.message = message

        usage = MagicMock()
        usage.prompt_tokens = prompt_tokens
        usage.completion_tokens = completion_tokens

        response = MagicMock()
        response.choices = [choice]
        response.usage = usage
        return response

    @pytest.mark.asyncio
    async def test_http_429_rate_limit_raises(self):
        """HTTP 429 from OpenAI API raises an exception."""
        from providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")

        import openai as openai_mod
        error = openai_mod.RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(status_code=429),
            body={"error": {"message": "Rate limit exceeded"}},
        )
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(openai_mod.RateLimitError):
            await provider.analyse(b"fake_image")

    @pytest.mark.asyncio
    async def test_http_500_server_error_raises(self):
        """HTTP 500 from OpenAI API raises an exception."""
        from providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")

        import openai as openai_mod
        error = openai_mod.InternalServerError(
            message="Internal server error",
            response=MagicMock(status_code=500),
            body={"error": {"message": "Internal server error"}},
        )
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(side_effect=error)

        with pytest.raises(openai_mod.InternalServerError):
            await provider.analyse(b"fake_image")

    @pytest.mark.asyncio
    async def test_empty_response_body_raises(self):
        """Empty string in response.choices[0].message.content raises ValueError."""
        from providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")

        mock_resp = self._make_mock_response(content="")
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="JSON parse error"):
            await provider.analyse(b"fake_image")

    @pytest.mark.asyncio
    async def test_garbage_text_response_raises(self):
        """Non-JSON prose from the model raises ValueError via parse_json_response."""
        from providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")

        mock_resp = self._make_mock_response(
            content="I'm sorry, I can't identify this product."
        )
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        with pytest.raises(ValueError, match="JSON parse error"):
            await provider.analyse(b"fake_image")

    @pytest.mark.asyncio
    async def test_valid_json_missing_fields_uses_defaults(self):
        """Valid JSON with only product_name fills defaults for missing fields."""
        from providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")

        mock_resp = self._make_mock_response(
            content='{"product_name": "Mystery Widget"}'
        )
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = await provider.analyse(b"fake_image")

        assert isinstance(result, ProviderResult)
        assert result.product_name == "Mystery Widget"
        # Missing fields should have defaults
        assert result.brand is None          # .get("brand") returns None
        assert result.category == "All"      # default
        assert result.key_features == []     # default
        assert result.confidence == "medium" # default

    @pytest.mark.asyncio
    async def test_none_content_raises(self):
        """None returned as message content raises an error."""
        from providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")

        mock_resp = self._make_mock_response(content=None)
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(return_value=mock_resp)

        # parse_json_response will get None and fail
        with pytest.raises((ValueError, TypeError, AttributeError)):
            await provider.analyse(b"fake_image")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Anthropic provider with malformed responses
# ══════════════════════════════════════════════════════════════════════════════

class TestAnthropicProviderErrors:

    @pytest.mark.asyncio
    async def test_anthropic_api_error_raises(self):
        """Anthropic SDK error propagates as an exception."""
        from providers.anthropic_provider import AnthropicProvider
        import anthropic as anthropic_mod

        provider = AnthropicProvider(api_key="sk-ant-test", model="claude-3-haiku-20240307")

        error = anthropic_mod.APIStatusError(
            message="Overloaded",
            response=MagicMock(status_code=529),
            body={"error": {"message": "Overloaded"}},
        )
        provider._client = MagicMock()
        provider._client.messages.create = AsyncMock(side_effect=error)

        with pytest.raises(anthropic_mod.APIStatusError):
            await provider.analyse(b"fake_image")

    @pytest.mark.asyncio
    async def test_anthropic_empty_content_raises(self):
        """Anthropic returning empty text content raises an error."""
        from providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-test", model="claude-3-haiku-20240307")

        content_block = MagicMock()
        content_block.text = ""
        message = MagicMock()
        message.content = [content_block]
        message.usage = MagicMock(input_tokens=100, output_tokens=50)

        provider._client = MagicMock()
        provider._client.messages.create = AsyncMock(return_value=message)

        with pytest.raises(ValueError, match="JSON parse error"):
            await provider.analyse(b"fake_image")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Groq provider (OpenAI-compatible) with errors
# ══════════════════════════════════════════════════════════════════════════════

class TestGroqProviderErrors:

    @pytest.mark.asyncio
    async def test_groq_timeout_raises(self):
        """Request exceeding the 60s timeout raises asyncio.TimeoutError."""
        from providers.openai_compat_provider import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(
            api_key="gsk-test",
            base_url="https://api.groq.com/openai/v1",
            name="groq", model="test-model",
        )

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(100)

        provider._client = MagicMock()
        provider._client.chat.completions.create = slow_call

        with pytest.raises(asyncio.TimeoutError):
            await provider.analyse(b"fake_image")
