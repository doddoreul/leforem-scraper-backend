# -*- coding: utf-8 -*-
"""End-to-end checks for the derived pay fields in a real run_scrape.

The Forem API is faked, so no network is used. What matters here is that the
derived fields reach the stored offer, that they stay out of content_hash, and
that the catch-up fills an offer cached before the fields existed without
marking it modified.

Run from the repository root:
    python -m unittest tests.test_salary_run -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)

# The JSON backend keeps this test close to the scraper's own fakes.
os.environ.setdefault("LEFOREM_STORAGE", "json")

from python import config  # noqa: E402
from python import core  # noqa: E402
from python import scraper  # noqa: E402
from python.salary import REMUNERATION_FIELDS  # noqa: E402


def iso_days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def make_detail(number: str, benefits=None, comment: str = "") -> dict:
    """One detail payload, with the pay text where a Forem offer would."""
    payload = {
        "numero": number,
        "titreOffre": "Electromecanicien",
        "descriptionJob": "<p>Vous rackserez.</p>",
        "nomEmployeur": "Start People",
        "datePublication": iso_days_ago(10),
        "metier": " electromecanicien ",
        "lieuxTravail": {"libelle": "LIEGE"},
        "typeContrat": "CDI",
        "benefitsComments": comment,
    }
    if benefits is not None:
        payload["benefits"] = benefits
    return payload


def make_listing(number: str) -> dict:
    """One search-result entry, in the shape the scraper expects."""
    return {
        "id": int(number),
        "number": number,
        "titre": "Electromecanicien",
        "publication": "Publie hier",
        "publication_label": "Il y a 3 jours",
        "fin": "2026-12-31T00:00:00+01:00",
        "debut": iso_days_ago(5) + "T00:00:00+02:00",
        "nomEmployeur": "Start People",
        "typeContrat": "CDI",
        "lieuxTravail": {"libelle": "LIEGE"},
        "url": "https://www.leforem.be/offres/" + number,
        "summary": "Vous rackserez des machines.",
    }


class SalaryRunTestCase(unittest.TestCase):
    """Runs run_scrape() against a temporary data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = config.DATA_DIR
        config.DATA_DIR = self.tmp.name

        self._api = {
            name: getattr(scraper, name)
            for name in ("search_offers", "fetch_detail", "get_session")
        }

        self.logs = []
        self.details: dict = {}
        self.search: list = []

        scraper.search_offers = self._search_offers
        scraper.fetch_detail = self._fetch_detail
        scraper.get_session = lambda: None

    def tearDown(self):
        config.DATA_DIR = self._saved
        for name, function in self._api.items():
            setattr(scraper, name, function)
        self.tmp.cleanup()

    def _search_offers(self, *args, **kwargs):
        return list(self.search)

    def _fetch_detail(self, session, number):
        return self.details[number]

    def _reporter(self):
        outer = self

        class Reporter:
            def log(self, message=""):
                outer.logs.append(message)

            def progress(self, done, total):
                pass

            def progress_done(self):
                pass

        return Reporter()

    def confirm(self, question=""):
        return True

    def run_scrape(self, **kwargs):
        options = {
            "occupation_guid": "occ-guid-1",
            "location_guid": "loc-guid-1",
            "base": "test",
            "label": "Metier / Liege",
            "reporter": self._reporter(),
            "confirm": self.confirm,
        }
        options.update(kwargs)
        return scraper.run_scrape(**options)

    def stored_offers(self) -> dict:
        from python.storage import get_storage

        payload = get_storage().read_scraping("test") or {}
        return {str(o.get("number")): o for o in (payload.get("offers") or [])}


class TestDerivedFieldsAreStored(SalaryRunTestCase):
    def test_the_fields_reach_the_stored_offer(self):
        self.search = [make_listing("1")]
        self.details = {"1": make_detail(
            "1",
            benefits={"basePay": "2 500 EUR brut par mois"},
        )}

        self.run_scrape()

        offer = self.stored_offers()["1"]
        self.assertEqual(offer["salary_kind"], "monthly")
        self.assertAlmostEqual(offer["salary_min"], 2500.0)
        self.assertAlmostEqual(offer["salary_max"], 2500.0)
        self.assertIs(offer["salary_gross"], True)
        self.assertEqual(offer["salary_confidence"], "high")
        self.assertAlmostEqual(offer["salary_hourly_estimate"],
                               2500.0 / (38.0 * 52.0 / 12.0), places=4)

    def test_the_text_wins_over_a_truncated_display(self):
        # pay is cut to 25 characters, which would hide the unit; the raw
        # benefits text is what gets parsed.
        self.search = [make_listing("1")]
        self.details = {"1": make_detail(
            "1",
            benefits={"basePay": "Salaire brut de 2 700 € par mois"},
        )}

        self.run_scrape()

        offer = self.stored_offers()["1"]
        self.assertEqual(offer["salary_kind"], "monthly")
        self.assertAlmostEqual(offer["salary_min"], 2700.0)

    def test_an_offer_without_pay_stays_unknown(self):
        self.search = [make_listing("1")]
        self.details = {"1": make_detail("1", comment="à discuter")}

        self.run_scrape()

        offer = self.stored_offers()["1"]
        self.assertEqual(offer["salary_kind"], "unknown")
        self.assertIsNone(offer["salary_min"])
        self.assertIsNone(offer["salary_hourly_estimate"])
        self.assertEqual(offer["salary_confidence"], "none")


