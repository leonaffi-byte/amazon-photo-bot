# CLAUDE.md

## Project Overview

Amazon Photo Bot is a Telegram bot that identifies products in user-submitted photos using AI vision models and finds matching items on Amazon. Built with Python 3.11+, the entire codebase is async-first (asyncio) with no threads or subprocesses. It supports 7 vision providers, 4 Amazon search backends, a FastAPI REST API, a custom URL shortener, and an admin panel for live configuration.

## Quick Commands

```bash
# Run the bot
python main.py                          # Telegram bot + URL shortener server

# Run auxiliary services
python api_server.py                    # FastAPI REST API (port 8001)
python testbot.py                       # Test bot variant

# Tests
pytest                                  # Full test suite (20 test files)

# Setup
pip install -r requirements.txt         # Install Python dependencies
playwright install --with-deps chromium # Install headless browser for scraping
cp .env.example .env                    # Create config (fill in API keys)

# Docker
docker compose up amazon-bot            # Main bot (port 8080 for shortener)
docker compose up amazon-api            # REST API (port 8001)
docker compose up test-bot              # Test bot
```

## Architecture

### Entry Points

- **`main.py`** — Primary entry point. Bootstraps the asyncio event loop, initializes the database, starts PTB polling and the shortener server. Handles graceful shutdown via SIGINT/SIGTERM.
- **`api_server.py`** — FastAPI REST API for Israel shipping verification. Runs independently.
- **`testbot.py`** — Testing bot variant with its own Telegram token.

### Core Modules

| Module | Purpose |
|--------|---------|
| `bot.py` | Telegram handlers, conversation flow, per-user `UserSession` state |
| `config.py` | Configuration from `.env`. Priority: **DB > .env > defaults** |
| `database.py` | Async SQLite via `aiosqlite` (14 tables, file at `{DATA_DIR}/bot_data.db`) |
| `admin.py` | Admin panel handlers (`/admin`, `/addtag`, `/settings`) |
| `admin_models.py` | Pydantic models for admin UI buttons and menus |
| `key_store.py` | API key storage with DB-first, `.env` fallback |
| `settings_store.py` | Runtime-editable settings — writes directly to `config` module attributes |
| `image_analyzer.py` | `ProductInfo` dataclass and vision analysis wrapper |
| `amazon_search.py` | Facade over search backends (auto-selects best available) |
| `style.py` | Message formatting, emoji theming, inline keyboards |
| `translator.py` | Language detection and search query translation |
| `scheduler.py` | Async task scheduler for daily/weekly/monthly admin reports |
| `notifications.py` | Telegram notification delivery to admins |

### Provider System (`providers/`)

Pluggable vision providers implement `VisionProvider` (defined in `providers/base.py`):

- `openai_provider.py` — GPT-4o, GPT-4o-mini
- `anthropic_provider.py` — Claude 3.5 Sonnet, Claude 3 Haiku
- `gemini_provider.py` — Gemini 1.5 Flash, 2.0 Flash, 1.5 Pro
- `groq_provider.py` — Llama 4 Scout
- `azure_openai_provider.py` — GPT-4o on Azure
- `openrouter_provider.py` — 100+ models via single API key

**Manager** (`providers/manager.py`) orchestrates providers in parallel with modes: `best`, `cheapest`, `compare`, `single:<provider>/<model>`. Only providers with valid API keys are activated.

### Search Backend System (`search_backends/`)

Pluggable search backends implement `SearchBackend` (defined in `search_backends/base.py`):

- `paapi_backend.py` — Amazon PA-API 5.0 (official, requires 3 qualifying sales)
- `rapidapi_backend.py` — RapidAPI Real-Time Amazon Data (easiest to start)
- `dataforseo_backend.py` — DataForSEO SERP scraping
- `playwright_backend.py` — Headless Chromium scraping (fallback)

**Facade** (`amazon_search.py`) hides backend selection. Auto mode tries PA-API first, falls back to RapidAPI.

### Supporting Modules

| Module | Purpose |
|--------|---------|
| `israel_scraper.py` | Playwright-based Israel shipping verification with proxy support |
| `price_history.py` | Price tracking via CamelCamelCamel + Keepa fallback |
| `captcha_solver.py` | Amazon CAPTCHA handling via CapSolver |
| `url_shortener.py` | Link shortening (TinyURL or custom self-hosted) |
| `shortener_server.py` | aiohttp web server for custom URL shortener |
| `playwright_utils.py` | Playwright stealth mode helpers |

## Key Conventions

