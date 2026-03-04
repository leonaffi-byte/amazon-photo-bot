"""
database.py — async SQLite persistence via aiosqlite.

Tables:
  affiliate_tags   — admin-managed affiliate/associate codes
  search_logs      — one row per Amazon search, tracks which tag was active
  users            — user language preferences and platform tracking

The DB file is created automatically on first run.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import time as _time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

# Store the DB in a dedicated data/ directory so Docker volume mounts work
# correctly (mount ./data:/app/data) and the file survives container restarts.
_DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = str(_DATA_DIR / "bot_data.db")
_lock = asyncio.Lock()          # serialise schema migrations
_conn_lock = asyncio.Lock()     # serialise persistent connection creation

_persistent_conn: aiosqlite.Connection | None = None


@asynccontextmanager
async def _get_conn():
    """Yield the persistent DB connection, creating it on first use under a lock."""
    global _persistent_conn
    async with _conn_lock:
        if _persistent_conn is None:
            _persistent_conn = await aiosqlite.connect(DB_PATH)
            _persistent_conn.row_factory = aiosqlite.Row
            await _persistent_conn.execute("PRAGMA journal_mode=WAL")
            await _persistent_conn.execute("PRAGMA busy_timeout=5000")
    yield _persistent_conn


# ── In-memory caches ─────────────────────────────────────────────────────────
_active_tag_cache: tuple[float, str | None] | None = None  # (timestamp, tag_string)
_ACTIVE_TAG_TTL = 60  # seconds

_disabled_models_cache: tuple[float, set[str]] | None = None
_DISABLED_MODELS_TTL = 30  # seconds


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class AffiliateTag:
    id: int
    tag: str                    # e.g. "mytag-20"
    description: str            # human label, e.g. "Primary US tag"
    added_by_id: int            # Telegram user id of the admin who added it
    added_by_name: str          # display name for audit trail
    added_at: datetime
    is_active: bool
    search_count: int = 0       # how many searches used this tag
    is_default: bool = False    # default tag for new users / fallback


@dataclass
class SearchLog:
    id: int
    user_id: int
    product_name: str
    tag_used: str               # affiliate tag at time of search (or "none")
    provider_used: str
    result_count: int
    israel_filter: bool
    searched_at: datetime


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS affiliate_tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tag         TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL DEFAULT '',
    added_by_id INTEGER NOT NULL,
    added_by_name TEXT  NOT NULL DEFAULT '',
    added_at    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0,
    search_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS search_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    product_name   TEXT    NOT NULL DEFAULT '',
    tag_used       TEXT    NOT NULL DEFAULT 'none',
    provider_used  TEXT    NOT NULL DEFAULT 'unknown',
    result_count   INTEGER NOT NULL DEFAULT 0,
    israel_filter  INTEGER NOT NULL DEFAULT 0,
    searched_at    TEXT    NOT NULL,
    correlation_id TEXT    NOT NULL DEFAULT ''
);

-- API keys set via Telegram admin panel (override .env values)
CREATE TABLE IF NOT EXISTS api_keys (
    key_name   TEXT PRIMARY KEY,
    key_value  TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    updated_at TEXT    NOT NULL
);

-- Admin users (bootstrapped from ADMIN_IDS env var, then managed in-bot)
CREATE TABLE IF NOT EXISTS admins (
    user_id   INTEGER PRIMARY KEY,
    username  TEXT NOT NULL DEFAULT '',
    full_name TEXT NOT NULL DEFAULT '',
    added_by  INTEGER,
    added_at  TEXT NOT NULL
);

-- One-time invite codes for adding new admins without knowing their user ID
CREATE TABLE IF NOT EXISTS admin_invites (
    code       TEXT    PRIMARY KEY,
    created_by INTEGER NOT NULL,
    label      TEXT    NOT NULL DEFAULT '',
    expires_at TEXT    NOT NULL,
    used_by    INTEGER,
    used_at    TEXT
);

-- Cache for shortened URLs via external services (TinyURL, bit.ly)
CREATE TABLE IF NOT EXISTS url_cache (
    long_url   TEXT PRIMARY KEY,
    short_url  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Custom self-hosted shortener: code → long URL
CREATE TABLE IF NOT EXISTS short_links (
    code        TEXT    PRIMARY KEY,
    long_url    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    created_by  INTEGER,             -- user_id of the bot user who triggered it (NULL = system)
    label       TEXT    NOT NULL DEFAULT '',
    click_count INTEGER NOT NULL DEFAULT 0
);

-- Per-click analytics for the custom shortener
CREATE TABLE IF NOT EXISTS link_clicks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL,
    clicked_at TEXT    NOT NULL,
    user_agent TEXT    NOT NULL DEFAULT '',
    referrer   TEXT    NOT NULL DEFAULT '',
    ip         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_link_clicks_code ON link_clicks (code);
CREATE INDEX IF NOT EXISTS idx_link_clicks_at   ON link_clicks (clicked_at);

-- Bot settings editable via Telegram admin panel (override .env values)
CREATE TABLE IF NOT EXISTS bot_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    updated_at TEXT    NOT NULL
);

-- Per-request AI API cost tracking (for daily reports)
CREATE TABLE IF NOT EXISTS api_cost_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    user_id       INTEGER NOT NULL DEFAULT 0,
    provider_name TEXT    NOT NULL,
    cost_usd      REAL    NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cost_log_ts ON api_cost_log (ts);

-- Model health: track failures with progressive degradation
CREATE TABLE IF NOT EXISTS model_health (
    provider_name        TEXT PRIMARY KEY,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_failures       INTEGER NOT NULL DEFAULT 0,
    is_disabled          INTEGER NOT NULL DEFAULT 0,
    disabled_at          TEXT,
    last_failure_ts      TEXT,
    last_failure_reason  TEXT    NOT NULL DEFAULT '',
    state                TEXT    NOT NULL DEFAULT 'healthy',
    disabled_until       REAL,
    last_notification_level INTEGER NOT NULL DEFAULT 0,
    failure_timestamps   TEXT    NOT NULL DEFAULT '[]',
    success_timestamps   TEXT    NOT NULL DEFAULT '[]'
);

-- Israel shipping verification cache (24-hour TTL, checked via WireGuard proxy)
CREATE TABLE IF NOT EXISTS israel_shipping_cache (
    asin             TEXT    PRIMARY KEY,
    ships_to_israel  INTEGER NOT NULL,   -- 0 = no, 1 = yes
    is_free_shipping INTEGER NOT NULL,   -- 0 = no, 1 = yes
    note             TEXT    NOT NULL,   -- display note for product card
    checked_at       REAL    NOT NULL    -- Unix timestamp
);

-- Historical price cache (CamelCamelCamel / Keepa), 6h TTL
CREATE TABLE IF NOT EXISTS price_history_cache (
    asin         TEXT    PRIMARY KEY,
    source       TEXT    NOT NULL,   -- "camelcamelcamel" | "keepa"
    current      REAL,
    low_all_time REAL,
    avg_90d      REAL,
    avg_30d      REAL,
    low_90d      REAL,
    cached_at    REAL    NOT NULL
);

-- External REST API keys (for the Israel Shipping Verifier public API)
CREATE TABLE IF NOT EXISTS external_api_keys (
    key             TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    plan            TEXT    NOT NULL DEFAULT 'free',   -- free / basic / pro
    daily_limit     INTEGER NOT NULL DEFAULT 100,
    total_requests  INTEGER NOT NULL DEFAULT 0,
    created_at      REAL    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    notes           TEXT    NOT NULL DEFAULT ''
);

-- Per-request log for the external API (analytics + billing)
CREATE TABLE IF NOT EXISTS api_request_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key          TEXT    NOT NULL,
    asin             TEXT    NOT NULL,
    cached           INTEGER NOT NULL DEFAULT 0,
    ships_to_israel  INTEGER,            -- NULL = unverified
    is_free_shipping INTEGER,            -- NULL = unverified
    requested_at     REAL    NOT NULL
);

-- Per-user rate limit overrides (admins can set custom limits per user)
CREATE TABLE IF NOT EXISTS user_rate_limits (
    user_id        INTEGER PRIMARY KEY,
    max_requests   INTEGER NOT NULL,
    window_seconds INTEGER NOT NULL,
    updated_by     INTEGER NOT NULL,
    updated_at     TEXT    NOT NULL
);

-- User language preferences and platform tracking
CREATE TABLE IF NOT EXISTS users (
    user_key    TEXT PRIMARY KEY,   -- "platform:native_user_id"
    platform    TEXT,
    lang        TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

_MIGRATIONS = [
    # Add search_type column to search_logs (distinguishes photo vs text searches)
    "ALTER TABLE search_logs ADD COLUMN search_type TEXT NOT NULL DEFAULT 'photo'",
    # C3: Add missing indexes for admin report queries
    "CREATE INDEX IF NOT EXISTS idx_search_logs_user_id ON search_logs (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_search_logs_searched_at ON search_logs (searched_at)",
    "CREATE INDEX IF NOT EXISTS idx_search_logs_tag_used ON search_logs (tag_used)",
    "CREATE INDEX IF NOT EXISTS idx_api_request_log_api_key ON api_request_log (api_key)",
    "CREATE INDEX IF NOT EXISTS idx_api_cost_log_user_id ON api_cost_log (user_id)",
    # F9: Progressive health degradation columns
    "ALTER TABLE model_health ADD COLUMN state TEXT NOT NULL DEFAULT 'healthy'",
    "ALTER TABLE model_health ADD COLUMN disabled_until REAL",
    "ALTER TABLE model_health ADD COLUMN last_notification_level INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE model_health ADD COLUMN failure_timestamps TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE model_health ADD COLUMN success_timestamps TEXT NOT NULL DEFAULT '[]'",
    # Add is_default column to affiliate_tags (bulk tag management — F3)
    "ALTER TABLE affiliate_tags ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0",
    # F8: Correlation ID column for end-to-end request tracing
    "ALTER TABLE search_logs ADD COLUMN correlation_id TEXT NOT NULL DEFAULT ''",
    # Task 4: User language preferences and platform tracking
    "ALTER TABLE users ADD COLUMN platform TEXT",
    "ALTER TABLE users ADD COLUMN lang TEXT",
    "ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
]


async def init_db() -> None:
    """Create tables if they don't exist. Safe to call multiple times."""
    async with _lock:
        async with _get_conn() as db:
            await db.executescript(_SCHEMA)
            # Run additive migrations (ALTER TABLE ADD COLUMN, etc.)
            # Each is wrapped in try/except because SQLite raises if column exists.
            for sql in _MIGRATIONS:
                try:
                    await db.execute(sql)
                except Exception:
                    pass   # already applied
            await db.commit()
    logger.info("Database initialised at %s", DB_PATH)


