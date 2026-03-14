"""
web_app/search_store.py — Persistence layer for public web searches.

Stores upload results in the web_searches SQLite table with 30-day expiry.
All DB access uses database._get_conn() following project convention.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import secrets
import time
from typing import Optional

import database


# 30-day TTL for web search results
_TTL_SECONDS = 30 * 24 * 60 * 60


def _serialize_products(products: list) -> str:
    """Serialize a list of ProductInfo dataclasses to JSON."""
    return json.dumps([dataclasses.asdict(p) for p in products])


def _serialize_results(all_results: list) -> str:
    """
    Serialize a list[list[AmazonItem]] to JSON.
    AmazonItem has computed fields (init=False) that asdict includes automatically.
    """
    serialized = []
    for item_list in all_results:
        serialized.append([dataclasses.asdict(item) for item in item_list])
    return json.dumps(serialized)


async def save_web_search(
    photo_bytes: bytes,
    annotated_bytes: Optional[bytes],
    products: list,
    all_results: list,
    lang: str = "he",
) -> str:
    """
    Persist a completed web search result and return the short_id.

    Args:
        photo_bytes: Original uploaded photo bytes (used for SHA256 hash).
        annotated_bytes: Annotated photo bytes (stored as BLOB). May be None.
        products: list[ProductInfo] — AI-detected products.
        all_results: list[list[AmazonItem]] — Amazon search results per product.
        lang: UI language ("he" | "en").

    Returns:
        short_id: URL-safe token for the result page (e.g. /search/{short_id}).
    """
    short_id = secrets.token_urlsafe(8)
    photo_hash = hashlib.sha256(photo_bytes).hexdigest()
    now = time.time()
    expires_at = now + _TTL_SECONDS

    results_json = _serialize_results(all_results)
    products_json = _serialize_products(products)

    async with database._get_conn() as db:
        await db.execute(
            """
            INSERT INTO web_searches
                (short_id, photo_hash, annotated_photo, results_json, products_json,
                 lang, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (short_id, photo_hash, annotated_bytes, results_json, products_json,
             lang, now, expires_at),
        )
        await db.commit()

    return short_id


async def get_web_search(short_id: str) -> Optional[dict]:
    """
    Retrieve a web search result by short_id.

    Returns:
        Row dict with all columns, or None if not found or expired.
    """
    now = time.time()
    async with database._get_conn() as db:
        async with db.execute(
            "SELECT * FROM web_searches WHERE short_id = ? AND expires_at > ?",
            (short_id, now),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return dict(row)


async def purge_expired() -> int:
    """
    Delete all expired web search rows.

    Returns:
        Number of rows deleted.
    """
    now = time.time()
    async with database._get_conn() as db:
        cursor = await db.execute(
            "DELETE FROM web_searches WHERE expires_at < ?",
            (now,),
        )
        await db.commit()
        return cursor.rowcount
