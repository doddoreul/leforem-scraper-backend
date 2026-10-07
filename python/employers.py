"""Index des employeurs, construit à partir des offres déjà scrapées.

Le script ne lance aucun scraping : il lit les fichiers `data_*.json`,
`historique_*.json` et `details_*.json` déjà présents dans `data/`, puis
écrit `data/companies.json` avec une entrée par `nomEmployeur`.

Les employeurs sont indexés sur le nom exact renvoyé par l'API : deux
noms voisins ("Acme" et "Acme SPRL") restent deux entrées distinctes, aucune
correspondance approximative n'est tentée.

Les coordonnées sont cumulées au fil des exécutions : une adresse ou un
e-mail trouvé lors d'un scraping est conservé même si l'offre disparaît
ensuite. Relancer le script après un nouveau scraping suffit à compléter
l'index.

Usage:
    python companies.py             # met à jour data/companies.json
    python companies.py --rebuild   # ignore l'index précédent
    python companies.py --stats     # affiche un résumé
"""

from __future__ import annotations

from typing import Any

import argparse
import logging
import re
from urllib.parse import urlsplit, urlunsplit

from python import config
from python import core
from python import scraper
from python.storage import get_storage

logger = logging.getLogger(__name__)

VERSION = config.VERSION

SCHEME_PREFIX = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
PHONE_KEEP = re.compile(r"[^\d+]")
PHONE_MIN_DIGITS = 8
DESCRIPTION_MAX_CHARS = 1000


# ============================================================
# TEXT HELPERS
# ============================================================

def company_key(value):
    """Index key for an employer: the employer name as published, with
    its whitespace collapsed. Nothing else is normalized, so two
    different names never end up in the same entry."""
    return scraper.clean_text(value)


def normalize_email(value):
    text = scraper.clean_text(value)
    if not text or not scraper.EMAIL_PATTERN.fullmatch(text):
        return ""
    return text.lower()


def normalize_phone(value):
    text = scraper.clean_text(value)
    if not text:
        return ""
    digits = PHONE_KEEP.sub("", text)
    if len(digits.replace("+", "")) < PHONE_MIN_DIGITS:
        return ""
    return text


def phone_identity(value):
    return PHONE_KEEP.sub("", value).lstrip("+")


def normalize_url(value):
    """Keep only the site itself (scheme + host).

    The Forem `webAddress` is usually a job-board page for that employer
    (e.g. `https://www.jobat.be/fr/emplois/electricien?job_6293847`),
    so the path, the query and the tracking parameters are dropped:
    only the domain is kept."""
    text = scraper.clean_text(value)
    if not text or " " in text:
        return ""
    scheme = SCHEME_PREFIX.match(text)
    if scheme and scheme.group(1).lower() not in ("http", "https"):
        return ""
    if "://" not in text:
        if "." not in text:
            return ""
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return urlunsplit((parts.scheme, parts.netloc.lower(), "", "", ""))


def url_domain(value):
    """The host of a normalized URL, e.g. ``ateliersdusud.be/jobs`` ->
    ``ateliersdusud.be``."""
    return normalize_url(value).split("//", 1)[-1]


def collapse_websites(values):
    """One URL per domain, so the many job-board pages of a same site
    (old or new) end up as a single entry."""
    by_domain: dict[str, Any] = {}
    for value in values:
        url = normalize_url(value)
        if not url:
            continue
        domain = url_domain(url)
        current = by_domain.get(domain)
        if current is None or len(url) < len(current):
            by_domain[domain] = url
    return [by_domain[domain] for domain in sorted(by_domain)]


