"""
Tests for db_backup.py — database backup, cleanup, and listing.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def db_with_data(tmp_data_dir):
    """Initialise the database so there is a real .db file to back up."""
    import database as db
    await db.init_db()
    return tmp_data_dir


@pytest.mark.asyncio
async def test_backup_creates_file(db_with_data):
    """backup_database() should create a timestamped .db file."""
    backup_dir = str(db_with_data / "backups")

    from db_backup import backup_database
    path = await backup_database(backup_dir=backup_dir)

    assert os.path.isfile(path)
    assert path.endswith(".db")
    assert "bot_data_" in os.path.basename(path)
    assert os.path.getsize(path) > 0


@pytest.mark.asyncio
async def test_backup_custom_dir(db_with_data):
    """backup_database() should accept a custom backup directory."""
    custom_dir = str(db_with_data / "my_backups")

    from db_backup import backup_database
    path = await backup_database(backup_dir=custom_dir)

    assert os.path.isfile(path)
    assert "my_backups" in path


@pytest.mark.asyncio
async def test_list_backups_empty(db_with_data):
    """list_backups() should return an empty list when no backups exist."""
    empty_dir = str(db_with_data / "empty_backups")

    from db_backup import list_backups
    result = await list_backups(backup_dir=empty_dir)
    assert result == []


@pytest.mark.asyncio
async def test_list_backups_after_backup(db_with_data):
    """list_backups() should return the backup we just created."""
    backup_dir = str(db_with_data / "backups")

    from db_backup import backup_database, list_backups
    await backup_database(backup_dir=backup_dir)
    result = await list_backups(backup_dir=backup_dir)

    assert len(result) == 1
    assert result[0]["filename"].startswith("bot_data_")
    assert result[0]["size"] > 0
    assert "date" in result[0]
    assert "path" in result[0]


@pytest.mark.asyncio
async def test_list_backups_sorted_newest_first(db_with_data):
    """Multiple backups should be listed newest-first."""
    import asyncio
    backup_dir = str(db_with_data / "backups")

    from db_backup import backup_database, list_backups

    await backup_database(backup_dir=backup_dir)
    await asyncio.sleep(1.1)   # ensure distinct timestamp
    await backup_database(backup_dir=backup_dir)

    result = await list_backups(backup_dir=backup_dir)
    assert len(result) == 2
    # Newest first
    assert result[0]["date"] >= result[1]["date"]


@pytest.mark.asyncio
async def test_cleanup_removes_old_backups(db_with_data):
    """cleanup_old_backups() should delete files older than keep_days."""
    backup_dir = str(db_with_data / "backups")

    from db_backup import backup_database, cleanup_old_backups, list_backups

    path = await backup_database(backup_dir=backup_dir)

    # Artificially age the file (set mtime to 10 days ago)
    old_time = time.time() - (10 * 86400)
    os.utime(path, (old_time, old_time))

    deleted = await cleanup_old_backups(backup_dir=backup_dir, keep_days=7)
    assert deleted == 1

    remaining = await list_backups(backup_dir=backup_dir)
    assert len(remaining) == 0


@pytest.mark.asyncio
async def test_cleanup_keeps_recent_backups(db_with_data):
    """cleanup_old_backups() should NOT delete recent files."""
    backup_dir = str(db_with_data / "backups")

    from db_backup import backup_database, cleanup_old_backups, list_backups

    await backup_database(backup_dir=backup_dir)

    deleted = await cleanup_old_backups(backup_dir=backup_dir, keep_days=7)
    assert deleted == 0

    remaining = await list_backups(backup_dir=backup_dir)
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_cleanup_nonexistent_dir(db_with_data):
    """cleanup_old_backups() should return 0 for a non-existent directory."""
    from db_backup import cleanup_old_backups
    deleted = await cleanup_old_backups(backup_dir=str(db_with_data / "nope"))
    assert deleted == 0


@pytest.mark.asyncio
async def test_backup_database_is_valid_sqlite(db_with_data):
    """The backup file should be a valid SQLite database."""
    import aiosqlite
    backup_dir = str(db_with_data / "backups")

    from db_backup import backup_database
    path = await backup_database(backup_dir=backup_dir)

    # Verify we can open it and query a table
    async with aiosqlite.connect(path) as conn:
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = await cursor.fetchall()
        table_names = [row[0] for row in tables]
        # The backup should contain the same tables as the source
        assert len(table_names) > 0


@pytest.mark.asyncio
async def test_default_backup_dir_uses_data_dir(db_with_data, monkeypatch):
    """_default_backup_dir() should fall back to DATA_DIR/backups."""
    from db_backup import _default_backup_dir

    # Mock config to avoid needing TELEGRAM_BOT_TOKEN
    import types
    fake_config = types.ModuleType("config")
    fake_config.BACKUP_DIR = str(db_with_data / "configured_backups")
    monkeypatch.setitem(__import__("sys").modules, "config", fake_config)

    result = _default_backup_dir()
    assert str(result) == str(db_with_data / "configured_backups")
