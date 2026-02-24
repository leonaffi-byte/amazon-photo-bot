"""
Tests for url_shortener.py.

Covers:
  - _try_custom: skipped when SHORTENER_ENABLED=False, code reuse for same URL
  - _try_bitly: called when token is present, skipped otherwise
  - _try_tinyurl: fallback when bitly unavailable
  - shorten(): priority chain custom → bitly → tinyurl → original
  - shorten_many(): concurrent, returns {long: short} dict
  - active_backend_name(): reflects config state
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

import config
import database as db
import url_shortener


@pytest_asyncio.fixture(autouse=True)
async def init_db(tmp_data_dir):
    await db.init_db()


@pytest.fixture(autouse=True)
def disable_shortener(monkeypatch):
    """Default: custom shortener OFF."""
    monkeypatch.setattr(config, "SHORTENER_ENABLED", False)
    monkeypatch.setattr(config, "SHORTENER_BASE_URL", None)


# ── _try_custom ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestTryCustom:
    async def test_returns_none_when_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "SHORTENER_ENABLED", False)
        result = await url_shortener._try_custom("https://amazon.com/dp/B001")
        assert result is None

    async def test_returns_none_when_no_base_url(self, monkeypatch):
        monkeypatch.setattr(config, "SHORTENER_ENABLED", True)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", None)
        result = await url_shortener._try_custom("https://amazon.com/dp/B001")
        assert result is None

    async def test_creates_short_link(self, monkeypatch):
        monkeypatch.setattr(config, "SHORTENER_ENABLED", True)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", "https://go.example.com")
        result = await url_shortener._try_custom("https://amazon.com/dp/B001")
        assert result is not None
        assert result.startswith("https://go.example.com/")

    async def test_reuses_existing_code(self, monkeypatch):
        monkeypatch.setattr(config, "SHORTENER_ENABLED", True)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", "https://go.example.com")
        long_url = "https://amazon.com/dp/B0UNIQUE"
        first  = await url_shortener._try_custom(long_url)
        second = await url_shortener._try_custom(long_url)
        assert first == second   # same code reused


# ── _try_bitly ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestTryBitly:
    async def test_skipped_when_no_token(self, monkeypatch):
        with patch("url_shortener.key_store.get", new_callable=AsyncMock, return_value=None):
            result = await url_shortener._try_bitly("https://amazon.com/dp/B001")
        assert result is None

    async def test_returns_bitly_link_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"link": "https://bit.ly/abc123"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("url_shortener.key_store.get", new_callable=AsyncMock, return_value="bitly-token"):
            with patch("url_shortener.aiohttp.ClientSession", return_value=mock_session):
                result = await url_shortener._try_bitly("https://amazon.com/dp/B001")

        assert result == "https://bit.ly/abc123"

    async def test_returns_cached_result(self):
        long_url = "https://amazon.com/dp/B0CACHED"
        await db.cache_short_url(long_url, "https://bit.ly/cached")

        # Even without a token, returns the cached URL
        with patch("url_shortener.key_store.get", new_callable=AsyncMock, return_value=None):
            result = await url_shortener._try_bitly(long_url)
        # Cache check happens before token check in the current code when "bit.ly" in cached
        # (only returns cached if "bit.ly" is in the URL)
        assert result == "https://bit.ly/cached"


# ── _try_tinyurl ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestTryTinyurl:
    async def test_returns_short_url_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="https://tinyurl.com/xyz789")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("url_shortener.aiohttp.ClientSession", return_value=mock_session):
            result = await url_shortener._try_tinyurl("https://amazon.com/dp/B001")

        assert result == "https://tinyurl.com/xyz789"

    async def test_returns_none_on_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("url_shortener.aiohttp.ClientSession", return_value=mock_session):
            result = await url_shortener._try_tinyurl("https://amazon.com/dp/B001")

        assert result is None

    async def test_returns_cached_result(self):
        long_url = "https://amazon.com/dp/B0TINY_CACHE"
        await db.cache_short_url(long_url, "https://tinyurl.com/cached")
        result = await url_shortener._try_tinyurl(long_url)
        assert result == "https://tinyurl.com/cached"


# ── shorten() priority chain ──────────────────────────────────────────────────

@pytest.mark.asyncio
class TestShorten:
    async def test_falls_back_to_original_url_on_all_failures(self):
        long_url = "https://amazon.com/dp/B0FALLBACK"
        with patch("url_shortener._try_custom",  new_callable=AsyncMock, return_value=None):
            with patch("url_shortener._try_bitly",   new_callable=AsyncMock, return_value=None):
                with patch("url_shortener._try_tinyurl", new_callable=AsyncMock, return_value=None):
                    result = await url_shortener.shorten(long_url)
        assert result == long_url

    async def test_custom_used_first_when_available(self, monkeypatch):
        monkeypatch.setattr(config, "SHORTENER_ENABLED", True)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", "https://go.example.com")
        long_url = "https://amazon.com/dp/B0CUSTOM1"

        with patch("url_shortener._try_bitly",   new_callable=AsyncMock, return_value="https://bit.ly/x") as mock_bitly:
            with patch("url_shortener._try_tinyurl", new_callable=AsyncMock) as mock_tiny:
                result = await url_shortener.shorten(long_url)

        # Custom shortener creates a link; bitly and tinyurl should not be called
        assert result.startswith("https://go.example.com/")
        mock_bitly.assert_not_called()
        mock_tiny.assert_not_called()


# ── shorten_many() ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestShortenMany:
    async def test_returns_dict_with_all_urls(self):
        urls = [
            "https://amazon.com/dp/B001",
            "https://amazon.com/dp/B002",
            "https://amazon.com/dp/B003",
        ]
        # All shorteners return None → original URLs used
        with patch("url_shortener._try_custom",  new_callable=AsyncMock, return_value=None):
            with patch("url_shortener._try_bitly",   new_callable=AsyncMock, return_value=None):
                with patch("url_shortener._try_tinyurl", new_callable=AsyncMock, return_value=None):
                    result = await url_shortener.shorten_many(urls)

        assert set(result.keys()) == set(urls)
        for long, short in result.items():
            assert short == long   # fell back to original

    async def test_concurrent_execution(self):
        """shorten_many must return correct mapping even when called concurrently."""
        urls = [f"https://amazon.com/dp/B{i:010d}" for i in range(10)]

        with patch("url_shortener._try_custom",  new_callable=AsyncMock, return_value=None):
            with patch("url_shortener._try_bitly",   new_callable=AsyncMock, return_value=None):
                with patch("url_shortener._try_tinyurl", new_callable=AsyncMock, return_value=None):
                    result = await url_shortener.shorten_many(urls)

        assert len(result) == 10
        for url in urls:
            assert url in result


# ── active_backend_name() ─────────────────────────────────────────────────────

class TestActiveBackendName:
    def test_shows_custom_when_enabled(self, monkeypatch):
        monkeypatch.setattr(config, "SHORTENER_ENABLED", True)
        monkeypatch.setattr(config, "SHORTENER_BASE_URL", "https://go.example.com")
        name = url_shortener.active_backend_name()
        assert "Custom" in name
        assert "go.example.com" in name

    def test_shows_tinyurl_when_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "SHORTENER_ENABLED", False)
        name = url_shortener.active_backend_name()
        assert "TinyURL" in name
