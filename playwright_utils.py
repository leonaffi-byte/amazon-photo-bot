"""
playwright_utils.py — Shared Playwright helpers.

Handles playwright-stealth v1/v2 API differences transparently.
"""
from __future__ import annotations

import logging


async def apply_stealth(page) -> None:
    """
    Apply playwright-stealth to a page, supporting both v1 and v2 APIs.
      v1: stealth_async(page)
      v2: Stealth().apply_stealth_async(page)
    Silently skips if playwright-stealth is not installed.
    """
    try:
        # v2 API (playwright-stealth >= 2.0)
        from playwright_stealth import Stealth
        await Stealth().apply_stealth_async(page)
        return
    except (ImportError, AttributeError):
        pass
    try:
        # v1 API (playwright-stealth < 2.0)
        from playwright_stealth import stealth_async
        await stealth_async(page)
    except (ImportError, AttributeError):
        logging.getLogger(__name__).warning(
            "playwright-stealth not installed — stealth mode unavailable. "
            "Install with: pip install playwright-stealth"
        )
