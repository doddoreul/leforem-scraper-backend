"""Forem (leforem.be) offer scraper.

Collects the offers of a search (occupation + location), enriches each one
with its detail page, then keeps the offers that disappeared over time in a
history file. Everything it needs on disk comes from
:mod:`python.config`, and the pure parsing/merging logic lives in
:mod:`python.core` so it can be tested without the network.

Run it from the repository root::

    python scraper.py --occupation-guid GUID --location-guid GUID
"""

from __future__ import annotations

from typing import Any

import argparse
import concurrent.futures
import json
import logging
import os
import random
import re
import sys
import threading
import time
from html.parser import HTMLParser

import requests

from python import config
from python import core
from python.jsonio import (
    now_iso_timestamp,
    read_details,
    read_json,
    write_json_atomically,
)

logger = logging.getLogger(__name__)
from python.storage import get_storage

_storage = get_storage()


# ============================================================
# CONFIGURATION
# ============================================================

SEARCH_URL_BASE = "https://www.leforem.be/recherche-offres/api/Recherches/Search"

DETAIL_URL = (
    "https://www.leforem.be/recherche-offres/"
    "api/Diffusion/DetailOffre/{}"
)

ROW = 50

# Polite scraping: bounded parallelism + global rate limit.
MAX_WORKERS = 4
REQUEST_INTERVAL = 0.35
JITTER = 0.1
MAX_ATTEMPTS = 3
RETRY_BACKOFF = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": "https://www.leforem.be",
    "Referer": "https://www.leforem.be/recherche-offres/resultat-recherche",
}

# Absolute so the scraper can also be started from the web server, whatever
# the current working directory is. Every path is built from
# python.config.DATA_DIR at call time, so pointing the tests or the
# command line at another folder is enough: there is a single source of truth.
BASE_DIR = config.BASE_DIR
VERSION = config.VERSION


# ============================================================
# POLITE REQUEST HELPERS
# ============================================================

_throttle_lock = threading.Lock()
_next_request_at = 0.0
_blacklist_lock = threading.Lock()
_details_lock = threading.Lock()
_thread_local = threading.local()


def throttle():
    global _next_request_at
    with _throttle_lock:
        now = time.monotonic()
        delay = _next_request_at - now
        if delay > 0:
            time.sleep(delay)
            now = time.monotonic()
        _next_request_at = (
            now + REQUEST_INTERVAL + random.uniform(0, JITTER)
        )


def request_json_with_retry(session, url):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        throttle()
        try:
            response = session.get(url, timeout=30)
        except requests.RequestException:
            if attempt == MAX_ATTEMPTS:
                raise
            time.sleep(RETRY_BACKOFF * attempt)
            continue

        status = response.status_code
        retryable = status == 429 or status >= 500
        if not retryable or attempt == MAX_ATTEMPTS:
            response.raise_for_status()
            return response.json()
        time.sleep(RETRY_BACKOFF * attempt)
    raise requests.RequestException(
        f"Failed after {MAX_ATTEMPTS} attempts"
    )


def get_session():
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def draw_progress(done, total, width=40):
    if total <= 0:
        return ""
    filled = int(round(width * done / total))
    bar = "=" * filled + "-" * (width - filled)
    pct = 100.0 * done / total
    return f"[{bar}] {pct:6.1f}% ({done}/{total})"


# ============================================================
# HELPERS
# ============================================================

class HTMLToTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def html_to_text(value):
    if not value:
        return ""
    parser = HTMLToTextParser()
    parser.feed(value)
    text = parser.get_text()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_text(value):
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def build_summary(description, max_chars=300):
    text = html_to_text(description)
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(sentences[:2]).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."
    return summary


# ============================================================
# DETAIL FIELD EXTRACTION
# ============================================================

def format_contract_type(value):
    if not value:
        return ""
    if isinstance(value, dict):
        for key in ("libelle", "label", "nom", "value"):
            v = value.get(key)
            if v:
                return clean_text(v)
        return ""
    return clean_text(value)


# The free-text fields written by employers are in French, so the
# patterns below mirror that vocabulary: hour ranges, shift cycles,
# and day/night/weekend work. Everything is evaluated per sentence so
# a negation ("Zéro nuit") only cancels its own sentence.
HOUR_RANGE_PATTERN = re.compile(
    r"(?:de\s+)?(\d{1,2})h(?::?(\d{2}))?"
    r"\s*(?:[-–—→]\s*(?:de\s+)?|à|a|au)\s*"
    r"(\d{1,2})h(?::?(\d{2}))?",
    re.I,
)
SHIFT_CODE_PATTERN = re.compile(r"\b([23])[xX]8\b")
SHIFT_WORD_PATTERN = re.compile(
    r"\b([23])\s*(?:pauses?|shifts?|équipes?|postes?)\b", re.I
)
DAY_WORK_PATTERN = re.compile(
    r"\b(?:horaire\s+)?(?:uniquement\s+)?(?:de\s+)?jour\b", re.I
)
NIGHT_WORK_PATTERN = re.compile(r"\b(?:de\s+)?nuit\b|nocturne", re.I)
WEEKEND_WORK_PATTERN = re.compile(r"\bweek-?end\b|\bweek\s+end\b", re.I)
NEGATION_PATTERN = re.compile(
    r"\b(?:pas\s+d[eu]?|sans|z[ée]ro|aucun(?:e)?|jamais)\b", re.I
)