def compose_address(postal):
    """Build a readable postal address from `howToApply.postalAddress`.

    The Forem fields are loose (the street may be missing while a free
    `adresse` list is filled, the house number may hold "44/46"), so the
    pieces are simply concatenated when they exist."""
    if not isinstance(postal, dict):
        return ""

    street = scraper.clean_text(postal.get("street"))
    number = scraper.clean_text(postal.get("numero"))
    if street and number and not street.endswith(number):
        street = f"{street} {number}"
    elif street and number:
        street = f"{street}, {number}"

    lines: list[str] = []
    if not street:
        extra = postal.get("adresse")
        if isinstance(extra, list):
            lines.extend(
                scraper.clean_text(item) for item in extra if scraper.clean_text(item)
            )
    else:
        lines.append(street)

    city = " ".join(
        part for part in (
            scraper.clean_text(postal.get("codePostal")),
            scraper.clean_text(postal.get("municipalite")),
        ) if part
    )
    if city:
        lines.append(city)

    country = scraper.clean_text(postal.get("pays"))
    if country and country.lower() not in ("belgique", "belgië", "belgium"):
        lines.append(country)

    return ", ".join(lines)


# ============================================================
# OFFER OBSERVATION
# ============================================================

def emails_from_offer(offer, detail):
    """Every e-mail address attached to an offer, from the structured
    contact block, the free text written by the employer and the postal
    block (which sometimes holds an e-mail instead of an organisation
    name)."""
    values = []

    def add(raw):
        email = normalize_email(raw)
        if email and email not in values:
            values.append(email)

    for candidate in (offer.get("email") if isinstance(offer, dict) else "",):
        for part in str(candidate or "").split(","):
            add(part)

    if isinstance(detail, dict):
        how = detail.get("howToApply")
        how = how if isinstance(how, dict) else {}
        add(how.get("email"))
        for field in scraper.EMAIL_TEXT_FIELDS:
            for match in scraper.EMAIL_PATTERN.findall(
                scraper.html_to_text(detail.get(field) or "")
            ):
                add(match)
        postal = how.get("postalAddress")
        if isinstance(postal, dict):
            add(postal.get("organisation"))

    return values


def contact_name(how):
    if not isinstance(how, dict):
        return ""
    name = scraper.clean_text(how.get("formattedName"))
    role = scraper.clean_text(how.get("fonctionPersonneContact"))
    if name and role and role.lower() not in name.lower():
        return f"{name} ({role})"
    return name or role


def observe_offer(offer, detail, base="", deleted=False):
    """Collect everything known about one offer for its employer."""
    if not isinstance(offer, dict):
        return None

    number = scraper.clean_text(offer.get("number"))
    detail = detail if isinstance(detail, dict) else {}
    how = detail.get("howToApply")
    how = how if isinstance(how, dict) else {}

    name = company_key(detail.get("nomEmployeur")) or company_key(
        offer.get("company")
    )
    if not name:
        return None

    # The publication channel ("nomPartenaire") is appended to the name:
    # some employers encode their information badly, and the channel
    # (Jobat, DaJobs, an agency, ...) is what tells two identical names
    # apart.
    partner = scraper.clean_text(detail.get("nomPartenaire"))
    key = f"{name} ({partner})" if partner else name

    if detail:
        published = core.parse_forem_date(
            detail.get("datePublication") or detail.get("dateDebutDiffusion")
        ) or core.parse_forem_date(
            offer.get("date_publication") or offer.get("published_on")
        )
        modified = core.parse_forem_date(detail.get("dateModification"))
    else:
        published = core.parse_forem_date(
            offer.get("date_publication") or offer.get("published_on")
        )
        modified = ""

    description = scraper.html_to_text(detail.get("descriptionEmployeur") or "")
    if len(description) > DESCRIPTION_MAX_CHARS:
        description = description[:DESCRIPTION_MAX_CHARS].rstrip() + "…"

    return {
        "name": key,
        "number": number,
        "base": base,
        "deleted": deleted,
        "title": scraper.clean_text(
            detail.get("titreOffre") or offer.get("offer_title")
        ),
        "location": scraper.extract_location(detail.get("lieuxTravail"))
        or scraper.clean_text(offer.get("location")),
        "sector": scraper.clean_text(detail.get("secteurActiviteEmployeur")),
        "partner": partner,
        "published": published,
        "modified": modified,
        "emails": emails_from_offer(offer, detail),
        "phone": normalize_phone(how.get("telephone")),
        "address": compose_address(how.get("postalAddress")),
        "website": normalize_url(how.get("webAddress")),
        "contact": contact_name(how),
        "description": description,
    }


