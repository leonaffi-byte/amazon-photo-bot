# Improvement Suggestions — Full Codebase Audit

> Generated from a comprehensive audit of all 66 Python files, every API integration, UI message, database query, config path, test file, and Docker configuration.

---

## CRITICAL — Bugs & Security

| # | Issue | File(s) | Detail |
|---|-------|---------|--------|
| C1 | **Container runs as root** | `Dockerfile` | No `USER` directive. Chromium + bot run as root inside the container. Add `RUN useradd -m botuser` and `USER botuser`. |
| C2 | **No timeouts on vision provider API calls** | All 6 provider files | `analyse()` calls OpenAI/Anthropic/Gemini/Groq/Azure/OpenRouter SDKs with no explicit timeout. A stalled upstream hangs the Telegram handler indefinitely. Wrap each in `asyncio.wait_for(..., timeout=45)`. |
| C3 | **Missing database indexes** | `database.py` | `search_logs.user_id`, `search_logs.searched_at`, `search_logs.tag_used`, `api_request_log.api_key`, `api_cost_log.user_id` are filtered in admin reports but have no indexes. Add `CREATE INDEX IF NOT EXISTS`. |
| C4 | **Race condition in `set_active_tag()`** | `database.py` | Two UPDATE statements (deactivate all, activate one) are not wrapped in a transaction. Between them, no tag is active. Use `BEGIN IMMEDIATE` transaction. |
| C5 | **Silent config failures** | `config.py:88-107` | `apply_db_settings()` catches all exceptions with bare `pass`. If the DB is corrupted, settings silently don't apply and the bot runs with stale defaults. Add `logger.warning()` at minimum. |
| C6 | **No file size validation on photo uploads** | `bot.py` | Downloaded photo bytes are forwarded to the vision API with no size check. A very large image could exhaust memory or burn API quota. Add a max-size guard (e.g., 20 MB). |
| C7 | **Groq docstring references non-existent models** | `providers/groq_provider.py` | Docstring lists `llama-3.2-11b-vision-preview` and `llama-3.2-90b-vision-preview`, but the pricing dict only has `llama-4-scout-17b-16e-instruct` and `llama-3.2-90b-vision-preview`. Update docstring to match reality. |
| C8 | **Graceful shutdown race condition** | `main.py` | `sched_task.cancel()` is fire-and-forget without `await`. PTB stop is called while handlers may still be running. Add `await sched_task` with timeout and `asyncio.wait_for(web_runner.cleanup(), timeout=10)`. |

---

## HIGH — Reliability & Error Handling

| # | Issue | File(s) | Detail |
|---|-------|---------|--------|
| H1 | **No retry logic on vision providers** | `providers/manager.py` | Single failure → auto-disable after 3 consecutive. No exponential-backoff retry before marking failed. Transient network blips permanently disable models until manual re-enable. |
| H2 | **Auto-disable threshold too aggressive** | `providers/manager.py` | 3 consecutive failures disables the model indefinitely. Failure counter never resets over time. Add time-windowed failure counting (e.g., 3 failures within 5 minutes). |
| H3 | **RapidAPI retry too simplistic** | `search_backends/rapidapi_backend.py` | Only retries once with a fixed 1.5 s delay. No exponential backoff. Burst searches can cascade rate-limit failures. |
| H4 | **DataForSEO only checks status 20000** | `search_backends/dataforseo_backend.py` | Status 20001 ("task queued") is treated as failure. Should retry after a short delay for queued tasks. |
| H5 | **Playwright selectors fragile** | `playwright_backend.py`, `israel_scraper.py` | CSS selectors are hardcoded to the current Amazon HTML layout. Any markup change breaks extraction silently. Add fallback selectors and structural validation. |
| H6 | **No timeout on Playwright operations** | `playwright_backend.py`, `israel_scraper.py`, `price_history.py` | `page.evaluate()`, `page.goto()`, and `wait_for_selector` calls have no explicit timeouts. A stuck page hangs the event loop. |
| H7 | **Israel scraper has no circuit breaker** | `israel_scraper.py` | If Decodo proxy credentials expire, every ASIN lookup launches Chromium → connects to dead proxy → times out → falls back. Add a circuit breaker that opens after N failures and stops trying the proxy for a cooldown period. |
| H8 | **Price history launches Chromium per proxy** | `price_history.py` | Sequential Chromium launches for each proxy attempt (1-2 s each). With 3 failed proxies the user waits 5+ seconds before getting results. Track proxy health and skip known-bad ones. |
| H9 | **Notification delivery has no retry** | `notifications.py` | Single attempt per admin. If Telegram rate-limits the message, it's lost forever. Add retry with exponential backoff. |
| H10 | **No request deduplication** | `bot.py` | Same photo sent twice in quick succession is analyzed twice. Cache recent analyses by SHA-256 of image bytes with a short TTL. |
| H11 | **Shortener click logging has no backpressure** | `shortener_server.py` | Every redirect spawns a fire-and-forget `asyncio.create_task()` for a DB write. A viral link could generate thousands of concurrent SQLite writes. Add write batching or a bounded queue. |
| H12 | **API server rate limiter doesn't scale** | `api_server.py` | In-memory deque with O(limit) cleanup per request. The pro tier allows 10k requests/day — scanning 10k entries on every incoming request is expensive. Switch to time-bucketed counters or sliding-window with sorted sets. |

