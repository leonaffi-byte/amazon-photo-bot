"""
admin_dashboard — Web-based admin dashboard for Amazon Photo Bot.

Provides a browser-accessible admin interface at /admin with:
  - Telegram Login Widget + fallback token authentication
  - Dashboard home with HTMX-polled stat cards and provider health
  - API key management, tag management, settings editor
  - Server-sent sparkline charts for usage trends

The router is mounted in gateway.py:
    from admin_dashboard import router as admin_router
    app.include_router(admin_router, prefix="/admin")
"""
from .router import router

__all__ = ["router"]
