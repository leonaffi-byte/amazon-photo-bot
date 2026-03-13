# External Integrations

**Analysis Date:** 2025-03-13

## APIs & External Services

**Vision Providers (AI image analysis):**
- **OpenAI** - GPT-4o, GPT-4o-mini vision models
  - SDK: `openai` package
  - Auth: `OPENAI_API_KEY` (env var → DB override via admin panel)
  - Endpoint: `https://api.openai.com/v1/chat/completions`
  - Max image size: 20 MB per image
  - Implementation: `providers/openai_provider.py`

- **Anthropic** - Claude 3.5 Sonnet, Claude 3 Haiku
  - SDK: `anthropic` package
  - Auth: `ANTHROPIC_API_KEY`
  - Endpoint: `https://api.anthropic.com/v1/messages`
  - Implementation: `providers/anthropic_provider.py`

- **Google Generative AI** - Gemini 1.5 Flash, 2.0 Flash, 1.5 Pro
  - SDK: `google-genai` package
  - Auth: `GOOGLE_API_KEY`
  - Endpoint: `https://generativelanguage.googleapis.com/v1beta/models`
  - Implementation: `providers/gemini_provider.py`

- **OpenRouter** - 100+ LLM/vision models via single API
  - SDK: None (custom httpx client)
  - Auth: `OPENROUTER_API_KEY`
  - Endpoint: `https://openrouter.ai/api/v1/chat/completions`
  - Implementation: `providers/openrouter_provider.py`

- **Azure OpenAI** - Azure-hosted GPT-4o
  - SDK: Custom httpx client (OpenAI-compatible)
  - Auth: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`
  - Implementation: `providers/azure_openai_provider.py`

**Search Backends (Amazon product lookup):**
- **RapidAPI Real-Time Amazon Data** - Primary recommended backend
  - Auth: `RAPIDAPI_KEY`
  - Endpoint: `https://real-time-amazon-data.p.rapidapi.com/search`
  - Free tier: 100 searches/month
  - Implementation: `search_backends/rapidapi_backend.py`

- **Amazon PA-API 5.0** - Official Amazon Product Advertising API
  - Auth: `AMAZON_ACCESS_KEY`, `AMAZON_SECRET_KEY`, `AMAZON_ASSOCIATE_TAG`
  - Endpoint: AWS signed API (partner-api.amazon.com)
  - Requirement: Associates account + 3 qualifying sales within 180 days
  - Implementation: `search_backends/paapi_backend.py`

- **DataForSEO SERP** - Search engine results parsing
  - Auth: `DATAFORSEO_API_KEY`
  - Endpoint: `https://api.dataforseo.com/v3/`
  - Implementation: `search_backends/dataforseo_backend.py`

- **Playwright Headless Scraping** - Last-resort fallback
  - Uses Chromium to scrape amazon.com directly
  - No API keys required
  - Implementation: `search_backends/playwright_backend.py`

- **Bright Data (formerly Luminati)** - Proxy provider for scraping
  - Auth: `BRIGHTDATA_ZONE`, `BRIGHTDATA_API_KEY`
  - Endpoint: `https://api.brightdata.com/request`
  - Used for price history and Israel shipping checks
  - Implementation: `search_backends/brightdata_backend.py`

**CAPTCHA Solving:**
- **CapSolver** - Amazon CAPTCHA solving
  - Auth: `CAPSOLVER_API_KEY`
  - Endpoint: `https://api.capsolver.com/`
  - Cost: ~$0.80/1000 solves
  - Used by: Israel scraper (`israel_scraper.py`), Playwright backend
  - Implementation: `captcha_solver.py`

**Price History Tracking:**
- **CamelCamelCamel** - Historical Amazon prices
  - URL: `https://camelcamelcamel.com/product/{asin}`
  - Method: Playwright scraping (via Decodo proxy)
  - Implementation: `price_history.py`

- **Keepa** - Amazon price history API via web scraping
  - URL: `https://keepa.com/#!product/1-{asin}`
  - Method: Playwright intercepts XHR requests
  - Implementation: `price_history.py`

