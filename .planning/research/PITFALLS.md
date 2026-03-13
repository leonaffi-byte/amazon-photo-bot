# Domain Pitfalls

**Domain:** Multi-platform product-finding bot with AI vision, web UI, photo annotations, price history
**Researched:** 2026-03-13

## Critical Pitfalls

Mistakes that cause rewrites or major issues.

### Pitfall 1: Building Multi-Platform on an Unstable Core

**What goes wrong:** Adding WhatsApp, web UI, and photo annotations on top of 68 known issues (race conditions, missing timeouts, stale caches, no DB transactions) multiplies every bug across every platform. A SQLite WAL lock that occasionally affects one Telegram user now affects Telegram + WhatsApp + web simultaneously. The existing graceful shutdown race condition becomes a data corruption risk when three adapters are running.

**Why it happens:** Pressure to ship new features while the foundation has critical bugs. Each new platform adapter adds concurrent database writers, concurrent sessions, and concurrent vision API calls -- all hitting the same fragile points.

**Consequences:** Intermittent failures that are near-impossible to reproduce because they depend on cross-platform timing. Users on WhatsApp see different behavior than Telegram users for the same photo. Admin changes via Telegram panel don't propagate to web sessions due to stale module-level caches.

**Prevention:**
- Fix the 8 critical bugs documented in CONCERNS.md before any multi-platform work
- Specifically: add `asyncio.wait_for()` timeouts on vision calls, wrap DB operations in transactions, fix graceful shutdown
- Add correlation IDs to trace requests across platforms
- Gate: no new adapter goes live until `pytest` passes with concurrent adapter simulation

**Detection:** Multiple `database is locked` errors in logs; sessions expiring mid-analysis on one platform but not another; admin settings not reflected on web.

**Phase mapping:** Must be addressed in Phase 1 (Stability) before any multi-platform phase.

---

### Pitfall 2: WhatsApp 24-Hour Messaging Window Breaking the Bot Flow

**What goes wrong:** The bot's photo-to-results flow assumes it can message the user at any time. On WhatsApp, businesses can only send free-form messages within 24 hours of the user's last message. After that window closes, only pre-approved template messages are allowed. If a vision API call takes 30+ seconds and the user doesn't respond for 24 hours before checking results, the bot cannot deliver them.

**Why it happens:** Developers build the WhatsApp adapter as a thin translation layer over the Telegram flow without understanding WhatsApp's fundamental messaging constraints. The existing `PlatformAdapter` interface has no concept of messaging windows or template requirements.

**Consequences:** Users submit photos, leave, come back 25 hours later -- the bot has results but cannot send them. Silent failures. Users think the bot is broken. Worse: attempting to send outside the window returns HTTP 400 from Meta's API, and the current broad `except Exception: pass` pattern silently swallows this.

**Prevention:**
- Add a `can_send_freeform(chat_id) -> bool` method to `PlatformAdapter` that checks messaging window status
- Store pending results in the database with a `delivery_status` field
- For WhatsApp: if window is closed, queue a template message ("Your product results are ready! Reply to see them")
- Register approved templates with Meta for: results-ready notification, price drop alert, welcome message
- Never assume fire-and-forget messaging works on all platforms

**Detection:** HTTP 400/403 errors from WhatsApp Cloud API in logs; users reporting they never received results; `send_text` failures that don't propagate to the user.

**Phase mapping:** Must be designed into the WhatsApp adapter from day one. Not a "fix later" item.

---

### Pitfall 3: SQLite Collapse Under Multi-Platform Concurrent Writes

**What goes wrong:** The current SQLite setup already has WAL lock contention with a single Telegram adapter. Adding WhatsApp webhooks (which can burst to 1,500+ status callbacks/second during campaigns), a web application with concurrent users, and background price history jobs creates a write storm that SQLite cannot handle. The existing Docker setup already shares a single `bot_data.db` across three services.

