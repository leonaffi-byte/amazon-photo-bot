# Architecture

**Analysis Date:** 2026-03-13

## Pattern Overview

**Overall:** Multi-adapter event-driven bot with pluggable vision providers and search backends

**Key Characteristics:**
- Async-first design (asyncio, no threads/subprocesses)
- Platform-agnostic core logic separated from platform adapters
- Modular provider system (vision models) with runtime health degradation
- Modular search backend system (Amazon search APIs) with automatic fallback
- Configuration priority: Database > .env > defaults, no restart required for changes
- Session-based state machine for per-user conversation flow
- SQLite persistence via aiosqlite with WAL mode for concurrency
- Request correlation IDs and Prometheus-compatible metrics built-in

## Layers

**Adapter Layer:**
- Purpose: Normalize messaging platforms (Telegram, WhatsApp, Discord, Instagram, Messenger, Viber, LINE) into a common interface
- Location: `adapters/` directory
- Contains: Platform-specific implementations inheriting from `PlatformAdapter` base class
  - `adapters/base.py` — Abstract `PlatformAdapter`, `MessageRef`, `Button`, `CarouselItem` types
  - `adapters/telegram.py` — PTB v20 polling bot with per_message=False ConversationHandler
  - `adapters/whatsapp.py` — WhatsApp Business API via Meta Cloud API
  - `adapters/discord_adapter.py` — Discord.py gateway client
  - `adapters/instagram.py`, `adapters/messenger.py`, `adapters/viber.py`, `adapters/line.py` — Webhook-based
  - `adapters/shared_meta.py` — Common auth/validation for Meta platforms (Instagram, Messenger, WhatsApp)
- Depends on: `PlatformAdapter` interface only
- Used by: `BotCore`, `main.py`

**Core Bot Layer:**
- Purpose: Platform-agnostic business logic that coordinates the entire request flow
- Location: `bot_core.py` (primary), `bot.py` (legacy Telegram-specific fallback)
- Contains:
  - `BotCore` class — Handles all callbacks: photo → vision → search → format → send
  - `UserSession` dataclass — Per-user state (chosen product, results page, Israel filter, etc.)
  - Photo compression and image validation
  - Session lifecycle with TTL and periodic cleanup
  - Conversation state management (choosing products, filtering, pagination)
- Depends on: `PlatformAdapter`, `VisionProvider`, `SearchBackend`, `Formatter`, database
- Used by: Adapters (forward reference via `_ref` pattern)

**Vision Analysis Layer:**
- Purpose: Identify products in photos using AI vision models
- Location: `providers/` directory
- Contains:
  - `providers/base.py` — Abstract `VisionProvider` class, `ProviderResult` dataclass, system/user prompts
  - `providers/manager.py` — Provider registry, mode selection (best/cheapest/compare/single), parallel execution, health tracking
  - `providers/openai_provider.py` — GPT-4o, GPT-4o-mini
  - `providers/anthropic_provider.py` — Claude 3.5 Sonnet, Claude 3 Haiku
  - `providers/gemini_provider.py` — Gemini 1.5 Flash, 2.0 Flash, 1.5 Pro
  - `providers/openai_compat_provider.py` — Groq (Llama 4 Scout), Mistral, SambaNova, Together
  - `providers/openrouter_provider.py` — 100+ models via single OpenRouter API key
  - `providers/azure_openai_provider.py` — GPT-4o on Azure
