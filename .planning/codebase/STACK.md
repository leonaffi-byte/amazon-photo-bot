# Technology Stack

**Analysis Date:** 2025-03-13

## Languages

**Primary:**
- Python 3.11+ - Entire codebase (async-first with asyncio)

**Secondary:**
- None (pure Python project)

## Runtime

**Environment:**
- Python 3.11+ (required)
- asyncio event loop (async-first, no threads or subprocesses)

**Package Manager:**
- pip (via requirements.txt)
- Lockfile: `requirements.lock` (for reproducible Docker builds)

## Frameworks

**Core:**
- python-telegram-bot 20.7 - Telegram bot framework (PTB v20 async handlers)
- FastAPI 0.110.0+ - REST API server (Israel shipping verifier API)
- aiohttp 3.10.0 - Async HTTP client for all API calls
- uvicorn[standard] 0.27.0+ - ASGI server for FastAPI
- Playwright 1.40.0+ - Headless browser automation (Chromium for scraping fallback)

**Vision Providers:**
- openai 1.50.0+ - GPT-4o, GPT-4o-mini vision models
- anthropic 0.40.0+ - Claude 3.5 Sonnet, Claude 3 Haiku
- google-genai 1.0.0+ - Gemini 1.5 Flash, 2.0 Flash, 1.5 Pro
- (Optional) OpenRouter SDK - 100+ models via single API

**Utilities:**
- python-dotenv 1.0.1 - Environment variable loading
- Pillow 10.4.0 - Image processing
- beautifulsoup4 4.12.0+ - HTML parsing (search backends, price history)
- playwright-stealth 1.0.6 - Stealth mode for Chromium (bypass detection)
- aiofiles 23.2.1 - Async file I/O
- aiosqlite 0.20.0 - Async SQLite3 wrapper
- tzdata - IANA timezone data (scheduled reports)

**Platform Adapters:**
- discord.py 2.3.0+ - Discord bot gateway (optional adapter)
- line-bot-sdk 3.0.0+ - LINE Messaging API client (optional adapter)

## Key Dependencies

**Critical:**
- **aiohttp** - All external API calls (vision providers, search backends, scrapers) use async HTTP
- **aiosqlite** - Async SQLite for persistence (14 tables: users, logs, settings, backups, URL shortener analytics)
- **Playwright** - Headless Chromium for: Amazon product scraping fallback, price history scraping, Israel shipping verification
- **python-telegram-bot** - Telegram webhook/polling handlers, conversation state

**Infrastructure:**
- **FastAPI** - REST API for Israel shipping verification endpoint (separate service)
- **uvicorn** - ASGI application server
- **beautifulsoup4** - HTML parsing for search results and price tracking
- **Pillow** - Image resizing/validation before sending to vision providers

## Configuration

**Environment:**
- `.env` file loaded via python-dotenv
- Priority order: Database (admin panel) > Environment variable > Hard-coded defaults
- Key categories:
  - Telegram: `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`
  - Vision providers: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, etc.
  - Search backends: `SEARCH_BACKEND`, `RAPIDAPI_KEY`, `AMAZON_ACCESS_KEY/SECRET_KEY`
  - URL shortener: `SHORTENER_ENABLED`, `SHORTENER_BASE_URL`, `SHORTENER_PORT`
  - Bot behavior: `RESULTS_PER_PAGE`, `MAX_RESULTS`, `FREE_DELIVERY_THRESHOLD`
  - Scheduled reports: `REPORT_TIMEZONE`, `REPORT_HOUR`

**Build:**
- Dockerfile - Python 3.11-slim base
- docker-compose.yml - 3 services (amazon-bot, amazon-api, test-bot) sharing `./data` volume
- PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers - Shared Chromium install in Docker

## Platform Requirements

**Development:**
- Python 3.11+
- pip packages from requirements.txt
- `playwright install --with-deps chromium` (optional, for scraping backends)
- Unix shell (bash/zsh) for scripts

**Production:**
- Docker (official supported deployment)
- Data volume mount for persistence (`./data:/app/data`)
- Environment file (.env) with API keys
- Optional: Nginx reverse proxy for custom URL shortener domain
- Optional: WireGuard VPN for Israel IP exit node (for israel_scraper)

**External Services Required:**
- Telegram BotFather (bot token)
- At least one vision provider (OpenAI, Anthropic, Google, OpenRouter, etc.)
- At least one search backend (RapidAPI recommended for new bots, PA-API if Associates member)
- Optional: CapSolver (CAPTCHA solving)
- Optional: Bright Data / Decodo (proxy for price history scraping)
- Optional: Custom domain for URL shortener (self-hosted via aiohttp web server)

## Additional Notes

- **Async-first design**: All I/O operations use async/await; zero synchronous blocking calls
- **No external databases**: SQLite only, single file at `{DATA_DIR}/bot_data.db`
- **Graceful shutdown**: SIGINT/SIGTERM handlers close connections cleanly
- **Health checks**: Model health tracking auto-disables providers after 3-5 consecutive failures, auto-recovers after cooldown
- **Logging**: Module-level loggers per file, outputs to stdout + `{DATA_DIR}/bot.log`
- **Docker multi-service**: Single Dockerfile builds image reused by all 3 services in docker-compose.yml

---

*Stack analysis: 2025-03-13*
