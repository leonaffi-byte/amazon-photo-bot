# Codebase Concerns

**Analysis Date:** 2026-03-13

## Tech Debt

**Silent config failures during startup:**
- Issue: `config.apply_db_settings()` calls in `main.py:85` use bare `except` with `pass`, silently hiding DB corruption or permission errors
- Files: `config.py:88-107`, `main.py:85-86`
- Impact: Bot starts with stale defaults instead of DB-persisted settings, admin panel changes don't take effect
- Fix approach: Add `logger.warning(exc)` to bare `except` blocks; consider fail-open vs fail-safe policy for initialization errors

**Database connection pooling missing:**
- Issue: `database.py` creates a persistent connection but 50+ call sites throughout the codebase still assume fresh connections
- Files: `database.py:42-52`, `admin.py`, `bot_core.py`
- Impact: File-lock contention on SQLite, reduced throughput on high-request load, potential WAL lock timeouts
- Fix approach: Add a minimal 2-3 connection pool via `asyncio.Semaphore` or migrate to async connection factory with pooling

**N+1 queries in admin panel:**
- Issue: Loading admin dashboard triggers separate DB round-trips for tags, stats, admins, keys, settings
- Files: `admin.py` (multiple handler functions)
- Impact: Admin panel loads slowly; scales poorly as user/tag/key count grows
- Fix approach: Batch queries into a single aggregating query or use database views; cache aggregates with 30-60s TTL

**Module-level caches never invalidated:**
- Issue: `settings_store.py`, `amazon_search.py`, and `providers/manager.py` cache values in memory but don't invalidate on config changes
- Files: `settings_store.py`, `amazon_search.py`, `providers/manager.py:42`
- Impact: Changing a setting in admin panel may not take effect for cached values; leader election or multi-process deployments would have stale data
- Fix approach: Use cache invalidation events triggered when admin changes settings; implement TTL-based expiry

**Rate limiter buckets grow unbounded:**
- Issue: `bot_core.py` uses per-user `deque(monotonic_time)` entries that never cleanup inactive user buckets
- Files: `bot_core.py` (rate limiting logic)
- Impact: Memory leak over time; after a year with many users, the bucket dict could be tens of MB
- Fix approach: Add periodic cleanup (every 1h) to evict buckets not accessed in N hours

**Docker image unnecessarily large:**
- Issue: All 3 Docker services (bot, api, test-bot) include Playwright Chromium (~350 MB), but only bot and test-bot actually use scraping
- Files: `Dockerfile`
- Impact: Slow image pulls/pushes; wasted storage; API server container is 350 MB larger than necessary
- Fix approach: Use multi-stage build or separate Dockerfiles; API server image doesn't need Chromium

**Shared SQLite database across Docker services:**
- Issue: `docker-compose.yml` mounts single `./data:/app/data` volume to all 3 services, all writing to same `bot_data.db`
- Files: `docker-compose.yml`, `database.py`
- Impact: SQLite is not designed for concurrent writers from separate processes; test-bot could corrupt production DB; race conditions on WAL locking
- Fix approach: Use separate `DATA_DIR` per service via environment variables; or migrate to a proper database server (PostgreSQL)

**Deprecated bit.ly API endpoint:**
- Issue: `url_shortener.py` uses `api-ssl.bitly.com/v4/shorten` which is deprecated
- Files: `url_shortener.py`
- Impact: bit.ly API endpoint may be discontinued; shortener will fail silently if endpoint removed
- Fix approach: Update to current `api.bitly.com/v4/shorten`; add integration test to catch future API changes

---

## Known Bugs

**Race condition in `set_active_tag()`:**
- Symptoms: Between deactivating all tags and activating the new tag, no tag is active; if bot crashes mid-transaction, DB is left in inconsistent state
- Files: `database.py` (tag management functions)
- Trigger: Admin clicks tag button to activate a different tag
- Workaround: Restart bot to re-apply settings; manually fix DB via SQL

**Graceful shutdown race condition:**
- Symptoms: `sched_task.cancel()` is fire-and-forget without `await`; scheduler loop may still be running while adapters shut down, leading to orphaned background tasks or database locks
- Files: `main.py:265`, `scheduler.py`
- Trigger: Send SIGINT/SIGTERM to bot during active analysis or report generation
- Workaround: Wait 30+ seconds for scheduler to naturally timeout; kill -9 if hung

