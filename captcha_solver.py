"""
captcha_solver.py — Amazon CAPTCHA solving via CapSolver API (capsolver.com).

Used by:
  - israel_scraper.py      (product-page shipping verification)
  - search_backends/playwright_backend.py  (search result scraping)

Setup:
  1. Register at capsolver.com → top up $1 (lasts thousands of solves)
  2. /admin → 🔑 API Keys → capsolver_api_key → paste your Client Key
  3. Done — both scrapers auto-detect the key and start solving CAPTCHAs

Pricing:
  ~$0.80 / 1 000 ImageToText solves  (Amazon uses simple distorted-text CAPTCHAs)
  $1 deposit → ~1 250 CAPTCHA solves
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_CAPSOLVER_BASE = "https://api.capsolver.com"
_POLL_INTERVAL  = 2     # seconds between status polls
_MAX_POLLS      = 30    # max 60 s wait


async def is_configured() -> bool:
    """Return True if a CapSolver API key is set in the admin panel."""
    import key_store
    key = await key_store.get("capsolver_api_key")
    return bool(key and key.strip())


async def solve_image_captcha(image_base64: str) -> str:
    """
    Submit a base64-encoded CAPTCHA image to CapSolver and return the solved text.

    Args:
        image_base64: Raw base64 string (no 'data:image/...' prefix).

    Returns:
        Solved text (e.g. "ABCD12").

    Raises:
        ValueError: API error or empty solution.
        TimeoutError: CapSolver did not respond within 60 s.
    """
    import key_store
    api_key = await key_store.get("capsolver_api_key")
    if not api_key:
        raise ValueError("CapSolver API key not set — add via /admin → 🔑 API Keys → capsolver_api_key")

    async with aiohttp.ClientSession() as session:
        # ── Create task ────────────────────────────────────────────────────────
        async with session.post(
            f"{_CAPSOLVER_BASE}/createTask",
            json={
                "clientKey": api_key,
                "task": {
                    "type": "ImageToTextTask",
                    "body": image_base64,
                    "case": False,   # case-insensitive — Amazon CAPTCHAs are
                },
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()

        if data.get("errorId", 1) != 0:
            raise ValueError(f"CapSolver createTask error: {data.get('errorDescription', 'unknown')}")

        task_id: Optional[str] = data.get("taskId")
        if not task_id:
            raise ValueError(f"CapSolver returned no taskId: {data}")

        logger.debug("CapSolver taskId=%s", task_id)

        # ── Poll for result ────────────────────────────────────────────────────
        for attempt in range(_MAX_POLLS):
            await asyncio.sleep(_POLL_INTERVAL)
            async with session.post(
                f"{_CAPSOLVER_BASE}/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                result = await resp.json()

            if result.get("errorId", 1) != 0:
                raise ValueError(f"CapSolver poll error: {result.get('errorDescription', 'unknown')}")

            if result.get("status") == "ready":
                text: str = result.get("solution", {}).get("text", "").strip()
                if not text:
                    raise ValueError("CapSolver returned empty solution text")
                logger.info("CapSolver solved CAPTCHA in %ds: '%s'", (attempt + 1) * _POLL_INTERVAL, text)
                return text

        raise TimeoutError(f"CapSolver did not solve within {_MAX_POLLS * _POLL_INTERVAL}s")


async def solve_playwright_captcha(page) -> bool:
    """
    High-level helper: detect an Amazon CAPTCHA on a Playwright page,
    solve it via CapSolver, fill in the answer, and submit.

    Returns True  if there was no CAPTCHA, or it was solved successfully.
    Returns False if CapSolver is not configured or solving failed.
    """
    # ── Detect ─────────────────────────────────────────────────────────────────
    captcha_form = await page.query_selector('form[action="/errors/validateCaptcha"]')
    if not captcha_form:
        return True   # No CAPTCHA on this page

    logger.info("Amazon CAPTCHA detected — solving via CapSolver")

    if not await is_configured():
        logger.warning("CapSolver not configured — cannot solve CAPTCHA. "
                       "Add key via /admin → 🔑 API Keys → capsolver_api_key")
        return False

    try:
        # ── Extract CAPTCHA image as base64 via in-page JS fetch ───────────────
        image_b64: Optional[str] = await page.evaluate("""
            async () => {
                const img = document.querySelector(
                    'form[action="/errors/validateCaptcha"] img'
                );
                if (!img) return null;
                try {
                    const resp  = await fetch(img.src);
                    const blob  = await resp.blob();
                    return await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onloadend = () => {
                            // Strip the "data:image/jpeg;base64," prefix
                            resolve(reader.result.split(',')[1]);
                        };
                        reader.onerror = reject;
                        reader.readAsDataURL(blob);
                    });
                } catch(e) {
                    return null;
                }
            }
        """)

        if not image_b64:
            logger.warning("Could not extract CAPTCHA image from page")
            return False

        # ── Solve ──────────────────────────────────────────────────────────────
        solution = await solve_image_captcha(image_b64)

        # ── Fill + submit ──────────────────────────────────────────────────────
        await page.fill('input[name="field-keywords"]', solution)
        await page.press('input[name="field-keywords"]', "Enter")
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)

        # ── Verify CAPTCHA is gone ─────────────────────────────────────────────
        still_captcha = await page.query_selector('form[action="/errors/validateCaptcha"]')
        if still_captcha:
            logger.warning("CAPTCHA still present after solve attempt (wrong answer?)")
            return False

        logger.info("CAPTCHA solved and dismissed ✓")
        return True

    except Exception as exc:
        logger.warning("CAPTCHA solving failed: %s", exc)
        return False
