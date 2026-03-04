# Optimizations + Per-User Subtag Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Optimize the photo processing pipeline (image compression, session cleanup, connection pooling, timeouts) and add per-user subtag tracking for affiliate URLs.

**Architecture:** Image compression via Pillow before AI analysis; periodic asyncio cleanup task for sessions/cache/rate-buckets; persistent aiohttp sessions per search backend; asyncio.wait_for timeouts on all vision providers; subtag=tg_user_id appended to affiliate URLs.

**Tech Stack:** Python 3.11, Pillow, aiohttp, asyncio, python-telegram-bot, SQLite

---

## Task 1: Image Compression
- Modify: bot.py - add _compress_image helper, apply after download
- Resize max 1024px, JPEG quality 85%, handle RGBA/P modes

## Task 2: Session and Cache Cleanup
- Modify: bot.py - add _periodic_cleanup coroutine, _created_at to UserSession
- 5min interval, 10min session TTL, evict stale cache + empty rate buckets

## Task 3: Shared aiohttp Sessions
- Modify: rapidapi_backend.py, paapi_backend.py, dataforseo_backend.py
- Add persistent _session + _get_session() to each backend class

## Task 4: Vision Provider Timeouts
- Modify: openai, anthropic, gemini, azure, openrouter providers
- Wrap API calls with asyncio.wait_for(timeout=45)

## Task 5: Fix OpenAI MIME Type
- Modify: openai_provider.py:67
- Use detect_media_type() instead of hardcoded image/jpeg

## Task 6: Fix Groq sanitize_query
- Modify: groq_provider.py imports + analyse method
- Add sanitize_query + _extract_features

## Task 7: Remove Fallback Search Sleep
- Modify: amazon_search.py:224
- Remove unnecessary 1s asyncio.sleep

## Task 8: Per-User Subtag
- Modify: search_backends/base.py affiliate_url, bot.py results_keyboard + _render_results
- Add ascsubtag=tg_user_id to affiliate URLs

## Task 9: Rebuild and Deploy
- Push, pull on VPS, docker compose build + up