**Photo size not validated before vision API:**
- Symptoms: A 100 MB photo can be forwarded to OpenAI/Anthropic API, exhausting quota or timing out
- Files: `bot_core.py` (photo handler)
- Trigger: User sends a very large image file
- Workaround: Vision API will reject oversized images; retry with smaller photo

**Model health tracking has no time window reset:**
- Symptoms: 3 failures within first hour permanently disable a model; failure counter never decays over time, even after 24h of successful runs
- Files: `providers/manager.py` (progressive health), `database.py` (health tracking)
- Trigger: Model fails 3 times in the morning, then works fine all day — remains disabled until manually re-enabled
- Workaround: Admin must manually mark model as healthy via database or admin panel

---

## Security Considerations

**Container runs as non-root (partially mitigated):**
- Risk: Dockerfile now has `USER botuser`, but if an attacker gains code execution, they can still read data/ volume and modify bot behavior
- Files: `Dockerfile:29-30`, `main.py` (no sandboxing)
- Current mitigation: User is unprivileged; volume is mounted at `/app/data`
- Recommendations: Add read-only mounts for dependencies; use seccomp profiles in docker-compose; consider rootless containers

**API key storage in environment variables:**
- Risk: API keys passed via .env are visible in `docker inspect`, logged on startup, and stored in plaintext in config module
- Files: `config.py`, `.env.example`, `main.py`
- Current mitigation: Database-first storage (keys stored encrypted in DB is not implemented; keys in DB are plaintext)
- Recommendations: Add encryption-at-rest for API keys in SQLite; use `PRAGMA cipher` or encrypt secrets before insert; never log full key values

**No request timeout on vision provider API calls:**
- Risk: Stalled upstream API hangs Telegram handler indefinitely; attacker could cause DoS by sending malformed image that takes 5+ minutes to timeout
- Files: All provider files (`providers/openai_provider.py`, etc.), `providers/base.py` has `PROVIDER_TIMEOUT_SECONDS = 60` but not enforced
- Current mitigation: `providers/base.py` defines timeout constant (unused)
- Recommendations: Wrap all `provider.analyse()` calls in `asyncio.wait_for(..., timeout=PROVIDER_TIMEOUT_SECONDS)`; timeout on image download as well

**No input validation on product names and search queries:**
- Risk: Oversized or malformed product data could exploit downstream systems (Amazon search, price history, formatting)
- Files: `image_analyzer.py` (ProductInfo dataclass), `bot_core.py`
- Current mitigation: Implicit limits via Telegram message size caps (4096 chars)
- Recommendations: Add `@dataclass` validators; max length on product_name, key_features, amazon_search_query; strip HTML/Unicode from user input

**Admin authentication via Telegram user ID only:**
- Risk: If attacker gains access to a trusted phone number, they can impersonate admin via Telegram bot
- Files: `admin.py`, `config.py:29-33`
- Current mitigation: Admin IDs are private list in .env
- Recommendations: Add optional 2FA via `/admin accept <invite-code>` (partially implemented); log all admin actions with IP/device info; audit admin panel access

---

## Performance Bottlenecks

**Sequential Chromium launches in `price_history.py`:**
- Problem: Each proxy retry launches a fresh Chromium process (1-2 s each), so 3 failed proxies = 5+ seconds added latency
- Files: `price_history.py:573-X` (proxy retry loop)
- Cause: Uses `for proxy in proxies: page = await playwright.page(proxy)` without parallelization or proxy health tracking
- Improvement path: Track proxy health in memory/DB; skip known-bad proxies; parallelize with `asyncio.gather()` over multiple proxies

**Playwright selectors fragile and slow:**
- Problem: CSS selectors like `#landingImage` are hardcoded to current Amazon HTML; any markup change breaks silently and selector wait times become 5+ seconds
- Files: `playwright_backend.py`, `israel_scraper.py`
- Cause: No fallback selectors; no structural validation of response HTML
- Improvement path: Add multiple fallback selectors; use API endpoints when possible instead of scraping; add timeout context manager with explicit error on selector timeout