# ============================================================
# EMPLOYER RECORD
# ============================================================

def new_record(name):
    return {
        "name": name,
        "offerCount": 0,
        "deletedCount": 0,
        "offers": [],
        "deletedOffers": [],
        "emails": [],
        "phones": [],
        "addresses": [],
        "locations": [],
        "websites": [],
        "contacts": [],
        "sectors": [],
        "partners": [],
        "description": "",
        "firstPublished": "",
        "lastModified": "",
        "edited": "",
    }


def add_unique(values, value):
    if not value:
        return
    folded = value.casefold()
    for existing in values:
        if existing.casefold() == folded:
            return
    values.append(value)


def add_phone(values, value):
    if not value:
        return
    identity = phone_identity(value)
    for existing in values:
        if phone_identity(existing) == identity:
            return
    values.append(value)


def keep_earliest(current, candidate):
    if not candidate:
        return current
    if not current:
        return candidate
    return min(current, candidate)


def keep_latest(current, candidate):
    if not candidate:
        return current
    if not current:
        return candidate
    return max(current, candidate)


def sort_offers(entries):
    """One entry per offer number, ordered by number."""
    by_number = {}
    for entry in entries:
        number = entry.get("number")
        if not number:
            continue
        by_number[number] = entry
    return [by_number[number] for number in sorted(by_number)]


def record_signature(record):
    """Offer numbers held by a record, used to recognize a record that
    was renamed by hand after a rebuild."""
    numbers = set()
    for field in ("offers", "deletedOffers"):
        for entry in list(record.get(field) or []):
            number = entry.get("number") if isinstance(entry, dict) else entry
            if number:
                numbers.add(str(number))
    return tuple(sorted(numbers))


def edited_rename_map(previous, fresh):
    """Map of scraped name -> name corrected by hand.

    A previous record flagged as `edited` and holding the same offers as
    a freshly built one passes its name on, so a correction made in the
    web interface survives the next run instead of leaving the scraped
    name behind as a duplicate.
    """
    employers = previous.get("employers") or {}
    by_signature: dict[str, Any] = {}
    for record in employers.values():
        if not isinstance(record, dict) or not record.get("edited"):
            continue
        name = record.get("name")
        if name:
            by_signature.setdefault(record_signature(record), name)

    rename: dict[str, str] = {}
    for name, record in fresh.items():
        target = by_signature.get(record_signature(record))
        if not target or target == name or target in rename.values():
            continue
        rename[name] = target
    return rename


def merge_observation(record, observation):
    """Fold one offer observation into its employer record."""
    entry = {
        "number": observation["number"],
        "base": observation["base"],
        "title": observation["title"],
        "published": observation["published"],
        "location": observation["location"],
    }
    if observation["deleted"]:
        record["deletedCount"] += 1
        if observation["number"]:
            record["deletedOffers"].append(entry)
    else:
        record["offerCount"] += 1
        if observation["number"]:
            record["offers"].append(entry)

    for email in observation["emails"]:
        add_unique(record["emails"], email)
    add_phone(record["phones"], observation["phone"])
    add_unique(record["addresses"], observation["address"])
    add_unique(record["locations"], observation["location"])
    add_unique(record["websites"], observation["website"])
    add_unique(record["contacts"], observation["contact"])
    add_unique(record["sectors"], observation["sector"])
    add_unique(record["partners"], observation["partner"])

    if len(observation["description"]) > len(record["description"]):
        record["description"] = observation["description"]

    record["firstPublished"] = keep_earliest(
        record["firstPublished"], observation["published"]
    )
    record["lastModified"] = keep_latest(
        record["lastModified"], observation["modified"]
    )


