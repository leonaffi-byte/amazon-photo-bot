# Codebase Structure

**Analysis Date:** 2026-03-13

## Directory Layout

```
amazon-photo-bot/
├── main.py                    # Entry point — asyncio loop, adapter startup, shutdown
├── bot_core.py                # Platform-agnostic bot logic, UserSession state machine
├── bot.py                     # Legacy Telegram-specific handler (backward compat, deprecated)
├── config.py                  # .env configuration with DB override support
├── database.py                # Async SQLite via aiosqlite (14 tables, WAL mode)
├── key_store.py               # API key storage (DB > .env priority)
├── settings_store.py          # Runtime-editable settings, live config updates
├── formatter.py               # Platform-aware message formatting (bold/italic/links)
├── image_analyzer.py          # ProductInfo dataclass (canonical home)
├── amazon_search.py           # Search backend façade with fallback logic
├── api_server.py              # FastAPI REST API (Israel shipping verification)
├── admin.py                   # Admin panel handlers (/admin, /settings, /addtag)
├── admin_models.py            # Pydantic models for admin UI buttons/menus
├── israel_scraper.py          # Playwright Israel shipping verification with proxy
├── captcha_solver.py          # Amazon CAPTCHA handling via CapSolver
├── price_history.py           # Price tracking (CamelCamelCamel + Keepa)
├── url_shortener.py           # Link shortening logic
├── shortener_server.py        # aiohttp web server for custom URL shortener
├── translator.py              # Language detection and query translation
├── scheduler.py               # Async task scheduler for periodic reports
├── notifications.py           # Telegram admin notifications
├── log_group.py               # Telegram log forwarding to admin group
├── webhook_server.py          # aiohttp webhook server (port 8081)
├── circuit_breaker.py         # Per-model health tracking with auto-disable
├── metrics.py                 # Prometheus-compatible metrics (no dependencies)
├── correlation.py             # Request correlation IDs via contextvars
├── i18n.py                    # i18n locale management
├── image_annotator.py         # Image bbox visualization (debug)
├── dataforseo_labs.py         # DataForSEO complementary searches
├── db_backup.py               # Database backup utilities
├── key_validator.py           # API key validation at startup
├── dataforseo_backend.py      # (Legacy, see search_backends/)
├──
├── adapters/                  # Platform-agnostic adapter layer
│   ├── __init__.py
│   ├── base.py                # Abstract PlatformAdapter, MessageRef, Button, CarouselItem
│   ├── telegram.py            # PTB v20 async polling bot
│   ├── whatsapp.py            # Meta Cloud API
│   ├── discord_adapter.py      # Discord.py gateway
│   ├── instagram.py           # Meta Instagram
│   ├── messenger.py           # Meta Messenger
│   ├── viber.py               # Viber Bot API
│   ├── line.py                # LINE Bot API
│   └── shared_meta.py         # Common auth for Meta platforms (Instagram/Messenger/WhatsApp)
│
├── providers/                 # Vision provider plugins
│   ├── __init__.py
│   ├── base.py                # Abstract VisionProvider, ProviderResult, system/user prompts
│   ├── manager.py             # Provider registry, mode selection (best/cheapest/compare/single)
│   ├── openai_provider.py     # GPT-4o, GPT-4o-mini
│   ├── anthropic_provider.py  # Claude 3.5 Sonnet, Claude 3 Haiku
│   ├── gemini_provider.py     # Gemini 1.5 Flash, 2.0 Flash, 1.5 Pro
│   ├── azure_openai_provider.py # GPT-4o on Azure
│   ├── openrouter_provider.py # 100+ models via OpenRouter
│   └── openai_compat_provider.py # Groq, Mistral, SambaNova, Together (OpenAI-compatible)
│
├── search_backends/           # Amazon search API backends
│   ├── __init__.py
│   ├── base.py                # Abstract SearchBackend, AmazonItem with scoring/Israel logic
│   ├── paapi_backend.py       # Amazon PA-API 5.0 (official, highest quality)
│   ├── rapidapi_backend.py    # RapidAPI Real-Time Amazon Data (easy, recommended)
│   ├── dataforseo_backend.py  # DataForSEO SERP scraping
│   ├── playwright_backend.py  # Headless Chromium fallback
│   └── brightdata_backend.py  # Bright Data proxy + Playwright
│
├── tests/                     # Test suite (20+ test files)
│   ├── __init__.py
│   ├── conftest.py            # Shared fixtures (tmp_data_dir for DB isolation)
│   ├── test_admin.py          # Admin panel handlers
│   ├── test_amazon_item.py    # AmazonItem scoring/Israel eligibility
│   ├── test_amazon_search.py  # Search backend façade and fallback
│   ├── test_api_server.py     # FastAPI REST API endpoints
│   ├── test_bot.py            # BotCore message handling
│   ├── test_captcha_solver.py # CAPTCHA handling
│   ├── test_circuit_breaker.py # Health tracking
│   ├── test_cmd_shorten.py    # URL shortener commands
│   ├── test_correlation.py    # Correlation ID tracking
│   ├── test_database.py       # SQLite operations
│   ├── test_dataforseo_*.py   # DataForSEO backend tests
│   ├── test_israel_scraper.py # Israel shipping verification
│   ├── test_key_*.py          # API key storage/validation
│   ├── test_paapi_backend.py  # PA-API backend
│   ├── test_playwright_backend.py # Playwright backend
│   ├── test_price_history.py  # Price tracking
│   └── test_*.py              # Other tests (20+ total)
│
├── locale/                    # i18n translation files
│   └── [language].po          # Gettext format translations
│
├── docs/                      # Documentation
│   ├── plans/                 # Phase planning documents
│   └── [various markdown files]
│
├── openspec/                  # OpenSpec change tracking
│   ├── specs/                 # Spec documents
│   └── changes/               # Change history
│
├── data/                      # Runtime data (Docker volume mount point)
│   ├── bot_data.db            # SQLite database (auto-created)
│   ├── bot.log                # Application logs
│   └── [generated shortener codes]
│
├── .planning/                 # GSD planning documents
│   ├── codebase/
│   │   ├── STACK.md           # Technology stack
│   │   ├── INTEGRATIONS.md    # External APIs
│   │   ├── ARCHITECTURE.md    # Architecture overview
│   │   ├── STRUCTURE.md       # This file
│   │   ├── CONVENTIONS.md     # Coding conventions
│   │   ├── TESTING.md         # Testing patterns
│   │   └── CONCERNS.md        # Technical debt and issues
│   └── phases/                # Implementation plans
│
├── .claude/                   # Claude Code workspace config
├── .github/                   # GitHub workflows
├── .env                       # Local environment (not committed)
├── .env.example               # Environment template
├── requirements.txt           # Python dependencies
├── Dockerfile                 # Python 3.11-slim + Playwright
├── docker-compose.yml         # 3 services: bot, api, test-bot
├── pytest.ini                 # Test configuration
├── CLAUDE.md                  # Project overview (this file!)
├── IMPROVEMENTS.md            # 80 prioritized improvement suggestions
├── README.md                  # Public documentation
└── [root level support files]
```

