"""Pure analysis helpers shared by the scraper, the employer index and the tests.

No network, no :mod:`requests` and no knowledge of where the files live: the
functions take dictionaries and sets, and return dictionaries and sets. That
keeps the interesting logic (state classification, hashes, dates, salaries)
testable on its own.
"""

from __future__ import annotations

from typing import Any

import json
import re
from datetime import date, datetime


# ============================================================
# IDENTIFICATION
# ============================================================

def clean_number(value):
    """Offer numbers are the primary key: normalize to a plain string."""
    if value is None:
        return ""
    return str(value).strip()


def norm_text(value):
    """Whitespace-normalized text used for comparisons."""
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


# ============================================================
# STATE CLASSIFICATION
# ============================================================

def offer_state_for(number, previous_numbers, deleted_numbers):
    """Classify a *current* offer: new / unchanged / reappeared."""
    if number in previous_numbers:
        return "unchanged"
    if number in deleted_numbers:
        return "reappeared"
    return "new"


def collect_states(previous_offers, current_offers, deleted_numbers):
    """Compute the full state picture of one scrape.

    Returns a dict mapping each state to the list of numbers in it.

    Since deleted offers are kept in the listing as tombstones, a number
    that is present again while its previous version was a tombstone is a
    reappearance, not an unchanged offer.
    """
    previous = {}
    tombstoned = set()
    for offer in previous_offers:
        if not isinstance(offer, dict) or not clean_number(offer.get("number")):
            continue
        number = clean_number(offer.get("number"))
        previous[number] = offer
        if offer.get("removed") is True:
            tombstoned.add(number)
    previous_numbers = set(previous)
    current_numbers = set()

    states: dict[str, list[Any]] = {
        "new": [],
        "unchanged": [],
        "reappeared": [],
        "deleted": [],
    }

    for offer in current_offers:
        if not isinstance(offer, dict):
            continue
        number = clean_number(offer.get("number"))
        if not number or number in current_numbers:
            continue
        current_numbers.add(number)
        if number in tombstoned:
            states["reappeared"].append(number)
            continue
        state = offer_state_for(number, previous_numbers, deleted_numbers)
        states[state].append(number)

    for number in sorted(previous_numbers - current_numbers):
        states["deleted"].append(number)

    return states, current_numbers


def tombstone_offers(previous_offers, current_offers, timestamp):
    """Return the offers that disappeared, kept in the listing.

    An offer present in the previous listing but not in the current one is
    not dropped: it is returned as a copy marked ``offer_state="deleted"``
    and ``removed_on=<timestamp>`` so the file keeps its whole content (and
    the raw detail payload can be kept too). The UI shows it as
    "Supprimée" instead of removing it.
    """
    current_numbers = {
        clean_number(o.get("number"))
        for o in current_offers
        if isinstance(o, dict)
    }
    tombstones = []
    seen = set()
    for offer in previous_offers:
        if not isinstance(offer, dict):
            continue
        number = clean_number(offer.get("number"))
        if not number or number in current_numbers or number in seen:
            continue
        seen.add(number)
        entry = dict(offer)
        entry["offer_state"] = "deleted"
        entry["is_new"] = False
        entry["removed"] = True
        if not entry.get("removed_on"):
            entry["removed_on"] = timestamp
        tombstones.append(entry)
    return tombstones


def summarize_scrape(states, total=None):
    """Compact counter dict used by the scrape history."""
    return {
        "nouvelles": len(states["new"]),
        "inchangees": len(states["unchanged"]),
        "reapparues": len(states["reappeared"]),
        "supprimees": len(states["deleted"]),
        "total_offres": total if total is not None
        else len(states["new"]) + len(states["unchanged"])
        + len(states["reappeared"]),
    }


# ============================================================
# SCRAPE HISTORY (per search)
# ============================================================

SCRAPES_MAX_ENTRIES = 500


