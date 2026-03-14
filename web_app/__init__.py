"""
web_app — Public web application for Amazon Photo Bot.

Provides a browser-accessible product search at / with:
  - Photo upload with drag-and-drop
  - Server-Sent Events (SSE) streaming for real-time 4-stage progress
  - AI-powered product identification via vision providers
  - Amazon search results with affiliate links
  - Result persistence in SQLite with 30-day expiry

The router is mounted in gateway.py BEFORE the shortener catch-all:
    from web_app import router as web_router
    app.include_router(web_router)
"""
from .router import router

__all__ = ["router"]