**Why it happens:** SQLite uses file-level locking -- one writer blocks all other writers. WAL mode helps with read concurrency but does not solve write contention. Real-world testing shows 96%+ error rates at just 10 concurrent writers on SQLite.

**Consequences:** `sqlite3.OperationalError: database is locked` errors cascading through all platforms simultaneously. Lost search logs, failed session writes, corrupted affiliate tag state. The race condition in `set_active_tag()` (documented in CONCERNS.md) becomes catastrophic rather than occasional.

**Prevention:**
- Migrate to PostgreSQL before launching the second platform adapter (not after problems appear)
- Use `asyncpg` for async PostgreSQL access (drop-in conceptual replacement for `aiosqlite`)
- Add a database abstraction layer now so the migration is a backend swap, not a rewrite
- If PostgreSQL is deferred: at minimum, implement a write queue that serializes all DB writes through a single asyncio task
- Separate read and write paths immediately

**Detection:** `database is locked` in logs more than once per week (per CONCERNS.md guidance). Response time spikes correlated with webhook bursts. Failed transaction warnings.

**Phase mapping:** Address in Phase 1 (Stability) or early Phase 2 at latest. Blocking issue for multi-platform.

---

### Pitfall 4: Pillow Image Annotation Blocking the Async Event Loop

**What goes wrong:** Pillow (PIL) operations are CPU-bound and synchronous. Drawing semi-transparent overlays, bounding boxes, and text labels on a high-resolution photo (e.g., 4000x3000 from a modern phone) can take 500ms-2s of pure CPU time. Running this in the async event loop blocks ALL concurrent request handling -- every user on every platform freezes.

**Why it happens:** The codebase is async-first with explicit guidance "no blocking calls." But image manipulation is inherently CPU-bound, and developers often call `ImageDraw.rectangle()` directly in an `async def` handler without offloading to an executor.

**Consequences:** While one user's photo is being annotated, all other users experience frozen interactions. With 10 concurrent users, annotation latency cascades to 5-20 seconds of blocked I/O for everyone. The Telegram and WhatsApp polling/webhook handlers miss heartbeats.

**Prevention:**
- Always run Pillow operations in `asyncio.get_event_loop().run_in_executor(None, annotate_fn, image_bytes)`
- Resize images to a reasonable max dimension (e.g., 1280px longest side) before annotation -- users see annotations on their phone, not a 4K monitor
- Pre-allocate overlay images with alpha channels rather than creating them per-request
- Consider `pillow-simd` for 2-4x speedup on x86 if annotation becomes a bottleneck
- Add a timeout wrapper: if annotation takes >3 seconds, send the plain photo with text-only product labels instead

**Detection:** Event loop lag metrics (if using `asyncio` debug mode); other users' requests queuing during annotation; PTB polling warnings about slow handlers.

**Phase mapping:** Photo annotation phase. Must be in the implementation spec, not discovered during coding.

---

### Pitfall 5: Web Interface Authentication and Session Model Mismatch

**What goes wrong:** The existing bot authenticates users by Telegram user ID (a platform-provided identity). The web interface has no equivalent -- web users need their own auth system. Developers either (a) bolt on a separate auth system that creates a parallel user identity, fragmenting analytics and session state, or (b) try to force web users through Telegram login, limiting reach.

**Why it happens:** The `UserSession` in `bot_core.py` keys on `platform:user_id` and stores state in memory with a 10-minute TTL. Web sessions need longer TTL (hours/days), persistent storage, and their own authentication flow. These are fundamentally different session models forced through the same abstraction.

**Consequences:** Two user identity systems with no cross-reference. A Telegram user who also uses the web app appears as two separate users. Affiliate tracking is split. Admin analytics are wrong. Session state (current search, pagination position) doesn't transfer between platforms.

