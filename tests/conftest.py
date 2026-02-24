"""
Shared pytest fixtures.

Every test that touches the database or config gets a clean
temporary DATA_DIR via the `tmp_data_dir` fixture so tests
are fully isolated from each other and from the real bot_data.db.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# ── Make the project root importable without installing the package ────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Redirect DATA_DIR to a fresh tmp directory for every test.
    This gives each test a clean SQLite file and prevents cross-test pollution.
    """
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data))

    # Patch the module-level DB_PATH that was already computed at import time
    import database
    monkeypatch.setattr(database, "DB_PATH", str(data / "bot_data.db"))
    monkeypatch.setattr(database, "_DATA_DIR", data)

    # Also reset the internal lock so tests don't share state
    import asyncio
    monkeypatch.setattr(database, "_lock", asyncio.Lock())

    yield data