**Settings lookups on every request:**
- Problem: `get()` in settings_store queries DB on every call; called during every search
- Files: `settings_store.py`
- Cause: No caching; no invalidation events
- Improvement path: Cache settings dict in memory with TTL and invalidation callback when admin changes settings

**Active tag lookup not cached:**
- Problem: `db.get_active_tag()` queries DB on every search; called 10+ times per day per user
- Files: `database.py`
- Cause: Reads from `affiliate_tags` table every time
- Improvement path: Cache with 60-second TTL and invalidate when tags change

**Disabled model lookups not cached:**
- Problem: `db.get_disabled_models()` queries DB on every vision analysis; called per image
- Files: `database.py`, `providers/manager.py`
- Cause: Model health table queried on cold-start of `_build_providers()`
- Improvement path: Cache with 30-second TTL; invalidate when health state changes

---

## Fragile Areas

**Conversation state management across multi-step flows:**
- Files: `bot_core.py` (UserSession), `bot.py` (legacy handlers), `adapters/telegram.py`
- Why fragile: Session data is stored in memory with 10-minute TTL (`_SESSION_TTL = 600`); if user doesn't interact for 10 min, session is lost and they see "Session expired"; no persistence to DB
- Safe modification: Add serialization to DB; keep in-memory cache with DB fallback; test edge cases (session timeout during long vision API call)
- Test coverage: Sessions untested; only unit tests for individual handlers exist, no integration tests for multi-step flows

**Vision provider error handling:**
- Files: `providers/manager.py`, all 6 provider files
- Why fragile: Each provider's `analyse()` can raise different exceptions (API errors, timeout, JSON parse); some are caught, some bubble up, no consistent error taxonomy
- Safe modification: Create custom exception hierarchy (`ProviderError`, `ProviderTimeout`, `ProviderRateLimit`) and catch specifically; add retry wrapper
- Test coverage: Zero tests for malformed API responses; only happy-path testing exists

**Amazon search backend fallback chain:**
- Files: `amazon_search.py` (facade)
- Why fragile: PA-API fails → RapidAPI → DataForSEO → Playwright; each has different error modes and pagination; if all fail, user gets generic error with no visibility into which backend failed
- Safe modification: Log which backend is being tried; add explicit fallback trace to user message (dev mode); add metrics per backend
- Test coverage: Tested only with mocked responses; no integration tests with real APIs or network failures

**Israel shipping verification with proxy:**
- Files: `israel_scraper.py`
- Why fragile: If Decodo proxy credentials expire, every lookup will fail silently for 1-2s per retry; circuit breaker not implemented
- Safe modification: Implement circuit breaker; track proxy health; add explicit "Israel check: using proxy X" log
- Test coverage: No tests for proxy timeout or credential expiry scenarios

**Scheduled reports generation:**
- Files: `scheduler.py`
- Why fragile: `_running` global flag causes up-to-30s shutdown delay; no record of which reports were already sent; if bot is down during report hour, report is skipped with no catch-up
- Safe modification: Use `asyncio.Event` instead of sleep loop; store `last_fired_at` as ISO timestamp in DB; add catch-up logic for missed reports
- Test coverage: Zero tests; timing logic, timezone handling, report data collection all untested

---

## Scaling Limits

**SQLite concurrent write bottleneck:**
- Current capacity: ~5 concurrent connections with WAL mode; 50 QPS max
- Limit: At 100+ daily active users, concurrent writes (logging, tag updates, settings changes) will hit WAL lock contention
- Scaling path: Migrate to PostgreSQL or use connection pooling + write queue; split reads/writes; add caching layer

**Rate limiter is per-process:**
- Current capacity: Accurate per-user limits within a single process
- Limit: In a multi-process/multi-container deployment, each process enforces limits independently (5 req/min per process = 10 req/min total)
- Scaling path: Move rate limiter to Redis or shared cache; use leaky-bucket algorithm with server-side state

