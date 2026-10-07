# -*- coding: utf-8 -*-
"""Tests for the data/ layout helpers.

config.py is the single place that knows how a file of data/ is named, so
these checks guard the naming convention itself: the server, the scraper and
the trash folder all build their paths from it.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from python import config  # noqa: E402


class TestFileNames(unittest.TestCase):
    def test_one_search_owns_three_files(self):
        self.assertEqual(config.data_file_name("liege"), "data_liege.json")
        self.assertEqual(
            config.history_file_name("liege"), "historique_liege.json"
        )
        self.assertEqual(
            config.details_file_name("liege"), "details_liege.json"
        )

    def test_the_separator_is_always_there(self):
        # The default search must not collapse into "data.json", which would
        # no longer be matched by SCRAPE_FILE_RE.
        self.assertEqual(config.data_file_name(""), "data_.json")
        self.assertEqual(config.history_file_name(""), "historique_.json")

    def test_paths_live_in_the_data_folder(self):
        for path in config.delete_scrape_files("liege"):
            self.assertEqual(os.path.dirname(path), config.DATA_DIR)
            self.assertTrue(path.endswith(".json"))

    def test_scrape_files_returns_offers_and_history(self):
        data, history = config.scrape_files("liege")
        self.assertEqual(data, config.data_file("liege"))
        self.assertEqual(history, config.history_file("liege"))


class TestScrapeBase(unittest.TestCase):
    def test_it_recovers_the_search_name(self):
        self.assertEqual(config.scrape_base("data_liege.json"), "liege")
        self.assertEqual(config.scrape_base("data_.json"), "")

    def test_anything_else_is_not_a_search_file(self):
        # The shared files and the details/history files must not be mistaken
        # for a search, or the scrapings list would show phantom entries.
        for name in ("details_liege.json", "historique_liege.json",
                     "historique_scrapes.json", "historique_modifications.json",
                     "companies.json", "blacklist.json", "data_liege.txt",
                     ".gitignore", "data_liege.json.bak"):
            with self.subTest(name=name):
                self.assertIsNone(config.scrape_base(name))


class TestValidSearchName(unittest.TestCase):
    def test_ordinary_names_are_accepted(self):
        for name in ("test", "metier_liege", "a-b_C9", "Liege2026"):
            with self.subTest(name=name):
                self.assertTrue(config.valid_search_name(name))

    def test_a_name_cannot_escape_the_data_folder(self):
        # This is the only guard against path traversal: a name reaching the
        # filesystem must never contain a separator or a dot-dot.
        for name in ("..", ".", "../etc", "a/b", "a\\b", "con.sole", "",
                     "liege/../../x", "/abs"):
            with self.subTest(name=name):
                self.assertFalse(config.valid_search_name(name), name)

    def test_the_check_is_anchored(self):
        # A name that merely contains legal characters must not pass.
        for name in ("a b", "liege\n", "liege.json", "école"):
            with self.subTest(name=name):
                self.assertFalse(config.valid_search_name(name), name)


class TestScrapeFilePattern(unittest.TestCase):
    def test_it_matches_the_files_we_write(self):
        for name in ("data_liege.json", "details_liege.json"):
            with self.subTest(name=name):
                self.assertTrue(config.SCRAPE_FILE_RE.match(name))

    def test_it_rejects_everything_else(self):
        for name in ("historique_liege.json", "historique_scrapes.json",
                     "companies.json", "blacklist.json", "readme.md",
                     "data_liege.py"):
            with self.subTest(name=name):
                self.assertIsNone(config.SCRAPE_FILE_RE.match(name))


class TestDataDirIsNotCached(unittest.TestCase):
    def test_the_tests_can_redirect_it(self):
        # config.DATA_DIR is a module attribute on purpose: resolving it at
        # call time is what lets the tests point it at a temporary folder.
        saved = config.DATA_DIR
        try:
            config.DATA_DIR = "/tmp/leforem-redirected"
            self.assertEqual(
                os.path.dirname(config.data_file("x")), "/tmp/leforem-redirected"
            )
        finally:
            config.DATA_DIR = saved
        self.assertEqual(os.path.dirname(config.data_file("x")), saved)


if __name__ == "__main__":
    unittest.main()
