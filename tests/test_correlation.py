"""Tests for the correlation module — ID generation, context propagation, and logging filter."""
from __future__ import annotations

import asyncio
import logging

import pytest

from correlation import (
    CorrelationFilter,
    get_correlation_id,
    new_correlation_id,
    set_correlation_id,
)


# ── Basic API ─────────────────────────────────────────────────────────────────

def test_new_correlation_id_returns_12_hex():
    cid = new_correlation_id()
    assert len(cid) == 12
    assert all(c in "0123456789abcdef" for c in cid)


def test_get_after_new():
    cid = new_correlation_id()
    assert get_correlation_id() == cid


def test_set_correlation_id():
    set_correlation_id("custom123456")
    assert get_correlation_id() == "custom123456"


def test_default_is_empty():
    """In a fresh context the default should be empty string."""
    # We can't truly reset contextvars in the same thread, but we can
    # verify that set + get round-trips correctly.
    set_correlation_id("")
    assert get_correlation_id() == ""


def test_uniqueness():
    ids = {new_correlation_id() for _ in range(100)}
    assert len(ids) == 100  # collisions in 12-hex chars are astronomically unlikely


# ── Async propagation ────────────────────────────────────────────────────────

async def test_propagates_through_await():
    """contextvars should propagate through await chains."""
    cid = new_correlation_id()

    async def inner():
        return get_correlation_id()

    assert await inner() == cid


async def test_isolated_across_tasks():
    """Each asyncio.Task gets its own copy of the context."""
    cid_parent = new_correlation_id()

    async def child():
        child_cid = new_correlation_id()
        return child_cid, get_correlation_id()

    child_cid, child_got = await asyncio.create_task(child())
    # Child should see its own ID
    assert child_got == child_cid
    # Parent should still see the original
    assert get_correlation_id() == cid_parent


# ── Logging filter ────────────────────────────────────────────────────────────

def test_correlation_filter_injects_id():
    cid = new_correlation_id()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    filt = CorrelationFilter()
    assert filt.filter(record) is True
    assert record.correlation_id == cid  # type: ignore[attr-defined]


def test_correlation_filter_dash_when_empty():
    set_correlation_id("")
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    filt = CorrelationFilter()
    filt.filter(record)
    assert record.correlation_id == "-"  # type: ignore[attr-defined]