---

## MEDIUM — Performance & Architecture

| # | Issue | File(s) | Detail |
|---|-------|---------|--------|
| M1 | **No database connection pooling** | `database.py` | Every function opens and closes a fresh `aiosqlite.connect()` — 50+ call sites. Use a persistent connection or a small pool for better performance and reduced file-lock contention. |
| M2 | **N+1 queries in admin panel** | `admin.py`, `database.py` | Loading the admin panel triggers 5+ separate DB round-trips (tags, stats, admins, keys, settings). Batch into fewer queries or a single aggregating query. |
| M3 | **Settings not cached in memory** | `settings_store.py` | `get()` queries the DB on every call. Should cache values in memory and invalidate on change (the write path already exists). |
| M4 | **Affiliate tag lookup not cached** | `database.py` | `get_active_tag()` queries the DB on every search request. Cache with invalidation when tags change. |
| M5 | **Model health lookups not cached** | `database.py` | `get_disabled_models()` queries the DB on every provider check — called multiple times per image analysis. Cache with short TTL. |
| M6 | **Hardcoded pricing will go stale** | All provider files | Pricing data is from "early 2025" and hardcoded in source. No admin-panel override exists. Make pricing configurable or add periodic validation against live API pricing endpoints. |
| M7 | **Rate limiter buckets never cleaned** | `bot.py` | In-memory deque per user (`time.monotonic()` entries) grows unboundedly. Inactive users' buckets are never removed. Add periodic cleanup. |
| M8 | **Scheduler uses polling instead of event-driven** | `scheduler.py` | Sleeps 30 s in a loop and checks time. Uses a `_running` global flag that delays shutdown by up to 30 s. Switch to `asyncio.Event` for cancellation and compute the next fire time to sleep exactly the right amount. |
| M9 | **Missed scheduled reports not recovered** | `scheduler.py` | If the bot is down during the report hour, the report is never generated. `last_fired_day` tracks day-of-year which breaks at year boundaries (day 365 → day 1). |
| M10 | **Docker image ~350 MB unnecessarily large** | `Dockerfile` | Playwright Chromium is installed in every service image, but only the bot and test-bot use scraping. Use multi-stage builds or separate images for the API server. |
| M11 | **All 3 Docker services share same SQLite DB** | `docker-compose.yml` | The shared `./data:/app/data` volume means all services write to the same `bot_data.db`. SQLite is not designed for concurrent writers from separate processes. The test-bot could corrupt production data. Use separate `DATA_DIR` per service. |
| M12 | **No health check in Docker** | `Dockerfile`, `docker-compose.yml` | No `HEALTHCHECK` directive. If the bot process crashes silently, Docker still reports the container as healthy. Add a health-check endpoint. |
| M13 | **`stop_grace_period` missing** | `docker-compose.yml` | No graceful shutdown timeout configured. Docker default (10 s) may not be enough for pending API calls. Add `stop_grace_period: 30s`. |
| M14 | **Debug scripts in production tree** | `debug_*.py` (7 files) | Development-only debug scripts sit in the project root alongside production code. Move to a `debug/` subdirectory or exclude from the main branch. |