- **Async-first**: All I/O uses async APIs (aiohttp, aiosqlite, Playwright async, PTB v20 async). No blocking calls.
- **Configuration priority**: Database (admin panel) > `.env` file > hard-coded defaults. Settings changed via admin panel take effect immediately without restart.
- **Module-level loggers**: Every module uses `logger = logging.getLogger(__name__)`. Logs go to both stdout and `{DATA_DIR}/bot.log`.
- **Type hints**: Python 3.11+ union syntax (`str | None`, `set[int]`). Dataclasses for structured data (`ProductInfo`, `ProviderResult`, `AmazonItem`, `UserSession`).
- **Error handling**: Try-except with fallback chains (e.g., PA-API fails → RapidAPI). Model health tracking auto-disables models after 5 consecutive failures.
- **Commit messages**: Conventional commits — `feat:` for new features, `fix:` for bug fixes.
- **No linter/formatter**: No pyproject.toml, pylint, ruff, or pre-commit hooks are configured.

## Testing

- **Framework**: pytest + pytest-asyncio (`asyncio_mode = auto` in `pytest.ini`)
- **Test discovery**: `tests/` directory, files matching `test_*.py`
- **Isolation**: `conftest.py` provides an autouse `tmp_data_dir` fixture that redirects `DATA_DIR` to a fresh temporary directory per test, giving each test its own clean SQLite database
- **Mocking**: External APIs are mocked with `unittest.mock.patch`
- **Coverage**: 20 test files covering providers, search backends, database, API server, admin features, and utilities

## Project Layout

```
amazon-photo-bot/
├── main.py                    # Entry point — asyncio loop, DB init, PTB polling
├── bot.py                     # Telegram handlers & UserSession state
├── config.py                  # .env config with DB override support
├── database.py                # Async SQLite (14 tables)
├── admin.py                   # Admin panel handlers
├── admin_models.py            # Admin UI models
├── image_analyzer.py          # ProductInfo dataclass & vision wrapper
├── amazon_search.py           # Search facade (backend selection)
├── israel_scraper.py          # Israel shipping verification
├── api_server.py              # FastAPI REST API
├── shortener_server.py        # URL shortener web server
├── url_shortener.py           # Link shortening logic
├── key_store.py               # API key storage (DB > .env)
├── settings_store.py          # Runtime settings persistence
├── style.py                   # Message formatting & keyboards
├── translator.py              # Language detection & translation
├── scheduler.py               # Scheduled admin reports
├── notifications.py           # Telegram notifications
├── captcha_solver.py          # CAPTCHA handling
├── price_history.py           # Price tracking
├── playwright_utils.py        # Playwright stealth helpers
├── testbot.py                 # Test bot variant
├── providers/                 # Vision provider plugins
│   ├── base.py                #   Abstract VisionProvider + ProviderResult
│   ├── manager.py             #   Multi-provider orchestration
│   ├── openai_provider.py
│   ├── anthropic_provider.py
│   ├── gemini_provider.py
│   ├── groq_provider.py
│   ├── azure_openai_provider.py
│   └── openrouter_provider.py
├── search_backends/           # Amazon search backends
│   ├── base.py                #   Abstract SearchBackend + AmazonItem
│   ├── paapi_backend.py
│   ├── rapidapi_backend.py
│   ├── dataforseo_backend.py
│   └── playwright_backend.py
├── tests/                     # Test suite (20 files)
│   ├── conftest.py            #   Shared fixtures (tmp_data_dir)
│   └── test_*.py
├── debug_*.py                 # Development-only debug scripts
├── requirements.txt           # Python dependencies
├── .env.example               # Environment variable template
├── Dockerfile                 # Python 3.11-slim + Playwright Chromium
├── docker-compose.yml         # 3 services: bot, api, test-bot
└── pytest.ini                 # Test configuration
```

## Environment Variables

See `.env.example` for all options. Key categories:

| Category | Examples |
|----------|----------|
| Telegram | `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, `REPORT_TIMEZONE` |
| Vision Providers | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY` |
| Vision Mode | `VISION_MODE` (`best` / `cheapest` / `compare` / `single:<provider>/<model>`) |
| Search Backend | `SEARCH_BACKEND` (`auto` / `paapi` / `rapidapi` / `dataforseo`), `RAPIDAPI_KEY` |
| Amazon PA-API | `AMAZON_ACCESS_KEY`, `AMAZON_SECRET_KEY`, `AMAZON_ASSOCIATE_TAG` |
| URL Shortener | `SHORTENER_ENABLED`, `SHORTENER_BASE_URL`, `SHORTENER_PORT` |
| Bot Behavior | `RESULTS_PER_PAGE` (5), `MAX_RESULTS` (20), `FREE_DELIVERY_THRESHOLD` (49) |

## Docker

- **Dockerfile**: Python 3.11-slim base, installs Playwright Chromium (~350 MB)
- **docker-compose.yml**: 3 services sharing `./data:/app/data` volume
  - `amazon-bot` — main bot (port 8080 for shortener)
  - `amazon-api` — FastAPI REST API (port 8001)
  - `test-bot` — test bot variant
- Logs: JSON file driver with size rotation (5-10 MB, 2-3 files)

## Known Issues & Improvements

See [`IMPROVEMENTS.md`](IMPROVEMENTS.md) for a comprehensive, prioritized list of 80 improvement suggestions across security, reliability, performance, UX, code quality, and testing — generated from a full codebase audit.