class TestHashIsUnaffected(SalaryRunTestCase):
    def test_the_derived_fields_are_not_in_the_content_hash(self):
        for field in REMUNERATION_FIELDS:
            with self.subTest(field=field):
                self.assertNotIn(field, core.CONTENT_HASH_FIELDS)

    def test_the_hash_ignores_the_derived_fields(self):
        # Same Forem data, different derived fields: the hash must match.
        detail = make_detail("1", benefits={"basePay": "2 500 EUR brut/mois"})
        first = scraper.build_offer(detail)
        second = scraper.build_offer(detail)

        for field in REMUNERATION_FIELDS:
            self.assertEqual(first[field], second[field])

        # Blank every derived field, the way a pre-upgrade offer looks.
        stripped = dict(first)
        for field in REMUNERATION_FIELDS:
            stripped.pop(field, None)
        self.assertEqual(first["content_hash"],
                         core.compute_content_hash(stripped))

    def test_the_hash_rule_is_untouched(self):
        self.assertEqual(core.CONTENT_HASH_RULE, 2)


class TestBackfillOnCachedOffers(SalaryRunTestCase):
    """A cached offer from before the feature gets the fields, and stays
    unmodified."""

    def seed_cached_offer(self):
        """An offer stored without any derived field, as a previous run left it."""
        from python.storage import get_storage

        detail = make_detail("1", benefits={"basePay": "2 500 EUR brut/mois"})
        offer = scraper.build_offer(detail)
        for field in REMUNERATION_FIELDS:
            offer.pop(field, None)
        # What a run before the upgrade would have written.
        offer["content_hash"] = core.compute_content_hash(offer)
        offer["modified"] = False
        offer["first_seen_at"] = iso_days_ago(30)
        offer["is_new"] = False
        # The summary hash decides whether the offer is re-downloaded; an
        # unchanged one must go down the cached branch for this test to mean
        # anything.
        offer["listing_hash"] = scraper.listing_hash(offer)

        store = get_storage()
        payload = {
            "label": "Metier / Liege",
            "occupation_guid": "occ-guid-1",
            "location_guid": "loc-guid-1",
            "offers": [offer],
        }
        store.write_scraping("test", payload)
        store.write_details("test", {"1": detail})
        return offer, detail

    def cached_run(self, listing_hash):
        """A run whose summary is unchanged, so the offer comes from cache.

        The detail map is empty on purpose: if the scraper tried to download
        the offer the fake would raise, which proves nothing was requested.
        """
        listing = make_listing("1")
        listing["listing_hash"] = listing_hash
        self.search = [listing]
        self.details = {}
        return self.run_scrape()

    def test_the_backfill_fills_the_fields(self):
        seeded, _detail = self.seed_cached_offer()
        before_hash = self.stored_offers()["1"]["content_hash"]

        self.cached_run(seeded["listing_hash"])

        offer = self.stored_offers()["1"]
        self.assertEqual(offer["salary_kind"], "monthly")
        self.assertAlmostEqual(offer["salary_min"], 2500.0)
        # The hash, the modification flag and first_seen_at are untouched.
        self.assertEqual(offer["content_hash"], before_hash)
        self.assertFalse(offer["modified"])
        self.assertEqual(offer["first_seen_at"], iso_days_ago(30))

    def test_the_standalone_backfill_does_the_same(self):
        from python.salary import backfill_search
        from python.storage import get_storage

        self.seed_cached_offer()
        before = self.stored_offers()["1"]

        visited, changed = backfill_search(get_storage(), "test")

        self.assertEqual(visited, 1)
        self.assertEqual(changed, 1)
        after = self.stored_offers()["1"]
        self.assertEqual(after["salary_kind"], "monthly")
        self.assertEqual(after["content_hash"], before["content_hash"])
        self.assertFalse(after["modified"])

    def test_a_second_backfill_changes_nothing(self):
        from python.salary import backfill_search
        from python.storage import get_storage

        self.seed_cached_offer()
        backfill_search(get_storage(), "test")
        visited, changed = backfill_search(get_storage(), "test")

        self.assertEqual(visited, 1)
        self.assertEqual(changed, 0)

    def test_the_backfill_does_not_touch_the_follow_up(self):
        from python.salary import backfill_search
        from python.storage import get_storage

        self.seed_cached_offer()
        store = get_storage()
        store.write_tracking("test", "1", {"statut": "postule", "favori": True})

        backfill_search(store, "test")

        tracked = store.read_tracking("test")["1"]
        self.assertEqual(tracked["statut"], "postule")
        self.assertIs(tracked["favori"], True)


class TestStatsReportsTheStoredOffers(SalaryRunTestCase):
    def test_stats_counts_kinds_and_confidences(self):
        self.search = [make_listing("1"), make_listing("2")]
        self.details = {
            "1": make_detail("1", benefits={"basePay": "20 EUR brut par heure"}),
            "2": make_detail("2", benefits={"basePay": "2 500 EUR par mois"}),
        }

        self.run_scrape()

        from python.salary import stats

        # stats() logs and returns 0; the stored offers are what it reads.
        self.assertEqual(stats(), 0)
        offers = self.stored_offers()
        kinds = sorted(o["salary_kind"] for o in offers.values())
        self.assertEqual(kinds, ["hourly", "monthly"])


if __name__ == "__main__":
    unittest.main()