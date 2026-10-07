"""Migrate JSON files to SQLite storage."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

from python import config
from python.jsonio import read_json, read_details
from python.storage.sqlite_store import SqliteStorage

logger = logging.getLogger(__name__)


def _load_json_safe(path: str, default=None):
    return read_json(path, default)


def migrate_all(data_dir: Optional[str] = None, db_path: Optional[str] = None) -> SqliteStorage:
    if data_dir is None:
        data_dir = config.DATA_DIR
    if db_path is None:
        db_path = os.path.join(data_dir, "leforem.db")

    store = SqliteStorage(db_path)
    data_dir_p = Path(data_dir)
    if not data_dir_p.is_dir():
        return store

    for file_name in sorted(data_dir_p.iterdir()):
        if not file_name.is_file():
            continue
        name = file_name.name

        if name.startswith("data_") and name.endswith(".json"):
            base = name[5:-5]
            payload = _load_json_safe(str(file_name), None)
            if isinstance(payload, dict):
                store.write_scraping(base, payload)

        elif name.startswith("details_") and name.endswith(".json"):
            base = name[8:-5]
            details = read_details(str(file_name))
            if details:
                store.write_details(base, details)

        elif name == config.SCRAPES_FILE_NAME:
            data = _load_json_safe(str(file_name), None)
            if isinstance(data, dict) and isinstance(data.get("scrapes"), list):
                store.write_history_scrapes(data["scrapes"])

        elif name == config.MODIFICATIONS_FILE_NAME:
            data = _load_json_safe(str(file_name), None)
            if isinstance(data, dict):
                store.write_history_modifications(data)

        elif name == config.COMPANIES_FILE_NAME:
            data = _load_json_safe(str(file_name), None)
            if isinstance(data, dict):
                store.write_companies(data)

        elif name == config.BLACKLIST_FILE_NAME:
            data = _load_json_safe(str(file_name), None)
            if isinstance(data, dict):
                store.write_blacklist(data)

        elif name == "scrape_state.json":
            data = _load_json_safe(str(file_name), None)
            if isinstance(data, dict):
                store.write_scrape_state(data)

        elif name.startswith("historique_") and name.endswith(".json") and name not in (
            config.SCRAPES_FILE_NAME,
            config.MODIFICATIONS_FILE_NAME,
        ):
            base = name[11:-5]
            data = _load_json_safe(str(file_name), None)
            if isinstance(data, dict):
                store.write_history_offers(base, data)

    return store


def main() -> None:
    migrate_all()
    logger.info("Migration terminée.")


if __name__ == "__main__":
    main()
