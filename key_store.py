"""
key_store.py — single source of truth for all API keys.

Priority order for every key:
  1. Database (set via Telegram admin panel) — takes precedence
  2. Environment variable / .env file       — fallback / bootstrap

This means:
  • You can start the bot with keys in .env
  • Then migrate to DB-only by setting them in the admin panel
  • Changing a key in the admin panel takes effect on the NEXT API call
    (no restart needed — key_store always reads fresh from DB)

Key names (stored in DB as-is, env vars are the uppercase equivalent):
  openai_api_key        →  OPENAI_API_KEY
  anthropic_api_key     →  ANTHROPIC_API_KEY
  google_api_key        →  GOOGLE_API_KEY
  rapidapi_key          →  RAPIDAPI_KEY
  amazon_access_key     →  AMAZON_ACCESS_KEY
  amazon_secret_key     →  AMAZON_SECRET_KEY
  amazon_associate_tag  →  AMAZON_ASSOCIATE_TAG
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependency at module load time
_db = None


def _get_db():
    global _db
    if _db is None:
        import database as db
        _db = db
    return _db


async def get(key_name: str) -> Optional[str]:
    """
    Return the value for key_name, checking DB first then env.
    Returns None if not set anywhere.
    """
    try:
        db_val = await _get_db().get_api_key(key_name)
        if db_val:
            return db_val
    except Exception as exc:
        logger.warning("key_store: DB lookup failed for %s: %s", key_name, exc)

    env_val = os.getenv(key_name.upper())
    return env_val or None


async def set(key_name: str, value: str, admin_id: int) -> None:
    """Save a key to the DB (overrides .env for all future calls)."""
    await _get_db().set_api_key(key_name, value, admin_id)


async def delete(key_name: str) -> None:
    """Remove a key from DB (will fall back to .env value if present)."""
    await _get_db().delete_api_key(key_name)


async def get_all_keys() -> dict[str, Optional[str]]:
    """Return all known keys with their current values (masked for display)."""
    names = [
        "openai_api_key",
        "anthropic_api_key",
        "google_api_key",
        "rapidapi_key",
        "amazon_access_key",
        "amazon_secret_key",
        "amazon_associate_tag",
        "bitly_token",
    ]
    result = {}
    for name in names:
        result[name] = await get(name)
    return result


def mask(value: Optional[str]) -> str:
    """Return a masked version safe to show in Telegram."""
    if not value:
        return "❌ not set"
    if len(value) <= 8:
        return "✅ ****"
    return f"✅ {value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"