---

## MEDIUM — UX & User-Facing Issues

| # | Issue | File(s) | Detail |
|---|-------|---------|--------|
| U1 | **Vague processing message** | `bot.py` | "Processing..." is shown during text search with no context. Show "Searching Amazon for '{query}'..." instead. |
| U2 | **No progress during lazy-load pagination** | `bot.py` | When fetching more results from Amazon, the user sees no feedback. Add a "Loading more results..." indicator. |
| U3 | **"Session expired" is a dead end** | `bot.py` | The message gives no guidance. Append "Send the photo again to start over." |
| U4 | **Message truncation without warning** | `style.py` | Captions are silently cut at 1020 chars, result messages at 4050 chars. Instead of raw `...`, append "…and N more features" or similar. |
| U5 | **Inconsistent error iconography** | `bot.py`, `style.py` | Some errors use "❌", others "⚠️", others have no icon at all. Pick a consistent set and apply it everywhere. |
| U6 | **No "end of results" message** | `bot.py` | When all pages are exhausted, pagination buttons simply disappear. Show an explicit "No more results found." message. |
| U7 | **Admin settings allow invalid values** | `admin.py` | Free-text settings (e.g., marketplace URL) have no validation. A typo can break the bot. Add value validation before applying. |
| U8 | **No confirmation for dangerous admin actions** | `admin.py` | Deleting tags, removing admins, and clearing API keys happen immediately with no "Are you sure?" confirmation step. |
| U9 | **API key deletion race condition** | `admin.py` | The message says "deleted immediately" but the actual deletion happens after the message is sent. Brief window where the key is still usable. |
| U10 | **Admin invite doesn't mention expiry** | `admin.py` | A 30-minute single-use invite link is created, but neither the creator nor recipient is told it expires. |
| U11 | **Translation failures are silent** | `translator.py` | If the LLM translation call fails, the original (possibly non-English) text is used as the Amazon search query without telling the user. |
| U12 | **No timeout on translation LLM calls** | `translator.py` | LLM calls can hang indefinitely. The user is stuck at "Processing..." forever. Add `asyncio.wait_for()`. |
| U13 | **Dead loading animation sequences** | `style.py` | `LOADING` and `SEARCH_LOADING` animation frame sequences are defined (lines 54-66) but never referenced anywhere. Either implement animated status messages or remove the dead code. |

---

## LOW — Code Quality & Best Practices

| # | Issue | File(s) | Detail |
|---|-------|---------|--------|
| L1 | **Duplicated escape function** | `admin.py` | `e()` duplicates `style.esc()`. Import from `style` instead. |
| L2 | **Duplicated background task spawning** | `bot.py` | `_spawn_israel_check()` and `_spawn_price_check()` are nearly identical. Extract a generic `_spawn_background_check(coro, session, field)`. |
| L3 | **Duplicated CAPTCHA detection** | `playwright_backend.py`, `captcha_solver.py`, `israel_scraper.py` | CAPTCHA detection logic exists in 3 places with slight variations. Centralize in `captcha_solver.py`. |
| L4 | **No foreign key constraints** | `database.py` | `api_keys.updated_by`, `bot_settings.updated_by`, `short_links.created_by`, `admin_invites.created_by/used_by` reference user IDs but have no FK constraints. Orphaned references are possible. |
| L5 | **No database migration versioning** | `database.py` | `_MIGRATIONS` is a plain list with no version numbers or checksums. Can't detect partially-applied migrations or skip already-applied ones safely. |
| L6 | **Unused DB columns** | `database.py` | `added_by_name` in `admin_invites` is never populated. `label` in `external_api_keys` is never read. |
| L7 | **`image_analyzer.py` is just a dataclass** | `image_analyzer.py` | Contains only the `ProductInfo` dataclass. Could be merged into `providers/base.py` to reduce module count. |
| L8 | **No structured logging** | All modules | Logs use `%`-formatting with no correlation IDs. For production, consider structured JSON logging with request IDs for tracing. |
| L9 | **Overly broad exception handlers** | Multiple files | Many `except Exception: pass` blocks hide real errors. Narrow to specific exception types and add `logger.exception()`. |
| L10 | **Azure deployment name used as model_id** | `providers/azure_openai_provider.py` | If the admin names a deployment "my-gpt-4o-prod", that string is stored in the cost log instead of "gpt-4o". Breaks cost aggregation. Normalize the model name. |
| L11 | **bit.ly uses deprecated SSL endpoint** | `url_shortener.py` | Uses `api-ssl.bitly.com/v4/shorten` instead of the current `api.bitly.com/v4/shorten`. |
| L12 | **ProductInfo has no validation** | `image_analyzer.py` | No length checks, no enum validation on `confidence`, no verification that `key_features` is non-empty. |
| L13 | **Stale temporary file in repo** | root | `check_keys_tmp.py` appears to be a one-off debug file that should be deleted. |