SCHEDULE_TEXT_FIELDS = (
    "benefitsComments",
    "commentaireGeneral",
    "descriptionJob",
    "descriptionEmployeur",
)
SCHEDULE_MAX_CHARS = 90


def _schedule_is_negated(sentence, match):
    return bool(NEGATION_PATTERN.search(sentence, 0, match.end()))


def _extract_schedule_tokens(sentences):
    """Free-text schedule facts, most informative first."""
    range_found = None
    shift_code = ""
    shift_word = ""
    day_state = "unknown"
    night_state = "unknown"
    weekend_state = "unknown"

    for sentence in sentences:
        if range_found is None:
            m = HOUR_RANGE_PATTERN.search(sentence)
            if m:
                range_found = "{0}h{1}-{2}h{3}".format(
                    m.group(1), m.group(2) or "", m.group(3), m.group(4) or ""
                )

        if not shift_code:
            m = SHIFT_CODE_PATTERN.search(sentence)
            if m:
                shift_code = m.group(1) + "x8"

        if not shift_word:
            m = SHIFT_WORD_PATTERN.search(sentence)
            if m:
                shift_word = m.group(1) + " pauses"

        if day_state != "yes":
            m = DAY_WORK_PATTERN.search(sentence)
            if m:
                day_state = "no" if _schedule_is_negated(sentence, m) else "yes"

        if night_state not in ("yes", "rare"):
            m = NIGHT_WORK_PATTERN.search(sentence)
            if m:
                if _schedule_is_negated(sentence, m):
                    night_state = "no"
                elif re.search(r"\bmoins\s+fr[ée]quen", sentence, re.I):
                    night_state = "rare"
                else:
                    night_state = "yes"

        if weekend_state != "yes":
            m = WEEKEND_WORK_PATTERN.search(sentence)
            if m:
                weekend_state = (
                    "no" if _schedule_is_negated(sentence, m) else "yes"
                )

    tokens = []
    if range_found:
        tokens.append(range_found)
    if shift_word:
        tokens.append(shift_word)
    elif shift_code:
        tokens.append(shift_code)
    if day_state == "yes":
        tokens.append("de jour")
    if night_state == "yes":
        tokens.append("nuit")
    elif night_state == "rare":
        tokens.append("nuit (rare)")
    if weekend_state == "yes":
        tokens.append("week-end")
    return tokens


def extract_schedule(detail):
    regime = clean_text(detail.get("regimeTravail"))
    precision = clean_text(detail.get("regimeTravailPrecision"))

    shift = detail.get("shift")
    if not isinstance(shift, dict):
        benefits = detail.get("benefits")
        if isinstance(benefits, dict):
            shift = benefits.get("shift")
    period = ""
    if isinstance(shift, dict):
        period = clean_text(shift.get("shiftPeriod"))

    parts = [part for part in (regime, precision, period) if part]

    text_sources = [
        html_to_text(detail.get(field) or "") for field in SCHEDULE_TEXT_FIELDS
    ]
    sentences = re.split(
        r"(?<=[.!?])\s+",
        " ".join(source for source in text_sources if source),
    )

    tokens = [
        token for token in _extract_schedule_tokens(sentences)
        if not any(token in part for part in parts)
    ]

    result = " — ".join(parts + tokens)
    if len(result) > SCHEDULE_MAX_CHARS:
        result = result[:SCHEDULE_MAX_CHARS].rstrip() + "…"
    return result


def extract_pay(benefits, max_chars=25):
    if not isinstance(benefits, dict):
        return ""
    value = benefits.get("basePay")
    if value is None:
        return ""
    text = " ".join(str(value).split()).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


# The source text (benefitsComments, etc.) is written in French by
# employers, so the patterns below intentionally match French wording.
SALARY_KEYWORDS = re.compile(
    r"salaire|salarial(e|es)?|r[eéè]mun[eéé]r|r[eéè]tribution|paye\b", re.I
)
AMOUNT_EUR = re.compile(r"\d[\d\s.,]*\s*€", re.I)
PERIODIC_AMOUNT = re.compile(
    r"€\s*/?\s*(?:h\b|heure|mois|an|semaine|jour)", re.I
)
FRINGE_BENEFITS = re.compile(
    r"ch[eè]ques?-repas|ticket|bon repas|frais de", re.I
)


def backfill_cached_pay_fields(base_name, offers, details):
    """Add the derived pay fields to offers cached before they existed.

    Offers already in the store were built without salary_kind and friends.
    They are recomputed from the raw detail payload, so a run after an upgrade
    fills them in without re-downloading anything.

    Only the derived keys are written: content_hash, modified, first_seen_at
    and the follow-up data are left exactly as they are, so no offer is
    wrongly reported as modified.
    """
    from python.salary import backfill_offer

    touched = 0
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        if offer.get("salary_kind") is not None:
            # Already derived: the offer came from build_offer.
            continue
        number = clean_text(offer.get("number"))
        detail = details.get(number) if number else None
        if not isinstance(detail, dict):
            continue
        if backfill_offer(detail, offer):
            touched += 1
    return touched


