"""
Tests for search backend timeout unification.

Covers:
  - SEARCH_TIMEOUT_SECONDS == 15 in search_backends/base.py
  - Each backend imports and uses SEARCH_TIMEOUT_SECONDS (no hardcoded values)
"""
from __future__ import annotations

import inspect
import re


def test_search_timeout_constant_is_15():
    """SEARCH_TIMEOUT_SECONDS must be exactly 15."""
    from search_backends.base import SEARCH_TIMEOUT_SECONDS
    assert SEARCH_TIMEOUT_SECONDS == 15


def test_paapi_backend_uses_search_timeout_constant():
    """paapi_backend must import and use SEARCH_TIMEOUT_SECONDS, not hardcode 15."""
    import search_backends.paapi_backend as mod
    source = inspect.getsource(mod)
    # Must import SEARCH_TIMEOUT_SECONDS
    assert "SEARCH_TIMEOUT_SECONDS" in source, (
        "paapi_backend must import SEARCH_TIMEOUT_SECONDS from search_backends.base"
    )
    # Must not have hardcoded numeric timeout value in ClientTimeout
    hardcoded = re.findall(r"ClientTimeout\(total=(\d+)\)", source)
    assert not hardcoded, (
        f"paapi_backend has hardcoded ClientTimeout values: {hardcoded}. "
        "Use SEARCH_TIMEOUT_SECONDS instead."
    )


def test_rapidapi_backend_uses_search_timeout_constant():
    """rapidapi_backend must import and use SEARCH_TIMEOUT_SECONDS, not hardcode 15."""
    import search_backends.rapidapi_backend as mod
    source = inspect.getsource(mod)
    assert "SEARCH_TIMEOUT_SECONDS" in source, (
        "rapidapi_backend must import SEARCH_TIMEOUT_SECONDS from search_backends.base"
    )
    hardcoded = re.findall(r"ClientTimeout\(total=(\d+)\)", source)
    assert not hardcoded, (
        f"rapidapi_backend has hardcoded ClientTimeout values: {hardcoded}. "
        "Use SEARCH_TIMEOUT_SECONDS instead."
    )


def test_dataforseo_backend_uses_search_timeout_constant():
    """dataforseo_backend must import and use SEARCH_TIMEOUT_SECONDS, not hardcode values."""
    import search_backends.dataforseo_backend as mod
    source = inspect.getsource(mod)
    assert "SEARCH_TIMEOUT_SECONDS" in source, (
        "dataforseo_backend must import SEARCH_TIMEOUT_SECONDS from search_backends.base"
    )
    hardcoded = re.findall(r"ClientTimeout\(total=(\d+)\)", source)
    assert not hardcoded, (
        f"dataforseo_backend has hardcoded ClientTimeout values: {hardcoded}. "
        "Use SEARCH_TIMEOUT_SECONDS instead."
    )


def test_playwright_backend_uses_search_timeout_constant():
    """playwright_backend must import and use SEARCH_TIMEOUT_SECONDS, not hardcode values."""
    import search_backends.playwright_backend as mod
    source = inspect.getsource(mod)
    assert "SEARCH_TIMEOUT_SECONDS" in source, (
        "playwright_backend must import SEARCH_TIMEOUT_SECONDS from search_backends.base"
    )
    # Should not have hardcoded ms timeout values like 30_000 or 10_000 in goto/wait calls
    hardcoded_ms = re.findall(r"timeout=(\d+_?\d+)", source)
    numeric_only = [v for v in hardcoded_ms if "_" not in v and int(v) > 100]
    assert not numeric_only, (
        f"playwright_backend has hardcoded numeric timeout values: {numeric_only}. "
        "Use SEARCH_TIMEOUT_SECONDS * 1000 instead."
    )


def test_brightdata_backend_uses_search_timeout_constant():
    """brightdata_backend must import and use SEARCH_TIMEOUT_SECONDS, not hardcode 30."""
    import search_backends.brightdata_backend as mod
    source = inspect.getsource(mod)
    assert "SEARCH_TIMEOUT_SECONDS" in source, (
        "brightdata_backend must import SEARCH_TIMEOUT_SECONDS from search_backends.base"
    )
    hardcoded = re.findall(r"ClientTimeout\(total=(\d+)\)", source)
    assert not hardcoded, (
        f"brightdata_backend has hardcoded ClientTimeout values: {hardcoded}. "
        "Use SEARCH_TIMEOUT_SECONDS instead."
    )
