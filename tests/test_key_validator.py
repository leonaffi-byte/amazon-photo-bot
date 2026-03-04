"""
Tests for key_validator.py.

Covers:
  - validate_key(): individual key validators (mocked HTTP)
  - validate_key_pair(): multi-key services (DataForSEO, Azure)
  - validate_all_stored_keys(): bulk validation
  - Timeout / connection error handling
  - Skip-validation keys
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import key_validator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(status: int = 200, body: str = "{}", json_data: dict | None = None):
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    else:
        resp.json = AsyncMock(return_value={})
    # Support async context manager
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(response):
    """Create a mock aiohttp.ClientSession that returns the given response."""
    session = AsyncMock()
    session.get = MagicMock(return_value=response)
    session.post = MagicMock(return_value=response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ══════════════════════════════════════════════════════════════════════════════
# INDIVIDUAL VALIDATORS
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestValidateOpenAI:
    async def test_valid_key_returns_true(self):
        resp = _mock_response(200)
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_openai("sk-valid-key")
        assert ok is True
        assert msg == ""

    async def test_invalid_key_returns_false(self):
        resp = _mock_response(401, body='{"error": "Invalid API key"}')
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_openai("sk-bad-key")
        assert ok is False
        assert "401" in msg

    async def test_connection_error_returns_false(self):
        session = AsyncMock()
        session.get = MagicMock(side_effect=Exception("Connection refused"))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_openai("sk-test")
        assert ok is False
        assert "Connection" in msg


@pytest.mark.asyncio
class TestValidateAnthropic:
    async def test_valid_key(self):
        resp = _mock_response(200)
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_anthropic("sk-ant-valid")
        assert ok is True

    async def test_invalid_key(self):
        resp = _mock_response(401, body="Unauthorized")
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_anthropic("sk-ant-bad")
        assert ok is False
        assert "401" in msg


@pytest.mark.asyncio
class TestValidateGoogle:
    async def test_valid_key(self):
        resp = _mock_response(200)
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_google("AIza-valid-key")
        assert ok is True

    async def test_invalid_key(self):
        resp = _mock_response(400, body="API key not valid")
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_google("bad-key")
        assert ok is False


@pytest.mark.asyncio
class TestValidateGroq:
    async def test_valid_key(self):
        resp = _mock_response(200)
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_groq("gsk_valid")
        assert ok is True

    async def test_invalid_key(self):
        resp = _mock_response(401, body="Invalid API Key")
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_groq("gsk_bad")
        assert ok is False


@pytest.mark.asyncio
class TestValidateOpenRouter:
    async def test_valid_key(self):
        resp = _mock_response(200)
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_openrouter("sk-or-valid")
        assert ok is True

    async def test_invalid_key(self):
        resp = _mock_response(401, body="Unauthorized")
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_openrouter("sk-or-bad")
        assert ok is False


@pytest.mark.asyncio
class TestValidateRapidAPI:
    async def test_valid_key(self):
        resp = _mock_response(200)
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_rapidapi("rapid-valid")
        assert ok is True

    async def test_invalid_key_403(self):
        resp = _mock_response(403, body="Forbidden")
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_rapidapi("rapid-bad")
        assert ok is False
        assert "unsubscribed" in msg.lower() or "Invalid" in msg


@pytest.mark.asyncio
class TestValidateCapSolver:
    async def test_valid_key_with_balance(self):
        resp = _mock_response(200, json_data={"errorId": 0, "balance": 5.42})
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_capsolver("CAP-valid")
        assert ok is True
        assert "5.42" in msg

    async def test_invalid_key(self):
        resp = _mock_response(200, json_data={"errorId": 1, "errorDescription": "Invalid key"})
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_capsolver("CAP-bad")
        assert ok is False
        assert "Invalid" in msg


# ══════════════════════════════════════════════════════════════════════════════
# DATAFORSEO (multi-key)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestValidateDataForSEO:
    async def test_valid_credentials(self):
        resp = _mock_response(200, json_data={"status_code": 20000})
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_dataforseo("user@test.com", "pass123")
        assert ok is True

    async def test_invalid_credentials(self):
        resp = _mock_response(401, body="Unauthorized")
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_dataforseo("bad@test.com", "wrong")
        assert ok is False
        assert "401" in msg

    async def test_api_error_code(self):
        resp = _mock_response(200, json_data={"status_code": 40100, "status_message": "Auth failed"})
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_dataforseo("user@test.com", "wrong")
        assert ok is False
        assert "Auth failed" in msg


# ══════════════════════════════════════════════════════════════════════════════
# AZURE OPENAI (multi-key)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestValidateAzureOpenAI:
    async def test_valid_key_with_endpoint(self):
        resp = _mock_response(200)
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_azure_openai(
                "azure-key", "https://myresource.openai.azure.com/"
            )
        assert ok is True

    async def test_missing_endpoint(self):
        ok, msg = await key_validator._validate_azure_openai("azure-key", None)
        assert ok is False
        assert "endpoint" in msg.lower()

    async def test_invalid_key(self):
        resp = _mock_response(401, body="Unauthorized")
        session = _mock_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            ok, msg = await key_validator._validate_azure_openai(
                "bad-key", "https://myresource.openai.azure.com"
            )
        assert ok is False


# ══════════════════════════════════════════════════════════════════════════════
# validate_key() — dispatch
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestValidateKey:
    async def test_skip_validation_keys_return_true(self):
        for key_name in key_validator._SKIP_VALIDATION:
            ok, msg = await key_validator.validate_key(key_name, "any-value")
            assert ok is True

    async def test_unknown_key_returns_true(self):
        ok, msg = await key_validator.validate_key("totally_unknown_key", "val")
        assert ok is True

    async def test_dispatches_to_correct_validator(self):
        with patch.object(key_validator, "_validate_openai", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = (True, "")
            ok, msg = await key_validator.validate_key("openai_api_key", "sk-test")
            mock_val.assert_called_once()

    async def test_catches_unexpected_exceptions(self):
        with patch.object(key_validator, "_validate_openai", new_callable=AsyncMock) as mock_val:
            mock_val.side_effect = RuntimeError("boom")
            ok, msg = await key_validator.validate_key("openai_api_key", "sk-test")
            assert ok is False
            assert "boom" in msg


# ══════════════════════════════════════════════════════════════════════════════
# validate_key_pair() — multi-key coordination
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestValidateKeyPair:
    async def test_single_key_delegates_to_validate_key(self):
        with patch.object(key_validator, "validate_key", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = (True, "")
            ok, msg = await key_validator.validate_key_pair(
                "openai_api_key", "sk-test", all_keys={}
            )
            mock_val.assert_called_once_with("openai_api_key", "sk-test")

    async def test_dataforseo_login_with_password_present(self):
        with patch.object(key_validator, "_validate_dataforseo", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = (True, "")
            ok, msg = await key_validator.validate_key_pair(
                "dataforseo_login", "user@test.com",
                all_keys={"dataforseo_password": "pass123"},
            )
            mock_val.assert_called_once_with("user@test.com", "pass123")

    async def test_dataforseo_login_without_password_skips(self):
        ok, msg = await key_validator.validate_key_pair(
            "dataforseo_login", "user@test.com",
            all_keys={"dataforseo_password": None},
        )
        assert ok is True
        assert "dataforseo_password" in msg

    async def test_dataforseo_password_with_login_present(self):
        with patch.object(key_validator, "_validate_dataforseo", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = (True, "")
            ok, msg = await key_validator.validate_key_pair(
                "dataforseo_password", "pass123",
                all_keys={"dataforseo_login": "user@test.com"},
            )
            mock_val.assert_called_once_with("user@test.com", "pass123")

    async def test_azure_key_with_endpoint(self):
        with patch.object(key_validator, "_validate_azure_openai", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = (True, "")
            ok, msg = await key_validator.validate_key_pair(
                "azure_openai_key", "key123",
                all_keys={
                    "azure_openai_endpoint": "https://my.openai.azure.com",
                    "azure_openai_deployment": "gpt4o",
                },
            )
            mock_val.assert_called_once()

    async def test_amazon_keys_skip_validation(self):
        ok, msg = await key_validator.validate_key_pair(
            "amazon_access_key", "AKIA1234", all_keys={}
        )
        assert ok is True

        ok, msg = await key_validator.validate_key_pair(
            "amazon_secret_key", "secret", all_keys={}
        )
        assert ok is True


# ══════════════════════════════════════════════════════════════════════════════
# validate_all_stored_keys()
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestValidateAllStoredKeys:
    async def test_empty_keys_returns_empty(self):
        mock_ks = MagicMock()
        mock_ks.get_all_keys = AsyncMock(return_value={
            "openai_api_key": None, "anthropic_api_key": None,
        })
        mock_ks.get = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {"key_store": mock_ks}):
            # Re-import to pick up the mocked module
            import importlib
            importlib.reload(key_validator)
            results = await key_validator.validate_all_stored_keys()
        assert len(results) == 0

    async def test_validates_set_keys(self):
        mock_ks = MagicMock()
        mock_ks.get_all_keys = AsyncMock(return_value={
            "openai_api_key": "sk-test",
            "anthropic_api_key": None,
        })
        mock_ks.get = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {"key_store": mock_ks}):
            import importlib
            importlib.reload(key_validator)
            with patch.object(key_validator, "validate_key_pair", new_callable=AsyncMock) as mock_val:
                mock_val.return_value = (True, "")
                results = await key_validator.validate_all_stored_keys()

        assert "openai_api_key" in results
        assert results["openai_api_key"][0] is True
        assert "anthropic_api_key" not in results  # not set, should be skipped

    async def test_skip_validation_keys_marked_as_skipped(self):
        mock_ks = MagicMock()
        mock_ks.get_all_keys = AsyncMock(return_value={
            "amazon_associate_tag": "mytag-20",
        })
        mock_ks.get = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {"key_store": mock_ks}):
            import importlib
            importlib.reload(key_validator)
            results = await key_validator.validate_all_stored_keys()

        assert "amazon_associate_tag" in results
        assert results["amazon_associate_tag"][0] is True
        assert "skipped" in results["amazon_associate_tag"][1].lower()
