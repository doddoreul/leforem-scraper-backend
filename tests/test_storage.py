# -*- coding: utf-8 -*-
"""Tests for the storage abstraction.

The scraper reads and writes through :class:`python.storage.base.Storage`
so the same behaviour must hold for the JSON files and the SQLite database.
A single contract is exercised against both backends.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from python import config  # noqa: E402
from python.jsonio import write_json_atomically  # noqa: E402
from python.storage.factory import get_storage, reset_storage  # noqa: E402
from python.storage.json_store import JsonStorage  # noqa: E402
from python.storage.sqlite_store import SqliteStorage  # noqa: E402


class StorageContract:
    def make_storage(self):
        raise NotImplementedError

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = config.DATA_DIR
        config.DATA_DIR = self.tmp.name
        self.store = self.make_storage()

    def tearDown(self):
        config.DATA_DIR = self._saved
        self.tmp.cleanup()

    def test_scraping_round_trip(self):
        payload = {"label": "Metier / Liege", "offers": [{"number": "1"}]}
        self.store.write_scraping("liege", payload)
        self.assertEqual(self.store.read_scraping("liege"), payload)
        self.assertIn("liege", self.store.get_scraping_names())

    def test_missing_scraping_is_none(self):
        self.assertIsNone(self.store.read_scraping("absent"))

    def test_details_round_trip(self):
        details = {"1": {"numero": "1", "titreOffre": "X"}}
        self.store.write_details("liege", details)
        self.assertEqual(self.store.read_details("liege"), details)
        self.assertTrue(self.store.exists("details", base_name="liege"))

    def test_offer_details_lookup(self):
        self.store.write_details("liege", {"1": {"numero": "1"}})
        self.assertEqual(
            self.store.get_offer_details("liege", "1"), {"numero": "1"}
        )
        self.assertIsNone(self.store.get_offer_details("liege", "9"))

    def test_list_all_offers(self):
        self.store.write_scraping(
            "liege",
            {"offers": [{"number": "1"}, {"id": 2}, {"numero": "3"}]},
        )
        ids = [offer_id for offer_id, _ in self.store.list_all_offers("liege")]
        self.assertEqual(ids, ["1", "2", "3"])

    def test_history_scrapes_round_trip(self):
        scrapes = [{"timestamp": "t1"}, {"timestamp": "t2"}]
        self.store.write_history_scrapes(scrapes)
        self.assertEqual(self.store.read_history_scrapes(), scrapes)

    def test_history_modifications_round_trip(self):
        payload = {"1": [{"field": "titre", "old": "a", "new": "b"}]}
        self.store.write_history_modifications(payload)
        self.assertEqual(self.store.read_history_modifications(), payload)

    def test_history_offers_round_trip(self):
        payload = {
            "version": 1,
            "updated_timestamp": "t",
            "offers": [{"number": "1", "offer_state": "removed"}],
        }
        self.store.write_history_offers("liege", payload)
        self.assertEqual(self.store.read_history_offers("liege"), payload)

    def test_delete_scraping_removes_every_trace(self):
        self.store.write_scraping("liege", {"offers": []})
        self.store.write_details("liege", {"1": {"numero": "1"}})
        self.store.write_history_offers("liege", {"offers": []})

        moved = self.store.delete_scraping("liege")

        self.assertIsNone(self.store.read_scraping("liege"))
        self.assertNotIn("liege", self.store.get_scraping_names())
        self.assertEqual(self.store.read_details("liege"), {})
        self.assertEqual(self.store.read_history_offers("liege"), {})
        self.assertIn("data_liege.json", moved)

    def test_companies_round_trip(self):
        payload = {"records": [{"key": "acme", "name": "Acme"}]}
        self.store.write_companies(payload)
        self.assertEqual(self.store.read_companies(), payload)

    def test_blacklist_round_trip(self):
        blacklist = {"1": {"misses": 2, "first_miss_at": "t"}}
        self.store.write_blacklist(blacklist)
        self.assertEqual(self.store.read_blacklist(), blacklist)

    def test_scrape_state_round_trip_and_delete(self):
        self.store.write_scrape_state({"phase": "offers"})
        self.assertEqual(self.store.read_scrape_state(), {"phase": "offers"})
        self.store.delete_scrape_state()
        self.assertIsNone(self.store.read_scrape_state())


class TestJsonStorage(StorageContract, unittest.TestCase):
    def make_storage(self):
        return JsonStorage()

    def test_history_scrapes_unwraps_the_nested_legacy_format(self):
        # Older runs wrote the whole history document inside the "scrapes"
        # key: {"scrapes": {"version": 1, "scrapes": [...]}}.
        legacy = {"version": 1, "scrapes": [{"timestamp": "t1"}]}
        write_json_atomically(
            config.scrapes_file(), {"scrapes": legacy}
        )

        self.assertEqual(
            self.store.read_history_scrapes(), [{"timestamp": "t1"}]
        )

    def test_history_scrapes_survives_a_corrupted_file(self):
        write_json_atomically(config.scrapes_file(), {"scrapes": 3})

        self.assertEqual(self.store.read_history_scrapes(), [])

    def test_write_history_scrapes_stores_a_flat_list(self):
        # Regression: the writer used to nest the whole document under
        # "scrapes". The round-trip contract above cannot catch it because the
        # reader unwraps, so the raw file is inspected instead.
        self.store.write_history_scrapes([{"timestamp": "t1"}])

        with open(config.scrapes_file(), encoding="utf-8") as handle:
            written = json.load(handle)

        self.assertIsInstance(written["scrapes"], list)
        self.assertEqual(written["scrapes"], [{"timestamp": "t1"}])

    def test_write_then_read_flattens_a_nested_legacy_file(self):
        # The migration branch must not keep the nesting alive: reading a
        # legacy file and writing it back produces the flat format.
        legacy = {"version": 1, "scrapes": [{"timestamp": "t1"}]}
        write_json_atomically(config.scrapes_file(), {"scrapes": legacy})

        self.store.write_history_scrapes(
            self.store.read_history_scrapes()
        )

        with open(config.scrapes_file(), encoding="utf-8") as handle:
            written = json.load(handle)

        self.assertIsInstance(written["scrapes"], list)
        self.assertEqual(written["scrapes"], [{"timestamp": "t1"}])


class TestSqliteStorage(StorageContract, unittest.TestCase):
    def make_storage(self):
        db_path = os.path.join(self.tmp.name, "leforem.db")
        return SqliteStorage(db_path)

    def test_sqlite_uses_data_dir_when_no_path(self):
        self.store = SqliteStorage()
        self.store.write_blacklist({"1": {}})
        self.assertTrue(
            os.path.exists(os.path.join(config.DATA_DIR, "leforem.db"))
        )


class TestStorageFactory(unittest.TestCase):
    """The backend is chosen by LEFOREM_STORAGE, SQLite by default."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved_data_dir = config.DATA_DIR
        self._saved_env = os.environ.get("LEFOREM_STORAGE")
        config.DATA_DIR = self.tmp.name
        reset_storage()

    def tearDown(self):
        reset_storage()
        config.DATA_DIR = self._saved_data_dir
        if self._saved_env is None:
            os.environ.pop("LEFOREM_STORAGE", None)
        else:
            os.environ["LEFOREM_STORAGE"] = self._saved_env
        self.tmp.cleanup()

    def test_the_default_backend_is_sqlite(self):
        os.environ.pop("LEFOREM_STORAGE", None)
        reset_storage()
        self.assertIsInstance(get_storage(), SqliteStorage)

    def test_the_environment_can_select_the_json_backend(self):
        os.environ["LEFOREM_STORAGE"] = "json"
        reset_storage()
        self.assertIsInstance(get_storage(), JsonStorage)


if __name__ == "__main__":
    unittest.main()