- Key types: `ProviderResult` (one model's output), `ProductInfo` (final chosen product)
- Health tracking: Circuit breaker pattern with auto-disable after N failures in window, auto-recovery after cooldown
- Used by: `BotCore` via `analyse_image()`

**Search Layer:**
- Purpose: Find products on Amazon matching identified products
- Location: `search_backends/` directory
- Contains:
  - `search_backends/base.py` — Abstract `SearchBackend`, `AmazonItem` dataclass with scoring/Israel eligibility
  - `search_backends/paapi_backend.py` — Amazon PA-API 5.0 (official, highest quality)
  - `search_backends/rapidapi_backend.py` — RapidAPI Real-Time Amazon Data (free tier available)
  - `search_backends/dataforseo_backend.py` — DataForSEO SERP scraping with proxy support
  - `search_backends/playwright_backend.py` — Headless Chromium fallback (slowest, most reliable)
  - `search_backends/brightdata_backend.py` — Bright Data proxy + Playwright
- Key types: `AmazonItem` with Israel delivery eligibility detection (FBA/Prime/sold-by-Amazon signals)
- Façade: `amazon_search.py` hides backend selection and fallback chain
- Used by: `BotCore` via `search_amazon()`

**Data & Configuration Layer:**
- Purpose: Persistent storage, API keys, settings
- Location: Top-level modules
- Contains:
  - `database.py` — Async SQLite with aiosqlite: users, affiliate tags, search logs, admin users, API keys, URL shortener cache
  - `config.py` — Environment variables with DB override support via `settings_store.py`
  - `key_store.py` — API key storage with DB > .env priority
  - `settings_store.py` — Runtime-editable settings (writes directly to `config` module attributes)
- Key: All reads go through layer → DB checks first, .env fallback
- Used by: All other layers

**Message Formatting Layer:**
- Purpose: Platform-aware message formatting with link/button generation
- Location: `formatter.py` (replaces old `style.py`)
- Contains:
  - `Formatter` class — Platform-specific bold/italic/link syntax, caption length limits
  - Message builders for product cards, pagination, filter buttons
  - Affiliate URL building with live tag substitution
- Capability-aware: Respects platform flags (supports_photo_edit, max_caption_length, etc.)
- Used by: `BotCore` for all outgoing messages

**Supporting Services:**
- `israel_scraper.py` — Playwright-based verification of Israel shipping eligibility with proxy support
- `url_shortener.py` + `shortener_server.py` — Custom domain URL shortener with click tracking
- `captcha_solver.py` — Amazon CAPTCHA handling via CapSolver
- `price_history.py` — Historical pricing via CamelCamelCamel + Keepa
- `translator.py` — Query language detection and translation
- `api_server.py` — FastAPI REST API for Israel shipping checks (standalone service)
- `scheduler.py` — Async task scheduler for daily/weekly/monthly admin reports
- `notifications.py` — Telegram notification delivery to admins
- `admin.py` + `admin_models.py` — Admin panel handlers (/admin, /settings, /addtag) with Pydantic models
- `circuit_breaker.py` — Per-model health tracking and auto-disable logic
- `metrics.py` — Lightweight Prometheus-compatible metrics (Counters, Histograms, Gauges)
- `correlation.py` — Request correlation IDs via Python contextvars for end-to-end tracing
- `log_group.py` — Telegram log forwarding to dedicated admin group
- `webhook_server.py` — aiohttp webhook server for webhook-based adapters (port 8081)

**Entry Points:**
- `main.py` — Primary entry point: asyncio loop, database init, adapter start, PTB/webhook servers, signal handling
- `api_server.py` — FastAPI REST API (separate process, port 8001)

## Data Flow

**Photo → Search → Results (Complete Flow):**

1. **Incoming Message**
   - Adapter receives event (photo + optional context)
   - Adapter downloads raw image bytes
   - Calls `BotCore.handle_photo(event)` with raw adapter event

2. **Photo Processing**
   - Compress image to ≤1024px to reduce API costs
   - Validate size (≤10 MB) and format
   - Store raw bytes in `UserSession.image_bytes` for "Try differently" re-analysis

3. **Vision Analysis**
   - Call `providers.manager.analyse_image(image_bytes, context_hint)`
   - Manager builds provider list from available keys (DB > .env)
   - Mode-based execution:
     - `best` — Run all in parallel, pick highest quality_score
     - `cheapest` — Run only cheapest
     - `compare` — Run all, return all results for side-by-side display
     - `single:X` — Run only provider X
   - Each provider calls AI API with structured JSON prompt
   - Provider tracks health: success increments recovery_counter, failure increments failure_counter
   - If model exceeds failure threshold → auto-disabled in DB, sent to DB as disabled
   - Returns `ProviderResult` (parsed JSON with one `ProductInfo` per detected product)

4. **Product Selection (Multi-product scenario)**
   - If vision detected multiple products (up to 6), show carousel with "Pick a product"
   - User selects one → stores in `UserSession.chosen_result`

5. **Amazon Search**
   - Call `amazon_search.search_amazon(product_info.amazon_search_query, max_results=20)`
   - `amazon_search.py` gets backend via lazy `get_backend()`:
     - If `SEARCH_BACKEND=auto` — tries PA-API first, falls back to RapidAPI
     - If backend fails, `_get_fallback_backend()` tries next available
   - Backend returns list of `AmazonItem` sorted by score (rating * log10(review_count))
   - Results stored in `UserSession.all_items`

6. **Israel Filtering**
   - User can toggle "Israel only" filter
   - `apply_filter(israel_only=True)` retains only items with:
     - `is_sold_by_amazon=True` (100% confidence)
     - OR `is_amazon_fulfilled=True` (FBA)
     - OR `is_prime=True` (97% FBA proxy)
   - Stores in `UserSession.filtered_items`

7. **Display & Pagination**
   - Format product card using `Formatter` (platform-aware)
   - Include affiliate URL with active tag
   - Show pagination buttons (prev/next)
   - User clicks button → `on_callback` → session updated → message edited in place

8. **Click Tracking**
   - All Amazon links go through custom shortener if enabled
   - Shortener logs click in database with search_log entry
   - Tracks which affiliate tag was active at click time

**State Machine (Conversation Flow):**

```
START
  ↓
  User sends photo
  ↓
  Vision analysis (parallel providers)
  ↓
  Multi-product carousel? → User picks → Single product
  ↓
  Amazon search → Results carousel
  ↓
  ├─ User clicks "Filter Israel" → Apply filter → Show filtered results
  ├─ User clicks "Prev/Next" → Update page → Show next results
  ├─ User clicks "Use this" → Shortener logs → Return affiliate link
  ├─ User sends new photo → START (new session)
  └─ Session timeout (10 min) → Clean up session
```

## Key Abstractions

**PlatformAdapter:**
- Purpose: Normalize platform-specific message handling
- Examples: `TelegramAdapter`, `WhatsAppAdapter`, `DiscordAdapter`
- Pattern: Subclasses implement abstract methods (start/stop/send_text/send_photo/edit_message/etc.)
- Capability flags: `supports_photo_edit`, `max_message_length`, `supports_inline_buttons`

**VisionProvider:**
- Purpose: Standardize AI vision model calls
- Examples: `OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`
- Pattern: Subclasses implement `analyse(image_bytes, context_hint)` → returns `ProviderResult`
- Health tracking: Each provider tracked separately in database with failure counter, disable flag, recovery cooldown

**SearchBackend:**
- Purpose: Standardize Amazon search APIs
- Examples: `PAAPIBackend`, `RapidAPIBackend`, `DataForSEOBackend`
- Pattern: Subclasses implement `search(query, max_results, page)` → returns list[`AmazonItem`]
- Fallback: If backend fails, next available backend tried automatically

**UserSession:**
- Purpose: Hold all per-user request state
- Location: `bot_core.py` lines 82-150
- Contains: All provider results, chosen product, Amazon results, filtering state, pagination state
- Lifecycle: Created on first request, auto-cleaned after 10 min TTL via periodic cleanup task

**AmazonItem:**
- Purpose: Structured product data from any backend
- Examples: ASIN, title, price, rating, FBA status
- Israel eligibility: Computed from FBA/Prime/sold-by-Amazon signals
- Scoring: `rating * log10(review_count + 1)` for ranking

## Error Handling

**Strategy:** Graceful degradation with fallback chains

**Patterns:**

1. **Vision Provider Failures:**
   - Individual provider fails → Try next provider in parallel
   - All providers fail → Show error to user, offer "Try again with different provider"
   - Health tracking: Count failures per provider in time window; auto-disable after threshold
   - Auto-recovery: After cooldown, move provider back to degraded state for retry

2. **Search Backend Failures:**
   - Primary backend fails → Auto-fallback to next available backend
   - All backends fail → Show "No results" or generic error
   - No automatic retry (user can manually re-search)

3. **Database Failures:**
   - If DB unavailable at startup → Fatal error, exit
   - If DB unavailable during operation → Proceed without persistence (no logging, no settings)

4. **Image Upload Failures:**
   - Image corrupted → Parser fails → Show "Couldn't analyze photo"
   - Image too large (>10 MB) → Reject before processing
   - Image too small/empty → Vision model returns empty products list

## Cross-Cutting Concerns

**Logging:**
- Every module has `logger = logging.getLogger(__name__)`
- Logs go to stdout + `{DATA_DIR}/bot.log`
- `CorrelationFilter` injects 12-char correlation ID into every log record
- Format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s` + correlation ID

**Validation:**
- API keys: `key_validator.py` tests all keys at /admin startup
- Config: Priority order enforced in `key_store.py` and `settings_store.py`
- Models: Pydantic models in `admin_models.py` for admin UI

**Authentication:**
- Telegram: `ADMIN_IDS` env var, checked via `db.is_admin(user_id)`
- FastAPI: `X-API-Key` header + rate limiting by key tier (free/basic/pro)
- Adapters: Platform-specific (Discord guild member check, WhatsApp phone number whitelist)

**Metrics:**
- `REQUESTS_TOTAL` — Request count by type (photo/text/callback)
- `VISION_LATENCY` — Per-provider latency histogram
- `API_COST_DOLLARS` — Cumulative cost by provider
- `ERRORS_TOTAL` — Error count by type
- Prometheus-compatible text format exported at `/metrics` (shortener server)

---

*Architecture analysis: 2026-03-13*