**Prevention:**
- Design a unified user identity layer early: `users` table with optional `telegram_id`, `whatsapp_id`, `web_session_id` columns
- Web auth: use lightweight token-based auth (JWT) or social login (Google/Facebook -- popular in Israel)
- Add a "Link your Telegram account" flow: bot sends a one-time code, user enters it on web
- Make `UserSession` persist to database (not just in-memory) so it survives across platforms
- The `platform:user_id` key in `bot_core.py` already supports this -- ensure the `user_id` portion maps to the unified user table

**Detection:** Admin reports showing inflated user counts; users complaining their search history doesn't appear on web; affiliate revenue attribution gaps.

**Phase mapping:** Must be designed before web interface implementation begins. User identity schema change is a Phase 1 or early Phase 2 deliverable.

---

### Pitfall 6: Vision Provider Bounding Box Format Inconsistency

**What goes wrong:** Photo annotation requires bounding box coordinates from vision providers. Different providers return coordinates in different formats: GPT-4o returns `[x_min, y_min, x_max, y_max]` as pixel coordinates, Gemini returns normalized `[y_min, x_min, y_max, x_max]` (note: different axis order), and Claude returns text descriptions of regions, not coordinates at all. The existing `ProductInfo` dataclass has no bounding box field.

**Why it happens:** The current system only asks providers for product identification (name, features, search query). Adding "where in the image is this product?" is a different prompt requirement with provider-specific output formats. Developers assume all providers can return coordinates and that the format will be consistent.

**Consequences:** Bounding boxes drawn in the wrong location (swapped axes), overlapping annotations, or complete failure for providers that don't support spatial output. The "compare" mode (running multiple providers) produces conflicting bounding boxes.

**Prevention:**
- Extend `ProductInfo` with an optional `bbox: tuple[float, float, float, float] | None` field using normalized coordinates `[0.0-1.0]`
- Add a provider-specific coordinate normalization layer in each provider's `analyse()` method
- For providers that don't return bounding boxes (Claude, some Groq models): fall back to numbered labels without spatial annotation
- Test bounding box accuracy with a fixed set of 10-20 reference images before shipping
- Don't block on bounding boxes: show results immediately, add annotations as an enhancement

**Detection:** Annotations appearing in wrong quadrant of image; users reporting boxes that don't match products; provider comparison mode showing wildly different box positions.

**Phase mapping:** Photo annotation phase. Requires prompt engineering work per-provider.

---

## Moderate Pitfalls

### Pitfall 7: CamelCamelCamel/Keepa Scraping Rate Limits Breaking Price History

**What goes wrong:** The existing `price_history.py` scrapes CamelCamelCamel with Playwright. Adding price chart images to every search result (5 products x N users) creates a scraping volume that triggers IP bans and CAPTCHAs within minutes. CamelCamelCamel explicitly warns scrapers and blocks after ~15 minutes of sustained access.

**Why it happens:** Price history works in testing (low volume) but fails at production scale. Each chart request launches a Chromium instance (1-2s each), and the sequential proxy retry loop documented in CONCERNS.md makes this worse.

