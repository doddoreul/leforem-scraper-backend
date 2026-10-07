# -*- coding: utf-8 -*-
"""Tests for scraper.run_scrape() — the blocking entry point of the scraper.

The Forem API is replaced by fakes, so no network is used. What is checked is
the behaviour the web interface relies on: one single stored version of each
offer, SHA-256 change detection with diffs, user data kept aside, the
12-month filter, the métier index, and a failed offer never stopping the run.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import builtins
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

import requests

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

os.environ.setdefault("LEFOREM_STORAGE", "json")

from python import config
from python import scraper


def iso_days_ago(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def make_detail(number, title="Electromecanicien", company="Start People",
                metier=" electromecanicien ", publication=None, description=""):
    return {
        "numero": number,
        "titreOffre": title,
        "descriptionJob": description or "<p>Vous rackserez.</p>",
        "nomEmployeur": company,
        "datePublication": publication or iso_days_ago(10),
        "metier": metier,
        "lieuxTravail": {"libelle": "LIEGE"},
        "typeContrat": "CDI",
    }


def make_listing(number, title="Electromecanicien", company="Start People",
                 publication="Publie hier", places=1):
    """One entry of the search result, with the fields Forem actually sends."""
    return {
        "id": int(number),
        "numero": number,
        "titre": title,
        # Relative text: "Publie hier" becomes "Il y a 3 jours" over time.
        "publication": publication,
        "fin": "2026-12-31T00:00:00+01:00",
        "debut": iso_days_ago(5) + "T00:00:00+02:00",
        "nomEmployeur": company,
        "typeContrat": "Duree indeterminee",
        "regimeTravail": "Temps plein",
        "lieuxTravail": ["Seraing"],
        "langues": ["FR"],
        "nombrePostes": places,
        "logo": "logo-" + number,
        "secteursActivite": ["Industrie"],
    }


class RunScrapeTestCase(unittest.TestCase):
    """Runs run_scrape() against a temporary data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = config.DATA_DIR
        config.DATA_DIR = self.tmp.name

        # The server never prompts: any read from stdin is a bug here.
        self._input = builtins.input
        builtins.input = self._forbidden_input

        self._api = {
            name: getattr(scraper, name)
            for name in ("search_offers", "fetch_detail", "get_session")
        }

        self.logs = []
        self.reporter = self._reporter()

        scraper.search_offers = self._search_offers
        scraper.fetch_detail = self._fetch_detail
        scraper.get_session = lambda: None

        self.details = {}
        self.search = []
        self.failures = {}
        self.confirmed = []
        self.fetched_numbers = []

    def tearDown(self):
        config.DATA_DIR = self._saved
        builtins.input = self._input
        for name, function in self._api.items():
            setattr(scraper, name, function)
        self.tmp.cleanup()

    # -- fakes ---------------------------------------------------

    def _forbidden_input(self, *args, **kwargs):
        raise AssertionError("run_scrape() must never read stdin")

    def _reporter(self):
        reporter = self

        class Reporter:
            def log(self, message=""):
                reporter.logs.append(str(message))

            def progress(self, done, total):
                reporter.logs.append(f"{done}/{total}")

            def progress_done(self):
                pass

        return Reporter()

    def _search_offers(self, session, limit=None, occupation_guid=None,
                       location_guid=None, log=print, progress=None):
        log("Search page 1...")
        results = []
        for entry in self.search:
            entry = dict(entry)
            listing = entry.get("listing")
            if listing is not None:
                # The real search hashes the Forem entry before returning it.
                entry["listing_hash"] = scraper.listing_hash(listing)
            results.append(entry)
        return results

    def _fetch_detail(self, session, number):
        self.fetched_numbers.append(number)
        error = self.failures.get(number)
        if error:
            raise error
        return self.details[number]

    def confirm(self, question):
        self.confirmed.append(question)
        return True

    def run_scrape(self, **kwargs):
        options = {
            "occupation_guid": "occ-guid-1",
            "location_guid": "loc-guid-1",
            "base": "test",
            "label": "Metier / Liege",
            "reporter": self.reporter,
            "confirm": self.confirm,
        }
        options.update(kwargs)
        return scraper.run_scrape(**options)

    def data_path(self):
        return os.path.join(self.tmp.name, "data_test.json")

    def read_data(self):
        with open(self.data_path(), "r", encoding="utf-8") as handle:
            return json.load(handle)

    def offers_by_number(self):
        return {
            offer["number"]: offer for offer in self.read_data()["offers"]
        }