## Directory Purposes

**`adapters/`:**
- Purpose: Platform-specific messaging implementations
- Contains: One file per messaging platform (Telegram, WhatsApp, Discord, etc.)
- Key files: `base.py` (abstract), `shared_meta.py` (common Meta logic)
- Each adapter calls back into `BotCore` via callback functions passed at init time

**`providers/`:**
- Purpose: Vision model implementations (pluggable)
- Contains: One file per provider (OpenAI, Anthropic, Google, etc.)
- Key files: `base.py` (abstract + prompts), `manager.py` (orchestration)
- Initialization: Lazy-loaded on first request; keys read from `key_store` (DB > .env)

**`search_backends/`:**
- Purpose: Amazon search API implementations (pluggable)
- Contains: One file per backend (PA-API, RapidAPI, DataForSEO, Playwright)
- Key files: `base.py` (abstract + `AmazonItem` type)
- Initialization: Lazy-loaded via `amazon_search.get_backend()`; fallback chain if active fails

**`tests/`:**
- Purpose: Test coverage for all modules
- Contains: pytest files matching `test_*.py` pattern
- Pattern: Async tests using `pytest-asyncio` with `asyncio_mode = auto`
- Isolation: `conftest.py` provides `tmp_data_dir` fixture for per-test DB cleanup

