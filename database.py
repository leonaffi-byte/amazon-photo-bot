"""
database.py — async SQLite persistence via aiosqlite.

Tables:
  affiliate_tags   — admin-managed affiliate/associate codes
  search_logs      — one row per Amazon search, tracks which tag was active

The DB file is created automatically on first run.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = "bot_data.db"
_lock = asyncio.Lock()          # serialise schema migrations


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
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    product_name TEXT    NOT NULL DEFAULT '',
    tag_used     TEXT    NOT NULL DEFAULT 'none',
    provider_used TEXT   NOT NULL DEFAULT 'unknown',
    result_count INTEGER NOT NULL DEFAULT 0,
    israel_filter INTEGER NOT NULL DEFAULT 0,
    searched_at  TEXT    NOT NULL
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
"""


async def init_db() -> None:
    """Create tables if they don't exist. Safe to call multiple times."""
    async with _lock:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
    logger.info("Database initialised at %s", DB_PATH)


# ── Affiliate tag operations ───────────────────────────────────────────────────

async def get_active_tag() -> Optional[str]:
    """Return the currently active affiliate tag string, or None if none set."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT tag FROM affiliate_tags WHERE is_active = 1 LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_all_tags() -> list[AffiliateTag]:
    """Return all affiliate tags ordered by is_active DESC, added_at DESC."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
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
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
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
    )


async def remove_tag(tag_id: int) -> bool:
    """Delete a tag by id. Returns True if a row was deleted."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM affiliate_tags WHERE id = ?", (tag_id,))
        await db.commit()
        return cursor.rowcount > 0


async def set_active_tag(tag_id: int) -> bool:
    """Deactivate all tags, then activate the one with tag_id. Returns True on success."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE affiliate_tags SET is_active = 0")
        cursor = await db.execute(
            "UPDATE affiliate_tags SET is_active = 1 WHERE id = ?", (tag_id,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def deactivate_all_tags() -> None:
    """Remove active status from every tag (run bot with no affiliate tag)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE affiliate_tags SET is_active = 0")
        await db.commit()


async def increment_tag_search_count(tag: str) -> None:
    """Bump search_count for the given tag string."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE affiliate_tags SET search_count = search_count + 1 WHERE tag = ?", (tag,)
        )
        await db.commit()


# ── Search log operations ─────────────────────────────────────────────────────

async def log_search(
    user_id: int,
    product_name: str,
    tag_used: str,
    provider_used: str,
    result_count: int,
    israel_filter: bool,
) -> None:
    """Record a search event."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO search_logs
               (user_id, product_name, tag_used, provider_used, result_count, israel_filter, searched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, product_name, tag_used, provider_used, result_count, 1 if israel_filter else 0, now),
        )
        await db.commit()


async def get_stats() -> dict:
    """Return summary stats for the admin panel."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT key_value FROM api_keys WHERE key_name = ?", (key_name,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_api_key(key_name: str, key_value: str, admin_id: int) -> None:
    """Insert or replace an API key in the DB."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM api_keys WHERE key_name = ?", (key_name,))
        await db.commit()


async def get_all_api_keys() -> dict[str, str]:
    """Return all DB-stored API keys as {key_name: key_value}."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        for uid in user_ids:
            await db.execute(
                """INSERT OR IGNORE INTO admins (user_id, username, full_name, added_by, added_at)
                   VALUES (?, ?, ?, NULL, ?)""",
                (uid, "", "Bootstrap admin", now),
            )
        await db.commit()


async def get_all_admins() -> list[Admin]:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ) as cur:
            return (await cur.fetchone()) is not None


async def add_admin(user_id: int, username: str, full_name: str, added_by: int) -> None:
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO admins (user_id, username, full_name, added_by, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, full_name, added_by, now),
        )
        await db.commit()


async def remove_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()
        return cur.rowcount > 0


# ── Admin invite codes ────────────────────────────────────────────────────────

async def create_invite(created_by: int, label: str, ttl_minutes: int = 30) -> str:
    """Generate a one-time invite code. Returns the code string."""
    import secrets
    from datetime import timedelta
    code = secrets.token_urlsafe(16)
    expires = (datetime.utcnow() + timedelta(minutes=ttl_minutes)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
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
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
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
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO short_links (code, long_url, created_at, created_by, label)
               VALUES (?, ?, ?, ?, ?)""",
            (code, long_url, now, created_by, label),
        )
        await db.commit()
    return code


async def get_long_url_by_code(code: str) -> Optional[str]:
    """Return the long URL for a short code, or None if not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT long_url FROM short_links WHERE code = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def get_code_by_long_url(long_url: str) -> Optional[str]:
    """Return an existing code for this long_url (avoids creating duplicates)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT code FROM short_links WHERE long_url = ?", (long_url,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def log_click(code: str, user_agent: str, referrer: str, ip: str = "") -> None:
    """Record a click on a short link and bump the counter atomically."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM short_links") as cur:
            return (await cur.fetchone())[0]


async def get_shortener_stats() -> dict:
    """Aggregate stats for the admin panel shortener section."""
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM short_links WHERE code = ?", (code,))
        await db.execute("DELETE FROM link_clicks WHERE code = ?", (code,))
        await db.commit()
        return cur.rowcount > 0


# ── External URL cache (TinyURL / bit.ly) ─────────────────────────────────────

async def get_short_url(long_url: str) -> Optional[str]:
    """Return cached short URL for long_url, or None if not cached."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT short_url FROM url_cache WHERE long_url = ?", (long_url,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def cache_short_url(long_url: str, short_url: str) -> None:
    """Store a long→short URL mapping in the cache."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO url_cache (long_url, short_url, created_at)
               VALUES (?, ?, ?)""",
            (long_url, short_url, now),
        )
        await db.commit()


async def get_active_invites(created_by: int) -> list[dict]:
    """List unexpired, unused invite codes created by this admin."""
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """SELECT code, label, expires_at FROM admin_invites
               WHERE created_by = ? AND used_by IS NULL AND expires_at > ?
               ORDER BY expires_at""",
            (created_by, now),
        ) as cur:
            rows = await cur.fetchall()
    return [{"code": r[0], "label": r[1], "expires_at": r[2]} for r in rows]