class TestFirstRun(RunScrapeTestCase):
    def test_writes_offers_history_and_metier_index(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1", metier="Electromecanicien")}

        summary = self.run_scrape()

        self.assertEqual(summary["status"], "done")
        self.assertEqual(summary["total_offres"], 1)
        self.assertEqual(summary["nouvelles"], 1)
        self.assertEqual(summary["modifiees"], 0)

        data = self.read_data()
        self.assertEqual(data["occupation_guid"], "occ-guid-1")
        self.assertEqual(data["label"], "Metier / Liege")
        self.assertEqual(list(data["metier_index"]), ["Electromecanicien"])
        self.assertEqual(data["metier_index"]["Electromecanicien"], ["1"])

        offer = data["offers"][0]
        self.assertEqual(len(offer["content_hash"]), 64)
        self.assertIn("first_seen_at", offer)
        self.assertIn("last_seen_at", offer)
        self.assertIn("last_scraped_at", offer)
        self.assertFalse(offer["modified"])
        self.assertEqual(offer["diff"], {})

        for name in (
            "historique_test.json",
            "historique_scrapes.json",
            "details_test.json",
            "blacklist.json",
        ):
            self.assertTrue(
                os.path.exists(os.path.join(self.tmp.name, name)), name
            )

    def test_the_run_reports_the_storage_not_the_json_files(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}

        self.run_scrape()

        self.assertTrue(
            any(line.startswith("Storage:") for line in self.logs)
        )
        self.assertTrue(
            any(line.startswith("Storage updated:") for line in self.logs)
        )
        for line in self.logs:
            self.assertNotIn("data_test.json", line)
            self.assertNotIn("details_test.json", line)
            self.assertNotIn("historique_test.json", line)

    def test_first_seen_at_is_not_overwritten_by_a_later_run(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}

        self.run_scrape()
        first_seen = self.read_data()["offers"][0]["first_seen_at"]

        self.run_scrape()
        offer = self.read_data()["offers"][0]
        self.assertEqual(offer["first_seen_at"], first_seen)
        self.assertEqual(offer["modified"], False)

    def test_new_offers_are_confirmed_without_prompting(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}

        self.run_scrape()

        self.assertEqual(len(self.confirmed), 1)
        self.assertIn("1 nouvelles annonces", self.confirmed[0])

    def test_declining_the_new_offers_keeps_the_files(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}

        summary = self.run_scrape(confirm=lambda question: False)

        self.assertEqual(summary["status"], "cancelled")
        self.assertFalse(os.path.exists(self.data_path()))


class TestChangeDetection(RunScrapeTestCase):
    def seed(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}
        self.run_scrape()

    def test_known_offers_are_kept_from_the_cache(self):
        # Default run: an offer already stored is not downloaded again,
        # it is simply carried over.
        self.seed()
        self.details.clear()

        summary = self.run_scrape()

        self.assertEqual(summary["cached"], 1)
        self.assertEqual(summary["fetched"], 0)
        self.assertEqual(summary["total_offres"], 1)
        self.assertEqual(
            self.read_data()["offers"][0]["offer_title"], "Electromecanicien"
        )

    def test_identical_hash_means_no_change(self):
        self.seed()
        summary = self.run_scrape(refresh=True)

        self.assertEqual(summary["modifiees"], 0)
        offer = self.read_data()["offers"][0]
        self.assertFalse(offer["modified"])
        self.assertEqual(offer["diff"], {})

    def test_new_hash_marks_the_offer_and_stores_the_diff(self):
        self.seed()
        previous_hash = self.read_data()["offers"][0]["content_hash"]

        self.details["1"] = make_detail(
            "1", title="Electromecanicien industriel H/F"
        )
        summary = self.run_scrape(refresh=True)

        self.assertEqual(summary["modifiees"], 1)
        offer = self.read_data()["offers"][0]
        self.assertTrue(offer["modified"])
        self.assertNotEqual(offer["content_hash"], previous_hash)
        self.assertEqual(
            offer["diff"]["offer_title"],
            ["Electromecanicien", "Electromecanicien industriel H/F"],
        )
        self.assertIn("modified_at", offer)

        # A single version of the offer is kept, with the new data.
        self.assertEqual(len(self.read_data()["offers"]), 1)
        self.assertEqual(
            offer["offer_title"], "Electromecanicien industriel H/F"
        )

    def test_modified_flag_is_reset_on_the_next_unchanged_run(self):
        self.seed()
        self.details["1"] = make_detail("1", title="Nouveau titre")
        self.run_scrape(refresh=True)

        summary = self.run_scrape(refresh=True)

        self.assertEqual(summary["modifiees"], 0)
        self.assertFalse(self.read_data()["offers"][0]["modified"])

    def test_user_data_survives_an_update(self):
        self.seed()
        path = self.data_path()
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data["offers"][0]["favorite"] = True
        data["offers"][0]["notes"] = "Postule le 12/09"
        data["offers"][0]["tags"] = ["urgent"]
        data["offers"][0]["status"] = "postule"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

        self.details["1"] = make_detail("1", title="Titre revise")
        self.run_scrape(refresh=True)

        offer = self.read_data()["offers"][0]
        self.assertTrue(offer["modified"])
        self.assertTrue(offer["favorite"])
        self.assertEqual(offer["notes"], "Postule le 12/09")
        self.assertEqual(offer["tags"], ["urgent"])
        self.assertEqual(offer["status"], "postule")
        self.assertEqual(offer["offer_title"], "Titre revise")

    def test_user_data_is_kept_when_nothing_changed(self):
        self.seed()
        path = self.data_path()
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data["offers"][0]["favorite"] = True
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

        self.run_scrape(refresh=True)

        self.assertTrue(self.read_data()["offers"][0]["favorite"])


class TestTemporalFilter(RunScrapeTestCase):
    def test_offers_older_than_12_months_are_left_out(self):
        """Offers older than 1 year are filtered out before downloading details."""
        self.search = [
            {"number": "1", "published_on": iso_days_ago(10)},
            {"number": "2", "published_on": iso_days_ago(400)},
        ]
        self.details = {
            "1": make_detail("1"),
            "2": make_detail("2", publication=iso_days_ago(400)),
        }

        summary = self.run_scrape()

        # The filter is applied before downloading, so hors_periode is 0
        # and the old offer is simply not in the listing.
        self.assertEqual(summary["hors_periode"], 0)
        self.assertEqual(summary["total_offres"], 1)
        self.assertEqual(list(self.offers_by_number()), ["1"])

    def test_offers_older_than_12_months_not_downloaded(self):
        """Offers older than 1 year are filtered out before downloading details."""
        self.search = [
            {"number": "1", "published_on": iso_days_ago(10)},
            {"number": "2", "published_on": iso_days_ago(400)},
        ]
        self.details = {
            "1": make_detail("1"),
            "2": make_detail("2", publication=iso_days_ago(400)),
        }

        summary = self.run_scrape()

        # Offer 2 should not be in the listing at all (filtered before download)
        self.assertEqual(summary["total_offres"], 1)
        self.assertEqual(list(self.offers_by_number()), ["1"])

    def test_offers_without_a_usable_date_are_kept(self):
        self.search = [{"number": "1", "published_on": "Publie aujourd'hui"}]
        self.details = {"1": make_detail("1")}

        summary = self.run_scrape()

        self.assertEqual(summary["total_offres"], 1)


class TestDeletedOffersAreKept(RunScrapeTestCase):
    """A disappeared offer stays in the listing, marked "Supprimée"."""

    def seed_two(self):
        self.search = [
            {"number": "1", "published_on": iso_days_ago(5)},
            {"number": "2", "published_on": iso_days_ago(5)},
        ]
        self.details = {"1": make_detail("1"), "2": make_detail("2")}
        self.run_scrape()

    def test_a_disappeared_offer_stays_in_the_listing(self):
        self.seed_two()
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]

        summary = self.run_scrape()

        self.assertEqual(summary["supprimees"], 1)
        offers = self.offers_by_number()
        self.assertEqual(set(offers), {"1", "2"})
        self.assertEqual(offers["2"]["offer_state"], "deleted")
        self.assertTrue(offers["2"]["removed"])
        self.assertFalse(offers["2"]["is_new"])
        self.assertEqual(offers["2"]["offer_title"], "Electromecanicien")

        # The raw Forem payload of the deleted offer is kept too.
        self.assertIn("2", scraper._storage.read_details("test"))

    def test_a_deleted_offer_that_returns_is_marked_reappeared(self):
        self.seed_two()
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.run_scrape()

        self.search = [
            {"number": "1", "published_on": iso_days_ago(5)},
            {"number": "2", "published_on": iso_days_ago(5)},
        ]
        summary = self.run_scrape()

        self.assertEqual(summary["reapparues"], 1)
        self.assertEqual(summary["supprimees"], 0)
        offer = self.offers_by_number()["2"]
        self.assertEqual(offer["offer_state"], "reappeared")
        self.assertTrue(offer["is_new"])
        self.assertNotIn("removed", offer)


