# -*- coding: utf-8 -*-
"""Tests for the candidate profile on both storage backends."""

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


def a_profile():
    return {
        "version": 1,
        "keywordsText": "nuit, maintenance",
        "keywords": ["nuit", "maintenance"],
        "hourlyRate": 15.5,
        "contractTypes": ["CDI"],
        "maxDistanceKm": 25,
        "updatedAt": "2026-10-05T12:00:00",
    }


class ProfileContract:
    """One profile for the whole app, whatever the backend."""

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

    def test_no_profile_yet(self):
        self.assertEqual(self.store.read_profile(), {})

    def test_round_trip(self):
        self.store.write_profile(a_profile())
        self.assertEqual(self.store.read_profile(), a_profile())

    def test_an_empty_profile_is_valid(self):
        # Every field is optional, so {} must survive the round trip.
        self.store.write_profile({})
        self.assertEqual(self.store.read_profile(), {})

    def test_writing_twice_replaces_the_profile(self):
        self.store.write_profile(a_profile())
        self.store.write_profile({"version": 1, "keywordsText": "jour"})
        found = self.store.read_profile()
        self.assertEqual(found["keywordsText"], "jour")
        self.assertNotIn("hourlyRate", found)

    def test_the_profile_is_readable_by_a_new_instance(self):
        self.store.write_profile(a_profile())
        reopened = self.make_storage()
        self.assertEqual(reopened.read_profile(), a_profile())

    def test_the_profile_is_not_per_search(self):
        # There is a single profile: no base name is involved anywhere.
        self.store.write_profile(a_profile())
        found = self.store.read_profile()
        self.assertEqual(found["contractTypes"], ["CDI"])


class TestJsonProfile(ProfileContract, unittest.TestCase):
    def make_storage(self):
        return JsonStorage()


class TestSqliteProfile(ProfileContract, unittest.TestCase):
    def make_storage(self):
        return SqliteStorage(os.path.join(self.tmp.name, "profile.db"))


if __name__ == "__main__":
    unittest.main()