**Proxy Services:**
- **Decodo (ip.decodo.com)** - IP detection for proxy validation
  - Used to validate that requests come from Israel IP
  - Implementation: `admin.py` (key validation), `israel_scraper.py`

**International Shipping Verification:**
- **Amazon Global via Playwright** - Check Israel shipping eligibility
  - Method: Headless browser loads product page and parses shipping info
  - Uses proxy to spoof Israeli IP (optional WireGuard)
  - Implementation: `israel_scraper.py`
  - Related REST API endpoint: `api_server.py`

**URL Shortening:**
- **TinyURL** - Free shortening service (default fallback)
  - No authentication required
  - Used if `SHORTENER_ENABLED=false`

- **Custom self-hosted shortener** - If `SHORTENER_ENABLED=true`
  - Server: `shortener_server.py` (aiohttp web server)
  - Port: Configurable `SHORTENER_PORT` (default 8080)
  - Stores click analytics in SQLite
  - Redirects via domain proxy (e.g., nginx)

## Data Storage

**Databases:**
- **SQLite 3** (local file)
  - Location: `{DATA_DIR}/bot_data.db` (default `data/bot_data.db`)
  - Client: `aiosqlite` (async wrapper)
  - Tables (14 total):
    - `users` - User IDs, language preferences, platform tracking
    - `api_keys` - Admin-persisted API keys (DB priority over .env)
    - `settings` - Runtime settings (vision mode, search backend, thresholds)
    - `search_logs` - Search history, which affiliate tag was active
    - `affiliate_tags` - Admin-managed associate codes
    - `url_shortener_links` - Shortened URL mappings
    - `url_clicks` - Click analytics (IP hash, user agent, timestamp)
    - `price_history_cache` - CamelCamelCamel/Keepa results (6h TTL)
    - `backups` - Metadata for daily database backups
    - Plus 5 more for admin reports, model health, etc.

**File Storage:**
- **Local filesystem only** (no S3/cloud storage)
  - `{DATA_DIR}/bot_data.db` - Main SQLite database
  - `{DATA_DIR}/bot.log` - Application logs
  - `{DATA_DIR}/backups/` - Daily SQLite backups (auto-cleanup after 7 days by default)

**Caching:**
- **In-memory**: Vision provider health state (failure counts, cooldown timers)
- **Database**: Price history (6-hour TTL in SQLite)
- **HTTP client session**: Reused aiohttp.ClientSession for connection pooling

## Authentication & Identity

**Telegram:**
- Auth Provider: Telegram BotFather
- Token: `TELEGRAM_BOT_TOKEN` (bot API token)
- Admin access: `ADMIN_IDS` (comma-separated Telegram user IDs)
- Implementation: `bot.py` (PTB handlers), `config.py`

