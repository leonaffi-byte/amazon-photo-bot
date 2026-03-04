"""
db_backup.py — Automated SQLite database backups.

Provides:
  - backup_database()   — create a timestamped copy via SQLite .backup()
  - cleanup_old_backups() — remove backups older than keep_days
  - list_backups()       — enumerate existing backups with metadata

The backup uses SQLite's own .backup() method, which is safe to call on a live
database (it acquires a read lock internally, so concurrent writes are fine).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


def _default_backup_dir() -> Path:
    """Return the configured or default backup directory."""
    import config
    backup_dir = getattr(config, "BACKUP_DIR", None)
    if backup_dir:
        return Path(backup_dir)
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    return data_dir / "backups"


async def backup_database(backup_dir: str | None = None) -> str:
    """
    Create a timestamped backup of the bot database using SQLite .backup().

    Args:
        backup_dir: Directory to store the backup. Defaults to {DATA_DIR}/backups/.

    Returns:
        Absolute path to the newly created backup file.

    Raises:
        OSError: If the backup directory cannot be created.
        Exception: If the SQLite backup operation fails.
    """
    import database as db

    dest = Path(backup_dir) if backup_dir else _default_backup_dir()
    dest.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    filename = f"bot_data_{now.strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = dest / filename

    logger.info("Starting database backup to %s", backup_path)

    async with aiosqlite.connect(db.DB_PATH) as source:
        async with aiosqlite.connect(str(backup_path)) as target:
            await source.backup(target)

    size = backup_path.stat().st_size
    logger.info("Backup complete: %s (%s bytes)", backup_path, f"{size:,}")

    return str(backup_path.resolve())


async def cleanup_old_backups(backup_dir: str | None = None, keep_days: int = 7) -> int:
    """
    Delete backup files older than *keep_days* days.

    Args:
        backup_dir: Directory containing backups.
        keep_days: Number of days to retain backups.

    Returns:
        Number of files deleted.
    """
    dest = Path(backup_dir) if backup_dir else _default_backup_dir()
    if not dest.exists():
        return 0

    cutoff = datetime.now(timezone.utc).timestamp() - (keep_days * 86400)
    deleted = 0

    for f in dest.iterdir():
        if f.is_file() and f.name.startswith("bot_data_") and f.suffix == ".db":
            if f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    deleted += 1
                    logger.debug("Deleted old backup: %s", f.name)
                except OSError as exc:
                    logger.warning("Failed to delete backup %s: %s", f.name, exc)

    if deleted:
        logger.info("Cleaned up %d old backup(s) (keep_days=%d)", deleted, keep_days)

    return deleted


async def list_backups(backup_dir: str | None = None) -> list[dict]:
    """
    Return a list of available backup files, sorted newest-first.

    Each entry is a dict with keys:
      - filename: str
      - path: str (absolute)
      - size: int (bytes)
      - date: str (ISO 8601 UTC)
    """
    dest = Path(backup_dir) if backup_dir else _default_backup_dir()
    if not dest.exists():
        return []

    backups = []
    for f in dest.iterdir():
        if f.is_file() and f.name.startswith("bot_data_") and f.suffix == ".db":
            stat = f.stat()
            backups.append({
                "filename": f.name,
                "path": str(f.resolve()),
                "size": stat.st_size,
                "date": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })

    backups.sort(key=lambda b: b["date"], reverse=True)
    return backups