async def close_db() -> None:
    """Close the persistent database connection (call at shutdown)."""
    global _persistent_conn
    if _persistent_conn is not None:
        await _persistent_conn.close()
        _persistent_conn = None


# ── Affiliate tag operations ───────────────────────────────────────────────────

async def get_active_tag() -> Optional[str]:
    """
    Return the currently active affiliate tag string.

    Falls back to the default tag if no tag is explicitly active,
    so new users / sessions always get a tag when one is configured.
    Returns None only if neither an active nor a default tag exists.
    """
    global _active_tag_cache
    if _active_tag_cache and (_time.monotonic() - _active_tag_cache[0]) < _ACTIVE_TAG_TTL:
        return _active_tag_cache[1]
    async with _get_conn() as db:
        async with db.execute(
            "SELECT tag FROM affiliate_tags WHERE is_active = 1 LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                tag_value = row[0]
                _active_tag_cache = (_time.monotonic(), tag_value)
                return tag_value

        # Fall back to the default tag
        async with db.execute(
            "SELECT tag FROM affiliate_tags WHERE is_default = 1 LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            tag_value = row[0] if row else None
    _active_tag_cache = (_time.monotonic(), tag_value)
    return tag_value


async def get_all_tags() -> list[AffiliateTag]:
    """Return all affiliate tags ordered by is_active DESC, added_at DESC."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT * FROM affiliate_tags ORDER BY is_active DESC, added_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
    return [
        AffiliateTag(
            id=r["id"],
            tag=r["tag"],
            description=r["description"],
            added_by_id=r["added_by_id"],
            added_by_name=r["added_by_name"],
            added_at=datetime.fromisoformat(r["added_at"]),
            is_active=bool(r["is_active"]),
            search_count=r["search_count"],
            is_default=bool(r["is_default"]) if "is_default" in r.keys() else False,
        )
        for r in rows
    ]


async def add_tag(
    tag: str,
    description: str,
    admin_id: int,
    admin_name: str,
    make_active: bool = False,
) -> AffiliateTag:
    """
    Insert a new affiliate tag.
    If make_active=True, deactivate all others first.
    Raises ValueError if tag already exists.
    """
    global _active_tag_cache
    _active_tag_cache = None
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute("BEGIN IMMEDIATE")
        # Check for duplicate
        async with db.execute("SELECT id FROM affiliate_tags WHERE tag = ?", (tag,)) as cur:
            if await cur.fetchone():
                raise ValueError(f"Tag '{tag}' already exists.")

        if make_active:
            await db.execute("UPDATE affiliate_tags SET is_active = 0")

        await db.execute(
            """INSERT INTO affiliate_tags
               (tag, description, added_by_id, added_by_name, added_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tag, description, admin_id, admin_name, now, 1 if make_active else 0),
        )
        await db.commit()

        async with db.execute("SELECT * FROM affiliate_tags WHERE tag = ?", (tag,)) as cur:
            r = await cur.fetchone()

    return AffiliateTag(
        id=r[0], tag=r[1], description=r[2],
        added_by_id=r[3], added_by_name=r[4],
        added_at=datetime.fromisoformat(r[5]),
        is_active=bool(r[6]), search_count=r[7],
        is_default=bool(r[8]) if len(r) > 8 else False,
    )


async def remove_tag(tag_id: int) -> bool:
    """Delete a tag by id. Returns True if a row was deleted."""
    global _active_tag_cache
    _active_tag_cache = None
    async with _get_conn() as db:
        cursor = await db.execute("DELETE FROM affiliate_tags WHERE id = ?", (tag_id,))
        await db.commit()
        return cursor.rowcount > 0


async def set_active_tag(tag_id: int) -> bool:
    """Deactivate all tags, then activate the one with tag_id. Returns True on success."""
    global _active_tag_cache
    _active_tag_cache = None
    async with _get_conn() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("UPDATE affiliate_tags SET is_active = 0")
        cursor = await db.execute(
            "UPDATE affiliate_tags SET is_active = 1 WHERE id = ?", (tag_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def deactivate_all_tags() -> None:
    """Remove active status from every tag (run bot with no affiliate tag)."""
    global _active_tag_cache
    _active_tag_cache = None
    async with _get_conn() as db:
        await db.execute("UPDATE affiliate_tags SET is_active = 0")
        await db.commit()


async def increment_tag_search_count(tag: str) -> None:
    """Bump search_count for the given tag string."""
    async with _get_conn() as db:
        await db.execute(
            "UPDATE affiliate_tags SET search_count = search_count + 1 WHERE tag = ?", (tag,)
        )
        await db.commit()


async def get_default_tag() -> str | None:
    """Return the tag string marked as default, or None if none is set."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tag FROM affiliate_tags WHERE is_default = 1 LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_default_tag(tag_id: int) -> bool:
    """Mark one tag as the default (unsets all others). Returns True on success."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE affiliate_tags SET is_default = 0")
        cursor = await db.execute(
            "UPDATE affiliate_tags SET is_default = 1 WHERE id = ?", (tag_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_default_tag() -> None:
    """Remove the default flag from all tags."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE affiliate_tags SET is_default = 0")
        await db.commit()


async def export_tags_csv() -> str:
    """Export all affiliate tags as a CSV string."""
    tags = await get_all_tags()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["tag_name", "description", "is_active", "is_default"])
    for t in tags:
        writer.writerow([
            t.tag,
            t.description,
            "1" if t.is_active else "0",
            "1" if t.is_default else "0",
        ])
    return output.getvalue()


async def import_tags_csv(csv_data: str, imported_by: int) -> dict[str, int]:
    """
    Bulk import affiliate tags from CSV data.

    Expected columns: tag_name, description, is_active, is_default
    (description, is_active, is_default are optional with sensible defaults).

    Returns {"imported": N, "skipped": N, "errors": N}.
    """
    reader = csv.DictReader(io.StringIO(csv_data))
    imported = 0
    skipped = 0
    errors = 0
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as conn:
        for row in reader:
            tag_name = (row.get("tag_name") or "").strip()
            if not tag_name:
                errors += 1
                continue

            description = (row.get("description") or "").strip()
            is_active = (row.get("is_active") or "0").strip() == "1"
            is_default = (row.get("is_default") or "0").strip() == "1"

            # Check for duplicates
            async with conn.execute(
                "SELECT id FROM affiliate_tags WHERE tag = ?", (tag_name,)
            ) as cur:
                if await cur.fetchone():
                    skipped += 1
                    continue

            try:
                # If this tag should be active, deactivate others first
                if is_active:
                    await conn.execute("UPDATE affiliate_tags SET is_active = 0")
                # If this tag should be default, clear others first
                if is_default:
                    await conn.execute("UPDATE affiliate_tags SET is_default = 0")

                await conn.execute(
                    """INSERT INTO affiliate_tags
                       (tag, description, added_by_id, added_by_name, added_at,
                        is_active, is_default)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (tag_name, description, imported_by, "CSV Import", now,
                     1 if is_active else 0, 1 if is_default else 0),
                )
                imported += 1
            except Exception:
                errors += 1

        await conn.commit()

    logger.info(
        "CSV tag import: %d imported, %d skipped, %d errors (by user %d)",
        imported, skipped, errors, imported_by,
    )
    return {"imported": imported, "skipped": skipped, "errors": errors}


# ── Search log operations ─────────────────────────────────────────────────────

async def log_search(
    user_id: int,
    product_name: str,
    tag_used: str,
    provider_used: str,
    result_count: int,
    israel_filter: bool,
    search_type: str = "photo",
    correlation_id: str = "",
) -> None:
    """Record a search event."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO search_logs
               (user_id, product_name, tag_used, provider_used, result_count, israel_filter, searched_at, search_type, correlation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, product_name, tag_used, provider_used, result_count,
             1 if israel_filter else 0, now, search_type, correlation_id),
        )
        await db.commit()


async def get_stats() -> dict:
    """Return summary stats for the admin panel."""
    async with _get_conn() as db:
        async with db.execute("SELECT COUNT(*) FROM search_logs") as cur:
            total_searches = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM search_logs"
        ) as cur:
            unique_users = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM search_logs WHERE israel_filter = 1"
        ) as cur:
            israel_filter_uses = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT tag_used, COUNT(*) as n FROM search_logs GROUP BY tag_used ORDER BY n DESC"
        ) as cur:
            searches_per_tag = dict(await cur.fetchall())

        async with db.execute(
            "SELECT searched_at FROM search_logs ORDER BY searched_at DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            last_search = row[0] if row else "never"

    return {
        "total_searches": total_searches,
        "unique_users": unique_users,
        "israel_filter_uses": israel_filter_uses,
        "searches_per_tag": searches_per_tag,
        "last_search": last_search,
    }


# ── API key operations ────────────────────────────────────────────────────────

async def get_api_key(key_name: str) -> Optional[str]:
    """Return DB-stored value for key_name, or None if not set."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT key_value FROM api_keys WHERE key_name = ?", (key_name,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_api_key(key_name: str, key_value: str, admin_id: int) -> None:
    """Insert or replace an API key in the DB."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO api_keys (key_name, key_value, updated_by, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(key_name) DO UPDATE SET
                 key_value=excluded.key_value,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (key_name, key_value, admin_id, now),
        )
        await db.commit()


async def delete_api_key(key_name: str) -> None:
    """Remove a key from DB (bot falls back to .env value)."""
    async with _get_conn() as db:
        await db.execute("DELETE FROM api_keys WHERE key_name = ?", (key_name,))
        await db.commit()


async def get_all_api_keys() -> dict[str, str]:
    """Return all DB-stored API keys as {key_name: key_value}."""
    async with _get_conn() as db:
        async with db.execute("SELECT key_name, key_value FROM api_keys") as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}


# ── Admin management ──────────────────────────────────────────────────────────

@dataclass
class Admin:
    user_id: int
    username: str
    full_name: str
    added_by: Optional[int]
    added_at: datetime


async def seed_admins(user_ids: set[int]) -> None:
    """
    Insert bootstrap admins from ADMIN_IDS env var.
    Called once at startup — safe to call multiple times (ignores existing rows).
    """
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        for uid in user_ids:
            await db.execute(
                """INSERT OR IGNORE INTO admins (user_id, username, full_name, added_by, added_at)
                   VALUES (?, ?, ?, NULL, ?)""",
                (uid, "", "Bootstrap admin", now),
            )
        await db.commit()


async def get_all_admins() -> list[Admin]:
    async with _get_conn() as db:
        async with db.execute(
            "SELECT user_id, username, full_name, added_by, added_at FROM admins ORDER BY added_at"
        ) as cur:
            rows = await cur.fetchall()
    return [
        Admin(
            user_id=r[0], username=r[1], full_name=r[2],
            added_by=r[3], added_at=datetime.fromisoformat(r[4]),
        )
        for r in rows
    ]


async def is_admin_in_db(user_id: int) -> bool:
    async with _get_conn() as db:
        async with db.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ) as cur:
            return (await cur.fetchone()) is not None


async def add_admin(user_id: int, username: str, full_name: str, added_by: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT OR IGNORE INTO admins (user_id, username, full_name, added_by, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, full_name, added_by, now),
        )
        await db.commit()


async def remove_admin(user_id: int) -> bool:
    async with _get_conn() as db:
        cur = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()
        return cur.rowcount > 0


# ── Admin invite codes ────────────────────────────────────────────────────────

async def create_invite(created_by: int, label: str, ttl_minutes: int = 30) -> str:
    """Generate a one-time invite code. Returns the code string."""
    import secrets
    code = secrets.token_urlsafe(16)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO admin_invites (code, created_by, label, expires_at)
               VALUES (?, ?, ?, ?)""",
            (code, created_by, label, expires),
        )
        await db.commit()
    return code


async def use_invite(code: str, user_id: int) -> Optional[str]:
    """
    Attempt to redeem an invite code.
    Returns the label string on success, None if invalid/expired/already used.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        async with db.execute(
            """SELECT label, expires_at, used_by FROM admin_invites WHERE code = ?""",
            (code,),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            return None
        label, expires_at, used_by = row

        if used_by is not None:
            return None  # already used
        if now > expires_at:
            return None  # expired

        await db.execute(
            "UPDATE admin_invites SET used_by = ?, used_at = ? WHERE code = ?",
            (user_id, now, code),
        )
        await db.commit()
    return label


# ── Custom self-hosted shortener ──────────────────────────────────────────────

async def create_short_link(
    long_url: str,
    code: str,
    label: str = "",
    created_by: Optional[int] = None,
) -> str:
    """Store a new short link. Returns the code."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT OR IGNORE INTO short_links (code, long_url, created_at, created_by, label)
               VALUES (?, ?, ?, ?, ?)""",
            (code, long_url, now, created_by, label),
        )
        await db.commit()
    return code


async def get_long_url_by_code(code: str) -> Optional[str]:
    """Return the long URL for a short code, or None if not found."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT long_url FROM short_links WHERE code = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_code_by_long_url(long_url: str) -> Optional[str]:
    """Return an existing code for this long_url (avoids creating duplicates)."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT code FROM short_links WHERE long_url = ?", (long_url,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def log_click(code: str, user_agent: str, referrer: str, ip: str = "") -> None:
    """Record a click on a short link and bump the counter atomically."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO link_clicks (code, clicked_at, user_agent, referrer, ip)
               VALUES (?, ?, ?, ?, ?)""",
            (code, now, user_agent[:512], referrer[:512], ip),
        )
        await db.execute(
            "UPDATE short_links SET click_count = click_count + 1 WHERE code = ?", (code,)
        )
        await db.commit()


async def get_link_stats(code: str) -> Optional[dict]:
    """Return click stats for a single short code."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT long_url, created_at, click_count FROM short_links WHERE code = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        long_url, created_at, click_count = row

        # Clicks per day (last 7 days)
        async with db.execute(
            """SELECT DATE(clicked_at) as day, COUNT(*) as n
               FROM link_clicks WHERE code = ?
               GROUP BY day ORDER BY day DESC LIMIT 7""",
            (code,),
        ) as cur:
            per_day = {r[0]: r[1] for r in await cur.fetchall()}

    return {
        "code":        code,
        "long_url":    long_url,
        "created_at":  created_at,
        "click_count": click_count,
        "per_day":     per_day,
    }


async def get_top_links(limit: int = 10) -> list[dict]:
    """Return the most-clicked short links."""
    async with _get_conn() as db:
        async with db.execute(
            """SELECT code, long_url, label, click_count, created_at
               FROM short_links ORDER BY click_count DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"code": r[0], "long_url": r[1], "label": r[2],
         "clicks": r[3], "created_at": r[4]}
        for r in rows
    ]


async def get_short_link_count() -> int:
    """Total number of short links stored."""
    async with _get_conn() as db:
        async with db.execute("SELECT COUNT(*) FROM short_links") as cur:
            return (await cur.fetchone())[0]


async def get_shortener_stats() -> dict:
    """Aggregate stats for the admin panel shortener section."""
    async with _get_conn() as db:
        async with db.execute("SELECT COUNT(*) FROM short_links") as cur:
            total_links = (await cur.fetchone())[0]
        async with db.execute("SELECT SUM(click_count) FROM short_links") as cur:
            total_clicks = (await cur.fetchone())[0] or 0
        async with db.execute(
            "SELECT COUNT(*) FROM link_clicks WHERE clicked_at >= DATE('now','-1 day')"
        ) as cur:
            clicks_24h = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM link_clicks WHERE clicked_at >= DATE('now','-7 days')"
        ) as cur:
            clicks_7d = (await cur.fetchone())[0]

    top = await get_top_links(5)
    return {
        "total_links":  total_links,
        "total_clicks": total_clicks,
        "clicks_24h":   clicks_24h,
        "clicks_7d":    clicks_7d,
        "top_links":    top,
    }


async def delete_short_link(code: str) -> bool:
    """Remove a short link and its click history."""
    async with _get_conn() as db:
        cur = await db.execute("DELETE FROM short_links WHERE code = ?", (code,))
        await db.execute("DELETE FROM link_clicks WHERE code = ?", (code,))
        await db.commit()
        return cur.rowcount > 0


# ── External URL cache (TinyURL / bit.ly) ─────────────────────────────────────

async def get_short_url(long_url: str) -> Optional[str]:
    """Return cached short URL for long_url, or None if not cached."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT short_url FROM url_cache WHERE long_url = ?", (long_url,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def cache_short_url(long_url: str, short_url: str) -> None:
    """Store a long→short URL mapping in the cache."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT OR REPLACE INTO url_cache (long_url, short_url, created_at)
               VALUES (?, ?, ?)""",
            (long_url, short_url, now),
        )
        await db.commit()


# ── Bot settings (editable via admin panel) ───────────────────────────────────

async def get_setting(key: str) -> Optional[str]:
    """Return DB-stored value for setting key, or None if not set."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT value FROM bot_settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_setting(key: str, value: str, admin_id: int) -> None:
    """Insert or replace a setting in the DB."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO bot_settings (key, value, updated_by, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 value=excluded.value,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (key, value, admin_id, now),
        )
        await db.commit()


async def delete_setting(key: str) -> None:
    """Remove a setting from DB (bot falls back to .env / default)."""
    async with _get_conn() as db:
        await db.execute("DELETE FROM bot_settings WHERE key = ?", (key,))
        await db.commit()


async def get_active_invites(created_by: int) -> list[dict]:
    """List unexpired, unused invite codes created by this admin."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        async with db.execute(
            """SELECT code, label, expires_at FROM admin_invites
               WHERE created_by = ? AND used_by IS NULL AND expires_at > ?
               ORDER BY expires_at""",
            (created_by, now),
        ) as cur:
            rows = await cur.fetchall()
    return [{"code": r[0], "label": r[1], "expires_at": r[2]} for r in rows]


# ── API cost logging ───────────────────────────────────────────────────────────

async def log_api_cost(
    provider_name: str,
    cost_usd: float,
    input_tokens: int,
    output_tokens: int,
    user_id: int = 0,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO api_cost_log (ts, user_id, provider_name, cost_usd, input_tokens, output_tokens)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (now, user_id, provider_name, cost_usd, input_tokens, output_tokens),
        )
        await db.commit()


# ── Model health (progressive degradation) ────────────────────────────────────

async def increment_model_failures(provider_name: str, reason: str) -> int:
    """Increment failure counter and record timestamp. Returns new consecutive_failures count."""
    import json as _json
    now_iso = datetime.now(timezone.utc).isoformat()
    now_ts = datetime.now(timezone.utc).timestamp()
    async with _get_conn() as conn:
        # Read existing row first (if any) to get current timestamps
        async with conn.execute(
            "SELECT failure_timestamps, consecutive_failures FROM model_health WHERE provider_name = ?",
            (provider_name,),
        ) as cur:
            existing = await cur.fetchone()

        if existing:
            # Row exists — update it and append timestamp
            try:
                ts_list = _json.loads(existing[0]) if existing[0] else []
            except Exception:
                ts_list = []
            ts_list.append(now_ts)
            ts_list = ts_list[-50:]  # bound storage
            await conn.execute(
                """UPDATE model_health SET
                     consecutive_failures = consecutive_failures + 1,
                     total_failures       = total_failures + 1,
                     last_failure_ts      = ?,
                     last_failure_reason  = ?,
                     failure_timestamps   = ?
                   WHERE provider_name = ?""",
                (now_iso, reason[:500], _json.dumps(ts_list), provider_name),
            )
            consec = existing[1] + 1
        else:
            # New row — insert with a single timestamp
            await conn.execute(
                """INSERT INTO model_health (provider_name, consecutive_failures, total_failures,
                       last_failure_ts, last_failure_reason, failure_timestamps)
                   VALUES (?, 1, 1, ?, ?, ?)""",
                (provider_name, now_iso, reason[:500], _json.dumps([now_ts])),
            )
            consec = 1

        await conn.commit()
        return consec


async def record_model_success(provider_name: str) -> None:
    """Record a success: reset consecutive failures, set state to healthy, record timestamp."""
    import json as _json
    global _disabled_models_cache
    _disabled_models_cache = None
    now_ts = datetime.now(timezone.utc).timestamp()
    async with _get_conn() as conn:
        # Ensure the row exists
        await conn.execute(
            """INSERT INTO model_health (provider_name, consecutive_failures, state, last_notification_level)
               VALUES (?, 0, 'healthy', 0)
               ON CONFLICT(provider_name) DO UPDATE SET
                 consecutive_failures    = 0,
                 state                   = 'healthy',
                 last_notification_level = 0,
                 is_disabled             = 0,
                 disabled_at             = NULL,
                 disabled_until          = NULL""",
            (provider_name,),
        )
        # Append success timestamp
        async with conn.execute(
            "SELECT success_timestamps FROM model_health WHERE provider_name = ?",
            (provider_name,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                try:
                    ts_list = _json.loads(row[0]) if row[0] else []
                except Exception:
                    ts_list = []
                ts_list.append(now_ts)
                ts_list = ts_list[-50:]
                await conn.execute(
                    "UPDATE model_health SET success_timestamps = ? WHERE provider_name = ?",
                    (_json.dumps(ts_list), provider_name),
                )
        await conn.commit()


async def reset_model_failures(provider_name: str) -> None:
    """Reset consecutive failure counter after a successful call (backward compat wrapper)."""
    await record_model_success(provider_name)


async def get_model_health_row(provider_name: str) -> dict | None:
    """Return the full health row for a single model, or None if not tracked yet."""
    import json as _json
    async with _get_conn() as conn:
        async with conn.execute(
            """SELECT provider_name, consecutive_failures, total_failures,
                      is_disabled, disabled_at, last_failure_ts, last_failure_reason,
                      state, disabled_until, last_notification_level,
                      failure_timestamps, success_timestamps
               FROM model_health WHERE provider_name = ?""",
            (provider_name,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    try:
        fail_ts = _json.loads(row[10]) if row[10] else []
    except Exception:
        fail_ts = []
    try:
        succ_ts = _json.loads(row[11]) if row[11] else []
    except Exception:
        succ_ts = []
    return {
        "provider_name": row[0],
        "consecutive_failures": row[1],
        "total_failures": row[2],
        "is_disabled": bool(row[3]),
        "disabled_at": row[4],
        "last_failure_ts": row[5],
        "last_failure_reason": row[6],
        "state": row[7] or "healthy",
        "disabled_until": row[8],
        "last_notification_level": row[9] or 0,
        "failure_timestamps": fail_ts,
        "success_timestamps": succ_ts,
    }


async def update_model_health_state(
    provider_name: str,
    state: str,
    is_disabled: bool = False,
    disabled_until: float | None = None,
    last_notification_level: int | None = None,
) -> None:
    """Update the progressive health state fields for a model."""
    global _disabled_models_cache
    _disabled_models_cache = None
    now_iso = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as conn:
        parts = ["state = ?", "is_disabled = ?"]
        params: list = [state, int(is_disabled)]

        if is_disabled:
            parts.append("disabled_at = ?")
            params.append(now_iso)
        elif state == "healthy":
            parts.append("disabled_at = NULL")

        parts.append("disabled_until = ?")
        params.append(disabled_until)

        if last_notification_level is not None:
            parts.append("last_notification_level = ?")
            params.append(last_notification_level)

        params.append(provider_name)
        await conn.execute(
            f"UPDATE model_health SET {', '.join(parts)} WHERE provider_name = ?",
            tuple(params),
        )
        await conn.commit()


async def mark_model_disabled(provider_name: str, reason: str) -> None:
    """Disable a model with an optional auto-recovery timestamp (backward compat)."""
    global _disabled_models_cache
    _disabled_models_cache = None
    now = datetime.now(timezone.utc).isoformat()
    async with _get_conn() as conn:
        await conn.execute(
            """INSERT INTO model_health (provider_name, is_disabled, disabled_at, last_failure_reason, state)
               VALUES (?, 1, ?, ?, 'disabled')
               ON CONFLICT(provider_name) DO UPDATE SET
                 is_disabled         = 1,
                 disabled_at         = excluded.disabled_at,
                 last_failure_reason = excluded.last_failure_reason,
                 state               = 'disabled'""",
            (provider_name, now, reason[:500]),
        )
        await conn.commit()


async def re_enable_model(provider_name: str) -> None:
    """Re-enable a previously auto-disabled model."""
    global _disabled_models_cache
    _disabled_models_cache = None
    async with _get_conn() as conn:
        await conn.execute(
            """INSERT INTO model_health (provider_name, is_disabled, consecutive_failures, state,
                   last_notification_level, disabled_until)
               VALUES (?, 0, 0, 'healthy', 0, NULL)
               ON CONFLICT(provider_name) DO UPDATE SET
                 is_disabled = 0, consecutive_failures = 0, disabled_at = NULL,
                 state = 'healthy', last_notification_level = 0, disabled_until = NULL""",
            (provider_name,),
        )
        await conn.commit()


async def get_disabled_models() -> set[str]:
    """Return set of auto-disabled provider full_names (excludes models past their recovery cooldown)."""
    import time as _t
    global _disabled_models_cache
    if _disabled_models_cache and (_time.monotonic() - _disabled_models_cache[0]) < _DISABLED_MODELS_TTL:
        return _disabled_models_cache[1]
    now = _t.time()
    async with _get_conn() as conn:
        async with conn.execute(
            "SELECT provider_name, disabled_until FROM model_health WHERE is_disabled = 1"
        ) as cur:
            rows = await cur.fetchall()
    result: set[str] = set()
    for r in rows:
        disabled_until = r[1]
        # If disabled_until is set and has passed, the model should be retried
        if disabled_until is not None and now >= disabled_until:
            continue
        result.add(r[0])
    _disabled_models_cache = (_time.monotonic(), result)
    return result


async def get_models_ready_for_recovery() -> list[str]:
    """Return provider names of models whose disabled_until has passed."""
    import time as _t
    now = _t.time()
    async with _get_conn() as conn:
        async with conn.execute(
            "SELECT provider_name FROM model_health WHERE is_disabled = 1 AND disabled_until IS NOT NULL AND disabled_until <= ?",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_all_model_health() -> list[dict]:
    """Return health stats for all tracked models."""
    import json as _json
    async with _get_conn() as conn:
        async with conn.execute(
            """SELECT provider_name, consecutive_failures, total_failures,
                      is_disabled, disabled_at, last_failure_ts, last_failure_reason,
                      state, disabled_until, last_notification_level,
                      failure_timestamps, success_timestamps
               FROM model_health ORDER BY is_disabled DESC, total_failures DESC"""
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        try:
            fail_ts = _json.loads(r[10]) if r[10] else []
        except Exception:
            fail_ts = []
        try:
            succ_ts = _json.loads(r[11]) if r[11] else []
        except Exception:
            succ_ts = []
        result.append({
            "provider_name": r[0],
            "consecutive_failures": r[1],
            "total_failures": r[2],
            "is_disabled": bool(r[3]),
            "disabled_at": r[4],
            "last_failure_ts": r[5],
            "last_failure_reason": r[6],
            "state": r[7] or "healthy",
            "disabled_until": r[8],
            "last_notification_level": r[9] or 0,
            "failure_timestamps": fail_ts,
            "success_timestamps": succ_ts,
        })
    return result


# ── Comprehensive stats for reports ───────────────────────────────────────────

async def get_stats_since(since: datetime) -> dict:
    """
    Gather all usage stats since the given UTC datetime.
    Returns a dict suitable for report formatting.
    """
    since_str = since.isoformat()
    async with _get_conn() as db:
        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) FROM search_logs WHERE searched_at >= ?",
            (since_str,),
        ) as cur:
            unique_users = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM search_logs WHERE searched_at >= ?",
            (since_str,),
        ) as cur:
            total_searches = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM search_logs WHERE searched_at >= ? AND search_type = 'photo'",
            (since_str,),
        ) as cur:
            photo_searches = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM link_clicks WHERE clicked_at >= ?",
            (since_str,),
        ) as cur:
            link_clicks = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM api_cost_log WHERE ts >= ?",
            (since_str,),
        ) as cur:
            row = await cur.fetchone()
            total_cost_usd = row[0] or 0.0
            api_calls      = row[1] or 0

        async with db.execute(
            """SELECT provider_name, COALESCE(SUM(cost_usd), 0), COUNT(*)
               FROM api_cost_log WHERE ts >= ?
               GROUP BY provider_name ORDER BY SUM(cost_usd) DESC""",
            (since_str,),
        ) as cur:
            cost_by_provider = [(r[0], r[1], r[2]) for r in await cur.fetchall()]

    return {
        "unique_users":    unique_users,
        "total_searches":  total_searches,
        "photo_searches":  photo_searches,
        "text_searches":   total_searches - photo_searches,
        "link_clicks":     link_clicks,
        "total_cost_usd":  total_cost_usd,
        "api_calls":       api_calls,
        "cost_by_provider": cost_by_provider,
    }


# ── Israel shipping cache ──────────────────────────────────────────────────────

_ISRAEL_CACHE_TTL = 86_400   # 24 hours in seconds


async def get_israel_cache(asin: str):
    """
    Return a cached IsraelShippingResult for this ASIN, or None if expired/missing.
    Import is deferred to avoid circular imports.
    """

    async with _get_conn() as db:
        async with db.execute(
            "SELECT ships_to_israel, is_free_shipping, note, checked_at "
            "FROM israel_shipping_cache WHERE asin = ?",
            (asin,),
        ) as cur:
            row = await cur.fetchone()

    if not row:
        return None

    ships, is_free, note, checked_at = row
    if _time.time() - checked_at > _ISRAEL_CACHE_TTL:
        return None   # Expired

    from israel_scraper import IsraelShippingResult
    return IsraelShippingResult(
        asin             = asin,
        verified         = True,
        ships_to_israel  = bool(ships),
        is_free_shipping = bool(is_free),
        note             = note,
    )


async def set_israel_cache(
    asin: str,
    ships_to_israel: Optional[bool],
    is_free_shipping: Optional[bool],
    note: str,
) -> None:
    """Upsert an Israel shipping verification result."""

    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO israel_shipping_cache
               (asin, ships_to_israel, is_free_shipping, note, checked_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(asin) DO UPDATE SET
                 ships_to_israel  = excluded.ships_to_israel,
                 is_free_shipping = excluded.is_free_shipping,
                 note             = excluded.note,
                 checked_at       = excluded.checked_at""",
            (asin, int(bool(ships_to_israel)), int(bool(is_free_shipping)),
             note, _time.time()),
        )
        await db.commit()


async def delete_israel_cache(asin: str) -> None:
    """Remove a cached Israel shipping result (force re-check on next call)."""
    async with _get_conn() as db:
        await db.execute("DELETE FROM israel_shipping_cache WHERE asin = ?", (asin,))
        await db.commit()


# ── External API key management ────────────────────────────────────────────────

def _key_row_to_dict(row) -> dict:
    return {
        "key":            row[0],
        "name":           row[1],
        "plan":           row[2],
        "daily_limit":    row[3],
        "total_requests": row[4],
        "created_at":     datetime.fromtimestamp(row[5], tz=timezone.utc).isoformat(),
        "is_active":      bool(row[6]),
        "notes":          row[7],
    }


async def create_external_api_key(
    key: str, name: str, plan: str, daily_limit: int, notes: str = ""
) -> dict:

    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO external_api_keys
               (key, name, plan, daily_limit, total_requests, created_at, is_active, notes)
               VALUES (?, ?, ?, ?, 0, ?, 1, ?)""",
            (key, name, plan, daily_limit, _time.time(), notes),
        )
        await db.commit()
    return await get_external_api_key(key)


async def get_external_api_key(key: str) -> Optional[dict]:
    async with _get_conn() as db:
        async with db.execute(
            "SELECT key, name, plan, daily_limit, total_requests, "
            "created_at, is_active, notes FROM external_api_keys WHERE key = ?",
            (key,),
        ) as cur:
            row = await cur.fetchone()
    return _key_row_to_dict(row) if row else None


async def list_external_api_keys() -> list[dict]:
    async with _get_conn() as db:
        async with db.execute(
            "SELECT key, name, plan, daily_limit, total_requests, "
            "created_at, is_active, notes FROM external_api_keys ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
    return [_key_row_to_dict(r) for r in rows]


async def revoke_external_api_key(key: str) -> None:
    async with _get_conn() as db:
        await db.execute(
            "UPDATE external_api_keys SET is_active = 0 WHERE key = ?", (key,)
        )
        await db.commit()


async def update_external_api_key(
    key: str,
    plan: Optional[str] = None,
    daily_limit: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> dict:
    async with _get_conn() as db:
        if plan is not None:
            await db.execute(
                "UPDATE external_api_keys SET plan = ? WHERE key = ?", (plan, key)
            )
        if daily_limit is not None:
            await db.execute(
                "UPDATE external_api_keys SET daily_limit = ? WHERE key = ?",
                (daily_limit, key),
            )
        if is_active is not None:
            await db.execute(
                "UPDATE external_api_keys SET is_active = ? WHERE key = ?",
                (int(is_active), key),
            )
        await db.commit()
    return await get_external_api_key(key)


async def log_api_request(
    asin: str,
    cached: bool,
    ships_to_israel: Optional[bool],
    is_free_shipping: Optional[bool],
    api_key: str = "unknown",
) -> None:

    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO api_request_log
               (api_key, asin, cached, ships_to_israel, is_free_shipping, requested_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                api_key,
                asin,
                int(cached),
                None if ships_to_israel is None else int(ships_to_israel),
                None if is_free_shipping is None else int(is_free_shipping),
                _time.time(),
            ),
        )
        await db.execute(
            "UPDATE external_api_keys SET total_requests = total_requests + 1 WHERE key = ?",
            (api_key,),
        )
        await db.commit()


# ── Price history cache ───────────────────────────────────────────────────────

_PRICE_CACHE_TTL = 6 * 3600   # 6 hours


async def get_price_cache(asin: str):
    """
    Return a PriceHistory object from cache, or None if expired / missing.
    Import is deferred to avoid circular imports.
    """

    async with _get_conn() as db:
        async with db.execute(
            "SELECT source, current, low_all_time, avg_90d, avg_30d, low_90d, cached_at "
            "FROM price_history_cache WHERE asin = ?",
            (asin,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    source, current, low_all_time, avg_90d, avg_30d, low_90d, cached_at = row
    if _time.time() - cached_at > _PRICE_CACHE_TTL:
        return None   # expired
    from price_history import PriceHistory
    return PriceHistory(
        asin         = asin,
        source       = source,
        current      = current,
        low_all_time = low_all_time,
        avg_90d      = avg_90d,
        avg_30d      = avg_30d,
        low_90d      = low_90d,
    )


async def set_price_cache(asin: str, ph) -> None:
    """Store a PriceHistory object in the cache."""

    async with _get_conn() as db:
        await db.execute(
            """INSERT INTO price_history_cache
               (asin, source, current, low_all_time, avg_90d, avg_30d, low_90d, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(asin) DO UPDATE SET
                 source=excluded.source, current=excluded.current,
                 low_all_time=excluded.low_all_time, avg_90d=excluded.avg_90d,
                 avg_30d=excluded.avg_30d, low_90d=excluded.low_90d,
                 cached_at=excluded.cached_at""",
            (asin, ph.source, ph.current, ph.low_all_time,
             ph.avg_90d, ph.avg_30d, ph.low_90d, _time.time()),
        )
        await db.commit()

# ── Analytics export ──────────────────────────────────────────────────────────

def _date_filter_clause(
    column: str,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, list[str]]:
    """
    Build a WHERE clause fragment for a date/timestamp column.
    Returns (sql_fragment, params).
    """
    conditions: list[str] = []
    params: list[str] = []
    if start_date:
        conditions.append(f"{column} >= ?")
        params.append(start_date)
    if end_date:
        conditions.append(f"{column} <= ?")
        params.append(end_date)
    clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return clause, params


def _rows_to_csv(headers: list[str], rows: list[tuple]) -> str:
    """Convert column headers + row tuples into a CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()


async def export_search_logs(
    fmt: str = "csv",
    start_date: str | None = None,
    end_date: str | None = None,
) -> str | list[dict]:
    """
    Export the search_logs table.

    Parameters
    ----------
    fmt : "csv" or "json"
    start_date, end_date : ISO-8601 strings to filter searched_at.

    Returns
    -------
    CSV string or list of dicts (for JSON).
    """
    where, params = _date_filter_clause("searched_at", start_date, end_date)
    sql = (
        "SELECT id, user_id, product_name, tag_used, provider_used, "
        "result_count, israel_filter, searched_at, search_type "
        f"FROM search_logs{where} ORDER BY searched_at DESC"
    )
    headers = [
        "id", "user_id", "product_name", "tag_used", "provider_used",
        "result_count", "israel_filter", "searched_at", "search_type",
    ]

    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

    if fmt == "json":
        return [dict(zip(headers, row)) for row in rows]

    return _rows_to_csv(headers, rows)


async def export_api_costs(
    fmt: str = "csv",
    start_date: str | None = None,
    end_date: str | None = None,
) -> str | list[dict]:
    """
    Export the api_cost_log table.

    Parameters
    ----------
    fmt : "csv" or "json"
    start_date, end_date : ISO-8601 strings to filter ts.

    Returns
    -------
    CSV string or list of dicts (for JSON).
    """
    where, params = _date_filter_clause("ts", start_date, end_date)
    sql = (
        "SELECT id, ts, user_id, provider_name, cost_usd, input_tokens, output_tokens "
        f"FROM api_cost_log{where} ORDER BY ts DESC"
    )
    headers = [
        "id", "ts", "user_id", "provider_name",
        "cost_usd", "input_tokens", "output_tokens",
    ]

    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

    if fmt == "json":
        return [dict(zip(headers, row)) for row in rows]

    return _rows_to_csv(headers, rows)


async def export_user_activity(
    fmt: str = "csv",
    start_date: str | None = None,
    end_date: str | None = None,
) -> str | list[dict]:
    """
    Export aggregated per-user activity stats.

    Columns: user_id, total_searches, photo_searches, text_searches,
             providers_used, first_search, last_search.

    Parameters
    ----------
    fmt : "csv" or "json"
    start_date, end_date : ISO-8601 strings to filter searched_at.

    Returns
    -------
    CSV string or list of dicts (for JSON).
    """
    where, params = _date_filter_clause("searched_at", start_date, end_date)
    sql = (
        "SELECT "
        "  user_id, "
        "  COUNT(*) AS total_searches, "
        "  SUM(CASE WHEN search_type = 'photo' THEN 1 ELSE 0 END) AS photo_searches, "
        "  SUM(CASE WHEN search_type != 'photo' THEN 1 ELSE 0 END) AS text_searches, "
        "  GROUP_CONCAT(DISTINCT provider_used) AS providers_used, "
        "  MIN(searched_at) AS first_search, "
        "  MAX(searched_at) AS last_search "
        f"FROM search_logs{where} "
        "GROUP BY user_id ORDER BY total_searches DESC"
    )
    headers = [
        "user_id", "total_searches", "photo_searches", "text_searches",
        "providers_used", "first_search", "last_search",
    ]

    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

    if fmt == "json":
        return [dict(zip(headers, row)) for row in rows]

    return _rows_to_csv(headers, rows)


# ── Per-user rate limits ──────────────────────────────────────────────────────

@dataclass
class UserRateLimit:
    user_id: int
    max_requests: int
    window_seconds: int
    updated_by: int
    updated_at: str


async def get_user_rate_limit(user_id: int) -> UserRateLimit | None:
    """Return custom rate limit for a user, or None (falls back to default)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id, max_requests, window_seconds, updated_by, updated_at "
            "FROM user_rate_limits WHERE user_id = ?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    return UserRateLimit(
        user_id=row[0],
        max_requests=row[1],
        window_seconds=row[2],
        updated_by=row[3],
        updated_at=row[4],
    )


async def set_user_rate_limit(
    user_id: int,
    max_requests: int,
    window_seconds: int,
    updated_by: int,
) -> None:
    """Set a custom rate limit for a specific user."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            """INSERT INTO user_rate_limits
               (user_id, max_requests, window_seconds, updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 max_requests=excluded.max_requests,
                 window_seconds=excluded.window_seconds,
                 updated_by=excluded.updated_by,
                 updated_at=excluded.updated_at""",
            (user_id, max_requests, window_seconds, updated_by, now),
        )
        await conn.commit()
    logger.info(
        "Rate limit set for user %d: %d req / %d sec (by admin %d)",
        user_id, max_requests, window_seconds, updated_by,
    )


async def remove_user_rate_limit(user_id: int) -> bool:
    """Remove custom rate limit for a user (reverts to default). Returns True if existed."""
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(
            "DELETE FROM user_rate_limits WHERE user_id = ?", (user_id,),
        )
        await conn.commit()
        return cur.rowcount > 0


async def list_user_rate_limits() -> list[UserRateLimit]:
    """List all users with custom rate limits (for admin panel)."""
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute(
            "SELECT user_id, max_requests, window_seconds, updated_by, updated_at "
            "FROM user_rate_limits ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
    return [
        UserRateLimit(
            user_id=r[0],
            max_requests=r[1],
            window_seconds=r[2],
            updated_by=r[3],
            updated_at=r[4],
        )
        for r in rows
    ]


# ── User language / platform tracking ─────────────────────────────────────────

async def get_user_lang(user_key: str) -> str | None:
    """Get user's preferred language. user_key is 'platform:user_id'."""
    async with _get_conn() as db:
        async with db.execute(
            "SELECT lang FROM users WHERE user_key = ?", (user_key,)
        ) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None


async def set_user_lang(user_key: str, lang: str, platform: str | None = None) -> None:
    """Set user's preferred language. Creates user if not exists."""
    async with _get_conn() as db:
        if platform:
            await db.execute(
                """INSERT INTO users (user_key, lang, platform)
                   VALUES (?, ?, ?)
                   ON CONFLICT(user_key) DO UPDATE SET lang = ?, platform = ?""",
                (user_key, lang, platform, lang, platform),
            )
        else:
            await db.execute(
                """INSERT INTO users (user_key, lang)
                   VALUES (?, ?)
                   ON CONFLICT(user_key) DO UPDATE SET lang = ?""",
                (user_key, lang, lang),
            )
        await db.commit()


async def ensure_user(user_key: str, platform: str | None = None) -> None:
    """Ensure user exists in the users table."""
    async with _get_conn() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_key, platform) VALUES (?, ?)",
            (user_key, platform),
        )
        await db.commit()
