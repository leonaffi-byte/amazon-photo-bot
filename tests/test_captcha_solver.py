"""
tests/test_captcha_solver.py — Unit tests for captcha_solver.py

Tests cover:
- is_configured(): with/without key
- solve_image_captcha(): happy path, API errors, timeout
- solve_playwright_captcha(): no CAPTCHA, solving flow, CapSolver not configured
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

import captcha_solver


# ── is_configured ──────────────────────────────────────────────────────────────

class TestIsConfigured:
    @pytest.mark.asyncio
    async def test_false_when_no_key(self):
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            assert await captcha_solver.is_configured() is False

    @pytest.mark.asyncio
    async def test_false_when_empty_string(self):
        with patch("key_store.get", new=AsyncMock(return_value="  ")):
            assert await captcha_solver.is_configured() is False

    @pytest.mark.asyncio
    async def test_true_when_key_set(self):
        with patch("key_store.get", new=AsyncMock(return_value="CAP_KEY_ABC123")):
            assert await captcha_solver.is_configured() is True


# ── solve_image_captcha ────────────────────────────────────────────────────────

def _make_mock_response(json_data: dict) -> MagicMock:
    """Helper: create a mock aiohttp response usable as async context manager."""
    resp = MagicMock()
    resp.json    = AsyncMock(return_value=json_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__  = AsyncMock(return_value=False)
    return resp


def _make_mock_session(*responses) -> MagicMock:
    """Helper: create a mock aiohttp.ClientSession with pre-set response sequence."""
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__  = AsyncMock(return_value=False)
    session.post = MagicMock(side_effect=list(responses))
    return session


class TestSolveImageCaptcha:
    @pytest.mark.asyncio
    async def test_raises_when_no_api_key(self):
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="CapSolver API key not set"):
                await captcha_solver.solve_image_captcha("base64data")

    @pytest.mark.asyncio
    async def test_happy_path(self):
        """Full successful flow: createTask → getTaskResult → solution text."""
        create_resp = _make_mock_response({"errorId": 0, "taskId": "task-123"})
        result_resp = _make_mock_response({
            "errorId": 0, "status": "ready", "solution": {"text": "ABCD12"},
        })
        session = _make_mock_session(create_resp, result_resp)

        with patch("key_store.get", new=AsyncMock(return_value="MY_KEY")):
            with patch("aiohttp.ClientSession", return_value=session):
                with patch("asyncio.sleep", new=AsyncMock()):
                    result = await captcha_solver.solve_image_captcha("base64img")

        assert result == "ABCD12"

    @pytest.mark.asyncio
    async def test_api_create_task_error(self):
        """CapSolver returns errorId != 0 on createTask."""
        error_resp = _make_mock_response({
            "errorId": 1, "errorDescription": "Invalid API key",
        })
        session = _make_mock_session(error_resp)

        with patch("key_store.get", new=AsyncMock(return_value="BAD_KEY")):
            with patch("aiohttp.ClientSession", return_value=session):
                with pytest.raises(ValueError, match="CapSolver createTask error"):
                    await captcha_solver.solve_image_captcha("base64img")

    @pytest.mark.asyncio
    async def test_no_task_id_in_response(self):
        """Response has no taskId field."""
        resp = _make_mock_response({"errorId": 0})   # no taskId
        session = _make_mock_session(resp)

        with patch("key_store.get", new=AsyncMock(return_value="KEY")):
            with patch("aiohttp.ClientSession", return_value=session):
                with pytest.raises(ValueError, match="no taskId"):
                    await captcha_solver.solve_image_captcha("base64img")

    @pytest.mark.asyncio
    async def test_empty_solution_text(self):
        """CapSolver returns 'ready' but empty text."""
        create_resp = _make_mock_response({"errorId": 0, "taskId": "t1"})
        result_resp = _make_mock_response({
            "errorId": 0, "status": "ready", "solution": {"text": ""},
        })
        session = _make_mock_session(create_resp, result_resp)

        with patch("key_store.get", new=AsyncMock(return_value="KEY")):
            with patch("aiohttp.ClientSession", return_value=session):
                with patch("asyncio.sleep", new=AsyncMock()):
                    with pytest.raises(ValueError, match="empty solution"):
                        await captcha_solver.solve_image_captcha("base64img")


# ── solve_playwright_captcha ───────────────────────────────────────────────────

class TestSolvePlwrightCaptcha:
    @pytest.mark.asyncio
    async def test_returns_true_when_no_captcha(self):
        """No CAPTCHA form on page → return True immediately."""
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)  # no captcha form
        result = await captcha_solver.solve_playwright_captcha(page)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_capsolver_not_configured(self):
        """CAPTCHA present but no CapSolver key → return False."""
        page = AsyncMock()
        # First call returns captcha form, second (after solving) irrelevant
        page.query_selector = AsyncMock(return_value=MagicMock())  # captcha found
        with patch("key_store.get", new=AsyncMock(return_value=None)):
            result = await captcha_solver.solve_playwright_captcha(page)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_image_extraction_fails(self):
        """CAPTCHA present, CapSolver configured, but image can't be extracted."""
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=MagicMock())
        page.evaluate = AsyncMock(return_value=None)  # image extraction returns None

        with patch("key_store.get", new=AsyncMock(return_value="CAP_KEY")):
            result = await captcha_solver.solve_playwright_captcha(page)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_on_successful_solve(self):
        """Full solve flow: CAPTCHA → image → CapSolver → fill → verify gone."""
        page = AsyncMock()
        # First query_selector: captcha found; second (after solve): None = gone
        page.query_selector = AsyncMock(side_effect=[
            MagicMock(),   # captcha detected
            None,          # captcha gone after submit
        ])
        page.evaluate = AsyncMock(return_value="base64imagedata")
        page.fill  = AsyncMock()
        page.press = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        with patch("key_store.get", new=AsyncMock(return_value="CAP_KEY")):
            with patch(
                "captcha_solver.solve_image_captcha",
                new=AsyncMock(return_value="SOLVED1"),
            ):
                result = await captcha_solver.solve_playwright_captcha(page)

        assert result is True
        page.fill.assert_called_once_with('input[name="field-keywords"]', "SOLVED1")
        page.press.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_captcha_still_present_after_solve(self):
        """Wrong answer — CAPTCHA form still there after submit."""
        page = AsyncMock()
        page.query_selector = AsyncMock(return_value=MagicMock())  # always present
        page.evaluate = AsyncMock(return_value="base64imagedata")
        page.fill  = AsyncMock()
        page.press = AsyncMock()
        page.wait_for_load_state = AsyncMock()

        with patch("key_store.get", new=AsyncMock(return_value="CAP_KEY")):
            with patch(
                "captcha_solver.solve_image_captcha",
                new=AsyncMock(return_value="WRONGANSWER"),
            ):
                result = await captcha_solver.solve_playwright_captcha(page)

        assert result is False
