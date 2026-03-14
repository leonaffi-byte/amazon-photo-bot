"""
admin_dashboard/auth.py — Authentication for the web admin dashboard.

Two authentication modes:
  1. Telegram Login Widget (HMAC-SHA256, bot token as secret)
  2. Fallback token (generated at startup, valid for 24h)

The fallback token is printed to logs on startup so admins can access
the dashboard without setting up the Telegram Login Widget.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

# ── Fallback token state ───────────────────────────────────────────────────────

_fallback_token: str | None = None
_token_issued_at: float = 0


# ── Telegram Login Widget HMAC verification ───────────────────────────────────

def verify_telegram_login(data: dict, bot_token: str) -> bool:
    """
    Verify a Telegram Login Widget callback against the bot token.

    The data dict contains fields like: id, first_name, auth_date, hash.
    This function:
      1. Makes a copy of data (does not mutate original)
      2. Pops the 'hash' field from the copy
      3. Checks that auth_date is within the last 24 hours
      4. Builds the check string: sorted key=value pairs joined by newlines
      5. Computes HMAC-SHA256 with secret = SHA256(bot_token)
      6. Returns True only if hash matches and data is fresh

    Args:
        data: Dict of query params from the Telegram Login Widget callback.
        bot_token: The bot's Telegram token (used to derive HMAC secret).

    Returns:
        True if valid and fresh, False otherwise.
    """
    data_copy = dict(data)
    received_hash = data_copy.pop("hash", None)
    if not received_hash:
        return False

    # Check freshness (auth_date must be within 24h)
    try:
        auth_date = int(data_copy.get("auth_date", 0))
    except (ValueError, TypeError):
        return False

    if time.time() - auth_date > 86400:
        return False

    # Build check string: sorted key=value\n...
    check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(data_copy.items())
    )

    # Derive secret key from bot token
    secret_key = hashlib.sha256(bot_token.encode()).digest()

    # Compute expected HMAC
    expected = hmac.new(
        secret_key,
        check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, received_hash)


# ── Fallback token management ─────────────────────────────────────────────────

def generate_fallback_token() -> str:
    """
    Generate a new one-time fallback admin token.

    The token is stored in module-level state and is valid for 24 hours.
    Call this at bot startup and log the result so admins can use it.

    Returns:
        A URL-safe random token string.
    """
    global _fallback_token, _token_issued_at
    _fallback_token = secrets.token_urlsafe(32)
    _token_issued_at = time.time()
    return _fallback_token


def verify_fallback_token(token: str) -> bool:
    """
    Verify a fallback admin token.

    Args:
        token: Token string submitted via the login form.

    Returns:
        True if the token matches the current token and is less than 24h old.
        False if no token has been generated, token is wrong, or it has expired.
    """
    if _fallback_token is None:
        return False

    if time.time() - _token_issued_at > 86400:
        return False

    return hmac.compare_digest(token, _fallback_token)