class TestErrorHandling(RunScrapeTestCase):
    def test_a_failed_offer_does_not_stop_the_run(self):
        self.search = [
            {"number": "1", "published_on": iso_days_ago(5)},
            {"number": "2", "published_on": iso_days_ago(5)},
        ]
        self.details = {
            "1": make_detail("1"),
            "2": make_detail("2"),
        }
        response = requests.Response()
        response.status_code = 500
        self.failures["2"] = requests.HTTPError(
            "500 Server Error", response=response
        )

        summary = self.run_scrape()

        self.assertEqual(summary["erreurs"], 1)
        self.assertEqual(summary["total_offres"], 1)
        self.assertEqual(list(self.offers_by_number()), ["1"])
        self.assertIn("Offer 2", summary["erreur_details"][0])

        # The failed number is remembered for the next run.
        with open(
            os.path.join(self.tmp.name, "blacklist.json"), encoding="utf-8"
        ) as handle:
            blacklist = json.load(handle)
        self.assertIn("2", blacklist)

    def test_a_failed_offer_keeps_the_previous_data(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}
        self.run_scrape()

        response = requests.Response()
        response.status_code = 404
        self.failures["1"] = requests.HTTPError("404", response=response)

        summary = self.run_scrape(refresh=True)

        self.assertEqual(summary["erreurs"], 1)
        offer = self.read_data()["offers"][0]
        self.assertEqual(offer["offer_title"], "Electromecanicien")
        self.assertFalse(offer["is_new"])

    def test_a_global_failure_is_reported_to_the_caller(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]

        def boom(session, limit=None, occupation_guid=None,
                 location_guid=None, log=print, progress=None):
            raise requests.ConnectionError("réseau indisponible")

        scraper.search_offers = boom
        # A network failure must reach the caller instead of being hidden.
        with self.assertRaises(requests.ConnectionError):
            self.run_scrape()


