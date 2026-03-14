"""
admin_dashboard/deps.py — FastAPI dependencies for the admin dashboard.

Provides require_admin() dependency that validates session cookies
and redirects unauthenticated requests to the login page.
"""
from __future__ import annotations

from fastapi import HTTPException, Request


async def require_admin(request: Request) -> int:
    """
    FastAPI dependency that enforces admin authentication.

    Reads the 'admin_user_id' key from the session cookie.
    If missing, raises HTTPException with 307 redirect to /admin/login.

    Args:
        request: The incoming FastAPI request (has .session from SessionMiddleware).

    Returns:
        The admin user ID as an integer (0 for fallback token logins).

    Raises:
        HTTPException(307): If no valid session cookie is present.
    """
    user_id = request.session.get("admin_user_id")
    if user_id is None:
        raise HTTPException(
            status_code=307,
            headers={"Location": "/admin/login"},
        )
    try:
        return int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=307,
            headers={"Location": "/admin/login"},
        )