---

## Testing Gaps

| # | Issue | File(s) | Detail |
|---|-------|---------|--------|
| T1 | **Zero tests for `bot.py`** | `tests/` | 919 lines of conversation handlers, session management, and callbacks — completely untested. This is the single largest coverage gap. |
| T2 | **Zero tests for `admin.py`** | `tests/` | 1022 lines of admin panel logic — access control, tag management, settings persistence all untested. |
| T3 | **Zero tests for `scheduler.py`** | `tests/` | Timezone handling, report generation timing, and fire-once-per-day logic all untested. |
| T4 | **Zero tests for `notifications.py`** | `tests/` | Admin notification delivery untested. |
| T5 | **Zero tests for `translator.py`** | `tests/` | Language detection and LLM-based translation untested. |
| T6 | **Zero tests for `style.py`** | `tests/` | Message formatting, HTML escaping, and truncation logic untested. |
| T7 | **No integration tests** | `tests/` | End-to-end flow (photo → vision analysis → Amazon search → result formatting → Telegram reply) is never tested as a whole. |
| T8 | **No CI/CD pipeline** | `.github/` | No GitHub Actions or equivalent. Tests never run automatically on push or PR. |
| T9 | **Test isolation incomplete** | `tests/conftest.py` | `conftest.py` resets the DB lock but doesn't reset module-level caches in `amazon_search.py` or `providers/manager.py`. Cross-test state pollution is possible. |
| T10 | **No tests for malformed API responses** | `tests/` | Providers are only tested with well-formed responses. No tests for invalid JSON, partial responses, HTTP 429/500 errors, or empty bodies. |

---

## Future Enhancements

| # | Feature | Detail |
|---|---------|--------|
| F1 | **Per-user rate limiting** | Current rate limiter is global (5 req / 60 s). Add configurable per-user limits managed via the admin panel. |
| F2 | **API key validation on save** | When an admin saves a new API key, make a lightweight health-check call to verify it actually works before persisting. |
| F3 | **Bulk affiliate tag management** | Import/export affiliate tags as CSV. Set a default tag for new users. |
| F4 | **Analytics export** | Export search stats, cost breakdowns, and user activity as CSV/JSON from the admin panel. |
| F5 | **Database backup automation** | Schedule daily SQLite backups to a configurable path. A `.backup()` call is inexpensive for SQLite. |
| F6 | **Circuit breaker pattern** | Wrap all external service calls (vision APIs, search backends, proxy endpoints) in a circuit breaker: open after N failures → half-open after cooldown → close on success. |
| F7 | **Prometheus metrics endpoint** | Expose request counts, latency histograms, error rates, and per-provider costs on a `/metrics` endpoint for Grafana dashboards. |
| F8 | **Request correlation IDs** | Assign a UUID to each user interaction and propagate it through vision → search → formatting for end-to-end tracing in logs. |
| F9 | **Progressive provider degradation** | Warn admins after 1 failure, alert after 2, auto-disable after 3+ within a time window — instead of the current hard 3-strikes-you're-out. |
| F10 | **Dependency lock file** | Add `requirements.lock` (or switch to Poetry/uv) for reproducible builds. Current `requirements.txt` uses `>=` pins, so `pip install` may produce different environments over time. |

---

## Summary

| Severity | Count |
|----------|-------|
| Critical (bugs & security) | 8 |
| High (reliability) | 12 |
| Medium — architecture | 14 |
| Medium — UX | 13 |
| Low — code quality | 13 |
| Testing gaps | 10 |
| Future enhancements | 10 |
| **Total** | **80** |