class TestListingHash(RunScrapeTestCase):
    """Phase 1 lists the offers, phase 2 only opens the ones worth opening.

    The search returns a summary of every offer; its hash is compared with the
    one stored by the previous run to know, before downloading anything,
    whether an offer changed.
    """

    def seed(self, count=3):
        self.search = []
        for index in range(1, count + 1):
            number = str(index)
            self.search.append({
                "number": number,
                "published_on": "Publie hier",
                "listing": make_listing(number),
            })
            self.details[number] = make_detail(number)
        self.run_scrape()
        self.fetched_numbers.clear()

    def listing(self, number, **changes):
        """Replaces the stored summary of one offer."""
        entry = next(
            item for item in self.search if item["number"] == number
        )
        entry["listing"] = make_listing(number, **changes)

    def stored_listing_hash(self, number):
        return self.offers_by_number()[number]["listing_hash"]

    def test_the_summary_hash_is_stored_on_every_offer(self):
        self.seed()
        for number in ("1", "2", "3"):
            self.assertEqual(
                self.stored_listing_hash(number),
                scraper.listing_hash(make_listing(number)),
            )

    def test_an_unchanged_summary_is_not_opened_again(self):
        self.seed()

        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, [])
        self.assertEqual(summary["fetched"], 0)
        self.assertEqual(summary["cached"], 3)
        self.assertEqual(summary["resumes_modifies"], 0)

    def test_a_changed_summary_opens_the_offer_again(self):
        self.seed()
        self.listing("2", places=2)

        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, ["2"])
        self.assertEqual(summary["resumes_modifies"], 1)
        # The detail did not change: the offer is not marked as modified.
        self.assertEqual(summary["modifiees"], 0)
        self.assertEqual(
            self.stored_listing_hash("2"),
            scraper.listing_hash(make_listing("2", places=2)),
        )

    def test_a_changed_detail_marks_the_offer_as_modified(self):
        self.seed()
        self.listing("2", title="Electromecanicien industriel H/F")
        self.details["2"] = make_detail("2", title="Electromecanicien industriel H/F")

        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, ["2"])
        self.assertEqual(summary["modifiees"], 1)
        offer = self.offers_by_number()["2"]
        self.assertTrue(offer["modified"])
        self.assertIn("offer_title", offer["diff"])

    def test_the_relative_publication_text_does_not_trigger_a_download(self):
        # "Publie hier" becomes "Il y a 3 jours": the summary did not change.
        self.seed()
        for entry in self.search:
            entry["listing"]["publication"] = "Il y a 3 jours"

        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, [])
        self.assertEqual(summary["fetched"], 0)

    def test_a_new_logo_does_not_trigger_a_download(self):
        self.seed()
        for entry in self.search:
            entry["listing"]["logo"] = "logo-renove"

        self.assertEqual(self.run_scrape()["fetched"], 0)
        self.assertEqual(self.fetched_numbers, [])

    def test_a_relative_publication_alone_does_not_modify_the_offer(self):
        # The offer is opened again (its summary changed) but the detail is
        # identical: it must not be reported as modified.
        self.seed()
        self.listing("2", places=3)
        for entry in self.search:
            entry["published_on"] = "Il y a 3 jours"

        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, ["2"])
        self.assertEqual(summary["modifiees"], 0)
        self.assertEqual(summary["erreur_details"], [])

    def test_offers_stored_before_the_summary_existed_are_not_downloaded(self):
        self.seed()
        # Simulate data scraped before the summary hashes existed.
        data = self.read_data()
        for offer in data["offers"]:
            offer.pop("listing_hash", None)
        with open(self.data_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.fetched_numbers.clear()

        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, [])
        self.assertEqual(summary["resumes_enregistres"], 3)
        # The reference is now stored for the next run.
        for number in ("1", "2", "3"):
            self.assertEqual(
                self.stored_listing_hash(number),
                scraper.listing_hash(make_listing(number)),
            )

    def test_an_offer_stored_with_an_old_hash_rule_is_not_reported(self):
        self.seed()
        data = self.read_data()
        for offer in data["offers"]:
            offer.pop("hash_rule", None)
        with open(self.data_path(), "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.fetched_numbers.clear()
        self.listing("2", places=4)

        summary = self.run_scrape()

        # The hashing rules changed: this first comparison is skipped instead
        # of flagging every offer as modified.
        self.assertEqual(summary["modifiees"], 0)
        for offer in self.read_data()["offers"]:
            self.assertEqual(offer["hash_rule"], 2)
            self.assertFalse(offer["modified"])

    def test_a_change_is_detected_again_on_the_next_run(self):
        self.seed()
        self.listing("2", places=5)
        self.details["2"] = make_detail("2", title="Titre revise")

        self.assertEqual(self.run_scrape()["modifiees"], 1)
        self.assertEqual(self.fetched_numbers, ["2"])

        # The next change of the same offer is detected again.
        self.fetched_numbers.clear()
        self.listing("2", places=6)
        self.details["2"] = make_detail("2", title="Titre revise encore")

        self.assertEqual(self.run_scrape()["modifiees"], 1)
        self.assertEqual(self.fetched_numbers, ["2"])


class TestIncrementalRun(RunScrapeTestCase):
    """A normal run must only download the offers it does not already know.

    This is the behaviour the « Actualiser » button relies on: re-downloading
    the whole search every time would be pointless and slow.
    """

    def seed(self, numbers=("1", "2", "3")):
        self.search = [
            {"number": number, "published_on": iso_days_ago(5)}
            for number in numbers
        ]
        for number in numbers:
            self.details[number] = make_detail(number)
        self.run_scrape()
        self.fetched_numbers.clear()

    def add_offer(self, number, days_ago=1):
        self.search.append(
            {"number": number, "published_on": iso_days_ago(days_ago)}
        )
        self.details[number] = make_detail(number)

    def test_only_the_new_offers_are_downloaded(self):
        self.seed()
        self.add_offer("4")

        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, ["4"])
        self.assertEqual(summary["fetched"], 1)
        self.assertEqual(summary["cached"], 3)
        self.assertEqual(summary["nouvelles"], 1)
        self.assertEqual(summary["total_offres"], 4)

    def test_a_known_offer_is_never_downloaded_again(self):
        self.seed()
        self.add_offer("4")
        self.run_scrape()
        self.fetched_numbers.clear()

        # Nothing new on Forem: the search still runs, but no detail is
        # requested at all.
        summary = self.run_scrape()

        self.assertEqual(self.fetched_numbers, [])
        self.assertEqual(summary["fetched"], 0)
        self.assertEqual(summary["cached"], 4)
        self.assertEqual(summary["nouvelles"], 0)
        self.assertEqual(summary["modifiees"], 0)
        self.assertEqual(summary["total_offres"], 4)

    def test_the_offer_that_failed_before_is_downloaded_again(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details["1"] = make_detail("1")
        self.failures["1"] = requests.HTTPError("réseau instable")
        self.run_scrape()
        self.fetched_numbers.clear()
        self.failures.clear()
        self.add_offer("2")

        summary = self.run_scrape()

        self.assertEqual(sorted(self.fetched_numbers), ["1", "2"])
        self.assertEqual(summary["fetched"], 2)
        self.assertEqual(summary["nouvelles"], 2)

    def test_refresh_is_the_only_way_to_download_everything(self):
        self.seed()

        summary = self.run_scrape(refresh=True)

        self.assertEqual(sorted(self.fetched_numbers), ["1", "2", "3"])
        self.assertEqual(summary["fetched"], 3)
        self.assertEqual(summary["cached"], 0)

    def test_each_run_adds_one_line_to_the_scrape_history(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}

        # Two runs in the same second share their timestamp, and a re-run with
        # the same timestamp replaces its entry: the history is given one
        # second per run so both lines are kept.
        saved_stamp = scraper.now_iso_timestamp
        stamps = iter(["2026-01-01T10:00:00+01:00",
                       "2026-01-01T10:00:01+01:00"])
        scraper.now_iso_timestamp = lambda: next(stamps)
        try:
            self.run_scrape()
            self.run_scrape()
        finally:
            scraper.now_iso_timestamp = saved_stamp

        from python.storage import get_storage

        entries = get_storage().read_history_scrapes()
        self.assertEqual(len(entries), 2)
        self.assertEqual([e["nouvelles"] for e in entries], [1, 0])

    def test_a_real_run_writes_a_flat_scrapes_file(self):
        # End-to-end lock on the nesting regression: a full run used to leave
        # {"scrapes": {"version": 1, "scrapes": [...]}} on disk, which the
        # browser cannot read (companies.js expects meta.scrapes to be an
        # array). The raw file is inspected, not the storage layer, because
        # the reader unwraps and would hide the problem.
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}

        self.run_scrape()

        with open(config.scrapes_file(), encoding="utf-8") as handle:
            written = json.load(handle)

        self.assertIsInstance(written["scrapes"], list)
        self.assertEqual(len(written["scrapes"]), 1)
        self.assertEqual(written["scrapes"][0]["nouvelles"], 1)

    def test_a_second_run_appends_rather_than_nests(self):
        self.search = [{"number": "1", "published_on": iso_days_ago(5)}]
        self.details = {"1": make_detail("1")}

        # Distinct timestamps, otherwise record_srapes dedupes the two runs
        # into a single entry and nothing is appended.
        saved_stamp = scraper.now_iso_timestamp
        stamps = iter(["2026-01-01T10:00:00+01:00",
                       "2026-01-01T10:00:01+01:00"])
        scraper.now_iso_timestamp = lambda: next(stamps)
        try:
            self.run_scrape()
            self.run_scrape()
        finally:
            scraper.now_iso_timestamp = saved_stamp

        with open(config.scrapes_file(), encoding="utf-8") as handle:
            written = json.load(handle)

        self.assertIsInstance(written["scrapes"], list)
        self.assertEqual(len(written["scrapes"]), 2)

    def test_progress_counts_the_downloads_only(self):
        # The terminal must not show "3 / 3420" for a run that downloads
        # 3 offers out of 3420.
        self.seed()
        self.add_offer("4")
        self.logs.clear()

        summary = self.run_scrape()

        progress = [
            line for line in self.logs
            if line.count("/") == 1
            and line.split("/")[0].strip().isdigit()
            and line.split("/")[1].strip().isdigit()
        ]
        self.assertEqual(progress, ["1/1"])
        self.assertEqual(summary["fetched"], 1)


if __name__ == "__main__":
    unittest.main()