def record_scrape(scrapes, entry):
    """Insert a scrape entry into the history list. A previous entry with the
    identical timestamp and search is replaced (re-run of the same scrape),
    and the list is capped to :data:`SCRAPES_MAX_ENTRIES` oldest-first.

    The list is mutated in place and also returned, so callers can use either
    form. It is a plain list on purpose: the storage layer reads and writes
    bare lists, and the browser expects ``meta.scrapes`` to be an array.
    """
    timestamp = entry.get("timestamp", "")
    search = entry.get("search", "")
    for index, current in enumerate(scrapes):
        if isinstance(current, dict) and (
            current.get("timestamp") == timestamp
            and current.get("search") == search
        ):
            scrapes[index] = entry
            break
    else:
        scrapes.append(entry)
    if len(scrapes) > SCRAPES_MAX_ENTRIES:
        del scrapes[: len(scrapes) - SCRAPES_MAX_ENTRIES]
    return scrapes


def merge_details(previous, fetched, keep):
    """Merge the raw detail payloads of two runs into one store.

    `previous` and `fetched` are dicts {number: payload}. Payloads are
    kept only for numbers in `keep` (current offers + deleted tombstone).
    A range freshly fetched this run always wins over the cached one.
    """
    merged = {}
    if isinstance(previous, dict):
        for number in keep:
            payload = previous.get(number)
            if payload is not None:
                merged[number] = payload
    if isinstance(fetched, dict):
        for number, payload in fetched.items():
            if payload is not None:
                merged[number] = payload
    return merged


# ============================================================
# DATES
# ============================================================

def parse_forem_date(value):
    """Normalize the various date formats found on the Forem API into
    YYYY-MM-DD (or "" when unknown).

    Accepts DD/MM/YYYY ("16/09/2026"), DD-MM-YY ("16-09-26") and ISO
    timestamps ("2026-09-16T00:00:00+02:00").
    """
    text = clean_number(value)
    if not text:
        return ""

    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", text)
    if match:
        day, month, year = match.groups()
        return "%s-%s-%s" % (year, month.zfill(2), day.zfill(2))

    match = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{2})$", text)
    if match:
        day, month, year = match.groups()
        return "20%s-%s-%s" % (year, month.zfill(2), day.zfill(2))

    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return "%s-%s-%s" % (year, month.zfill(2), day.zfill(2))

    return ""


def days_between(value, today=None):
    """Days between ISO date `value` and today (negative = in the past)."""
    iso = parse_forem_date(value)
    if not iso:
        return None
    try:
        target = date(*[int(part) for part in iso.split("-")])
    except ValueError:
        return None
    today = today or date.today()
    return (target - today).days


# ============================================================
# SALARY ANALYSIS
# ============================================================

HOURLY_HINT_PATTERN = re.compile(
    r"/\s*h\b|(?:^|\s)l[''´`]?\s*heure|par\s+heure|horaire", re.I
)
AMOUNT_PATTERN = re.compile(r"\d{1,3}(?:[.,]\d{1,2})?")


def has_salary(offer):
    """True when some remuneration info is present."""
    text = norm_text(offer.get("salary") or offer.get("pay"))
    return bool(text)


def extract_hourly_values(text):
    """Return numeric €/hour values when the text is clearly hourly.

    Returns [] when the text is not expressed per hour or cannot be
    parsed reliably (never invents or mixes €/month / €/year values).
    """
    if not text:
        return []
    lower = text.lower()
    if not HOURLY_HINT_PATTERN.search(lower):
        return []
    amounts = []
    for match in AMOUNT_PATTERN.finditer(text):
        raw = match.group().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            continue
        if 0 < value <= 200:
            amounts.append(value)
    return sorted(set(amounts))


