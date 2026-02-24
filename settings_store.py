"""
settings_store.py — runtime-editable bot settings.

Priority order (same pattern as key_store.py):
  1. Database (set via /admin → ⚙️ Settings) — takes precedence, no restart needed
  2. Environment variable / .env file         — fallback / bootstrap

All settings are stored as strings in the DB and cast to the right type on read.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_db = None


def _get_db():
    global _db
    if _db is None:
        import database as db
        _db = db
    return _db


# ── Setting definitions ────────────────────────────────────────────────────────
# Each entry: key → (env_var, default, type, label, description, choices)
# type: "str" | "int" | "float" | "bool"

SETTINGS_META: dict[str, dict] = {
    "vision_mode": {
        "env": "VISION_MODE",
        "default": "best",
        "type": "str",
        "label": "🤖 Vision Mode",
        "desc": "How to use multiple AI providers",
        "choices": ["best", "cheapest", "compare"],
    },
    "show_cost_info": {
        "env": "SHOW_COST_INFO",
        "default": "true",
        "type": "bool",
        "label": "💸 Show Cost Info",
        "desc": "Show cost & latency in result messages",
        "choices": ["true", "false"],
    },
    "search_backend": {
        "env": "SEARCH_BACKEND",
        "default": "auto",
        "type": "str",
        "label": "🛒 Search Backend",
        "desc": "Amazon search source (auto/rapidapi/paapi)",
        "choices": ["auto", "rapidapi", "paapi"],
    },
    "amazon_marketplace": {
        "env": "AMAZON_MARKETPLACE",
        "default": "www.amazon.com",
        "type": "str",
        "label": "🌍 Amazon Marketplace",
        "desc": "e.g. www.amazon.com / www.amazon.co.uk",
        "choices": [],
    },
    "results_per_page": {
        "env": "RESULTS_PER_PAGE",
        "default": "5",
        "type": "int",
        "label": "📄 Results Per Page",
        "desc": "How many items shown per page (1–20)",
        "choices": [],
    },
    "max_results": {
        "env": "MAX_RESULTS",
        "default": "20",
        "type": "int",
        "label": "🔢 Max Results",
        "desc": "Maximum items to fetch from Amazon (5–50)",
        "choices": [],
    },
    "free_delivery_threshold": {
        "env": "FREE_DELIVERY_THRESHOLD",
        "default": "49",
        "type": "float",
        "label": "✈️ Free Delivery Threshold ($)",
        "desc": "Min cart value for free Israel shipping",
        "choices": [],
    },
    "shortener_enabled": {
        "env": "SHORTENER_ENABLED",
        "default": "false",
        "type": "bool",
        "label": "🔗 Custom Shortener",
        "desc": "Use self-hosted URL shortener",
        "choices": ["true", "false"],
    },
    "shortener_base_url": {
        "env": "SHORTENER_BASE_URL",
        "default": "",
        "type": "str",
        "label": "🌐 Shortener Base URL",
        "desc": "e.g. https://go.yourdomain.com",
        "choices": [],
    },
    "shortener_port": {
        "env": "SHORTENER_PORT",
        "default": "8080",
        "type": "int",
        "label": "🔌 Shortener Port",
        "desc": "Port for the self-hosted shortener server",
        "choices": [],
    },
}


def _cast(raw: str, typ: str) -> Any:
    if typ == "bool":
        return raw.strip().lower() in ("true", "1", "yes")
    if typ == "int":
        return int(raw.strip())
    if typ == "float":
        return float(raw.strip())
    return raw.strip()


async def get(key: str) -> Any:
    """Return the current value for a setting, DB first then env/default."""
    meta = SETTINGS_META.get(key)
    if meta is None:
        raise KeyError(f"Unknown setting: {key}")

    try:
        raw = await _get_db().get_setting(key)
        if raw is not None:
            return _cast(raw, meta["type"])
    except Exception as exc:
        logger.warning("settings_store: DB lookup failed for %s: %s", key, exc)

    env_val = os.getenv(meta["env"], "").strip()
    raw = env_val if env_val else meta["default"]
    return _cast(raw, meta["type"])


async def get_raw(key: str) -> str:
    """Return raw string value (for display in admin panel)."""
    meta = SETTINGS_META[key]
    try:
        raw = await _get_db().get_setting(key)
        if raw is not None:
            return raw
    except Exception:
        pass
    env_val = os.getenv(meta["env"], "").strip()
    return env_val if env_val else meta["default"]


async def set(key: str, value: str, admin_id: int) -> None:
    """Persist a setting to DB and apply it live to the config module."""
    meta = SETTINGS_META.get(key)
    if meta is None:
        raise KeyError(f"Unknown setting: {key}")

    # Validate
    _cast(value, meta["type"])  # raises ValueError/TypeError on bad input
    await _get_db().set_setting(key, value, admin_id)
    _apply_to_config(key, value, meta["type"])


async def delete(key: str) -> None:
    """Remove a setting from DB (falls back to .env / default)."""
    meta = SETTINGS_META.get(key)
    if meta is None:
        raise KeyError(f"Unknown setting: {key}")
    await _get_db().delete_setting(key)
    # Revert config to env/default
    env_val = os.getenv(meta["env"], "").strip()
    raw = env_val if env_val else meta["default"]
    _apply_to_config(key, raw, meta["type"])


async def get_all() -> dict[str, str]:
    """Return all settings as raw strings (source: DB or env/default)."""
    result = {}
    for key in SETTINGS_META:
        result[key] = await get_raw(key)
    return result


def _apply_to_config(key: str, raw: str, typ: str) -> None:
    """Immediately update the live config module so no restart is needed."""
    import config as cfg
    value = _cast(raw, typ)
    attr = key.upper()
    if hasattr(cfg, attr):
        setattr(cfg, attr, value)
        logger.info("settings_store: config.%s = %r (live)", attr, value)
    # Special case: reload backends when search_backend or marketplace changes
    if key in ("search_backend", "amazon_marketplace"):
        try:
            import amazon_search
            amazon_search._backend = None
        except Exception:
            pass
    if key == "vision_mode":
        try:
            import providers.manager as pm
            pm._providers = {}
        except Exception:
            pass
