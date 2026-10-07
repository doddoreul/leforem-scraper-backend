"""Reading and writing the JSON files of ``data/``.

Every generated file goes through :func:`write_json_atomically`, so an
interrupted run never leaves a half-written file behind: the content is
written next to the target, flushed to disk, then moved into place.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any


def now_iso_timestamp() -> str:
    """Local time as ``YYYY-MM-DDTHH:MM:SS+HH:MM``."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: str, default: Any = None) -> Any:
    """Read a JSON file, falling back to ``default``.

    A missing file, an unreadable one and invalid JSON all give ``default``:
    a corrupted cache must never stop the scraper.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
    if default is not None and not isinstance(data, dict):
        return default
    return data


def write_json_atomically(path: str, content: Any) -> None:
    """Write ``content`` as JSON, replacing ``path`` in one step."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(content, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def read_details(path: str) -> dict[str, Any]:
    """The cached detail payloads of one search, keyed by offer number."""
    data = read_json(path, None)
    if isinstance(data, dict) and isinstance(data.get("details"), dict):
        return data.get("details") or {}
    return {}