"""Storage factory and current instance."""
from __future__ import annotations

import os
from typing import Optional

from python import config
from python.storage.base import Storage
from python.storage.json_store import JsonStorage


_BACKEND: Optional[str] = None
_INSTANCE: Optional[Storage] = None


def get_storage() -> Storage:
    global _INSTANCE, _BACKEND
    if _INSTANCE is not None:
        return _INSTANCE
    backend = os.environ.get("LEFOREM_STORAGE", "sqlite").lower()
    _BACKEND = backend
    if backend == "sqlite":
        from python.storage.sqlite_store import SqliteStorage

        _INSTANCE = SqliteStorage()
        _migrate_json_if_needed(_INSTANCE)
    else:
        _INSTANCE = JsonStorage()
    return _INSTANCE


def _migrate_json_if_needed(storage: Storage) -> None:
    """Import the old JSON files the first time a database is opened.

    Switching the default to SQLite must not hide data already scraped:
    when no database exists yet, the JSON files of ``config.DATA_DIR`` are
    imported in one go. Runs once: after that the database file exists and
    this is a no-op.
    """
    db_path = getattr(storage, "db_path", None)
    if not db_path or os.path.exists(db_path):
        return
    data_dir = config.DATA_DIR
    if not os.path.isdir(data_dir):
        return
    if not any(name.endswith(".json") for name in os.listdir(data_dir)):
        return
    from python.migrate_to_sqlite import migrate_all

    migrate_all(data_dir, db_path)


def set_storage(storage: Storage) -> None:
    global _INSTANCE
    _INSTANCE = storage


def reset_storage() -> None:
    global _INSTANCE, _BACKEND
    _INSTANCE = None
    _BACKEND = None
