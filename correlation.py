"""
correlation.py — Request correlation IDs for end-to-end tracing.

Uses Python's contextvars so the ID automatically propagates through
async/await chains without explicit parameter threading.

A CorrelationFilter is provided for the logging subsystem — once
installed on the root logger every log line includes [correlation_id].
"""
from __future__ import annotations

import contextvars
import logging
import uuid

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def new_correlation_id() -> str:
    """Generate and set a new correlation ID (12-char hex)."""
    cid = uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str:
    """Get the current correlation ID (empty string if none set)."""
    return _correlation_id.get()


def set_correlation_id(cid: str) -> None:
    """Manually set the correlation ID (e.g. from an incoming header)."""
    _correlation_id.set(cid)


class CorrelationFilter(logging.Filter):
    """Inject ``correlation_id`` into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = get_correlation_id() or "-"  # type: ignore[attr-defined]
        return True