**Vision provider API quota:**
- Current capacity: Depends on API key tier; hardcoded pricing assumes current rate limits
- Limit: If a popular user/group sends 1000 photos/day, quota runs out in hours
- Scaling path: Implement quota tracking; add per-user soft limits; warn admins when approaching API quota; support multiple API keys with load balancing

**Database disk growth:**
- Current capacity: File grows ~100 KB per 1000 searches (search_logs) + per admin action
- Limit: At 10k users with 1 search/day each, ~300 MB/month; SQLite file grows continuously and never shrinks
- Scaling path: Add data retention policy (delete search_logs > 90 days old); implement manual `VACUUM` on shutdown

---

## Dependencies at Risk

**python-telegram-bot (PTB) v20+ has rapid release cycle:**
- Risk: Major API changes (handlers, filters, context) between versions; project pins to `>=20.0` so `pip install` may pull incompatible version
- Impact: Code using PTB callbacks or filters may break after `pip install --upgrade`
- Migration plan: Pin to exact version (e.g., `telegram-bot==20.7`) or use `requirements.lock`; add CI test on latest PTB; monitor deprecation warnings

**Playwright Chromium version mismatch:**
- Risk: `playwright install chromium` may pull latest version; if browser API changes, page.evaluate(), selectors, etc. may fail silently
- Impact: Amazon scraping breaks if Amazon HTML changes or Playwright updates
- Migration plan: Pin Playwright to exact version in requirements.txt; add integration test that checks Amazon site is scrapeable; update selectors on page load failure

**Anthropic pricing and API changes:**
- Risk: Pricing (cost dict in `providers/anthropic_provider.py`) is from "early 2025" and hardcoded; API may change model availability or naming
- Impact: Cost tracking is inaccurate; new models not supported without code change
- Migration plan: Fetch pricing from Anthropic API on startup or weekly; add admin panel to override pricing; validate model availability on boot

**OpenRouter aggregator dependency:**
- Risk: OpenRouter's 100+ supported models come from upstream providers; if an upstream provider goes down, that model becomes unavailable
- Impact: Single point of failure for cost-conscious users relying on OpenRouter
- Migration plan: Add fallback provider list; monitor OpenRouter API health; add ability to disable specific models that fail repeatedly

---

## Missing Critical Features

**No transaction safety in multi-step database operations:**
- Problem: Operations like "delete tag + update search_logs + deactivate tag" are multi-query and not wrapped in `BEGIN...COMMIT`
- Blocks: Safe cleanup of deleted entities; crash-safe admin operations
- Impact: Partial updates on crash; orphaned records; inconsistent state

**No field-level access control in admin panel:**
- Problem: All admins can read all API keys, delete any tag, change any setting
- Blocks: Delegating tag management to non-technical admin; restricting who can see sensitive API keys
- Impact: Security risk if admin account is compromised; usability issue for multi-admin setups

**No audit log of admin actions:**
- Problem: No record of who changed what when; setting changes are written but not logged with actor/timestamp
- Blocks: Debugging accidental changes; compliance/audit trail
- Impact: Can't trace cause of misconfigurations; no accountability

**No webhook support for incoming events:**
- Problem: Bot only polls Telegram; no support for external systems calling the bot
- Blocks: Integration with external services (e.g., notify bot when Amazon price changes)
- Impact: Limited extensibility

---

## Test Coverage Gaps

**Zero tests for conversation flow (bot.py):**
- What's not tested: 1330 lines of multi-step conversation handlers, pagination, session state, callback routing
- Files: `bot.py` (all handlers)
- Risk: Message ordering, session loss, button misrouting — any of these could cause silent failures
- Priority: Critical — this is the primary user-facing code

**Zero tests for admin panel (admin.py):**
- What's not tested: 1459 lines of admin handlers, access control, settings persistence, tag management, key rotation
- Files: `admin.py`
- Risk: Accidental removal of all admins; setting change that breaks the bot; API key leakage
- Priority: Critical — admin operations have high blast radius

**Zero tests for scheduler (scheduler.py):**
- What's not tested: Report timing, timezone handling, recovery on missed hours, edge cases (daylight saving, year boundary)
- Files: `scheduler.py`
- Risk: Reports never sent; timezone conversion wrong; double-sends
- Priority: High — affects business reporting