**`locale/`:**
- Purpose: i18n translation files
- Format: Gettext (.po files)
- Loading: Called in `main.py` before adapters start

**`docs/` & `openspec/`:**
- Purpose: Documentation and change tracking
- Generated by tools, not manually edited in most cases

**`data/`:**
- Purpose: Runtime data directory
- Contents: SQLite database, logs, generated shortener codes
- Docker: Mounted as volume `./data:/app/data` for persistence

## Key File Locations

**Entry Points:**
- `main.py` — Primary bot entry point
- `api_server.py` — REST API entry point
- `tests/conftest.py` — Test fixture setup (pytest runs this first)

**Configuration:**
- `config.py` — All environment variables and defaults
- `key_store.py` — API key lookup (DB > .env)
- `settings_store.py` — Runtime setting updates (writes to `config` module)
- `.env` — Local environment (not committed, see `.env.example`)
- `requirements.txt` — Python dependencies

**Core Logic:**
- `bot_core.py` — Main request handler (`BotCore` class)
- `image_analyzer.py` — `ProductInfo` dataclass
- `amazon_search.py` — Search backend selection
- `formatter.py` — Message formatting

**Database:**
- `database.py` — Schema, queries, migrations
- `database.py` lines 92-150 — `_SCHEMA` with 14 tables (users, admin_users, affiliate_tags, search_logs, api_keys, etc.)
- Location: `{DATA_DIR}/bot_data.db` (default: `data/bot_data.db`)

**Admin Interface:**
- `admin.py` — /admin, /settings, /addtag handlers (~70 KB)
- `admin_models.py` — Pydantic models for UI buttons

**Utilities:**
- `url_shortener.py` — Link shortening
- `shortener_server.py` — aiohttp server (port 8080)
- `israel_scraper.py` — Playwright-based scraping
- `translator.py` — Language detection
- `scheduler.py` — Periodic tasks
- `notifications.py` — Admin alerts
- `circuit_breaker.py` — Model health tracking
- `correlation.py` — Request correlation IDs
- `metrics.py` — Prometheus metrics
- `captcha_solver.py` — CAPTCHA handling

**Tests:**
- `tests/conftest.py` — Fixture setup
- `tests/test_bot.py` — BotCore tests
- `tests/test_database.py` — Database operations
- `tests/test_admin.py` — Admin panel
- `tests/test_amazon_search.py` — Search backends
- `tests/test_israel_scraper.py` — Scraping
- `tests/test_*.py` — Provider/backend-specific tests (20+ files)

## Naming Conventions

**Files:**
- `*_provider.py` — Vision provider implementation
- `*_backend.py` — Search backend implementation
- `test_*.py` — Test files
- `*_server.py` — Web server implementations
- `*_adapter.py` — Platform adapters

**Directories:**
- `providers/` — Vision providers (pluggable)
- `search_backends/` — Amazon search backends (pluggable)
- `adapters/` — Platform adapters (pluggable)
- `tests/` — Test suite
- `locale/` — Translation files

**Functions:**
- `async def` — All I/O uses async
- `get_*()` — Getter functions (often cached/lazy-loaded)
- `handle_*()` — Event handlers (callbacks)
- `_*()` — Private/internal functions
- `@property` — Computed attributes

