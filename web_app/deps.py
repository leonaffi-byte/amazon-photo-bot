"""
web_app/deps.py — Shared FastAPI dependencies for the public web app.

Provides:
  - SlowAPI rate limiter keyed by remote IP address
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
