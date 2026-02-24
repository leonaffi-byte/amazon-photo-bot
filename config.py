"""
Central configuration — reads from .env file.

Settings priority order:
  1. Database (set via /admin → ⚙️ Settings) — live, no restart needed
  2. Environment variable / .env file         — fallback / bootstrap

API keys follow the same priority via key_store.py.
settings_store.py writes directly to the module attributes below when a
setting is changed in the admin panel, so all code reading config.X always
gets the latest value without restarting.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# Comma-separated Telegram user IDs that have admin access, e.g. "123456789,987654321"
# Get your ID by messaging @userinfobot on Telegram
ADMIN_IDS: set[int] = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

# ── AI Vision providers ────────────────────────────────────────────────────────
# Add keys for whichever providers you have access to.
# The bot automatically uses only the providers whose keys are present.
OPENAI_API_KEY: str | None    = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
GOOGLE_API_KEY: str | None    = os.getenv("GOOGLE_API_KEY")

# Vision mode — how to use multiple providers:
#   best      → run all providers in parallel, pick highest-quality result (default)
#   cheapest  → always use the cheapest available provider
#   compare   → run all providers and show a side-by-side comparison in the bot
#   single:openai/gpt-4o  → force a specific provider
# NOTE: overridden at runtime by settings_store / admin panel
VISION_MODE: str = os.getenv("VISION_MODE", "best")

# ── Search backend ────────────────────────────────────────────────────────────
# auto     → uses paapi if keys present, otherwise rapidapi
# paapi    → Amazon PA-API 5.0 (requires Associates account + 3 qualifying sales)
# rapidapi → RapidAPI "Real-Time Amazon Data" (easy, no sales requirement)
# NOTE: overridden at runtime by settings_store / admin panel
SEARCH_BACKEND: str = os.getenv("SEARCH_BACKEND", "auto")

# ── RapidAPI (recommended for new bots — no Amazon relationship needed) ────────
# Sign up free at https://rapidapi.com → search "Real-Time Amazon Data"
# Free tier: 100 searches/month. Paid from ~$9/month for 1,000 searches.
RAPIDAPI_KEY: str | None = os.getenv("RAPIDAPI_KEY")

# ── Amazon PA-API (optional — only needed if SEARCH_BACKEND=paapi or auto+present) ──
AMAZON_ACCESS_KEY: str | None    = os.getenv("AMAZON_ACCESS_KEY")
AMAZON_SECRET_KEY: str | None    = os.getenv("AMAZON_SECRET_KEY")
AMAZON_ASSOCIATE_TAG: str | None = os.getenv("AMAZON_ASSOCIATE_TAG")
# NOTE: overridden at runtime by settings_store / admin panel
AMAZON_MARKETPLACE: str          = os.getenv("AMAZON_MARKETPLACE", "www.amazon.com")

# ── Custom URL shortener ──────────────────────────────────────────────────────
# Set SHORTENER_BASE_URL to your domain to use your own shortener
# e.g. https://go.yourdomain.com
# Leave blank to use TinyURL (free, no setup) or bit.ly (if key is set)
# NOTE: overridden at runtime by settings_store / admin panel
SHORTENER_ENABLED:  bool       = os.getenv("SHORTENER_ENABLED", "false").lower() == "true"
SHORTENER_BASE_URL: str | None = os.getenv("SHORTENER_BASE_URL", "").strip() or None
SHORTENER_PORT:     int        = int(os.getenv("SHORTENER_PORT", "8080"))

# ── Bot behaviour ─────────────────────────────────────────────────────────────
# NOTE: all overridden at runtime by settings_store / admin panel
RESULTS_PER_PAGE: int          = int(os.getenv("RESULTS_PER_PAGE", "5"))
MAX_RESULTS: int               = int(os.getenv("MAX_RESULTS", "20"))
FREE_DELIVERY_THRESHOLD: float = float(os.getenv("FREE_DELIVERY_THRESHOLD", "49"))

# Show per-request cost info in the bot (useful during development)
SHOW_COST_INFO: bool = os.getenv("SHOW_COST_INFO", "true").lower() == "true"


async def apply_db_settings() -> None:
    """
    Load all DB-persisted settings and apply them to this module's attributes.
    Called once at bot startup (post_init) so DB values override .env from the start.
    """
    try:
        import settings_store
        for key in settings_store.SETTINGS_META:
            try:
                meta = settings_store.SETTINGS_META[key]
                raw = await settings_store.get_raw(key)
                # Only apply if there's a DB override (don't stomp .env unnecessarily)
                import database as _db
                db_raw = await _db.get_setting(key)
                if db_raw is not None:
                    settings_store._apply_to_config(key, db_raw, meta["type"])
            except Exception:
                pass
    except Exception:
        pass
