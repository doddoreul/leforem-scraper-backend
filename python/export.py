# -*- coding: utf-8 -*-
"""Export the offers held in SQLite to a single JSON file for the frontend.

The database stays the only source of truth. This only reads it, after the
scrape has finished, so the file describes the state of the database and not
the transient result of a run.

Deleted offers are kept: they carry ``offer_state="deleted"``, exactly as in
the database, so the frontend can hide them or show them as it already does.

    python -m python.export            write raw.json
    python -m python.export --check    only report what would be written
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from python import config
from python import core
from python.storage import get_storage

EXPORT_VERSION = 1

# A drop this large means something went wrong rather than that the offers
# simply aged out. The previous file is kept instead of publishing the drop.
DEFAULT_MIN_RATIO = 0.5


def build_details(store: Any, name: str) -> Dict[str, Any]:
    """The Forem payloads of one search, without the embedded logos.

    The offer sheet needs the raw payload. The employer logo is a base64
    image embedded in it, and those weigh 36 MB for 173 offers, which would
    make the file unusable in a browser. The sheet already falls back to the
    employer's initials when the logo is missing, so it is left out here and
    the mime type is kept for the fallback.
    """
    details = store.read_details(name)
    if not isinstance(details, dict):
        return {}

    out: Dict[str, Any] = {}
    for offer_id, payload in details.items():
        if not isinstance(payload, dict):
            continue
        if "logoEmployeur" in payload:
            payload = dict(payload)
            payload.pop("logoEmployeur", None)
        out[offer_id] = payload
    return out


def build_export(store: Any) -> Dict[str, Any]:
    """Everything the frontend needs, read from the storage layer.

    One entry per search, mirroring what /api/scrapings exposes so the
    frontend can keep its search selector without a server.
    """
    searches: List[Dict[str, Any]] = []
    total = 0

    for name in store.get_scraping_names():
        data = store.read_scraping(name)
        if not isinstance(data, dict):
            continue
        offers = data.get("offers")
        offers = offers if isinstance(offers, list) else []
        total += len(offers)
        searches.append({
            "name": name,
            "label": data.get("label", "") or "",
            "scrape_timestamp": data.get("scrape_timestamp", "") or "",
            "occupationGuid": data.get("occupation_guid", "") or "",
            "locationGuid": data.get("location_guid", "") or "",
            "offerCount": len(offers),
            "offers": offers,
            "details": build_details(store, name),
        })

    searches.sort(key=lambda entry: entry["label"] or entry["name"])

    deleted = sum(
        1
        for entry in searches
        for offer in entry["offers"]
        if isinstance(offer, dict) and offer.get("offer_state") == "deleted"
    )

    # The employer index and the scrape history are read from the same storage
    # layer: the frontend has no server to ask them from any more.
    companies = store.read_companies()
    scrapes = store.read_history_scrapes()

    return {
        "version": EXPORT_VERSION,
        "generated_at": core.now_iso_timestamp(),
        "offerCount": total,
        "deletedCount": deleted,
        "searches": searches,
        "companies": companies if isinstance(companies, dict) else {},
        "scrapes": scrapes if isinstance(scrapes, list) else [],
    }


def count_in_file(path: str) -> Optional[int]:
    """How many offers the file already on disk holds, None when unusable."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    count = payload.get("offerCount")
    return count if isinstance(count, int) else None


def write_atomically(path: str, payload: Dict[str, Any], min_ratio: float) -> Dict[str, Any]:
    """Write the file, and keep the old one when the offer count collapsed.

    An interrupted write leaves the previous valid file untouched: the new one
    is written beside it and only then renamed over it.
    """
    previous = count_in_file(path)
    current = int(payload.get("offerCount", 0))

    if previous and current < previous * min_ratio:
        raise ValueError(
            "Export refuse : %d offres contre %d dans le fichier existant. "
            "La limite est une chute de plus de %d%%."
            % (current, previous, round((1 - min_ratio) * 100))
        )

    body = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # newline="" keeps the line endings as written: the file is committed and
    # diffed, so it must not depend on the machine that produced it.
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)

    return {
        "offers": current,
        "previous": previous,
        "bytes": len(body.encode("utf-8")),
        "path": path,
    }


def report(payload: Dict[str, Any], written: Optional[Dict[str, Any]], path: str) -> None:
    searches = payload.get("searches") or []
    print("Recherches : %d" % len(searches))
    for entry in searches:
        offers = entry.get("offers") or []
        deleted = sum(
            1 for o in offers
            if isinstance(o, dict) and o.get("offer_state") == "deleted"
        )
        print("  %-52s %4d offres (%d supprimees)"
              % ((entry.get("label") or entry.get("name"))[:52],
                 len(offers), deleted))
    print("Offres dans l'export : %d" % payload.get("offerCount", 0))
    print("Dont supprimees       : %d" % payload.get("deletedCount", 0))
    if written:
        print("Fichier ecrit         : %s" % written["path"])
        print("Taille                : %.1f Ko" % (written["bytes"] / 1024))
    else:
        print("Fichier (non ecrit)   : %s" % path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exporte les offres de SQLite vers un JSON pour le frontend."
    )
    parser.add_argument(
        "--out",
        default=None,
        help="fichier a ecrire, par defaut raw.json a la racine du projet",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="rapporte le contenu sans ecrire le fichier",
    )
    parser.add_argument(
        "--min-ratio",
        type=float,
        default=DEFAULT_MIN_RATIO,
        help="chute du nombre d'offres refusee au-dela (0.5 par defaut)",
    )
    args = parser.parse_args(argv)

    path = args.out or config.raw_json_file()
    store = get_storage()
    payload = build_export(store)

    written = None
    if not args.check:
        try:
            written = write_atomically(path, payload, args.min_ratio)
        except ValueError as error:
            print("Export annule : %s" % error, file=sys.stderr)
            report(payload, None, path)
            return 1

    report(payload, written, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
