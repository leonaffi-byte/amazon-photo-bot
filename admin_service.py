"""
admin_service.py — Admin business logic service layer.

This module contains all admin operations as plain Python functions returning
plain data (dataclasses or dicts), with no Telegram dependencies.

Purpose: Enable the web admin dashboard (Phase 3) and the Telegram admin panel
(admin.py) to share the same business logic. admin.py uses this as a data source
and handles all Telegram-specific formatting and conversation flow.

Function groups:
  - Tags:    list_tags, add_tag, remove_tag, set_active_tag, deactivate_all_tags
  - Keys:    list_key_groups, get_key_group, set_api_key, delete_api_key, test_api_group
  - Settings: list_settings, set_setting, reset_setting
  - Stats:   get_stats, get_shortener_stats
  - Health:  get_provider_health
  - Admins:  list_admins, is_admin
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import database as db
import key_store
import settings_store

logger = logging.getLogger(__name__)


# ── Tags group ─────────────────────────────────────────────────────────────────

@dataclass
class TagInfo:
    id: int
    name: str
    description: str
    is_active: bool
    search_count: int
    is_default: bool = False


async def list_tags() -> list[TagInfo]:
    """Return all affiliate tags as TagInfo dataclasses."""
    tags = await db.get_all_tags()
    return [
        TagInfo(
            id=t.id,
            name=t.tag,
            description=t.description,
            is_active=t.is_active,
            search_count=t.search_count,
            is_default=getattr(t, "is_default", False),
        )
        for t in tags
    ]


async def add_tag(
    tag: str,
    description: str,
    admin_id: int,
    make_active: bool = False,
) -> TagInfo:
    """Add a new affiliate tag. Returns the created TagInfo."""
    result = await db.add_tag(
        tag=tag,
        description=description,
        admin_id=admin_id,
        admin_name=str(admin_id),
        make_active=make_active,
    )
    return TagInfo(
        id=result.id,
        name=result.tag,
        description=result.description,
        is_active=result.is_active,
        search_count=result.search_count,
        is_default=getattr(result, "is_default", False),
    )


async def remove_tag(tag_id: int) -> bool:
    """Delete a tag by ID. Returns True if deleted."""
    return await db.remove_tag(tag_id)


async def set_active_tag(tag_id: int) -> bool:
    """Set a tag as the active affiliate tag. Returns True on success."""
    return await db.set_active_tag(tag_id)


async def deactivate_all_tags() -> None:
    """Deactivate all affiliate tags."""
    await db.deactivate_all_tags()


async def set_default_tag(tag_id: int) -> bool:
    """Set the default tag (if DB supports it). Returns True on success."""
    try:
        return await db.set_active_tag(tag_id)
    except Exception:
        return False


async def clear_default_tag() -> None:
    """Clear the default tag."""
    await db.deactivate_all_tags()


# ── Keys group ─────────────────────────────────────────────────────────────────

# Groups for the admin panel display — mirrors admin.py's _API_GROUPS
_API_GROUPS: list[dict] = [
    {"name": "openai",       "label": "OpenAI",       "keys": ["openai_api_key"]},
    {"name": "anthropic",    "label": "Anthropic",     "keys": ["anthropic_api_key"]},
    {"name": "google",       "label": "Google",        "keys": ["google_api_key"]},
    {"name": "groq",         "label": "Groq",          "keys": ["groq_api_key"]},
    {"name": "openrouter",   "label": "OpenRouter",    "keys": ["openrouter_api_key"]},
    {"name": "mistral",      "label": "Mistral",       "keys": ["mistral_api_key"]},
    {"name": "sambanova",    "label": "SambaNova",     "keys": ["sambanova_api_key"]},
    {"name": "together",     "label": "Together AI",   "keys": ["together_api_key"]},
    {"name": "fireworks",    "label": "Fireworks AI",  "keys": ["fireworks_api_key"]},
    {"name": "azure",        "label": "Azure OpenAI",  "keys": ["azure_openai_key", "azure_openai_endpoint", "azure_openai_deployment"]},
    {"name": "dataforseo",   "label": "DataForSEO",    "keys": ["dataforseo_login", "dataforseo_password"]},
    {"name": "amazon_paapi", "label": "Amazon PA-API", "keys": ["amazon_access_key", "amazon_secret_key", "amazon_associate_tag"]},
    {"name": "decodo",       "label": "Decodo Proxy",  "keys": ["decodo_user", "decodo_password"], "optional": ["decodo_port"]},
    {"name": "rapidapi",     "label": "RapidAPI",      "keys": ["rapidapi_key"]},
    {"name": "capsolver",    "label": "CapSolver",     "keys": ["capsolver_api_key"]},
    {"name": "israel_proxy", "label": "Israel Proxy",  "keys": ["israel_proxy_url"]},
    {"name": "bitly",        "label": "Bit.ly",        "keys": ["bitly_token"]},
    {"name": "brightdata",   "label": "Bright Data",   "keys": ["brightdata_api_token"], "optional": ["brightdata_zone", "brightdata_customer_id"]},
]


@dataclass
class KeyGroupStatus:
    group_name: str
    label: str
    keys: dict[str, bool]  # key_name -> is_set
    all_required_set: bool
    optional_keys: dict[str, bool] = field(default_factory=dict)  # optional key_name -> is_set


async def list_key_groups() -> list[KeyGroupStatus]:
    """Return status for all API key groups."""
    all_keys = await key_store.get_all_keys()
    result = []
    for group in _API_GROUPS:
        required = group["keys"]
        optional = group.get("optional", [])
        keys_status = {k: bool(all_keys.get(k)) for k in required}
        optional_status = {k: bool(all_keys.get(k)) for k in optional}
        all_required_set = all(keys_status.values())
        result.append(KeyGroupStatus(
            group_name=group["name"],
            label=group["label"],
            keys=keys_status,
            all_required_set=all_required_set,
            optional_keys=optional_status,
        ))
    return result


async def get_key_group(group_name: str) -> KeyGroupStatus | None:
    """Return status for a specific API key group. Returns None if not found."""
    group = next((g for g in _API_GROUPS if g["name"] == group_name), None)
    if not group:
        return None
    all_keys = await key_store.get_all_keys()
    required = group["keys"]
    optional = group.get("optional", [])
    keys_status = {k: bool(all_keys.get(k)) for k in required}
    optional_status = {k: bool(all_keys.get(k)) for k in optional}
    return KeyGroupStatus(
        group_name=group["name"],
        label=group["label"],
        keys=keys_status,
        all_required_set=all(keys_status.values()),
        optional_keys=optional_status,
    )


async def set_api_key(key_name: str, value: str, admin_id: int = 0) -> None:
    """Store an API key in the database."""
    await key_store.set(key_name, value, admin_id)


async def delete_api_key(key_name: str) -> None:
    """Remove an API key from the database (falls back to .env)."""
    await key_store.delete(key_name)


async def test_api_group(group_name: str) -> tuple[bool, str]:
    """
    Test connectivity for an API key group.
    Returns (success, message).
    """
    import aiohttp
    import time as _time
    start = _time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            if group_name == "openai":
                key = await key_store.get("openai_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://api.openai.com/v1/models",
                                 headers={"Authorization": f"Bearer {key}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "anthropic":
                key = await key_store.get("anthropic_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://api.anthropic.com/v1/models",
                                 headers={"x-api-key": key, "anthropic-version": "2023-06-01"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "google":
                key = await key_store.get("google_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get(f"https://generativelanguage.googleapis.com/v1/models?key={key}") as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "groq":
                key = await key_store.get("groq_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://api.groq.com/openai/v1/models",
                                 headers={"Authorization": f"Bearer {key}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "openrouter":
                key = await key_store.get("openrouter_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://openrouter.ai/api/v1/models",
                                 headers={"Authorization": f"Bearer {key}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "azure":
                key = await key_store.get("azure_openai_key")
                endpoint = await key_store.get("azure_openai_endpoint")
                if not all([key, endpoint]):
                    missing = []
                    if not key:
                        missing.append("key")
                    if not endpoint:
                        missing.append("endpoint")
                    return False, f"Missing: {', '.join(missing)}"
                url = f"{endpoint.rstrip('/')}/openai/models?api-version=2024-02-01"
                async with s.get(url, headers={"api-key": key}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "dataforseo":
                login = await key_store.get("dataforseo_login")
                password = await key_store.get("dataforseo_password")
                if not all([login, password]):
                    return False, "Login or password not set"
                import base64
                auth = base64.b64encode(f"{login}:{password}".encode()).decode()
                async with s.get("https://api.dataforseo.com/v3/appendix/user_data",
                                 headers={"Authorization": f"Basic {auth}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "rapidapi":
                key = await key_store.get("rapidapi_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://real-time-amazon-data.p.rapidapi.com/search",
                                 params={"query": "test", "country": "US"},
                                 headers={"X-RapidAPI-Key": key,
                                          "X-RapidAPI-Host": "real-time-amazon-data.p.rapidapi.com"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "amazon_paapi":
                ak = await key_store.get("amazon_access_key")
                sk = await key_store.get("amazon_secret_key")
                tag = await key_store.get("amazon_associate_tag")
                if not all([ak, sk, tag]):
                    missing = []
                    if not ak:
                        missing.append("access key")
                    if not sk:
                        missing.append("secret key")
                    if not tag:
                        missing.append("tag")
                    return False, f"Missing: {', '.join(missing)}"
                return True, "All 3 fields set (signature test skipped)"

            elif group_name == "capsolver":
                key = await key_store.get("capsolver_api_key")
                if not key:
                    return False, "Key not set"
                async with s.post("https://api.capsolver.com/getBalance",
                                  json={"clientKey": key}) as r:
                    elapsed = _time.monotonic() - start
                    data = await r.json()
                    if data.get("errorId", 1) == 0:
                        bal = data.get("balance", "?")
                        return True, f"OK (${bal}) ({elapsed:.1f}s)"
                    return False, str(data.get("errorDescription", f"HTTP {r.status}"))[:60]

            elif group_name == "decodo":
                user = await key_store.get("decodo_user")
                pwd = await key_store.get("decodo_password")
                if not all([user, pwd]):
                    return False, "Username or password not set"
                port = await key_store.get("decodo_port") or "7000"
                proxy = f"http://{user}:{pwd}@gate.decodo.com:{port}"
                async with s.get("https://ip.decodo.com/json", proxy=proxy) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        data = await r.json()
                        ip = data.get("ip", "?")
                        return True, f"OK (IP: {ip}) ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "israel_proxy":
                url = await key_store.get("israel_proxy_url")
                if not url:
                    return False, "URL not set"
                url = url.strip()
                if "://" not in url:
                    url = f"socks5://{url}"
                import urllib.parse as _up
                p = _up.urlparse(url)
                host = p.hostname
                port = p.port or 1080
                if not host:
                    return False, f"Bad URL: {url[:40]}"
                if p.scheme.startswith("socks"):
                    import asyncio as _aio
                    try:
                        r, w = await _aio.wait_for(
                            _aio.open_connection(host, port), timeout=10
                        )
                        w.close()
                        elapsed = _time.monotonic() - start
                        return True, f"OK, SOCKS5 reachable at {host}:{port} ({elapsed:.1f}s)"
                    except Exception as exc:
                        return False, f"Cannot connect to {host}:{port}: {exc}"
                else:
                    async with s.get("https://httpbin.org/ip", proxy=url) as r:
                        elapsed = _time.monotonic() - start
                        if r.status == 200:
                            return True, f"OK ({elapsed:.1f}s)"
                        return False, f"HTTP {r.status}"

            elif group_name == "bitly":
                token = await key_store.get("bitly_token")
                if not token:
                    return False, "Token not set"
                async with s.get("https://api-ssl.bitly.com/v4/user",
                                 headers={"Authorization": f"Bearer {token}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "mistral":
                key = await key_store.get("mistral_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://api.mistral.ai/v1/models",
                                 headers={"Authorization": f"Bearer {key}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "sambanova":
                key = await key_store.get("sambanova_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://api.sambanova.ai/v1/models",
                                 headers={"Authorization": f"Bearer {key}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "together":
                key = await key_store.get("together_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://api.together.xyz/v1/models",
                                 headers={"Authorization": f"Bearer {key}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "fireworks":
                key = await key_store.get("fireworks_api_key")
                if not key:
                    return False, "Key not set"
                async with s.get("https://api.fireworks.ai/inference/v1/models",
                                 headers={"Authorization": f"Bearer {key}"}) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        return True, f"OK ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            elif group_name == "brightdata":
                token = await key_store.get("brightdata_api_token")
                zone = await key_store.get("brightdata_zone") or "unlocker"
                if not token:
                    return False, "Token not set"
                async with s.get(
                    f"https://api.brightdata.com/zone?zone={zone}",
                    headers={"Authorization": f"Bearer {token}"},
                ) as r:
                    elapsed = _time.monotonic() - start
                    if r.status == 200:
                        data = await r.json()
                        product = (data.get("plan") or {}).get("product", "unknown")
                        return True, f"OK, zone={zone} type={product} ({elapsed:.1f}s)"
                    return False, f"HTTP {r.status}"

            else:
                return False, "Unknown API group"
    except Exception as exc:
        return False, str(exc)[:80]


# ── Settings group ─────────────────────────────────────────────────────────────

@dataclass
class SettingInfo:
    key: str
    value: str
    default: str
    type: str
    description: str
    label: str
    choices: list[str] = field(default_factory=list)


async def list_settings() -> list[SettingInfo]:
    """Return all settings with current values."""
    all_vals = await settings_store.get_all()
    result = []
    for key, meta in settings_store.SETTINGS_META.items():
        result.append(SettingInfo(
            key=key,
            value=all_vals.get(key, meta["default"]),
            default=meta["default"],
            type=meta["type"],
            description=meta["desc"],
            label=meta["label"],
            choices=meta.get("choices", []),
        ))
    return result


async def set_setting(key: str, value: str, admin_id: int) -> None:
    """Persist a setting to DB and apply it live."""
    await settings_store.set(key, value, admin_id)


async def reset_setting(key: str) -> None:
    """Remove a setting DB override (falls back to .env / default)."""
    await settings_store.delete(key)


# ── Stats group ────────────────────────────────────────────────────────────────

@dataclass
class BotStats:
    total_users: int
    total_searches: int
    total_clicks: int
    today_searches: int
    today_users: int
    searches_per_tag: dict[str, int] = field(default_factory=dict)
    israel_filter_uses: int = 0
    last_search: str | None = None


async def get_stats() -> BotStats:
    """Return aggregated bot usage statistics."""
    raw = await db.get_stats()
    today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_raw = await db.get_stats_since(today_midnight)
    return BotStats(
        total_users=raw.get("unique_users", 0),
        total_searches=raw.get("total_searches", 0),
        total_clicks=0,  # not tracked separately in current DB schema
        today_searches=today_raw.get("total_searches", 0),
        today_users=today_raw.get("unique_users", 0),
        searches_per_tag=raw.get("searches_per_tag", {}),
        israel_filter_uses=raw.get("israel_filter_uses", 0),
        last_search=raw.get("last_search"),
    )


async def get_shortener_stats() -> dict:
    """Return URL shortener statistics."""
    return await db.get_shortener_stats()


# ── Health group ───────────────────────────────────────────────────────────────

@dataclass
class ProviderHealth:
    name: str
    status: str  # "healthy", "degraded", "disabled"
    failure_count: int
    last_failure: str | None


async def get_provider_health() -> list[ProviderHealth]:
    """Return health status for all vision providers."""
    rows = await db.get_all_model_health()
    result = []
    for r in rows:
        if r["is_disabled"]:
            status = "disabled"
        elif r["consecutive_failures"] >= 2:
            status = "degraded"
        else:
            status = "healthy"
        result.append(ProviderHealth(
            name=r["provider_name"],
            status=status,
            failure_count=r["consecutive_failures"],
            last_failure=r.get("last_failure_reason"),
        ))
    return result


# ── Admin management ───────────────────────────────────────────────────────────

async def list_admins() -> list[int]:
    """Return list of admin user IDs."""
    admins = await db.get_all_admins()
    return [a.user_id for a in admins]


async def is_admin(user_id: int) -> bool:
    """Check if a user ID has admin access (DB check only, not config)."""
    try:
        return await db.is_admin_in_db(user_id)
    except Exception:
        return False