# ============================================================
# DATA SOURCES
# ============================================================

def scrape_bases():
    """Scrape names known to the storage ("" for the default scrape)."""
    return get_storage().get_scraping_names()


def offers_of(data):
    if isinstance(data, dict):
        offers = data.get("offers", [])
    elif isinstance(data, list):
        offers = data
    else:
        offers = []
    return [o for o in offers if isinstance(o, dict)]


def collect_observations():
    """Read every scrape (active + removed offers) and return the
    observations plus the list of scrape names."""
    store = get_storage()
    observations = []
    names = []

    for base in scrape_bases():
        names.append(base)
        details = store.read_details(base)
        history = store.read_history_offers(base)

        for offer in offers_of(store.read_scraping(base)):
            number = scraper.clean_text(offer.get("number"))
            observation = observe_offer(offer, details.get(number), base=base)
            if observation:
                observations.append(observation)

        for offer in offers_of(history):
            number = scraper.clean_text(offer.get("number"))
            observation = observe_offer(
                offer, details.get(number), base=base, deleted=True
            )
            if observation:
                observations.append(observation)

    return names, observations


# ============================================================
# INDEX BUILDING
# ============================================================

def load_previous(path=None):
    data = get_storage().read_companies()
    if isinstance(data, dict) and isinstance(data.get("employers"), dict):
        return data
    return {"employers": {}}


def build_index(previous=None, rebuild=False):
    """Build the employer index.

    Contact details already recorded are kept (emails, phones, addresses,
    websites, contacts, sectors, description) so information gathered
    earlier is not lost when an offer disappears; offer counts and dates
    always mirror the current data."""
    previous = previous or {}
    if rebuild:
        previous = {}

    names, observations = collect_observations()

    fresh: dict[str, Any] = {}
    for observation in observations:
        record = fresh.get(observation["name"])
        if record is None:
            record = new_record(observation["name"])
            fresh[observation["name"]] = record
        merge_observation(record, observation)

    rename = edited_rename_map(previous, fresh)
    moved = moved_record_names(previous, fresh)

    employers = {}
    kept_from_previous = 0

    for name, old in (previous.get("employers") or {}).items():
        if not isinstance(old, dict):
            continue
        # Skip a record left under the scraped name when the interface
        # corrected it by hand: the corrected one is kept instead.
        if name in rename or name in moved:
            continue
        record = new_record(scraper.clean_text(name) or company_key(old.get("name")))
        if not record["name"]:
            continue
        for field in (
            "emails", "phones", "addresses", "websites", "contacts",
            "sectors", "partners",
        ):
            for value in old.get(field) or []:
                value = scraper.clean_text(value)
                if field == "phones":
                    add_phone(record[field], value)
                else:
                    add_unique(record[field], value)
        record["description"] = scraper.clean_text(old.get("description"))
        record["locations"] = [
            scraper.clean_text(v) for v in (old.get("locations") or [])
            if scraper.clean_text(v)
        ]
        if old.get("edited"):
            record["edited"] = old["edited"]
        employers[record["name"]] = record
        kept_from_previous += 1

    for observation in observations:
        name = rename.get(observation["name"], observation["name"])
        record = employers.get(name)
        if record is None:
            record = new_record(name)
            employers[name] = record
        merge_observation(record, observation)

    for record in employers.values():
        record["offers"] = sort_offers(record["offers"])
        record["deletedOffers"] = sort_offers(record["deletedOffers"])
        record["websites"] = collapse_websites(record["websites"])

    stats = summarize(employers, len(observations))
    stats["employeursSansNom"] = len(observations) - sum(
        r["offerCount"] + r["deletedCount"] for r in employers.values()
    )
    stats["conserves"] = kept_from_previous

    return {
        "version": VERSION,
        "updated_timestamp": scraper.now_iso_timestamp(),
        "scrapes": names,
        "stats": stats,
        "employers": employers,
    }


