"""Salary and meal-voucher parsing, as pure text functions.

No network and no knowledge of the scraper inside the parsers themselves:
``parse_remuneration`` and ``parse_meal_vouchers`` take strings and return
dictionaries, which keeps them testable on their own.

The texts handed to the parsers are the raw ``benefits`` fields and the raw
pay/salary comment, *before* the truncation the scraper applies for display.
Truncated text loses its unit and turns a monthly wage into a bare number.

The module is also the entry point for the catch-up on offers already in the
store::

    python -m python.salary --backfill
    python -m python.salary --stats
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# CONVERSION ASSUMPTIONS
#
# These are the only assumptions behind hourly_estimate. They are
# constants so the choice is visible and can be challenged.
# ============================================================

#: Working time in a Belgian full-time week.
HOURS_PER_WEEK = 38.0

#: Average weeks per year, so a month averages 52 / 12 weeks.
WEEKS_PER_YEAR = 52.0
MONTHS_PER_YEAR = 12.0

#: Hours in an average month: 38 h per week over 52 weeks, spread on 12
#: months, so 38 * 52 / 12 = 164.666...
HOURS_PER_MONTH = HOURS_PER_WEEK * WEEKS_PER_YEAR / MONTHS_PER_YEAR

# ============================================================
# AMOUNT READING
# ============================================================

# A number as it appears in a French or English job ad: both separators are
# accepted, plus a space as a thousands separator.
NUMBER = r"\d{1,3}(?:[ .\u00a0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"

AMOUNT_RE = re.compile(r"(?<![\d,.])(%s)(?![\d])" % NUMBER)

# "18-24", "18 a 24", "entre 22 et 24", "de 2800 a 3200", "entre 16.80 EUR et 20 EUR".
# The currency symbol often sits between the two numbers, so it is allowed
# there as well as after the last one.
_APOS = r"[\u2019']"
_MONEY = r"(?:\s*(?:€|eur(?:os)?|bruts?|h(?:eure?)?))?"
RANGE_RE = re.compile(
    r"(?:entre|de)?\s*(%s)%s\s*(?:et|à|a|-|–|—)\s*(%s)" % (NUMBER, _MONEY, NUMBER),
    re.I,
)
OPEN_RANGE_RE = re.compile(
    r"(?:à\s*partir\s+de|a\s*partir\s+de|minimum|au\s+minimum)\s*(%s)" % NUMBER,
    re.I,
)

# A time of day ("9h", "9h30", "9:00") is not a wage. A dash followed by a
# digit is NOT listed: that is a numeric range ("18-24"), which the range
# pattern already reads as wages.
CLOCK_RE = re.compile(r"^\s*(?:h\b|h\d|:\d)", re.I)

# ============================================================
# UNITS
# ============================================================

# A unit is only believed when it sits close to an amount: "horaire de
# travail" mentions the word without being a pay unit, hence the negative
# lookahead below.
HOURLY_UNIT_RE = re.compile(
    r"(?:"
    r"€\s*/\s*h(?:r|eure?)?"
    r"|\beuros?\s*(?:de|par|à)\s+l" + _APOS + r"?heure"
    r"|\bpar\s+h(?:eure?)?\b"
    r"|\bde\s+l" + _APOS + r"?heure"
    r"|\bbruts?\s*/\s*h(?:eure?)?"
    r"|\bhoraire\b(?!\s+de\s+travail)"
    r"|\bl" + _APOS + r"?heure\b"
    r")",
    re.I,
)

MONTHLY_UNIT_RE = re.compile(
    r"(?:"
    r"€\s*/\s*mois"
    r"|\beuros?\s*par\s+mois"
    r"|\bpar\s+mois\b"
    r"|\bau\s+mois\b"
    r"|\bbruts?\s*/\s*mois"
    r"|\bmensuel(?:le)?s?\b"
    r")",
    re.I,
)

ANNUAL_UNIT_RE = re.compile(
    r"(?:"
    r"€\s*/\s*an\b"
    r"|\beuros?\s*par\s+an\b"
    r"|\bpar\s+an\b"
    r"|\bpar\s+année"
    r"|\bbruts?\s*/\s*an\b"
    r"|\bannuel(?:le)?s?\b"
    r")",
    re.I,
)

#: Magnitude thresholds used only when no unit is written.
HOURLY_MAX = 100.0
MONTHLY_MIN = 1000.0
MONTHLY_MAX = 10000.0

# ============================================================
# FRINGE BENEFITS: never read as a salary
# ============================================================

#: Keywords that mark the amount next to them as a benefit, not a wage.
FRINGE_BENEFITS = (
    r"ch[eè]ques?[-\s]?repas|titres?[-\s]?repas|tickets?[-\s]?repas"
    r"|tickets?\s+restaurant|ch[eè]que\s+restaurant|restaurant"
    r"|panier\s?repas|prime(?:s)?|13e\s+mois|treizi[eè]me\s+mois"
    r"|frais\s+de|indemnit[eé]|transport|carte\s+essence"
    r"|assurance|formation|bonus|avantage|prime\s+d'\s+embauche"
)

FRINGE_RE = re.compile(FRINGE_BENEFITS, re.I)

#: How far from the amount a fringe keyword may sit and still disqualify it.
#: "17 EUR/h + cheques-repas de 5 EUR/jour" is one sentence, so the two
#: amounts are close; the wage must not be read as 5.
FRINGE_WINDOW = 40


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def to_number(raw: Any) -> Optional[float]:
    """Read a French or English amount.

    ``2.700`` is two thousand seven hundred (French thousands separator) while
    ``18.00`` is eighteen (English decimal point): the separator is a decimal
    mark when one or two digits follow it, and a thousands mark when exactly
    three do.
    """
    text = str(raw or "").strip()
    if not text:
        return None

    # Whitespace, including the narrow no-break space, is a thousands mark.
    text = text.replace("\u00a0", "").replace(" ", "")

    has_comma = "," in text
    has_dot = "." in text

    if has_comma and has_dot:
        # The rightmost separator is the decimal one.
        if text.rindex(",") > text.rindex("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        tail = text.split(",")[-1]
        # Three digits after the comma: a thousands separator, not decimals.
        text = text.replace(",", "") if len(tail) == 3 else text.replace(",", ".")
    elif has_dot:
        parts = text.split(".")
        if (len(parts) == 2 and len(parts[1]) == 3
                and len(parts[0]) <= 3 and parts[0] != "0"):
            # "2.700" and "36.000": thousands.
            text = parts[0] + parts[1]
        else:
            text = ".".join(parts)

    try:
        return float(text)
    except ValueError:
        return None


def _unit_near(text: str, start: int, end: int) -> Optional[str]:
    """The pay unit next to an amount, looking a short way either side."""
    window = text[max(0, start - 24): end + 24]
    for kind, pattern in (
        ("hourly", HOURLY_UNIT_RE),
        ("monthly", MONTHLY_UNIT_RE),
        ("annual", ANNUAL_UNIT_RE),
    ):
        if pattern.search(window):
            return kind
    return None


def _is_clock(text: str, end: int) -> bool:
    """True when the amount is immediately a time of day, e.g. "9h à 17h"."""
    return bool(CLOCK_RE.match(text[end:end + 6]))


def _is_fringe(text: str, start: int, end: int) -> bool:
    """True when a fringe benefit keyword surrounds the amount."""
    window = text[max(0, start - FRINGE_WINDOW): end + FRINGE_WINDOW]
    return bool(FRINGE_RE.search(window))


def parse_remuneration(*texts: str) -> Dict[str, Any]:
    """Read the pay period out of one or more free-text fields.

    Returns a dict with:

    ``kind``
        ``hourly``, ``monthly``, ``annual`` or ``unknown``.
    ``min`` / ``max``
        Amounts in the unit of ``kind``, ``None`` when unknown. A single amount
        gives ``min == max``; "a partir de X" gives ``min=X`` and ``max=None``.
    ``gross``
        ``True`` when "brut" is mentioned, ``False`` for "net", else ``None``.
    ``hourly_estimate``
        Gross-equivalent euros per hour, or ``None`` when the kind is unknown.
        Never derived from a net amount, since net-to-gross needs the
        deductions, which are not known here.
    ``confidence``
        ``high`` when the unit sits next to the amount, ``low`` when the unit
        was guessed from the magnitude, ``none`` when nothing was read.
    """
    text = _clean(" ".join(t for t in texts if t))

    result: Dict[str, Any] = {
        "kind": "unknown",
        "min": None,
        "max": None,
        "gross": None,
        "hourly_estimate": None,
        "confidence": "none",
    }
    if not text:
        return result

    lowered = text.lower()
    if "brut" in lowered:
        result["gross"] = True
    if re.search(r"\bnet\b", lowered):
        result["gross"] = False

    # Every amount in the text, tagged with its unit and whether it is a
    # benefit. Ranges are handled first so "de 2800 a 3200" is not read as
    # two separate amounts.
    candidates: List[Tuple[float, Optional[str], bool, str]] = []
    consumed: List[Tuple[int, int]] = []

    for match in RANGE_RE.finditer(text):
        if _is_clock(text, match.end(1)):
            # "9h à 17h": working hours, not a wage range.
            continue
        low = to_number(match.group(1))
        high = to_number(match.group(2))
        if low is None or high is None:
            continue
        unit = _unit_near(text, match.start(1), match.end(2))
        candidates.append((min(low, high), unit, False, "range"))
        candidates.append((max(low, high), unit, False, "range"))
        consumed.append((match.start(), match.end()))

    for match in OPEN_RANGE_RE.finditer(text):
        if any(s <= match.start() < e for s, e in consumed):
            continue
        value = to_number(match.group(1))
        if value is None:
            continue
        unit = _unit_near(text, match.start(1), match.end(1))
        candidates.append((value, unit, False, "open"))
        consumed.append((match.start(), match.end()))

    for match in AMOUNT_RE.finditer(text):
        if any(s <= match.start() < e for s, e in consumed):
            continue
        if _is_clock(text, match.end(1)):
            # "9h à 17h": working hours, not a wage.
            continue
        value = to_number(match.group(1))
        if value is None:
            continue
        unit = _unit_near(text, match.start(1), match.end(1))
        candidates.append((value, unit, _is_fringe(text, match.start(1), match.end(1)), "single"))

    # Wages only: a benefit amount is never taken for a wage.
    wages = [c for c in candidates if not c[2]]

    kind = _kind_of(wages)
    if kind == "unknown":
        return result

    values = sorted(c for c, _u, _f, _k in wages)
    result["kind"] = kind
    result["min"] = values[0]
    result["max"] = values[-1] if len(values) > 1 else values[0]

    explicit = any(u == kind for _v, u, _f, _k in wages)
    result["confidence"] = "high" if explicit else "low"

    if kind == "monthly":
        result["hourly_estimate"] = result["min"] / HOURS_PER_MONTH
    elif kind == "annual":
        result["hourly_estimate"] = result["min"] / MONTHS_PER_YEAR / HOURS_PER_MONTH
    elif kind == "hourly":
        result["hourly_estimate"] = result["min"]

    return result


def _kind_of(wages: Sequence[Tuple[float, Optional[str], bool, str]]) -> str:
    """The pay period, from an explicit unit or from the magnitude.

    A unit carried by most of the amounts wins. Otherwise the amount decides:
    "horaire de travail" must not turn a yearly figure into an hourly one.
    """
    units = [u for _v, u, _f, _k in wages if u]
    if units:
        for kind in ("hourly", "monthly", "annual"):
            if units.count(kind) > len(units) / 2:
                return kind
        return units[0]

    if not wages:
        return "unknown"

    biggest = max(v for v, _u, _f, _k in wages)
    if 0 < biggest < HOURLY_MAX:
        return "hourly"
    if MONTHLY_MIN <= biggest <= MONTHLY_MAX:
        return "monthly"
    if biggest > MONTHLY_MAX:
        return "annual"
    return "unknown"


# ============================================================
# MEAL VOUCHERS
# ============================================================

#: Every spelling seen in a Forem ad. "repas" alone is included because the
#: amount next to it is what identifies the voucher.
MEAL_KEYWORDS = (
    r"ch[eè]ques?[-\s]?repas"
    r"|titres?[-\s]?repas"
    r"|tickets?[-\s]?repas"
    r"|ch[eè]ques?[-\s]?restaurant"
    r"|tickets?[-\s]?restaurant"
    r"|vouchers?\s+repas"
    r"|meal\s+voucher"
    r"|repas"
)

MEAL_RE = re.compile(MEAL_KEYWORDS, re.I)

#: "pas de cheques-repas", "sans titres-repas": the benefit is absent.
NEGATION_RE = re.compile(
    r"(?:pas\s+de|pas\s+d'|sans|aucun(?:e)?|ni)\s*$|"
    r"(?:pas\s+de|pas\s+d'|sans|aucun(?:e)?|ni)\s+(?:de\s+|d')?\s*\w{0,12}\s*$",
    re.I,
)

#: The voucher is stated per day or per month.
MEAL_DAY_RE = re.compile(r"(?:/|par\s+|\s+)jour|\bday\b|\bjournalier", re.I)
MEAL_MONTH_RE = re.compile(r"(?:/|par\s+)\s*mois|\bmonth\b|\bmensuel", re.I)

#: A per-day amount, or an employer contribution, identifies a voucher even
#: without the word "repas": "8 EUR/jour", "1,09 EUR part travailleur".
MEAL_HINT_RE = re.compile(
    r"(?:€|eur(?:os)?)?\s*/\s*(?:jour|day)"
    r"|\bpart\s+travailleur"
    r"|\bpart\s+employeur"
    r"|\bch[eè]que\b",
    re.I,
)


def parse_meal_vouchers(*texts: str) -> Dict[str, Any]:
    """Read the meal-voucher amount out of one or more free-text fields.

    Returns ``{"amount": float|None, "period": "day"|"month"|"unknown",
    "mentioned": bool}``.

    A voucher named without an amount gives ``mentioned=True`` and
    ``amount=None``. When a wage and a voucher share the sentence, the amount
    closest to the meal keyword wins, so "17 EUR/h + cheques repas de 5
    EUR/jour" reports 5 and never 17.
    """
    text = _clean(" ".join(t for t in texts if t))
    result: Dict[str, Any] = {
        "amount": None,
        "period": "unknown",
        "mentioned": False,
    }
    if not text:
        return result

    keyword = MEAL_RE.search(text)
    if keyword:
        # "pas de cheques-repas" means there are none.
        if NEGATION_RE.search(text[max(0, keyword.start() - 20): keyword.start()]):
            return result
        result["mentioned"] = True
        window = text[keyword.start(): keyword.end() + FRINGE_WINDOW]
    else:
        hint = MEAL_HINT_RE.search(text)
        if not hint:
            return result
        result["mentioned"] = True
        window = text[max(0, hint.start() - 24): hint.end() + 12]

    if MEAL_DAY_RE.search(window):
        result["period"] = "day"
    elif MEAL_MONTH_RE.search(window):
        result["period"] = "month"

    # The amount closest to the keyword inside the window.
    best: Optional[Tuple[int, float]] = None
    for match in AMOUNT_RE.finditer(window):
        value = to_number(match.group(1))
        if value is None or value <= 0:
            continue
        if best is None or match.start(1) < best[0]:
            best = (match.start(1), value)
    if best is not None:
        result["amount"] = best[1]

    return result


# ============================================================
# BRIDGE TO THE OFFER
# ============================================================

#: Fields added to an offer. None of them is in core.CONTENT_HASH_FIELDS.
REMUNERATION_FIELDS = (
    "salary_kind",
    "salary_min",
    "salary_max",
    "salary_gross",
    "salary_hourly_estimate",
    "salary_confidence",
)

MEAL_VOUCHER_FIELDS = (
    "meal_voucher_amount",
    "meal_voucher_period",
    "meal_voucher_mentioned",
)

#: The raw texts a pay figure can hide in.
SALARY_SOURCE_KEYS = (
    "benefitsComments",
    "commentaireGeneral",
    "descriptionComment",
)


def _clean_html(text: Any) -> str:
    """Turn the HTML the Forem returns into plain text.

    Deliberately coarse: the field may be missing, None or a list, and only
    the amounts and units matter here.
    """
    if text is None:
        return ""
    if isinstance(text, (list, tuple)):
        text = " ".join(str(part) for part in text)
    text = str(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&eacute;", "é")
    text = text.replace("&egrave;", "è").replace("&agrave;", "à")
    text = text.replace("&ecirc;", "ê").replace("&rsquo;", "'")
    text = text.replace("&amp;", "&").replace("&quot;", '"')
    return _clean(text)


def _benefits_texts(detail: Any) -> List[str]:
    """The structured benefits text, whatever the keys are called.

    The real payloads only carry ``basePay`` and ``otherBenefits``, so every
    key present is read and unknown spellings are tolerated rather than
    guessed.
    """
    texts: List[str] = []
    if not isinstance(detail, dict):
        return texts
    benefits = detail.get("benefits")
    if isinstance(benefits, dict):
        for value in benefits.values():
            if isinstance(value, (list, tuple)):
                texts.append(" ".join(str(v) for v in value))
            else:
                texts.append(str(value))
    elif isinstance(benefits, (list, tuple, str)):
        texts.append(_clean_html(benefits))
    return texts


def salary_source_texts(detail: Any) -> List[str]:
    """Every text a pay or voucher figure can be read from, raw."""
    texts: List[str] = []
    if not isinstance(detail, dict):
        return texts
    texts.extend(_benefits_texts(detail))
    for key in SALARY_SOURCE_KEYS:
        if key in detail:
            texts.append(_clean_html(detail.get(key)))
    return [t for t in texts if t]


def derive_remuneration_fields(detail: Any) -> Dict[str, Any]:
    """The derived pay and voucher fields of one offer, from its raw detail.

    Takes the untruncated text: the displayed ``pay`` is cut to 25 characters
    and the ``salary`` to 75, which regularly drops the unit and turns a
    monthly wage into a bare number.
    """
    texts = salary_source_texts(detail)
    pay = parse_remuneration(*texts)
    meal = parse_meal_vouchers(*texts)

    return {
        "salary_kind": pay["kind"],
        "salary_min": pay["min"],
        "salary_max": pay["max"],
        "salary_gross": pay["gross"],
        "salary_hourly_estimate": pay["hourly_estimate"],
        "salary_confidence": pay["confidence"],
        "meal_voucher_amount": meal["amount"],
        "meal_voucher_period": meal["period"],
        "meal_voucher_mentioned": meal["mentioned"],
    }


def backfill_offer(detail: Any, offer: Dict[str, Any]) -> bool:
    """Recompute the derived fields on a cached offer. True when it changed.

    Only the derived keys are touched: ``content_hash``, ``modified``,
    ``first_seen_at`` and the follow-up data are left exactly as they are.
    """
    changed = False
    for key, value in derive_remuneration_fields(detail).items():
        if offer.get(key) != value:
            offer[key] = value
            changed = True
    return changed


# ============================================================
# COMMAND LINE
# ============================================================


def _store() -> Any:
    from python.storage import get_storage

    return get_storage()


def _offers_of(store: Any, base_name: str) -> List[Dict[str, Any]]:
    return [offer for _id, offer in store.list_all_offers(base_name)]


def backfill_search(store: Any, base_name: str) -> Tuple[int, int]:
    """Recompute the derived fields of one search.

    Returns (offers visited, offers changed). The payload is written back
    once per search, so content_hash, modified, first_seen_at and the
    follow-up data all survive untouched.
    """
    details = store.read_details(base_name)
    offers = _offers_of(store, base_name)

    visited = 0
    changed = 0
    for offer in offers:
        number = str(
            offer.get("number") or offer.get("id") or offer.get("numero") or ""
        )
        detail = details.get(number)
        if not isinstance(detail, dict):
            # Nothing to read from: the offer is left as it is.
            continue
        visited += 1
        if backfill_offer(detail, offer):
            changed += 1

    if visited == 0:
        return visited, changed

    stored = store.read_scraping(base_name)
    payload: Dict[str, Any] = dict(stored) if isinstance(stored, dict) else {}
    payload["offers"] = offers
    store.write_scraping(base_name, payload)
    return visited, changed


def backfill_all() -> Tuple[int, int]:
    """Walk every search in the store."""
    store = _store()
    total_visited = 0
    total_changed = 0
    for base_name in store.get_scraping_names():
        visited, changed = backfill_search(store, base_name)
        logger.info("%-24s %4d offres, %4d mises a jour", base_name, visited, changed)
        total_visited += visited
        total_changed += changed
    return total_visited, total_changed


def _pct(part: int, whole: int) -> str:
    return "0" if not whole else "%.0f" % (100.0 * part / whole)


def stats() -> int:
    """Report how the parser did on the stored offers."""
    store = _store()
    by_kind: Counter = Counter()
    by_confidence: Counter = Counter()
    vouchers = 0
    hourly_known = 0
    total = 0

    for base_name in store.get_scraping_names():
        for offer in _offers_of(store, base_name):
            total += 1
            by_kind[str(offer.get("salary_kind", "absent"))] += 1
            by_confidence[str(offer.get("salary_confidence", "absent"))] += 1
            if offer.get("salary_hourly_estimate") is not None:
                hourly_known += 1
            if offer.get("meal_voucher_mentioned"):
                vouchers += 1

    logger.info("Offres analysees : %d", total)
    logger.info("")
    logger.info("Par nature de remuneration :")
    for kind, count in by_kind.most_common():
        logger.info("  %-10s %5d  (%s%%)", kind, count, _pct(count, total))
    logger.info("")
    logger.info("Par confiance :")
    for confidence, count in by_confidence.most_common():
        logger.info("  %-10s %5d  (%s%%)", confidence, count, _pct(count, total))
    logger.info("")
    logger.info("Equivalent horaire connu : %d / %d", hourly_known, total)
    logger.info("Cheques-repas mentionnes : %d / %d", vouchers, total)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m python.salary",
        description="Reanalyse les remunerations des offres deja stockees.",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="recalcule les champs derives sur les offres en cache",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="compte les offres par nature et par confiance",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.stats:
        return stats()
    if args.backfill:
        visited, changed = backfill_all()
        logger.info("")
        logger.info("Total : %d offres lues, %d mises a jour.", visited, changed)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())