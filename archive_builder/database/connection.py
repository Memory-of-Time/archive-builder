from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from ..constants import DATABASE_NAME
from .schema import SCHEMA_VERSION, initialize_schema


def _existing_schema_version(path: Path) -> int | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        connection = sqlite3.connect(path)
        try:
            row = connection.execute("SELECT version FROM schema_info LIMIT 1").fetchone()
            return int(row[0]) if row else None
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def _backup_before_migration(path: Path, version: int) -> Path:
    backup = path.with_name(f"{path.stem}.schema{version}.backup{path.suffix}")
    if backup.exists():
        return backup
    source = sqlite3.connect(path)
    target = sqlite3.connect(backup)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return backup


def recover_abandoned_state(database: sqlite3.Connection) -> None:
    """Recover states left by a prior terminated writer.

    This is deliberately explicit. Merely opening the database, including from an
    observer or second process, must never rewrite a live operation's state.
    """
    database.execute("UPDATE pages SET download_state='pending' WHERE download_state='downloading'")
    database.execute("UPDATE pages SET extraction_state='pending' WHERE extraction_state='extracting'")
    database.execute(
        "UPDATE operation_runs SET status='interrupted',completed_at=datetime('now'),updated_at=datetime('now') "
        "WHERE status='running'"
    )
    database.commit()


def open_database(
    root: Path,
    *,
    read_only: bool = False,
    recover_abandoned: bool = False,
) -> sqlite3.Connection:
    root = Path(root)
    path = root / DATABASE_NAME
    if read_only:
        if not path.exists():
            raise FileNotFoundError(path)
        database = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=60)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA busy_timeout=60000")
        return database

    root.mkdir(parents=True, exist_ok=True)
    existing_version = _existing_schema_version(path)
    if existing_version is not None and existing_version < SCHEMA_VERSION:
        _backup_before_migration(path, existing_version)

    database = sqlite3.connect(path, timeout=60)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("PRAGMA synchronous=NORMAL")
    database.execute("PRAGMA foreign_keys=ON")
    database.execute("PRAGMA temp_store=MEMORY")
    database.execute("PRAGMA cache_size=-32768")
    database.execute("PRAGMA busy_timeout=60000")
    initialize_schema(database)
    if recover_abandoned:
        recover_abandoned_state(database)
    return database