def moved_record_names(previous, fresh):
    """Names of the previous entries whose offers are now filed under
    another key (the publication channel appeared, disappeared or
    changed). They are skipped instead of being copied, so no empty
    leftover stays behind.

    An entry keeping its own offers is kept as is, so the contacts
    gathered earlier survive, and a record corrected by hand is never
    dropped.
    """
    employers = previous.get("employers") or {}
    fresh_signatures = {
        name: set(record_signature(record)) for name, record in fresh.items()
    }

    moved = set()
    for name, record in employers.items():
        if not isinstance(record, dict) or record.get("edited"):
            continue
        numbers = set(record_signature(record))
        if not numbers:
            continue
        for other, other_numbers in fresh_signatures.items():
            if other != name and numbers & other_numbers:
                moved.add(name)
                break
    return moved


def summarize(employers, observations=0):
    """Recount the index. Tolerates hand-written records that miss a
    field, since the web interface can save a partial record."""
    records = [r for r in employers.values() if isinstance(r, dict)]
    emails = set()
    for record in records:
        emails.update(list(record.get("emails") or []))
    return {
        "employeurs": len(records),
        "avecEmail": sum(1 for r in records if list(r.get("emails") or [])),
        "avecTelephone": sum(
            1 for r in records if list(r.get("phones") or [])),
        "avecAdresse": sum(
            1 for r in records if list(r.get("addresses") or [])),
        "avecSite": sum(1 for r in records if list(r.get("websites") or [])),
        "emails": len(emails),
        "offresActives": sum(count(r, "offers") for r in records),
        "offresSupprimees": sum(count(r, "deletedOffers") for r in records),
        "observations": observations,
    }


def count(record, field):
    value = record.get(field)
    if isinstance(value, list):
        return len(value)
    return 0


def refresh_index(path=None):
    """Rebuild the employer index and write it.

    Called after each scraping so the "Entreprises" page stays in step with
    the offers without anyone running the script by hand. The previous index
    is merged in (the default behaviour of the command line), so contacts
    gathered earlier survive.

    Returns the new stats. Raises whatever the build raises: the caller
    decides whether a failure matters.
    """
    index = build_index(previous=load_previous())
    get_storage().write_companies(index)
    return index["stats"]


def main():
    parser = argparse.ArgumentParser(
        description="Indexe les employeurs des offres déjà scrapées"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Ignore l'index existant et repartir des seules données "
             "présentes dans data/.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Affiche un résumé de l'index dans le terminal.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="N'écrit pas le fichier (utile pour vérifier le résultat).",
    )
    args = parser.parse_args()

    previous = {} if args.rebuild else load_previous()
    index = build_index(previous=previous, rebuild=args.rebuild)

    stats = index["stats"]
    logger.info("Recherches lues : %s", ", ".join(n or "(défaut)" for n in index["scrapes"]))
    logger.info("Employeurs indexés : %s", stats["employeurs"])
    logger.info("  avec email      : %s (%s adresses)", stats["avecEmail"], stats["emails"])
    logger.info("  avec téléphone  : %s", stats["avecTelephone"])
    logger.info("  avec adresse    : %s", stats["avecAdresse"])
    logger.info("  avec site       : %s", stats["avecSite"])
    logger.info("Offres actives    : %s", stats["offresActives"])
    logger.info("Offres supprimées : %s", stats["offresSupprimees"])
    if stats["employeursSansNom"]:
        logger.info("Offres sans nom d'employeur : %s", stats["employeursSansNom"])

    if args.dry_run:
        logger.info("(--dry-run : aucun fichier écrit)")
        return

    get_storage().write_companies(index)
    logger.info("Écrit : %s", config.companies_file())

    if args.stats:
        for name, record in sorted(
            index["employers"].items(), key=lambda kv: kv[0].casefold()
        ):
            contacts = ", ".join(record["emails"]) or "-"
            logger.info("  %s | %s offre(s) | %s", name, record["offerCount"], contacts)


if __name__ == "__main__":
    main()