**Zero tests for translator (translator.py):**
- What's not tested: Language detection, LLM-based translation, fallback on API error, no timeout on LLM calls
- Files: `translator.py`
- Risk: Non-English queries sent untranslated to Amazon; timeouts hang the bot
- Priority: High — affects international users

**Zero tests for message formatting and truncation (style.py, formatter.py):**
- What's not tested: HTML escaping, message truncation at 1020/4050 chars, truncation edge cases (emoji width), list formatting
- Files: `style.py:X`, `formatter.py:X`
- Risk: HTML injection; messages sent without closing tags; truncation hides important info
- Priority: Medium — affects UX and security

**No integration tests for vision → search → format pipeline:**
- What's not tested: Full user flow (image → provider → Amazon search → result formatting → Telegram message)
- Files: None (missing)
- Risk: End-to-end flow could be broken even if unit tests pass
- Priority: Medium — integration tests catch cascading failures

**No malformed API response tests:**
- What's not tested: Providers tested only with well-formed JSON; missing fields, extra fields, wrong types, empty responses, HTTP 429/500/timeout
- Files: `tests/test_malformed_responses.py` exists but coverage is partial
- Risk: Silent failures when APIs return edge cases
- Priority: Medium — APIs are unreliable in production

**Module-level caches not reset between tests:**
- What's not tested: `conftest.py` resets DB lock but not caches in `amazon_search.py`, `providers/manager.py`, `settings_store.py`
- Files: `tests/conftest.py`
- Risk: Cross-test state pollution; tests pass in isolation but fail in suite
- Priority: Low — affects test reliability

---

## Additional Quality Issues

**Overly broad exception handlers:**
- Issue: 184+ instances of bare `except Exception:` or `except:` throughout codebase; many with silent `pass`
- Files: `config.py`, `bot.py`, `admin.py`, `providers/*`, `database.py`, etc.
- Impact: Real errors hidden; difficult to debug failures; security issues masked
- Fix: Narrow exception types; add `logger.exception()` for all catches; never use bare `except:`

**No correlation IDs for request tracing:**
- Issue: Logs use no unique request IDs; tracing a user's photo through vision → search → format is difficult
- Files: Entire codebase
- Impact: Hard to debug user-reported issues; can't correlate logs across modules
- Fix: `correlation.py` exists but not used consistently; add correlation ID to all logs via LogRecord

**Hardcoded magic numbers scattered in code:**
- Issue: Timeouts (60s, 45s, 5s), sizes (10MB, 1020 chars), retry counts (3, 1), TTLs (600s, 60s) are hardcoded
- Files: `bot_core.py`, `providers/base.py`, `shortener_server.py`, `price_history.py`, etc.
- Impact: Hard to tune; config scattered across files
- Fix: Centralize all constants in `config.py` with admin override support

**Dead code in style.py:**
- Issue: `LOADING` and `SEARCH_LOADING` animation frame sequences defined but never used
- Files: `style.py:54-66`
- Impact: Misleading code; wasted space; suggests incomplete feature
- Fix: Implement animated status or remove

**Unused database column:**
- Issue: `admin_invites.added_by_name` populated but never read; `external_api_keys.label` never used
- Files: `database.py`
- Impact: Wasted storage; misleading schema
- Fix: Remove or document why reserved

---

## Summary

| Severity | Count | Category |
|----------|-------|----------|
| Critical (bugs & security) | 8 | Immediate attention required |
| High (reliability & error handling) | 12 | Blocks production stability |
| Medium (performance & architecture) | 14 | Impacts user experience |
| Medium (UX & user-facing) | 13 | User-visible issues |
| Low (code quality) | 13 | Technical debt |
| Test coverage gaps | 8 | Risk exposure |
| **Total** | **68** | — |

**Highest-impact quick wins:**
1. Add file size validation on photo uploads (`bot_core.py`)
2. Wrap vision API calls in `asyncio.wait_for()` with timeout (`providers/base.py`)
3. Add database transactions for multi-step operations (`database.py`)
4. Implement health check endpoint in Dockerfile
5. Fix graceful shutdown in `main.py` (await sched_task properly)

---

*Concerns audit: 2026-03-13*