**Prevention:**
- Cache price history aggressively: same ASIN within 24 hours should never re-scrape
- Use Keepa API (paid, $19/mo) as primary source instead of scraping CamelCamelCamel
- Generate charts server-side from cached data using `matplotlib` or `plotly` (don't screenshot web pages)
- Rate limit price history requests: max 1 per second globally, with a queue
- Make price history optional and lazy-loaded (user clicks "Show price history" rather than auto-fetching)

**Detection:** Increasing CAPTCHA solve rates in logs; proxy rotation exhaustion; price history returning empty for most products.

**Phase mapping:** Price history phase. Design the caching layer first.

---

### Pitfall 8: WhatsApp Button Limitations Breaking Navigation UX

**What goes wrong:** The current bot uses inline keyboards with 3+ buttons per row for pagination (Prev/Next), filtering (Israel shipping Yes/No), and product selection. WhatsApp limits interactive messages to 3 buttons total, each full-width, with no URL buttons in the same message as reply buttons. The existing `style.py`/`formatter.py` navigation pattern doesn't translate.

**Why it happens:** Telegram's inline keyboard is extremely flexible (unlimited rows, mixed URL/callback buttons). Developers design UX for Telegram and expect the adapter layer to handle the rest. But WhatsApp's limitations are fundamental, not cosmetic.

**Consequences:** Pagination breaks (can't have Prev + Next + Filter in 3 buttons). Product selection for multi-product photos requires a different interaction model entirely. Users on WhatsApp get a degraded, confusing experience.

**Prevention:**
- Design WhatsApp UX separately: use numbered lists ("Reply 1 for Nike shoes, 2 for...") instead of buttons for product selection
- Use WhatsApp's List Message type (up to 10 items, 1 section) for product results instead of individual messages
- The `PlatformAdapter` already has `max_buttons_per_row` and `max_buttons_total` -- `bot_core.py` must actually respect these (verify it does)
- Implement a `format_for_platform()` strategy pattern that produces platform-optimal layouts, not lowest-common-denominator
- Test the full flow on WhatsApp before shipping, not just send/receive

**Detection:** WhatsApp API rejecting messages with too many buttons (HTTP 400); users stuck in navigation loops; high drop-off rates on WhatsApp vs. Telegram.

**Phase mapping:** WhatsApp adapter phase. UX design must precede implementation.

---

### Pitfall 9: Web Admin Dashboard Duplicating Logic from Telegram Admin

**What goes wrong:** The existing admin panel is 1,459 lines of Telegram-specific handler code (`admin.py`). Building a web admin dashboard without extracting the business logic means duplicating all admin operations: tag management, API key rotation, settings changes, report generation. Two codepaths doing the same thing inevitably diverge.

**Why it happens:** The admin panel is deeply coupled to Telegram's callback query pattern (inline buttons, multi-step flows via `ConversationHandler`). It's faster to rewrite admin logic in FastAPI than to refactor the existing code.

**Consequences:** A setting changed via web dashboard doesn't appear in Telegram admin (or vice versa). Bug fixes applied to one admin interface are missed in the other. Admin actions lack audit trail because logging is done differently in each.

**Prevention:**
- Extract admin business logic into a service layer (`admin_service.py`) that both Telegram handlers and FastAPI endpoints call
- The service layer handles: validation, DB writes, cache invalidation, audit logging
- Telegram `admin.py` becomes a thin UI adapter over the service
- FastAPI admin routes become another thin UI adapter
- Share Pydantic models (already started in `admin_models.py`) between both interfaces

**Detection:** Admin settings that work on Telegram but not web (or vice versa); admin audit log gaps; duplicated code across `admin.py` and new FastAPI routes.

**Phase mapping:** Web interface phase. Extract service layer before building web admin.

---

### Pitfall 10: Webhook Server Security for WhatsApp and Web

**What goes wrong:** WhatsApp requires a public HTTPS webhook endpoint. The current bot uses polling (no inbound webhooks). Adding a webhook endpoint exposes the server to the internet, creating attack surface: replay attacks, forged webhook payloads, DDoS. The existing `shortener_server.py` (aiohttp) runs on port 8080 with no rate limiting or authentication.

**Why it happens:** Telegram polling is inherently secure (bot initiates connections). Webhooks flip the model -- the internet connects to you. Developers add the webhook route to the existing aiohttp server without hardening it.

**Consequences:** Forged WhatsApp webhooks trigger fake photo analyses (wasting vision API quota). DDoS on the webhook endpoint takes down the URL shortener (same server). No signature verification means any HTTP client can inject messages.

**Prevention:**
- Always verify WhatsApp webhook signatures (`X-Hub-Signature-256` header) -- the `shared_meta.py` already has `verify_webhook_signature()`, ensure it's called on every request
- Rate limit webhook endpoints: max 100 requests/second per source IP
- Run webhook server behind a reverse proxy (nginx/Caddy) with TLS termination
- Separate the webhook server from the URL shortener -- different security profiles
- Add request ID logging to trace webhook deliveries end-to-end
- Implement webhook payload deduplication (Meta can retry, causing duplicate processing)

**Detection:** Vision API cost spikes with no corresponding user activity; duplicate search results; shortener downtime correlated with webhook traffic.

**Phase mapping:** WhatsApp/web interface phase. Security review before going live.

---

### Pitfall 11: Memory Leaks from Long-Running Multi-Adapter Process

**What goes wrong:** The current bot already has an unbounded rate limiter bucket (documented in CONCERNS.md). Adding WhatsApp (long-lived webhook connections), web sessions (potentially thousands), and background price history jobs to a single asyncio process means memory grows continuously. The in-memory `UserSession` with 10-minute TTL multiplied across three platforms means 3x the session objects.

**Why it happens:** Each adapter adds its own in-memory state (connection pools, session caches, webhook buffers). The existing cleanup mechanisms are inadequate (rate limiter buckets never purged, sessions only expire on access, module-level caches never invalidated).

**Consequences:** Process memory grows from 200MB to 1GB+ over days. Docker container hits memory limit and gets OOM-killed. All platforms go down simultaneously because they share a process.

**Prevention:**
- Add periodic cleanup tasks: sweep expired sessions, purge old rate limiter buckets, clear stale caches (every 1 hour)
- Set explicit memory limits in `docker-compose.yml` with alerting
- Monitor memory growth in production: log `psutil.Process().memory_info().rss` every 5 minutes
- Consider running each adapter in a separate process with shared PostgreSQL (not SQLite) for state
- Implement session persistence to DB so in-memory session count stays bounded

**Detection:** Gradual memory increase visible in Docker stats; OOM kills in Docker logs; performance degradation after 24-48 hours of uptime.

**Phase mapping:** Stability phase and ongoing. Add memory monitoring early.

---

## Minor Pitfalls

### Pitfall 12: Affiliate Link Compliance Across Platforms

**What goes wrong:** Amazon Associates requires disclosure ("As an Amazon Associate, I earn from qualifying purchases") on every page/message containing affiliate links. Telegram messages include this. WhatsApp messages and web pages also need it -- but the disclosure format, placement, and requirements differ by platform. Missing disclosure on any platform risks Amazon Associates account termination.

**Prevention:**
- Add disclosure text to the `Formatter` class per-platform, not hard-coded in handlers
- Web interface: add disclosure to page footer AND near product links
- WhatsApp: include disclosure in results message template
- Test: verify every message/page containing affiliate links includes disclosure

**Phase mapping:** Every platform adapter. Include in acceptance criteria.

---

### Pitfall 13: Photo Size and Format Differences Across Platforms

**What goes wrong:** Telegram compresses photos to ~1280px and converts to JPEG. WhatsApp may send WebP, HEIC, or full-resolution photos. Web upload accepts anything the browser supports. The vision API receives wildly different image quality depending on platform, affecting identification accuracy.

**Prevention:**
- Normalize all incoming photos in `PlatformAdapter.download_photo()`: resize to max 2048px, convert to JPEG, validate file size <10MB
- The existing `bot_core.py` imports PIL (`from PIL import Image as _PILImage`) but photo validation is documented as missing in CONCERNS.md
- Add this normalization as a `PlatformAdapter` base class method, not per-adapter

**Phase mapping:** Multi-platform phase. Add to base adapter before implementing WhatsApp/web.

---

### Pitfall 14: Progress Indicators Behaving Differently Per Platform

**What goes wrong:** The bot shows "Analyzing your photo..." as a progress message, then edits it with results. Telegram supports message editing. WhatsApp does not (`supports_photo_edit: False`). Web can use WebSocket/SSE for real-time updates. Using the same progress pattern on all platforms results in: Telegram gets smooth updates, WhatsApp gets multiple separate messages cluttering the chat, web gets nothing until results are complete.

**Prevention:**
- Check `adapter.supports_photo_edit` before using edit-based progress (already in the adapter model)
- WhatsApp: send one "Processing..." message, then send results as a new message, then delete the processing message
- Web: use Server-Sent Events (SSE) or WebSocket for streaming progress
- Define a `ProgressReporter` interface in `bot_core.py` that adapters implement differently

**Phase mapping:** Multi-platform UX design phase.

---

### Pitfall 15: Hardcoded Hebrew/English Text in Non-I18n Paths

**What goes wrong:** The codebase has `i18n.py` and uses `t()` for translations in some places, but `style.py` (1000+ lines) and error messages in handlers contain hardcoded Hebrew/English strings. Adding a web interface (which may need to support additional languages for SEO) means these strings need extraction.

**Prevention:**
- Audit all user-facing strings before building web interface
- Move remaining hardcoded strings to the `i18n` system
- Web interface should consume the same translation keys as bot adapters

**Phase mapping:** Web interface phase. Do the string audit early in the phase.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Stability fixes | Fixing bugs while adding features creates merge conflicts | Complete stability phase before branching for features |
| WhatsApp integration | 24-hour window, 3-button limit, template approval delays | Design WhatsApp UX independently; submit templates to Meta early (approval takes 1-7 days) |
| Web interface | Session/auth model mismatch with bot sessions | Design unified user identity before building web UI |
| Photo annotations | Blocking event loop, inconsistent bounding boxes | `run_in_executor()` for all Pillow ops; per-provider coordinate normalization |
| Price history charts | Scraping rate limits, Chromium resource usage | Cache aggressively; use Keepa API; generate charts from data, not screenshots |
| Web admin dashboard | Logic duplication with Telegram admin | Extract admin service layer first |
| Multi-platform launch | SQLite write contention from concurrent adapters | Migrate to PostgreSQL before second adapter goes live |
| Security | Webhook endpoints exposed to internet | Signature verification, rate limiting, reverse proxy, separate from shortener |

## Sources

- [CamelCamelCamel scraping notice](https://camelcamelcamel.com/blog/a-kind-note-to-those-crawling-our-sites/)
- [WhatsApp Business Messaging Policy](https://business.whatsapp.com/policy)
- [WhatsApp 24-hour window guide](https://www.smsmode.com/en/whatsapp-business-api-customer-care-window-ou-templates-comment-les-utiliser/)
- [WhatsApp webhook architecture](https://www.chatarchitect.com/news/building-a-scalable-webhook-architecture-for-custom-whatsapp-solutions)
- [SQLite scalability limitations](https://www.slingacademy.com/article/sqlite-scalability-limitations-and-workarounds/)
- [SQLite scaling to 100K users](https://medium.com/@codeandcortex/the-surprising-way-i-used-sqlite-to-scale-a-side-project-to-100k-users-1295dccf1212)
- [Cross-platform bot strategies](https://www.chatarchitect.com/news/cross-platform-strategies-integrating-whatsapp-with-telegram-and-beyond)
- [Pillow ImageDraw documentation](https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html)
- [FastAPI security documentation](https://fastapi.tiangolo.com/tutorial/security/)
- [Bot development for messenger platforms 2025](https://alexasteinbruck.medium.com/bot-development-for-messenger-platforms-whatsapp-telegram-and-signal-2025-guide-50635f49b8c6)
- [WhatsApp webhook implementation guide](https://business.whatsapp.com/blog/how-to-use-webhooks-from-whatsapp-business-api)
- Internal: `.planning/codebase/CONCERNS.md` (68 documented issues)
- Internal: `.planning/PROJECT.md` (project requirements and constraints)

---

*Pitfalls audit: 2026-03-13*