def derive_remuneration_fields(detail):
    """The derived pay fields of an offer, read from the raw detail.

    Imported lazily so the salary module stays out of the way when only the
    scraper's own fields are needed.
    """
    from python.salary import derive_remuneration_fields as _derive

    return _derive(detail)


def extract_salary(detail, max_chars=75):
    sources = (
        detail.get("benefitsComments")
        or detail.get("commentaireGeneral")
        or detail.get("descriptionComment")
        or ""
    )
    text = html_to_text(sources)
    if not text:
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chosen = ""

    for sentence in sentences:
        if AMOUNT_EUR.search(sentence) and SALARY_KEYWORDS.search(sentence):
            chosen = sentence
            break

    if not chosen:
        for sentence in sentences:
            if not AMOUNT_EUR.search(sentence):
                continue
            if not PERIODIC_AMOUNT.search(sentence):
                continue
            if FRINGE_BENEFITS.search(sentence):
                continue
            chosen = sentence
            break

    if not chosen:
        return ""

    keyword = SALARY_KEYWORDS.search(chosen)
    if keyword:
        chosen = chosen[keyword.start():]

    chosen = re.sub(r"\s+", " ", chosen).strip()
    if len(chosen) > max_chars:
        chosen = chosen[:max_chars].rstrip() + "…"
    return chosen


EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}"
)

# Free-text fields that sometimes carry the contact address when the
# structured "howToApply.email" field is empty (French source text).
EMAIL_TEXT_FIELDS = (
    "descriptionJob",
    "benefitsComments",
    "descriptionEmployeur",
    "commentaireGeneral",
)

EMAIL_MAX_COUNT = 3


def extract_email(detail):
    # 1) Structured contact field, when provided.
    how = detail.get("howToApply")
    structured = ""
    if isinstance(how, dict):
        value = how.get("email")
        if EMAIL_PATTERN.fullmatch(str(value or "").strip()):
            structured = str(value).strip()

    if structured:
        return structured

    # 2) Fallback: scan the free-text fields written by employers.
    found: list[str] = []
    seen = set()

    for field in EMAIL_TEXT_FIELDS:
        if len(found) >= EMAIL_MAX_COUNT:
            break
        text = html_to_text(detail.get(field) or "")
        for match in EMAIL_PATTERN.findall(text):
            email = match.strip().rstrip(".")
            key = email.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(email)
            if len(found) >= EMAIL_MAX_COUNT:
                break

    return ", ".join(found)


def extract_locations(workplaces):
    """Every workplace of an offer, as a list.

    An offer may name several: "Arrondissement de Waremme, Arrondissement de
    Liege, Hannut". They are three distinct places and have to be searchable as
    such, so they are returned separately instead of one joined string.
    """
    if not workplaces:
        return []

    values = []

    def add(value):
        v = clean_text(value)
        if v and v not in values:
            values.append(v)

    if isinstance(workplaces, list):
        for item in workplaces:
            if isinstance(item, dict):
                add(
                    item.get("nom")
                    or item.get("libelle")
                    or item.get("ville")
                    or item.get("codeLocalite")
                    or ""
                )
            else:
                add(item)
    elif isinstance(workplaces, dict):
        add(
            workplaces.get("nom")
            or workplaces.get("libelle")
            or workplaces.get("ville")
            or ""
        )

    return values


def extract_location(workplaces):
    """The workplaces as one readable string, for display and for export."""
    return ", ".join(extract_locations(workplaces))


def build_offer(detail, published_on=""):
    number = clean_text(detail.get("numero"))

    if number:
        url = (
            "https://www.leforem.be/recherche-offres/"
            f"offre-detail/{number}?originPostuler=RECHOFFRE"
        )
    else:
        url = ""

    description = html_to_text(detail.get("descriptionJob"))

    date_publication = clean_text(
        detail.get("datePublication") or detail.get("dateDebutDiffusion")
    )
    if not core.parse_forem_date(date_publication):
        date_publication = clean_text(detail.get("dateDebutDiffusion"))

    # Build base offer data (Forem fields only)
    offer = {
        "number": number,
        "offer_title": clean_text(detail.get("titreOffre")),
        "description": description,
        "company": clean_text(detail.get("nomEmployeur")),
        "email": extract_email(detail),
        "url": url,
        "contract_type": format_contract_type(detail.get("typeContrat")),
        "schedule": extract_schedule(detail),
        # The raw texts feed the parsers before truncation: the truncated
        # values below often lose their unit ("2797" instead of "2 797 EUR/mois").
        "pay": extract_pay(detail.get("benefits")),
        "salary": extract_salary(detail),
        "location": extract_location(detail.get("lieuxTravail")),
        # Same places, kept apart, so the locality filter can offer and match
        # each one. "location" stays the joined string for display.
        "locations": extract_locations(detail.get("lieuxTravail")),
        "published_on": clean_text(published_on),
        "date_publication": date_publication,
        "date_fin_diffusion": clean_text(detail.get("dateFinDiffusion")),
        "metier": clean_text(detail.get("metier")),
        "summary": build_summary(description),
    }

    # The derived pay fields are added after the content hash on purpose:
    # they are read back from the text, so including them would mark offers as
    # modified whenever the parser is improved. CONTENT_HASH_RULE is untouched.
    offer.update(derive_remuneration_fields(detail))

    # Add content hash for change detection
    offer["content_hash"] = core.compute_content_hash(offer)
    offer["hash_rule"] = core.CONTENT_HASH_RULE

    # Add timestamps - only set first_seen_at if not already present
    now = core.now_iso_timestamp()
    offer["last_scraped_at"] = core.now_iso_timestamp()
    offer["last_seen_at"] = core.now_iso_timestamp()
    # first_seen_at and modified_at will be set when the offer is first created or modified
    # We don't set them here since we don't know if it's a new offer or existing one

    return offer