**Classes:**
- `*Provider` — Vision provider implementations
- `*Backend` — Search backend implementations
- `*Adapter` — Platform adapters
- `*Filter` — Logging filters
- `*Manager` — Orchestrators (e.g., ProviderManager)

**Variables:**
- `_*` — Module-level private variables
- `_SCREAMING_SNAKE_CASE` — Constants
- `snake_case` — Functions and variables
- `CamelCase` — Classes only

## Where to Add New Code

**New Vision Provider:**
- Primary code: `providers/new_provider.py`
  - Subclass `VisionProvider` from `providers/base.py`
  - Implement `analyse(image_bytes, context_hint)` → return `ProviderResult`
  - Add cost estimates (cost_usd_input, cost_usd_output, cost_usd_per_image)
- Registration: Update `providers/manager.py` → add instantiation in `_build_providers()`
- Tests: `tests/test_new_provider.py`
- Env vars: Add ENABLE flags if model-specific toggles needed

**New Search Backend:**
- Primary code: `search_backends/new_backend.py`
  - Subclass `SearchBackend` from `search_backends/base.py`
  - Implement `search(query, max_results, page)` → return list[`AmazonItem`]
- Registration: Update `amazon_search.py` → add instantiation in `_build_backend()`
- Tests: `tests/test_new_backend.py`
- Env vars: Add keys as needed (checked in `_build_backend()`)

**New Platform Adapter:**
- Primary code: `adapters/new_platform.py`
  - Subclass `PlatformAdapter` from `adapters/base.py`
  - Implement lifecycle (start/stop) and message handling (send_text/send_photo/etc.)
  - Set capability flags (supports_photo_edit, max_message_length, etc.)
- Registration: Add to `main.py` → instantiate if env var set → pass callbacks
- Tests: `tests/test_adapter_new_platform.py`
- Webhook: If webhook-based, implement `handle_webhook` + optionally `handle_webhook_verify`
  - Routes auto-registered in `webhook_server.py`

**New Admin Command:**
- Primary code: Add handler function in `admin.py`
- Models: Add Pydantic model in `admin_models.py` if complex UI (buttons, menus)
- Handler signature: `async def _cmd_foo(uid: int, cid: int, args: list[str], event)`
- Registration: Add route in `admin.py` ConversationHandler or direct command handler
- Tests: `tests/test_admin.py`

**New Database Table:**
- Schema: Add CREATE TABLE in `database.py` → `_SCHEMA` string (~line 92)
- Queries: Add functions in `database.py` (get_*, set_*, create_*, delete_*)
- Migrations: If schema changes, add migration in `database.py` → `init_db()` function
- Tests: `tests/test_database.py`

**New Utility/Helper:**
- Primary code: Top-level module (e.g., `foo.py`)
- Logging: Add `logger = logging.getLogger(__name__)` at top
- Async: Use async functions if any I/O
- Tests: `tests/test_foo.py`

**Test Writing:**
- Location: `tests/test_*.py`
- Pattern: Use `tmp_data_dir` fixture for DB isolation
- Mocking: Use `unittest.mock.patch` for external APIs
- Async: Mark with `@pytest.mark.asyncio` or use `pytest-asyncio` auto mode
- Example: See `tests/test_bot.py` for BotCore tests

## Special Directories

**`data/`:**
- Purpose: Runtime data
- Generated: Yes (auto-created at startup)
- Committed: No (ignored in .gitignore)
- Docker: Mounted as `/app/data` volume

**`.env`:**
- Purpose: Local configuration
- Generated: No (user copies .env.example and fills in)
- Committed: No (in .gitignore)
- Secrets: Contains API keys (NEVER commit)

**`locale/`:**
- Purpose: i18n translations
- Generated: Yes (by translation tools, may be auto-generated)
- Committed: Yes (translations checked in)

**`.planning/codebase/`:**
- Purpose: GSD analysis documents
- Generated: Yes (by /gsd:map-codebase command)
- Committed: Yes
- Updated: On each GSD analysis

---

*Structure analysis: 2026-03-13*