**Multi-Platform Adapters (optional, experimental):**
- **WhatsApp** (Meta Cloud API)
  - Token: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`
  - Verify token: `WHATSAPP_VERIFY_TOKEN`
  - Implementation: `adapters/whatsapp.py`

- **Instagram** (Meta Messenger API)
  - Token: `INSTAGRAM_TOKEN`, `INSTAGRAM_PAGE_ID`
  - Shared with: `META_APP_SECRET`, `META_VERIFY_TOKEN`
  - Implementation: `adapters/instagram.py`

- **Facebook Messenger**
  - Token: `MESSENGER_TOKEN`, `MESSENGER_PAGE_ID`
  - Verify token: `MESSENGER_VERIFY_TOKEN`
  - Implementation: `adapters/messenger.py`

- **Viber**
  - Token: `VIBER_TOKEN`, `VIBER_BOT_NAME`
  - Webhook URL: `VIBER_WEBHOOK_URL`
  - Implementation: `adapters/viber.py`

- **Discord**
  - Token: `DISCORD_TOKEN`
  - SDK: `discord.py`
  - Implementation: `adapters/discord.py` (if exists)

- **LINE**
  - Channel secret: `LINE_CHANNEL_SECRET`
  - Channel token: `LINE_CHANNEL_TOKEN`
  - SDK: `line-bot-sdk`
  - Implementation: `adapters/line.py`

**API Admin:**
- Secret key: `API_ADMIN_SECRET`
- Used for: REST API admin endpoints (`/v1/admin/*`)
- Location: `api_server.py`

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry, DataDog, etc.)

**Logs:**
- **stdout** - Console output (Docker logs captured here)
- **File** - `{DATA_DIR}/bot.log` (module-level loggers per file)
- **JSON file driver** - Docker compose uses `json-file` logging with rotation (5-10 MB, 2-3 files)

**Metrics:**
- **Prometheus metrics** - Optional via `/metrics` endpoint on shortener server
  - Enabled: `METRICS_ENABLED=true` (default)
  - Location: Exposed on shortener port

**Health Checks:**
- Docker healthcheck: `python -c "import sys; sys.exit(0)"` (basic)
- Shortener health: GET `/health` endpoint (plain-text)

## CI/CD & Deployment

**Hosting:**
- Docker (primary supported method)
- Bare metal Python (alternative, requires manual setup)
- Cloud deployment friendly (12-factor app — all config via .env)

**CI Pipeline:**
- None detected (no GitHub Actions, GitLab CI, etc.)
- Tests: pytest locally or in container via `docker compose up test-bot`

**Deployment Methods:**
- Docker: `docker build` + `docker compose up amazon-bot`
- Manual: `python main.py` (requires Python 3.11+, dependencies installed, .env present)

## Environment Configuration

**Required env vars (at minimum):**
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- At least one vision provider key: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `OPENROUTER_API_KEY`
- `SEARCH_BACKEND=auto` (default) or one search backend key: `RAPIDAPI_KEY`, `AMAZON_ACCESS_KEY/SECRET_KEY`

**Optional env vars:**
- `VISION_MODE` - How to use multiple providers (`best`, `cheapest`, `compare`, `single:<provider>/<model>`)
- `SHORTENER_ENABLED`, `SHORTENER_BASE_URL`, `SHORTENER_PORT` - Custom URL shortener
- `ADMIN_IDS` - Comma-separated admin Telegram IDs
- `REPORT_TIMEZONE`, `REPORT_HOUR` - Scheduled admin reports
- `BACKUP_ENABLED`, `BACKUP_DIR`, `BACKUP_KEEP_DAYS` - Database backups
- `DATA_DIR` - Database and logs location (default `data/`)
- `METRICS_ENABLED` - Prometheus metrics (default `true`)
- Rate limiting: `DEFAULT_RATE_LIMIT`, `DEFAULT_RATE_WINDOW`

**Secrets location:**
- File: `.env` (git-ignored, never committed)
- Database: SQLite (keys can be set via admin panel, persisted in DB)
- Environment: Passed to Docker via `env_file: .env` in docker-compose.yml

## Webhooks & Callbacks

**Incoming:**
- **Telegram** - Long polling via PTB (no webhook required, but webhook-capable)
- **Meta platforms** - Webhook endpoints:
  - `/webhook/whatsapp` - WhatsApp incoming messages
  - `/webhook/instagram` - Instagram direct messages
  - `/webhook/messenger` - Facebook Messenger
  - Base: `WEBHOOK_BASE_URL` (e.g., `https://yourdomain.com`) + listener port 8081
- **Viber** - Webhook: `VIBER_WEBHOOK_URL`
- **LINE** - Webhook integration via SDK
- **Discord** - Gateway connection (not webhook-based)

**Outgoing:**
- **Telegram** - Outgoing messages via sendMessage API
- **Meta platforms** - sendMessage API via graph.facebook.com
- **URL shortener clicks** - Logged to SQLite (no external webhook)
- **REST API responses** - FastAPI JSON responses for Israel shipping checks

---

*Integration audit: 2025-03-13*