# ============================================================
# OFFER SEARCH
# ============================================================

# Fields of a search result that change without the offer being modified:
# `publication` is a relative text ("Publié hier" becomes "Il y a 3 jours")
# and `logo` is a file id the employer can renew at any time. Everything else
# describes the offer, so anything new appearing there means a real change.
LISTING_VOLATILE_FIELDS = frozenset({"publication", "logo", "id"})


def listing_hash(entry):
    """SHA-256 of one search result, i.e. of the summary of the offer.

    Phase 1 of a run (the search) already knows every offer of the query.
    Comparing this hash with the one stored by the previous run tells, before
    opening anything, whether the summary changed. Phase 2 then downloads the
    detail of the new offers and of the offers whose summary changed, and only
    those.
    """
    stable = {
        key: value
        for key, value in entry.items()
        if key not in LISTING_VOLATILE_FIELDS
    }
    return core.compute_hash(stable)

def search_offers(session, limit=None, occupation_guid=None, location_guid=None,
                  log=print, progress=None):
    """List the offers matching one search.

    `log` and `progress` are the two reporting hooks used to follow the run
    from the terminal or from the web interface; they default to stdout so the
    command-line behaviour is unchanged.
    """
    payload = {
        "filtres": [],
        "filtresCodifies": [],
        "metier": [],
        "secteur": [],
        "lieuxTravail": [
            {"nom": "Nomenclatures/CodeInsBelge", "guid": location_guid}
        ],
        "locutionsGufids": [occupation_guid],
        "priority": 1,
    }

    seen_numbers = set()
    offers = []
    page = 1

    while True:
        url = f"{SEARCH_URL_BASE}?page={page}&row={ROW}"
        log(f"Search page {page}...")
        if progress is not None:
            progress(page)

        throttle()
        response = session.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        results = data.get("offreEmploiResumees") or []
        total = data.get("total", 0)
        page_count = data.get("pageCount") or 1

        log(f"  -> {len(results)} result(s) (total: {total})")

        for offer in results:
            if not isinstance(offer, dict):
                continue
            number = clean_text(offer.get("numero"))
            if not number or number in seen_numbers:
                continue
            seen_numbers.add(number)
            offers.append({
                "number": number,
                "published_on": clean_text(offer.get("publication")),
                "listing_hash": listing_hash(offer),
            })

            if limit is not None and len(offers) >= limit:
                break

        if limit is not None and len(offers) >= limit:
            break

        if page >= page_count:
            break

        page += 1

    log(f"\nOffers retained: {len(offers)}")
    return offers


# ============================================================
# OFFER DETAIL
# ============================================================

def fetch_detail(session, number):
    url = DETAIL_URL.format(number)
    return request_json_with_retry(session, url)


# ============================================================
# JSON FILE READING / WRITING
# ============================================================

def read_previous_offers(path):
    name = config.scrape_base(os.path.basename(path)) if path else ""
    data = _storage.read_scraping(name) if name else None
    if not data:
        from python.jsonio import read_json

        data = read_json(path, None)
    if isinstance(data, dict):
        offers = data.get("offers", [])
    elif isinstance(data, list):
        offers = data
    else:
        offers = []

    numbers = {
        clean_text(o.get("number"))
        for o in offers
        if isinstance(o, dict) and clean_text(o.get("number"))
    }
    return offers, numbers


def empty_history(timestamp):
    return {
        "version": VERSION,
        "updated_timestamp": timestamp,
        "offers": [],
    }


def read_history(path, timestamp=""):
    name = ""
    if path:
        base_name = os.path.basename(path)
        name = config.scrape_base(base_name) or config.history_base(base_name) or ""
    data = _storage.read_history_offers(name) if name else {}
    if data and isinstance(data, dict) and isinstance(data.get("offers"), list):
        data.setdefault("version", VERSION)
        data.setdefault("updated_timestamp", timestamp)
        return data
    if not data:
        from python.jsonio import read_json

        data = read_json(path, None)
    if isinstance(data, dict) and isinstance(data.get("offers"), list):
        data.setdefault("version", VERSION)
        data.setdefault("updated_timestamp", timestamp)
        return data
    return empty_history(timestamp)


