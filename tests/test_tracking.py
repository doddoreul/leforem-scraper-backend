# -*- coding: utf-8 -*-
"""Tests for the tracking storage (the follow-up moved out of localStorage).

The follow-up data (statuses, remarks, favourites, priorities) is written
through :class:`python.storage.base.Storage` so it can live in SQLite or in
JSON files. A single contract is exercised against both backends.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from python import config  # noqa: E402
from python.storage.json_store import JsonStorage  # noqa: E402
from python.storage.sqlite_store import SqliteStorage  # noqa: E402


class TrackingContract:
    """Behaviour every storage backend must provide for tracking."""

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

    # -- reading and writing ---------------------------------------

    def test_an_unknown_search_is_empty(self):
        self.assertEqual(self.store.read_tracking("liege"), {})

    def test_write_then_read_round_trips(self):
        self.store.write_tracking(
            "liege", "1", {"statut": "postule", "favori": True}
        )
        found = self.store.read_tracking("liege")
        self.assertEqual(found["1"]["statut"], "postule")
        self.assertIs(found["1"]["favori"], True)

    def test_every_field_is_kept(self):
        fields = {
            "statut": "contacte",
            "statut_date": "2026-01-01T10:00:00+01:00",
            "remarque": "relancer mardi",
            "favori": True,
            "priorite": 2,
        }
        self.store.write_tracking("liege", "1", fields)
        found = self.store.read_tracking("liege")["1"]
        for key, value in fields.items():
            self.assertEqual(found[key], value, key)

    def test_several_offers_coexist(self):
        self.store.write_tracking("liege", "1", {"statut": "postule"})
        self.store.write_tracking("liege", "2", {"statut": "refuse"})
        found = self.store.read_tracking("liege")
        self.assertEqual(sorted(found), ["1", "2"])
        self.assertEqual(found["1"]["statut"], "postule")
        self.assertEqual(found["2"]["statut"], "refuse")

    def test_searches_do_not_see_each_other(self):
        self.store.write_tracking("liege", "1", {"statut": "postule"})
        self.assertEqual(self.store.read_tracking("namur"), {})

    # -- partial updates -------------------------------------------

    def test_a_partial_update_keeps_the_other_fields(self):
        # index.js sends only the field the user just changed; the rest of
        # the follow-up must survive.
        self.store.write_tracking(
            "liege", "1", {"statut": "postule", "remarque": "garder"}
        )
        self.store.write_tracking("liege", "1", {"remarque": "relancer"})
        found = self.store.read_tracking("liege")["1"]
        self.assertEqual(found["statut"], "postule")
        self.assertEqual(found["remarque"], "relancer")

    def test_an_unset_field_reads_as_none(self):
        self.store.write_tracking("liege", "1", {"statut": "postule"})
        found = self.store.read_tracking("liege")["1"]
        self.assertIsNone(found["remarque"])
        self.assertIsNone(found["priorite"])
        self.assertIs(found["favori"], False)

    def test_an_explicit_null_clears_the_field(self):
        # Clearing a status must actually clear it, not be ignored.
        self.store.write_tracking(
            "liege", "1", {"statut": "postule", "remarque": "x"}
        )
        self.store.write_tracking("liege", "1", {"remarque": None})
        found = self.store.read_tracking("liege")["1"]
        self.assertIsNone(found["remarque"])
        self.assertEqual(found["statut"], "postule")

    def test_favori_false_is_not_read_back_as_true(self):
        self.store.write_tracking("liege", "1", {"favori": True})
        self.store.write_tracking("liege", "1", {"favori": False})
        self.assertIs(self.store.read_tracking("liege")["1"]["favori"], False)

    def test_a_priorite_of_zero_survives(self):
        # 0 must not be mistaken for "absent".
        self.store.write_tracking("liege", "1", {"priorite": 0})
        self.assertEqual(self.store.read_tracking("liege")["1"]["priorite"], 0)

    def test_an_empty_update_changes_nothing(self):
        self.store.write_tracking("liege", "1", {"statut": "postule"})
        self.store.write_tracking("liege", "1", {})
        self.assertEqual(
            self.store.read_tracking("liege")["1"]["statut"], "postule"
        )

    def test_unknown_fields_are_ignored(self):
        self.store.write_tracking("liege", "1", {"statut": "postule", "zzz": 1})
        self.assertEqual(
            self.store.read_tracking("liege")["1"]["statut"], "postule"
        )

    # -- deleting ---------------------------------------------------

    def test_delete_removes_only_that_offer(self):
        self.store.write_tracking("liege", "1", {"statut": "postule"})
        self.store.write_tracking("liege", "2", {"statut": "refuse"})
        self.store.delete_tracking("liege", "1")
        found = self.store.read_tracking("liege")
        self.assertNotIn("1", found)
        self.assertIn("2", found)

    def test_deleting_an_unknown_offer_is_harmless(self):
        self.store.delete_tracking("liege", "404")
        self.assertEqual(self.store.read_tracking("liege"), {})

    # -- durability -------------------------------------------------

    def test_the_data_is_readable_by_a_new_instance(self):
        self.store.write_tracking("liege", "1", {"statut": "postule"})
        # A fresh store over the same location sees the same data: this is
        # what makes the follow-up survive a browser restart.
        reopened = self.make_storage()
        self.assertEqual(
            reopened.read_tracking("liege")["1"]["statut"], "postule"
        )


class TestJsonTracking(TrackingContract, unittest.TestCase):
    def make_storage(self):
        return JsonStorage()


class TestSqliteTracking(TrackingContract, unittest.TestCase):
    def make_storage(self):
        return SqliteStorage(os.path.join(self.tmp.name, "tracking.db"))


if __name__ == "__main__":
    unittest.main()