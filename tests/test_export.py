# -*- coding: utf-8 -*-
"""The JSON export is a faithful mirror of SQLite, written safely.

The database stays the source of truth, so these tests check that the file
describes the storage layer and not something else: deleted offers survive,
the write is atomic, and a collapsing offer count leaves the old file alone.
"""

import json
import os
import tempfile
import unittest

from python import config
from python import export


class FakeStore:
    """The bit of the storage interface the export uses."""

    def __init__(self, scrapings):
        self._scrapings = scrapings

    def get_scraping_names(self):
        return list(self._scrapings)

    def read_scraping(self, name):
        return self._scrapings.get(name)

    def read_details(self, name):
        return {}

    def read_companies(self):
        return {"employers": {"Ateliers du Sud": {"name": "Ateliers du Sud"}}}

    def read_history_scrapes(self):
        return [{"timestamp": "2026-09-26T08:15:00", "name": "a"}]


def a_scraping(label, offers):
    return {
        "label": label,
        "scrape_timestamp": "2026-09-26T08:15:00",
        "occupation_guid": "occ-1",
        "location_guid": "loc-1",
        "offers": offers,
    }


class BuildExportTest(unittest.TestCase):
    def test_one_entry_per_search(self):
        store = FakeStore({
            "b": a_scraping("Bravo", [{"number": "2"}]),
            "a": a_scraping("Alpha", [{"number": "1"}]),
        })
        payload = export.build_export(store)

        # Sorted by label, so the menu order is stable across exports.
        self.assertEqual([e["name"] for e in payload["searches"]], ["a", "b"])
        self.assertEqual(payload["offerCount"], 2)

    def test_the_search_metadata_the_selector_needs(self):
        store = FakeStore({"a": a_scraping("Alpha", [{"number": "1"}])})
        entry = export.build_export(store)["searches"][0]

        for key in ("name", "label", "scrape_timestamp", "occupationGuid",
                    "locationGuid", "offerCount", "offers"):
            self.assertIn(key, entry)

    def test_deleted_offers_are_kept_with_their_content(self):
        offers = [
            {"number": "1", "offer_state": "unchanged", "offer_title": "Vive"},
            {"number": "2", "offer_state": "deleted", "offer_title": "Partie"},
        ]
        store = FakeStore({"a": a_scraping("Alpha", offers)})
        payload = export.build_export(store)

        self.assertEqual(payload["offerCount"], 2)
        self.assertEqual(payload["deletedCount"], 1)
        kept = payload["searches"][0]["offers"][1]
        self.assertEqual(kept["offer_state"], "deleted")
        self.assertEqual(kept["offer_title"], "Partie")

    def test_the_offers_are_not_rewritten(self):
        offers = [{"number": "1", "contract_type": "CDI", "pay": {"min": 2000}}]
        store = FakeStore({"a": a_scraping("Alpha", offers)})
        payload = export.build_export(store)

        self.assertEqual(payload["searches"][0]["offers"], offers)

    def test_a_search_without_offers_does_not_break_the_export(self):
        store = FakeStore({"a": a_scraping("Alpha", None)})
        payload = export.build_export(store)

        self.assertEqual(payload["offerCount"], 0)
        self.assertEqual(payload["searches"][0]["offers"], [])

    def test_an_empty_database_exports_an_empty_file(self):
        payload = export.build_export(FakeStore({}))

        self.assertEqual(payload["searches"], [])
        self.assertEqual(payload["offerCount"], 0)

    def test_the_generated_date_is_present(self):
        payload = export.build_export(FakeStore({}))
        self.assertTrue(payload["generated_at"])

    def test_the_employer_index_travels_with_the_export(self):
        # The employer page has no server to read it from any more.
        payload = export.build_export(FakeStore({}))

        self.assertIn("Ateliers du Sud", payload["companies"]["employers"])

    def test_the_scrape_history_travels_with_the_export(self):
        payload = export.build_export(FakeStore({}))

        self.assertEqual(payload["scrapes"][0]["name"], "a")

    def test_an_empty_database_still_carries_both_indexes(self):
        # No search at all: the indexes are still present, so the pages that
        # read them do not have to special-case an empty database.
        payload = export.build_export(FakeStore({}))

        self.assertIsInstance(payload["companies"], dict)
        self.assertIsInstance(payload["scrapes"], list)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "raw.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _payload(self, count, deleted=0):
        offers = [
            {"number": str(i), "offer_state": "unchanged"} for i in range(count)
        ]
        for i in range(deleted):
            offers[i]["offer_state"] = "deleted"
        return {
            "version": 1,
            "generated_at": "2026-09-26T08:15:00",
            "offerCount": count,
            "deletedCount": deleted,
            "searches": [{"name": "a", "label": "A", "offers": offers}],
        }

    def test_the_file_is_written_and_readable(self):
        report = export.write_atomically(self.path, self._payload(12, 3), 0.5)

        self.assertEqual(report["offers"], 12)
        with open(self.path, encoding="utf-8") as handle:
            written = json.load(handle)
        self.assertEqual(written["offerCount"], 12)

    def test_no_temporary_file_is_left_behind(self):
        export.write_atomically(self.path, self._payload(12), 0.5)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_a_collapse_keeps_the_previous_file(self):
        export.write_atomically(self.path, self._payload(100), 0.5)
        with open(self.path, encoding="utf-8") as handle:
            before = handle.read()

        with self.assertRaises(ValueError):
            export.write_atomically(self.path, self._payload(10), 0.5)

        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    def test_a_small_drop_is_published(self):
        # A scrape that ages a few offers out is normal, not an accident.
        export.write_atomically(self.path, self._payload(100), 0.5)
        export.write_atomically(self.path, self._payload(90), 0.5)

        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["offerCount"], 90)

    def test_a_corrupted_file_is_not_trusted_as_a_reference(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ not json")

        # No usable reference, so the export is written rather than refused.
        report = export.write_atomically(self.path, self._payload(5), 0.5)
        self.assertEqual(report["offers"], 5)

    def test_line_endings_do_not_depend_on_the_machine(self):
        export.write_atomically(self.path, self._payload(3), 0.5)
        with open(self.path, "rb") as handle:
            self.assertNotIn(b"\r\n", handle.read())


class PathTest(unittest.TestCase):
    def test_the_export_is_not_written_under_data(self):
        # data/ is ignored by git, so GitHub Pages could not serve it there.
        path = config.raw_json_file()
        self.assertFalse(
            os.path.abspath(path).startswith(
                os.path.abspath(config.data_dir()) + os.sep
            )
        )
        self.assertTrue(path.endswith(".json"))


if __name__ == "__main__":
    unittest.main()
