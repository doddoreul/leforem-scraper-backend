"""Where the application reads from and writes to.

Everything that builds a path lives here, so the layout of ``data/`` is
described in a single place.

The data directory is a module attribute on purpose: the tests point it at
a temporary directory, and the command line accepts ``--data-dir``. Resolve
it through :func:`data_dir` rather than caching it at import time.
"""

from __future__ import annotations

import os
import re

# Repository root, i.e. the folder holding the entry-point scripts.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Folder holding every generated file.
DATA_DIR = os.path.join(BASE_DIR, "data")

# Files shared by all searches.
SCRAPES_FILE_NAME = "historique_scrapes.json"
BLACKLIST_FILE_NAME = "blacklist.json"
COMPANIES_FILE_NAME = "companies.json"
MODIFICATIONS_FILE_NAME = "historique_modifications.json"
SCRAPE_STATE_FILE_NAME = "scrape_state.json"

# Format version of the scraper history files.
VERSION = 1


def data_dir() -> str:
    """The current data directory (read at call time, not cached)."""
    return DATA_DIR


def trash_dir() -> str:
    """Deleted searches are moved aside instead of being removed."""
    return os.path.join(data_dir(), "trash")


# The export read by the frontend. It cannot live under data/, which is
# ignored by git, because GitHub Pages has to serve it from the repository.
# Tests point it somewhere else through raw_json_path().
RAW_JSON_FILE_NAME = "raw.json"

# None means "next to the project". The browser tests set it to a temporary
# directory so the export they produce is the one the page reads.
_RAW_JSON_PATH = None


def raw_json_file() -> str:
    if _RAW_JSON_PATH:
        return _RAW_JSON_PATH
    return os.path.join(BASE_DIR, RAW_JSON_FILE_NAME)


def set_raw_json_path(path: str | None) -> None:
    global _RAW_JSON_PATH
    _RAW_JSON_PATH = path


def shared_file(name: str) -> str:
    """A file shared by every search, e.g. the blacklist."""
    return os.path.join(data_dir(), name)


def scrapes_file() -> str:
    return shared_file(SCRAPES_FILE_NAME)


def blacklist_file() -> str:
    return shared_file(BLACKLIST_FILE_NAME)


def companies_file() -> str:
    return shared_file(COMPANIES_FILE_NAME)


def modifications_file() -> str:
    return shared_file(MODIFICATIONS_FILE_NAME)


def scrape_state_file() -> str:
    return shared_file(SCRAPE_STATE_FILE_NAME)


# One search owns exactly three files, named after it: the offers
# (data_liege.json), the offers that disappeared over time
# (historique_liege.json) and the cached detail payloads
# (details_liege.json). The separator is always there, so the default
# search is data_.json.
DATA_PREFIX = "data_"
HISTORY_PREFIX = "historique_"
DETAILS_PREFIX = "details_"
JSON_SUFFIX = ".json"

# What a search name may contain, and therefore what a generated file name
# may look like. Kept here so the server never has to repeat the pattern.
SEARCH_NAME = r"[A-Za-z0-9_-]+"

# The per-search files the page may read: data_<search>.json (the offers) and
# details_<search>.json (their cached detail payloads).
SCRAPE_FILE_RE = re.compile(
    r"\A(?:" + DATA_PREFIX + "|" + DETAILS_PREFIX + ")" + SEARCH_NAME + JSON_SUFFIX + r"\Z"
)

# The per-search history, which also covers the two shared files of the same
# shape: historique_scrapes.json and historique_modifications.json.
HISTORY_NAME_RE = re.compile(
    r"\A" + HISTORY_PREFIX + SEARCH_NAME + JSON_SUFFIX + r"\Z"
)


def data_file_name(base_name: str) -> str:
    """File name of the offers of one search."""
    return f"{DATA_PREFIX}{base_name}{JSON_SUFFIX}"


def history_file_name(base_name: str) -> str:
    """File name of the offers of one search that have disappeared."""
    return f"{HISTORY_PREFIX}{base_name}{JSON_SUFFIX}"


def details_file_name(base_name: str) -> str:
    """File name of the cached detail payloads of one search."""
    return f"{DETAILS_PREFIX}{base_name}{JSON_SUFFIX}"


def scrape_base(file_name: str) -> str | None:
    """The search name a data file belongs to.

    ``data_liege.json`` is ``liege``. Returns ``None`` for anything that is
    not one of our own files, so callers can simply skip it.
    """
    if not (file_name.startswith(DATA_PREFIX)
            and file_name.endswith(JSON_SUFFIX)):
        return None
    return file_name[len(DATA_PREFIX):-len(JSON_SUFFIX)]


def history_base(file_name: str) -> str | None:
    """The search name a history file belongs to.

    ``historique_liege.json`` is ``liege``. Returns ``None`` for anything
    that is not one of our own files.
    """
    if not (file_name.startswith(HISTORY_PREFIX)
            and file_name.endswith(JSON_SUFFIX)):
        return None
    return file_name[len(HISTORY_PREFIX):-len(JSON_SUFFIX)]


def valid_search_name(name: str) -> bool:
    """True if `name` is safe to use as a file name and in a URL.

    The characters are restricted precisely so the name cannot escape the
    data folder; the server relies on this instead of sanitising by hand.
    """
    return bool(SCRAPE_FILE_RE.match(data_file_name(name)))


def data_file(base_name: str) -> str:
    """The offers of one search, the file the page actually reads."""
    return os.path.join(data_dir(), data_file_name(base_name))


def history_file(base_name: str) -> str:
    """The offers of one search that have disappeared over time."""
    return os.path.join(data_dir(), history_file_name(base_name))


def details_file(base_name: str) -> str:
    """Cached detail payloads of one search, used for the extra fields."""
    return os.path.join(data_dir(), details_file_name(base_name))


def scrape_files(base_name: str) -> tuple[str, str]:
    """(offers, history) paths of one search."""
    return data_file(base_name), history_file(base_name)


def delete_scrape_files(base_name: str) -> tuple[str, str, str]:
    """(data, history, details) paths of one search, in that order."""
    return (
        data_file(base_name),
        history_file(base_name),
        details_file(base_name),
    )