def analyze_salaries(offers):
    """Aggregate remuneration facts across offers.

    Returns:
      {"renseignees": n, "total": n,
       "hourly": [values...]} with hourly values only when the offers
       are expressed per hour (comparable unit).
    """
    total = 0
    renseignees = 0
    hourly = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        total += 1
        if has_salary(offer):
            renseignees += 1
        hourly.extend(extract_hourly_values(offer.get("salary") or offer.get("pay")))
    return {
        "renseignees": renseignees,
        "total": total,
        "hourly": sorted(set(hourly)),
    }


def mean(values):
    if not values:
        return None
    return sum(values) / len(values)


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    size = len(ordered)
    if size % 2 == 1:
        return ordered[size // 2]
    return (ordered[size // 2 - 1] + ordered[size // 2]) / 2


# ============================================================
# BLACKLIST / MISS POLICY
# ============================================================

# Number of consecutive 404/miss outcomes before a number is skipped
# during the detail phase (it can still be recovered explicitly).
MISS_BLACKLIST_AFTER = 2


def read_blacklist_data(data):
    """Accept both the legacy list format and the current dict format.

    Legacy: ["1880409", ...]  ->  {"1880409": {"attempts": INF, ...}}
    Current: {"1880409": {"first_seen": ..., "last_seen": ..., "attempts": n}}
    """
    if isinstance(data, list):
        result = {}
        for number in data:
            number = clean_number(number)
            if number:
                result[number] = {"attempts": MISS_BLACKLIST_AFTER + 1}
        return result
    if isinstance(data, dict):
        result = {}
        for number, entry in data.items():
            number = clean_number(number)
            if not number:
                continue
            if isinstance(entry, dict):
                result[number] = {
                    "first_seen": clean_number(entry.get("first_seen")),
                    "last_seen": clean_number(entry.get("last_seen")),
                    "attempts": int(entry.get("attempts") or 0),
                }
            else:
                result[number] = {"attempts": MISS_BLACKLIST_AFTER + 1}
        return result
    return {}


def should_fetch(number, blacklist, force=False):
    """Whether the scraper should request the detail for this number.

    A single 404 never blocks the next attempt (temporary errors are
    retried). Only after MISS_BLACKLIST_AFTER consecutive misses is the
    number skipped; `force` (--refresh) bypasses it.
    """
    if force:
        return True
    entry = blacklist.get(number)
    if not entry:
        return True
    return int(entry.get("attempts") or 0) < MISS_BLACKLIST_AFTER


def note_miss(blacklist, number, timestamp):
    """Register a 404/HTTP error for a number (increments attempts)."""
    entry = blacklist.get(number) or {}
    if not entry.get("first_seen"):
        entry["first_seen"] = timestamp
    entry["last_seen"] = timestamp
    entry["attempts"] = int(entry.get("attempts") or 0) + 1
    blacklist[number] = entry


def note_recovery(blacklist, number):
    """A successful fetch removes the number from the blacklist."""
    blacklist.pop(number, None)


# ============================================================
# CONTENT HASH & CHANGE DETECTION
# ============================================================

import hashlib


def normalize_for_hash(value):
    """Normalize a value for deterministic hashing."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return [normalize_for_hash(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize_for_hash(v) for k, v in sorted(value.items())}
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    return value


def compute_hash(value):
    """SHA-256 of any JSON-able value, normalized so it is stable."""
    json_str = json.dumps(
        normalize_for_hash(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(json_str.encode("utf-8")).hexdigest()


# Rule used by compute_content_hash(). It is stored on every offer so an
# offer stored with an older rule is never reported as modified just because
# the hashing rules changed (see run_scrape's change detection).
CONTENT_HASH_RULE = 2

# Fields that are part of the Forem data (determine content changes).
# `published_on` is left out on purpose: the search result gives it as a
# relative text ("Publié hier") which changes with the passing days, while
# the absolute `date_publication` below already covers the publication date.
CONTENT_HASH_FIELDS = [
    "number",
    "offer_title",
    "description",
    "company",
    "email",
    "url",
    "contract_type",
    "schedule",
    "pay",
    "salary",
    "location",
    "date_publication",
    "date_fin_diffusion",
    "metier",
    "summary",
]


def compute_content_hash(offer):
    """Compute SHA-256 hash of the normalized offer content.

    Only hashes the Forem data fields, not user data or metadata.
    """
    normalized = {
        field: offer.get(field, "") for field in CONTENT_HASH_FIELDS
    }
    return compute_hash(normalized)


def compute_diff(old_offer, new_offer):
    """Compute diff between two offer versions.
    
    Returns a dict mapping field names to [old_value, new_value] pairs
    for fields that have changed.
    """
    # Fields to compare for diffs (all Forem data fields)
    compare_fields = [
        "number",
        "offer_title",
        "description",
        "company",
        "email",
        "url",
        "contract_type",
        "schedule",
        "pay",
        "salary",
        "location",
        "published_on",
        "date_publication",
        "date_fin_diffusion",
        "metier",
        "summary",
    ]
    
    diff = {}
    for field in compare_fields:
        old_val = old_offer.get(field, "")
        new_val = new_offer.get(field, "")
        if normalize_for_hash(old_val) != normalize_for_hash(new_val):
            diff[field] = [old_val, new_val]
    
    return diff


def get_current_timestamp():
    """Return current ISO timestamp."""
    return datetime.now().isoformat()


def now_iso_timestamp():
    """Return current ISO timestamp (alias for get_current_timestamp)."""
    return datetime.now().isoformat()


def is_offer_recent(offer, max_age_days=365):
    """Check if offer is within the last N days based on date_publication.
    
    Uses the absolute date_publication field (DD-MM-YY or DD/MM/YYYY) rather
    than the relative published_on text ("Publié hier") which cannot be parsed.
    Offers without a usable date are kept: an unknown date is not a reason to
    drop an offer that may still be active.
    """
    # Prefer the absolute date_publication field
    date_str = offer.get("date_publication", "")
    if not date_str:
        # Fall back to published_on if date_publication is missing
        date_str = offer.get("published_on", "")
    
    if not date_str:
        return True  # Unknown date, assume recent
    
    days = days_between(date_str)
    if days is None:
        return True  # Unparseable date, assume recent
    
    return days >= -max_age_days


def filter_recent_offers(offers, max_age_days=365):
    """Filter offers to only those within the last N days.
    
    Offers without a usable date are kept. This is a scope filter for
    scraping, not a deletion: offers already in SQLite are never removed
    by this function.
    """
    return [offer for offer in offers if is_offer_recent(offer, max_age_days)]


# ============================================================
# JOB/MÉTIER INDEXING
# ============================================================

def build_metier_index(offers):
    """Build an index mapping métiers to offer numbers."""
    index: dict[str, list[Any]] = {}
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        number = clean_number(offer.get("number"))
        if not number:
            continue
        metier = offer.get("metier", "").strip()
        if not metier:
            continue
        if metier not in index:
            index[metier] = []
        index[metier].append(number)
    return index


# ============================================================
# USER DATA SEPARATION
# ============================================================

USER_DATA_FIELDS = {
    "favorite", "status", "notes", "tags", "priority",
    "statut_dates", "remarques", "suivi"
}

def separate_user_data(offer):
    """Separate user data from Forem data in an offer.
    
    Returns (forem_data, user_data) tuple.
    """
    forem_data = {}
    user_data = {}
    
    for key, value in offer.items():
        if key in USER_DATA_FIELDS:
            user_data[key] = value
        else:
            forem_data[key] = value
    
    return forem_data, user_data


def merge_user_data(forem_data, user_data):
    """Merge user data back into Forem data."""
    result = dict(forem_data)
    result.update(user_data)
    return result


def preserve_user_data(old_offer, new_forem_data):
    """Update offer with new Forem data while preserving user data."""
    _, user_data = separate_user_data(old_offer)
    return merge_user_data(new_forem_data, user_data)