def read_blacklist(path=None):
    data = _storage.read_blacklist()
    if not data and path is not None:
        from python.jsonio import read_json

        data = read_json(path, None)
    return core.read_blacklist_data(data)


def write_blacklist(blacklist, path=None):
    _storage.write_blacklist(blacklist)


def normalize_published_on(offer):
    """Le Forem sometimes returns relative text ("Publie aujourd'hui")
    as the search publication date. Fall back to the absolute
    date_publication from the offer detail, stored as DD-MM-YY."""
    raw = offer.get("published_on")
    if core.parse_forem_date(raw):
        return
    absolute = core.parse_forem_date(offer.get("date_publication"))
    if absolute:
        year, month, day = absolute.split("-")
        offer["published_on"] = "%s-%s-%s" % (day, month, year[2:])


def scraper_files(base_name):
    """File names for a scrape, keeping each search isolated."""
    return config.scrape_files(base_name)


def details_file(base_name):
    """Raw detail payload store for a scrape (`details_<base>.json`), kept under data/."""
    return config.details_file(base_name)


# ============================================================
# REMOVED-OFFERS HISTORY
# ============================================================

def update_history(previous_offers, new_offers, history, timestamp):
    previous_numbers = {
        clean_text(o.get("number"))
        for o in previous_offers
        if isinstance(o, dict)
    }
    new_numbers = {
        clean_text(o.get("number"))
        for o in new_offers
        if isinstance(o, dict)
    }

    previous_by_number = {
        clean_text(o.get("number")): o
        for o in previous_offers
        if isinstance(o, dict)
    }

    # An offer that reappears is removed from the history.
    entries = []
    already_present = set()
    for entry in history.get("offers", []):
        if not isinstance(entry, dict):
            continue
        number = clean_text(entry.get("number"))
        if not number or number in new_numbers or number in already_present:
            continue
        already_present.add(number)
        entries.append(entry)

    # Disappeared offers are added (one entry each).
    removed_numbers = previous_numbers - new_numbers
    for number in sorted(removed_numbers):
        if number in already_present:
            continue
        previous = previous_by_number.get(number, {})
        entry = dict(previous)
        entry["is_new"] = False
        entry["offer_state"] = "deleted"
        entry["removed"] = True
        entry["removed_on"] = timestamp
        entries.append(entry)
        already_present.add(number)

    history["version"] = VERSION
    history["updated_timestamp"] = timestamp
    history["offers"] = entries
    return history


# ============================================================
# RUN REPORTING
# ============================================================

class ConsoleReporter:
    """Default reporter: writes to stdout (command-line behaviour)."""

    def log(self, message=""):
        logger.info("%s", message)

    def progress(self, done, total):
        sys.stdout.write(
            "\rFetching details: " + draw_progress(done, total)
        )
        sys.stdout.flush()

    def progress_done(self):
        sys.stdout.write("\n")
        sys.stdout.flush()


