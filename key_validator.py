"""
key_validator.py — lightweight health-check validation for API keys.

Each validator makes a minimal API call to confirm the key is accepted
by the remote service. All validators:
  - Use aiohttp with a 10-second timeout
  - Return (is_valid: bool, error_message: str) — never raise
  - Are async-first (no blocking calls)

Keys that don't have a practical health-check endpoint are skipped
(validator returns (True, "")).
"""
from __future__ import annotations

import base64
import logging

import aiohttp

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ── Individual validators ─────────────────────────────────────────────────────

async def _validate_openai(key: str) -> tuple[bool, str]:
    """GET https://api.openai.com/v1/models — list available models."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_anthropic(key: str) -> tuple[bool, str]:
    """GET https://api.anthropic.com/v1/models — list available models."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_google(key: str) -> tuple[bool, str]:
    """GET Gemini list-models endpoint with the API key."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_groq(key: str) -> tuple[bool, str]:
    """GET https://api.groq.com/openai/v1/models — OpenAI-compatible list."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_openrouter(key: str) -> tuple[bool, str]:
    """GET https://openrouter.ai/api/v1/models — list available models."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_rapidapi(key: str) -> tuple[bool, str]:
    """Lightweight call to RapidAPI Real-Time Amazon Data with depth=1."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://real-time-amazon-data.p.rapidapi.com/search",
                params={"query": "test", "country": "US", "page": "1"},
                headers={
                    "X-RapidAPI-Key": key,
                    "X-RapidAPI-Host": "real-time-amazon-data.p.rapidapi.com",
                },
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                if resp.status == 403:
                    return False, "Invalid or unsubscribed key"
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_capsolver(key: str) -> tuple[bool, str]:
    """POST https://api.capsolver.com/getBalance — check balance."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.capsolver.com/getBalance",
                json={"clientKey": key},
                timeout=_TIMEOUT,
            ) as resp:
                data = await resp.json()
                if data.get("errorId", 1) == 0:
                    balance = data.get("balance", "?")
                    return True, f"Balance: ${balance}"
                return False, data.get("errorDescription", f"HTTP {resp.status}")
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_dataforseo(login: str, password: str) -> tuple[bool, str]:
    """
    Validate DataForSEO credentials by calling their status endpoint.
    Requires both login and password together.
    """
    creds = base64.b64encode(f"{login}:{password}".encode()).decode()
    try:
        async with aiohttp.ClientSession() as session:
            # Use the AppData endpoint to check auth — lightweight, no cost
            async with session.get(
                "https://api.dataforseo.com/v3/appendix/user_data",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/json",
                },
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status_code") == 20000:
                        return True, ""
                    return False, data.get("status_message", "Unknown error")
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


async def _validate_azure_openai(
    key: str,
    endpoint: str | None = None,
    deployment: str | None = None,
) -> tuple[bool, str]:
    """
    Validate Azure OpenAI key by listing deployments.
    Requires the endpoint URL to be set.
    """
    if not endpoint:
        return False, "Azure endpoint not set — set azure_openai_endpoint first"
    # Clean up endpoint URL
    endpoint = endpoint.rstrip("/")
    try:
        async with aiohttp.ClientSession() as session:
            # List deployments — lightweight, no token cost
            async with session.get(
                f"{endpoint}/openai/deployments?api-version=2024-02-01",
                headers={"api-key": key},
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return True, ""
                body = await resp.text()
                return False, f"HTTP {resp.status}: {body[:200]}"
    except Exception as exc:
        return False, f"Connection error: {exc}"


# ── Key name → validator dispatch ─────────────────────────────────────────────

# Keys that don't have a validation endpoint — just save them.
_SKIP_VALIDATION = {
    "amazon_associate_tag",     # Just a tag string, no API to validate
    "bitly_token",              # Low priority, not critical
    "decodo_user",              # Proxy credentials — no simple health-check
    "decodo_password",
    "decodo_port",              # Just a port number
    "israel_proxy_url",         # Proxy URL — no standard health-check
    "azure_openai_endpoint",    # URL, not a key — validated with azure_openai_key
    "azure_openai_deployment",  # Deployment name — validated with azure_openai_key
}


async def validate_key(key_name: str, value: str) -> tuple[bool, str]:
    """
    Validate a single API key by making a lightweight health-check call.

    Args:
        key_name: The internal key name (e.g. "openai_api_key")
        value: The key value to validate

    Returns:
        (is_valid, error_or_info) — is_valid=True if the key works,
        error_or_info contains error details on failure or optional info on success.
    """
    if key_name in _SKIP_VALIDATION:
        return True, ""

    validators: dict[str, object] = {
        "openai_api_key":     lambda: _validate_openai(value),
        "anthropic_api_key":  lambda: _validate_anthropic(value),
        "google_api_key":     lambda: _validate_google(value),
        "groq_api_key":       lambda: _validate_groq(value),
        "openrouter_api_key": lambda: _validate_openrouter(value),
        "rapidapi_key":       lambda: _validate_rapidapi(value),
        "capsolver_api_key":  lambda: _validate_capsolver(value),
    }

    validator = validators.get(key_name)
    if validator is None:
        # Unknown key — skip validation rather than blocking
        logger.debug("No validator for key %s, skipping", key_name)
        return True, ""

    try:
        return await validator()
    except Exception as exc:
        logger.warning("Validation failed unexpectedly for %s: %s", key_name, exc)
        return False, f"Unexpected error: {exc}"


async def validate_key_pair(
    key_name: str,
    value: str,
    all_keys: dict[str, str | None] | None = None,
) -> tuple[bool, str]:
    """
    Validate a key, handling multi-key services (DataForSEO, Azure OpenAI).

    For services that need multiple keys together (e.g. DataForSEO login+password),
    validation only runs when the last required key is being set.
    Pass all_keys dict to provide context of other stored keys.

    Args:
        key_name: The internal key name being saved
        value: The new value being saved
        all_keys: Dict of all currently stored keys (for cross-referencing)

    Returns:
        (is_valid, error_or_info)
    """
    if all_keys is None:
        import key_store
        all_keys = await key_store.get_all_keys()

    # DataForSEO needs both login and password
    if key_name == "dataforseo_login":
        password = all_keys.get("dataforseo_password")
        if password:
            return await _validate_dataforseo(value, password)
        # No password yet — can't validate, just save
        return True, "Set dataforseo_password to complete validation"

    if key_name == "dataforseo_password":
        login = all_keys.get("dataforseo_login")
        if login:
            return await _validate_dataforseo(login, value)
        return True, "Set dataforseo_login to complete validation"

    # Azure OpenAI needs endpoint + key together
    if key_name == "azure_openai_key":
        endpoint = all_keys.get("azure_openai_endpoint")
        deployment = all_keys.get("azure_openai_deployment")
        return await _validate_azure_openai(value, endpoint, deployment)

    # Amazon PA-API needs access_key + secret_key together
    if key_name in ("amazon_access_key", "amazon_secret_key"):
        # PA-API validation requires a signed request which is complex.
        # Skip for now — the backend's own availability check handles this.
        return True, ""

    # All other single-key validators
    return await validate_key(key_name, value)


async def validate_all_stored_keys() -> dict[str, tuple[bool, str]]:
    """
    Validate all currently stored API keys.
    Returns a dict of {key_name: (is_valid, error_or_info)} for keys that have values.
    Skips keys that are not set.
    """
    import key_store

    all_keys = await key_store.get_all_keys()

    # Also fetch the extra keys not in get_all_keys but in admin panel
    extra_names = [
        "groq_api_key", "openrouter_api_key",
        "azure_openai_key", "azure_openai_endpoint", "azure_openai_deployment",
        "capsolver_api_key",
        "dataforseo_login", "dataforseo_password",
    ]
    for name in extra_names:
        if name not in all_keys:
            all_keys[name] = await key_store.get(name)

    results: dict[str, tuple[bool, str]] = {}

    for key_name, value in all_keys.items():
        if not value:
            continue  # Skip unset keys

        if key_name in _SKIP_VALIDATION:
            results[key_name] = (True, "skipped (no validator)")
            continue

        ok, msg = await validate_key_pair(key_name, value, all_keys)
        results[key_name] = (ok, msg)

    return results