def ask_confirmation(question):
    """Interactive confirmation used when a scrape starts from a terminal."""
    try:
        answer = input(f"{question} (O/n) ").strip().lower()
    except EOFError:
        return True
    return answer in ("", "y", "yes", "o", "oui")


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Forem offers scraper"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of offers to fetch.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-download every offer instead of only the new ones "
             "(offers recorded as missing are retried).",
    )
    parser.add_argument(
        "--occupation-guid",
        required=True,
        help="Occupation GUID (required).",
    )
    parser.add_argument(
        "--location-guid",
        required=True,
        help="Work location GUID (required).",
    )
    parser.add_argument(
        "--base",
        default="",
        help="Optional override for the scrape base name. "
             "Default is generated from the GUIDs.",
    )
    parser.add_argument(
        "--label",
        default="",
        help="Human-readable label for this search (shown in the web UI).",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")

    run_scrape(
        occupation_guid=args.occupation_guid,
        location_guid=args.location_guid,
        base=args.base,
        label=args.label,
        limit=args.limit,
        refresh=args.refresh,
    )

    # Export raw.json after the scrape, so the frontend always has a fresh
    # mirror of SQLite. A failure here must not turn a successful scrape into
    # an error: the data is safe in the database and the export can be re-run.
    try:
        from python import export
        export.main([])
    except Exception as exc:
        print(f"Export failed (data kept in SQLite): {exc}", file=sys.stderr)


def run_scrape(occupation_guid, location_guid, base="", label="", limit=None,
               refresh=False, reporter=None, confirm=None):
    """Run one complete scrape and return its summary.

    The call is blocking: it only returns once every file has been written.
    `reporter` receives the real scraper events (see ConsoleReporter) and
    `confirm` asks whether the newly found offers should be downloaded. Both
    are injected so the same code serves the CLI and the web server.
    """
    reporter = reporter or ConsoleReporter()
    log = reporter.log
    confirm = confirm or ask_confirmation

    if limit is not None and limit <= 0:
        raise ValueError("--limit must be a positive integer")

    # Generate base_name from GUIDs (first 8 chars each) unless overridden.
    if base:
        base_name = re.sub(r"[^A-Za-z0-9_-]+", "-", base).strip("-")
    else:
        occ8 = occupation_guid[:8]
        loc8 = location_guid[:8]
        base_name = f"{occ8}-{loc8}"

    data_file, history_file = scraper_files(base_name)
    blacklist_file = config.blacklist_file()
    started_at = time.monotonic()

    now = now_iso_timestamp()

    previous_offers, previous_numbers = read_previous_offers(
        path=data_file
    )
    previous_by_number = {
        clean_text(o.get("number")): o
        for o in previous_offers
        if isinstance(o, dict) and clean_text(o.get("number"))
    }
    history = read_history(
        path=history_file, timestamp=now
    )
    deleted_numbers = {
        clean_text(entry.get("number"))
        for entry in history.get("offers", [])
        if isinstance(entry, dict) and clean_text(entry.get("number"))
    }

    # The scrape history is read through the storage layer: with SQLite there
    # is no scrapes.json to open, and the storage returns the bare list the
    # writer expects.
    scrapes = list(_storage.read_history_scrapes())

    previous_details = _storage.read_details(base_name)

    blacklist = read_blacklist(blacklist_file)
    if blacklist:
        log(f"Offers tracked as missing: {len(blacklist)}")
        if refresh:
            log("  (--refresh: attempting them again)")

    if base_name:
        log(f"Scrape: {base_name}")
    if label:
        log(f"Label: {label}")
    log(f"Storage: {_storage.describe()}")

    if limit is not None:
        log(f"Limit requested: {limit} offer(s)")
    else:
        log("No limit: fetching every offer.")
    log()

    if refresh:
        log("--refresh mode: every offer is downloaded again.")
    else:
        log(
            "Only the new offers, the offers whose summary changed and the "
            "previously failed ones are downloaded."
        )
    log()

    session = requests.Session()
    session.headers.update(HEADERS)

    log(f"Connecting to the Forem API...")
    search_results = search_offers(
        session,
        limit=limit,
        occupation_guid=occupation_guid,
        location_guid=location_guid,
        log=log,
    )

    new_count = sum(
        1 for entry in search_results
        if entry["number"] not in previous_numbers
    )

    if new_count > 0 and not refresh:
        if not confirm(
            f"{new_count} nouvelles annonces trouvées, "
            "souhaitez-vous les scraper?"
        ):
            log("Annulation. Les données existantes sont conservées.")
            return {
                "status": "cancelled",
                "name": base_name,
                "label": label,
                "total_offres": len(previous_offers),
                "duration_seconds": round(time.monotonic() - started_at, 1),
            }

    new_offers = []
    new_details = {}
    fetched_count = 0
    cached_count = 0
    errors = []
    changed_summaries = []

    if search_results:
        # Phase 1 gave the summary of every offer of the search. Phase 2 only
        # opens the offers that are new, the ones whose summary changed since
        # the previous run, and the ones that previously failed. The others
        # are kept from the cache without a single request.
        # --refresh downloads every offer again.
        
        # Apply the 12-month temporal filter BEFORE downloading details.
        # This avoids fetching details of offers older than 1 year.
        # Offers already in SQLite are never removed by this filter.
        search_results = core.filter_recent_offers(search_results, max_age_days=365)
        
        tasks: list[tuple[Any, ...]] = []
        for index, entry in enumerate(search_results, start=1):
            number = entry["number"]
            known = number in previous_by_number
            if known and not refresh:
                previous_listing = clean_text(
                    previous_by_number[number].get("listing_hash")
                )
                current_listing = clean_text(entry.get("listing_hash"))
                if not previous_listing:
                    # First run storing the summaries: the baseline is saved
                    # without downloading the offers again.
                    tasks.append((index, entry, True))
                    continue
                if current_listing and current_listing != previous_listing:
                    changed_summaries.append(number)
                else:
                    tasks.append((index, entry, True))
                    continue
            if core.should_fetch(number, blacklist, force=refresh):
                tasks.append((index, entry))
            else:
                tasks.append((index, entry, True))
        to_fetch = len([t for t in tasks if len(t) == 2])
        cached_count = len(tasks) - to_fetch
        log(
            f"\nFetching details ({to_fetch} offer(s)) "
            f"with {MAX_WORKERS} parallel workers..."
        )
        if cached_count:
            log(f"  ({cached_count} offer(s) kept from cache)")
        if changed_summaries:
            preview = ", ".join(changed_summaries[:5])
            more = (
                f" (+{len(changed_summaries) - 5})"
                if len(changed_summaries) > 5 else ""
            )
            log(
                f"  ({len(changed_summaries)} summary(ies) changed since the "
                f"last run: {preview}{more})"
            )

        def process_entry(task):
            index, entry = task[:2]
            number = entry["number"]
            published_on = entry.get("published_on", "")

            if len(task) == 3:
                return task, None, "cached"

            try:
                detail = fetch_detail(get_session(), number)
                offer = build_offer(
                    detail, published_on=published_on
                )
                with _details_lock:
                    new_details[number] = detail
                return task, offer, "fetched"
            except requests.HTTPError as e:
                response = getattr(e, "response", None)
                status = response.status_code if response is not None else "?"
                with _blacklist_lock:
                    core.note_miss(blacklist, number, now)
                return task, None, f"HTTP {status}: {e}"
            except requests.RequestException as e:
                with _blacklist_lock:
                    core.note_miss(blacklist, number, now)
                return task, None, f"Network error: {e}"
            except Exception as e:
                with _blacklist_lock:
                    core.note_miss(blacklist, number, now)
                return task, None, f"Error: {e}"

        done = 0
        issue_lines = []
        # The progress counts the offers actually downloaded: the cached ones
        # are not requests to Forem, so counting them would make an
        # incremental run look like a full one.
        fetch_total = to_fetch

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:
            for task, offer, outcome in executor.map(
                process_entry, tasks
            ):
                index, entry = task[0], task[1]
                number = entry["number"]
                if offer is not None:
                    with _blacklist_lock:
                        core.note_recovery(blacklist, number)
                    new_offers.append(offer)
                    fetched_count += 1
                elif outcome == "cached":
                    cached = previous_by_number.get(number)
                    if cached is not None:
                        reused = dict(cached)
                        # An offer cached before the derived pay fields existed
                        # is filled in from its raw detail payload, so an
                        # upgrade needs no re-download. content_hash and the
                        # modification flags are left alone.
                        if reused.get("salary_kind") is None:
                            # The raw payload of a cached offer lives in the
                            # previous details, not in this run's fetches.
                            backfill_cached_pay_fields(
                                base_name, [reused], previous_details
                            )
                        new_offers.append(reused)
                else:
                    # Temporary failure: keep the previous data so a
                    # single bad request never wipes an offer out.
                    if number in previous_by_number:
                        cached = dict(previous_by_number[number])
                        cached["is_new"] = False
                        new_offers.append(cached)
                    issue_lines.append(f"  Offer {number}: {outcome}")
                    errors.append(f"Offer {number}: {outcome}")
                if outcome != "cached":
                    done += 1
                    reporter.progress(done, fetch_total)

        reporter.progress_done()
        for line in issue_lines:
            log(line)

    if not new_offers and not search_results:
        log("\nNo offer found.")

    # Hash-based change detection and diff generation
    modified_count = 0
    summaries_stored = 0
    listing_hashes = {
        entry["number"]: entry.get("listing_hash") or ""
        for entry in search_results
    }
    for position, offer in enumerate(new_offers):
        if not isinstance(offer, dict):
            continue
        number = clean_text(offer.get("number"))
        if not number:
            continue

        previous = previous_by_number.get(number)
        new_hash = offer.get("content_hash")

        # Every offer keeps the summary hash of this run: it is the reference
        # the next run compares against before opening the offer.
        current_listing = listing_hashes.get(number, "")
        if current_listing:
            offer["listing_hash"] = current_listing
            if previous is not None and not previous.get("listing_hash"):
                summaries_stored += 1
        if offer.get("hash_rule") != core.CONTENT_HASH_RULE:
            offer["hash_rule"] = core.CONTENT_HASH_RULE

        if previous is not None:
            # Existing offer - check for changes
            previous_hash = previous.get("content_hash")
            comparable = (
                previous.get("hash_rule") == core.CONTENT_HASH_RULE
            )
            if previous_hash and comparable and new_hash != previous_hash:
                # Content changed - compute diff, then replace the stored
                # data and keep a single version of the offer.
                stamp = core.now_iso_timestamp()
                offer["diff"] = core.compute_diff(previous, offer)
                offer["modified"] = True
                offer["content_hash"] = new_hash
                offer["modified_at"] = stamp
                offer["last_seen_at"] = stamp
                offer["last_scraped_at"] = stamp
                # Preserve original first_seen_at
                if "first_seen_at" in previous:
                    offer["first_seen_at"] = previous["first_seen_at"]
                modified_count += 1
            else:
                # No content change
                offer["modified"] = False
                offer["content_hash"] = previous_hash or new_hash
                offer["diff"] = {}
                # Preserve existing timestamps
                if "first_seen_at" in previous:
                    offer["first_seen_at"] = previous["first_seen_at"]
                if "modified_at" in previous:
                    offer["modified_at"] = previous["modified_at"]
                offer["last_seen_at"] = core.now_iso_timestamp()
                offer["last_scraped_at"] = core.now_iso_timestamp()
            # User data (favorite, status, notes, tags…) lives apart from
            # the Forem payload and is carried over to the new version.
            new_offers[position] = core.preserve_user_data(previous, offer)
        else:
            # New offer - set initial timestamps
            stamp = core.now_iso_timestamp()
            if "first_seen_at" not in offer:
                offer["first_seen_at"] = stamp
            offer["last_seen_at"] = stamp
            offer["last_scraped_at"] = stamp
            offer["modified"] = False
            # No diff for new offers
            offer["diff"] = {}

    # State classification.
    states, current_numbers = core.collect_states(
        previous_offers, new_offers, deleted_numbers
    )

    for offer in new_offers:
        if not isinstance(offer, dict):
            continue
        number = clean_text(offer.get("number"))
        if not number:
            continue
        normalize_published_on(offer)
        state = (
            "reappeared" if number in states["reappeared"]
            else "new" if number in states["new"]
            else "unchanged"
        )
        offer["offer_state"] = state
        offer["is_new"] = state in ("new", "reappeared")
        if state == "reappeared":
            offer["reappeared"] = True
            offer.pop("removed", None)
            offer.pop("removed_on", None)

    # Apply 12-month temporal filter
    offers_before_filter = len(new_offers)
    new_offers = core.filter_recent_offers(new_offers, max_age_days=365)
    dropped_count = offers_before_filter - len(new_offers)

    update_history(
        previous_offers, new_offers, history, now
    )

    # Deleted offers are not dropped: they stay in the listing as tombstones
    # (offer_state="deleted") so their whole content, and the raw detail
    # payload kept below, stay available. The UI shows them as "Supprimée".
    deleted_offers = core.tombstone_offers(previous_offers, new_offers, now)
    listing_offers = new_offers + deleted_offers

    summary = core.summarize_scrape(states, total=len(new_offers))
    entry = {
        "timestamp": now,
        "search": base_name,
        "label": label,
        **summary,
    }
    core.record_scrape(scrapes, entry)

    # Métier index, written with the data file so the search stays able to
    # filter offers by métier. Nothing reads it yet: the pages show the flat
    # offer list, and the browser has no métier filter. It is kept because the
    # index is built from the offers that were just fetched (no extra cost)
    # and dropping it would throw away data the UI will need as soon as the
    # filter appears. Remove it together with core.build_metier_index if the
    # filter is never built.
    metier_index = core.build_metier_index(new_offers)

    data = {
        "version": VERSION,
        "scrape_timestamp": now,
        "name": base_name,
        "label": label,
        "occupation_guid": occupation_guid,
        "location_guid": location_guid,
        "offers": listing_offers,
        "metier_index": metier_index,
    }

    _storage.write_history_offers(base_name, history)
    _storage.write_history_scrapes(scrapes)
    _storage.write_scraping(base_name, data)
    write_blacklist(blacklist, blacklist_file)

    # Raw Forem payloads are kept for every offer still in the listing
    # (tombstones included) and for every entry of the deleted history.
    keep_details_numbers = {
        clean_text(o.get("number"))
        for o in listing_offers
        if isinstance(o, dict) and clean_text(o.get("number"))
    } | {
        clean_text(entry.get("number"))
        for entry in history.get("offers", [])
        if isinstance(entry, dict) and clean_text(entry.get("number"))
    }
    final_details = core.merge_details(
        previous=previous_details,
        fetched=new_details,
        keep=keep_details_numbers,
    )
    _storage.write_details(base_name, final_details)

    log()
    log(
        f"Done: {len(new_offers)} offer(s) "
        f"({fetched_count} fetched, {cached_count} from cache)"
    )
    if deleted_offers:
        log(
            f"Deleted: {len(deleted_offers)} offer(s) kept in the listing "
            "(marked Supprimée)"
        )
    log(
        "States: {nouvelles} new, {reapparues} back, "
        "{inchangees} unchanged, {supprimees} deleted".format(**summary)
    )
    log(f"Change detection: {modified_count} offer(s) modified")
    if changed_summaries:
        log(
            f"Summaries changed: {len(changed_summaries)} offer(s) opened "
            "again to compare the detail"
        )
    if summaries_stored:
        log(
            f"Summary hashes stored: {summaries_stored} offer(s) "
            "(reference for the next run)"
        )
    if dropped_count:
        log(
            f"Temporal filter (12 months): {dropped_count} offer(s) ignored"
        )
    log(f"Métiers indexed: {len(metier_index)}")
    if errors:
        log(f"Errors: {len(errors)} offer(s) failed, data kept")
    log(f"Storage updated: {_storage.describe()}")

    return {
        "status": "done",
        "name": base_name,
        "label": label,
        "scrape_timestamp": now,
        "occupation_guid": occupation_guid,
        "location_guid": location_guid,
        "total_offres": len(new_offers),
        "traitees": offers_before_filter,
        "nouvelles": summary["nouvelles"],
        "reapparues": summary["reapparues"],
        "inchangees": summary["inchangees"],
        "supprimees": summary["supprimees"],
        "modifiees": modified_count,
        "resumes_modifies": len(changed_summaries),
        "resumes_enregistres": summaries_stored,
        "erreurs": len(errors),
        "erreur_details": errors,
        "hors_periode": dropped_count,
        "metiers": len(metier_index),
        "fetched": fetched_count,
        "cached": cached_count,
        "duration_seconds": round(time.monotonic() - started_at, 1),
    }


if __name__ == "__main__":
    main()