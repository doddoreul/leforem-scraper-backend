# -*- coding: utf-8 -*-
"""Unit tests for python.core (+ scraper.update_history pure logic).

Runs offline on fictional data. No network, no real files.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

from python import core
from python import scraper


def make_offer(number, title="Titre", company="Entreprise",
               location="Liege", contract_type="Duree indeterminee",
               schedule="Temps plein", salary="", pay="",
               published_on="16-09-26", date_publication="16/09/2026"):
    return {
        "number": number,
        "offer_title": title,
        "company": company,
        "location": location,
        "contract_type": contract_type,
        "schedule": schedule,
        "salary": salary,
        "pay": pay,
        "published_on": published_on,
        "date_publication": date_publication,
        "email": "contact@example.com",
        "description": "Description de " + str(number),
        "url": "",
        "summary": "",
        "is_new": False,
    }


class TestDates(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(core.parse_forem_date("16/09/2026"), "2026-09-16")
        self.assertEqual(core.parse_forem_date("16-09-26"), "2026-09-16")
        self.assertEqual(
            core.parse_forem_date("2026-09-16T10:00:00+02:00"),
            "2026-09-16",
        )

    def test_relative_text_is_unknown(self):
        self.assertEqual(core.parse_forem_date("Publie aujourd'hui"), "")
        self.assertEqual(core.parse_forem_date(""), "")
        self.assertEqual(core.parse_forem_date(None), "")

    def test_days_between(self):
        self.assertEqual(
            core.days_between("2026-09-26", today=date(2026, 9, 26)), 0
        )
        self.assertEqual(
            core.days_between("2026-09-20", today=date(2026, 9, 26)), -6
        )
        self.assertEqual(core.days_between("garbage"), None)


class TestStates(unittest.TestCase):
    def test_three_scrape_scenario(self):
        # -- Scrape 1: three offers all new.
        previous = []
        current = [make_offer("100"), make_offer("200"), make_offer("300")]
        states, current_numbers = core.collect_states(
            previous, current, set()
        )
        self.assertEqual(sorted(states["new"]), ["100", "200", "300"])
        self.assertEqual(states["unchanged"], [])
        self.assertEqual(states["deleted"], [])
        self.assertEqual(len(current_numbers), 3)

        # Offer 200 disappears between scrape 1 and 2 -> keep it in the
        # deleted history for the reappearance detection.
        history = scraper.update_history(
            current, current[:1] + current[2:], {"offers": []}, "t2"
        )
        self.assertEqual(len(history["offers"]), 1)
        self.assertEqual(history["offers"][0]["number"], "200")
        self.assertTrue(history["offers"][0]["removed"])

        deleted_numbers = {str(o["number"]) for o in history["offers"]}

        # -- Scrape 2: 100 unchanged, 400 new, 200 and 300 gone.
        previous = [make_offer("100"), make_offer("300")]
        current = [make_offer("100"), make_offer("400")]
        states, current_numbers = core.collect_states(
            previous, current, deleted_numbers
        )
        self.assertEqual(states["unchanged"], ["100"])
        self.assertEqual(states["new"], ["400"])
        self.assertEqual(states["reappeared"], [])
        self.assertEqual(states["deleted"], ["300"])

        # -- Scrape 3: 200 is back, 100 and 400 unchanged.
        previous = [make_offer("100"), make_offer("400")]
        current = [make_offer("200"), make_offer("100"), make_offer("400")]
        states, current_numbers = core.collect_states(
            previous, current, deleted_numbers
        )
        self.assertEqual(states["reappeared"], ["200"])
        self.assertEqual(states["unchanged"], ["100", "400"])
        self.assertEqual(states["deleted"], [])

    def test_offers_in_current_counts_as_current(self):
        current = [make_offer("1")]
        states, current_numbers = core.collect_states([], current, set())
        self.assertEqual(current_numbers, {"1"})
        self.assertEqual(states["deleted"], [])

    def test_a_tombstoned_offer_that_returns_is_reappeared(self):
        previous = [
            make_offer("1"),
            {"number": "2", "removed": True, "offer_state": "deleted"},
        ]
        current = [make_offer("1"), make_offer("2")]
        states, _ = core.collect_states(previous, current, set())
        self.assertEqual(states["reappeared"], ["2"])
        self.assertEqual(states["unchanged"], ["1"])
        self.assertEqual(states["deleted"], [])

    def test_summarize(self):
        states = {
            "new": ["1"],
            "unchanged": ["3"],
            "reappeared": [],
            "deleted": ["4"],
        }
        summary = core.summarize_scrape(states, total=3)
        self.assertEqual(summary["nouvelles"], 1)
        self.assertEqual(summary["inchangees"], 1)
        self.assertEqual(summary["supprimees"], 1)
        self.assertEqual(summary["total_offres"], 3)


class TestTombstones(unittest.TestCase):
    def test_disappeared_offers_are_returned_marked_deleted(self):
        previous = [make_offer("1"), make_offer("2")]
        current = [make_offer("1")]
        tombstones = core.tombstone_offers(previous, current, "t2")
        self.assertEqual([o["number"] for o in tombstones], ["2"])
        self.assertEqual(tombstones[0]["offer_state"], "deleted")
        self.assertTrue(tombstones[0]["removed"])
        self.assertFalse(tombstones[0]["is_new"])
        self.assertEqual(tombstones[0]["removed_on"], "t2")

    def test_the_original_removal_date_is_not_overwritten(self):
        previous = [{"number": "2", "removed": True, "removed_on": "t1"}]
        tombstones = core.tombstone_offers(previous, [], "t2")
        self.assertEqual(tombstones[0]["removed_on"], "t1")

    def test_a_still_present_offer_is_not_a_tombstone(self):
        previous = [make_offer("1")]
        self.assertEqual(core.tombstone_offers(previous, previous, "t2"), [])


class TestScrapeHistory(unittest.TestCase):
    def test_record_dedup_and_cap(self):
        scrapes = []
        core.record_scrape(scrapes, {"timestamp": "t1", "search": "",
                                     "total_offres": 3})
        core.record_scrape(scrapes, {"timestamp": "t1", "search": "",
                                     "total_offres": 5})
        self.assertEqual(len(scrapes), 1)
        self.assertEqual(scrapes[0]["total_offres"], 5)

        scrapes[:] = [{"timestamp": "s%d" % i, "search": "",
                       "n": i} for i in range(600)]
        core.record_scrape(scrapes, {"timestamp": "s0", "search": ""})
        self.assertLessEqual(len(scrapes), core.SCRAPES_MAX_ENTRIES)

    def test_record_returns_the_same_list_it_mutates(self):
        scrapes = []
        self.assertIs(core.record_scrape(scrapes, {"timestamp": "t1"}),
                      scrapes)
        self.assertEqual(scrapes, [{"timestamp": "t1"}])

    def test_a_different_search_keeps_both_entries(self):
        scrapes = []
        core.record_scrape(scrapes, {"timestamp": "t1", "search": "a"})
        core.record_scrape(scrapes, {"timestamp": "t1", "search": "b"})
        self.assertEqual([e["search"] for e in scrapes], ["a", "b"])

    def test_the_cap_drops_the_oldest_entries(self):
        # Exactly at the cap, then one more: the oldest entry must go.
        cap = core.SCRAPES_MAX_ENTRIES
        scrapes = [{"timestamp": "t%d" % i, "search": ""} for i in range(cap)]
        core.record_scrape(scrapes, {"timestamp": "new", "search": ""})

        self.assertEqual(len(scrapes), cap)
        self.assertEqual(scrapes[0]["timestamp"], "t1")
        self.assertEqual(scrapes[-1]["timestamp"], "new")


class TestSalaryAnalysis(unittest.TestCase):
    def test_hourly_values(self):
        self.assertEqual(
            core.extract_hourly_values(
                "Salaire brut de 18,00 eur à 19,25 eur de l'heure"
            ),
            [18.0, 19.25],
        )

    def test_monthly_is_not_hourly(self):
        self.assertEqual(
            core.extract_hourly_values("2400 eur par mois"), []
        )

    def test_analyze_salaries(self):
        offers = [
            make_offer("1", salary="18 eur de l'heure"),
            make_offer("2", pay="20,5 eur de l'heure"),
            make_offer("3", salary=""),
            "not-a-dict",
        ]
        result = core.analyze_salaries(offers)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["renseignees"], 2)
        self.assertIn(18.0, result["hourly"])
        self.assertIn(20.5, result["hourly"])

    def test_mean_median(self):
        self.assertEqual(core.mean([2, 4]), 3)
        self.assertEqual(core.median([1, 3, 9]), 3)
        self.assertEqual(core.median([1, 3, 9, 27]), 6)
        self.assertIsNone(core.mean([]))
        self.assertIsNone(core.median([]))


class TestBlacklistPolicy(unittest.TestCase):
    def test_legacy_list_format(self):
        blacklist = core.read_blacklist_data(["1880409"])
        self.assertIn("1880409", blacklist)
        # Legacy entries must effectively be skipped.
        self.assertFalse(core.should_fetch("1880409", blacklist))

    def test_single_miss_is_not_blacklisted(self):
        blacklist = {}
        core.note_miss(blacklist, "1", "t1")
        self.assertTrue(core.should_fetch("1", blacklist))

    def test_consecutive_misses_blacklist(self):
        blacklist = {}
        core.note_miss(blacklist, "1", "t1")
        core.note_miss(blacklist, "1", "t2")
        self.assertFalse(core.should_fetch("1", blacklist))

    def test_force_bypasses(self):
        blacklist = {}
        core.note_miss(blacklist, "1", "t1")
        core.note_miss(blacklist, "1", "t2")
        self.assertTrue(core.should_fetch("1", blacklist, force=True))

    def test_recovery_clears(self):
        blacklist = {}
        core.note_miss(blacklist, "1", "t1")
        core.note_miss(blacklist, "1", "t2")
        core.note_recovery(blacklist, "1")
        self.assertTrue(core.should_fetch("1", blacklist))
        self.assertNotIn("1", blacklist)

    def test_dict_format_is_normalized(self):
        data = {
            "7": {"first_seen": "t1", "last_seen": "t2", "attempts": 2},
            "8": "ignored",
        }
        blacklist = core.read_blacklist_data(data)
        self.assertEqual(blacklist["7"]["attempts"], 2)
        self.assertFalse(core.should_fetch("7", blacklist))
        self.assertEqual(blacklist["8"]["attempts"],
                         core.MISS_BLACKLIST_AFTER + 1)


class TestScraperHistoryUpdate(unittest.TestCase):
    def test_appearance_removes_from_history(self):
        previous = [make_offer("100"), make_offer("200")]
        current = [make_offer("100")]
        history = scraper.update_history(
            previous, current, {"offers": []}, "t1"
        )
        self.assertEqual(len(history["offers"]), 1)
        self.assertEqual(history["offers"][0]["number"], "200")
        self.assertEqual(history["offers"][0]["offer_state"], "deleted")

        # 200 comes back: it must leave the history.
        newer = [make_offer("100"), make_offer("200")]
        history = scraper.update_history(
            current, newer, history, "t2"
        )
        self.assertEqual(history["offers"], [])

    def test_duplicate_offers_not_duplicated_in_history(self):
        previous = [make_offer("100"), make_offer("200"), make_offer("200")]
        current = [make_offer("100")]
        history = scraper.update_history(
            previous, current, {"offers": []}, "t1"
        )
        self.assertEqual(len(history["offers"]), 1)


class TestScraperHelpers(unittest.TestCase):
    def test_normalize_published_on_uses_absolute(self):
        offer = make_offer("1")
        offer["published_on"] = "Publie aujourd'hui"
        offer["date_publication"] = "26/09/2026"
        scraper.normalize_published_on(offer)
        self.assertEqual(offer["published_on"], "26-09-26")

    def test_normalize_published_on_keeps_absolute(self):
        offer = make_offer("1", published_on="16-09-26")
        scraper.normalize_published_on(offer)
        self.assertEqual(offer["published_on"], "16-09-26")


class TestMergeDetails(unittest.TestCase):
    def test_fetched_wins_over_previous(self):
        previous = {"1": {"a": 1}, "2": {"a": 2}}
        fetched = {"1": {"a": 3}}
        merged = core.merge_details(
            previous=previous, fetched=fetched, keep={"1", "2", "3"}
        )
        self.assertEqual(merged["1"], {"a": 3})
        self.assertEqual(merged["2"], {"a": 2})

    def test_only_kept_numbers_are_preserved(self):
        previous = {"1": {"a": 1}, "2": {"a": 2}, "3": {"a": 3}}
        fetched = {"4": {"a": 4}}
        merged = core.merge_details(
            previous=previous, fetched=fetched, keep={"2", "4"}
        )
        self.assertEqual(set(merged.keys()), {"2", "4"})

    def test_fetched_number_added_even_if_not_in_keep(self):
        previous = {"1": {"a": 1}}
        fetched = {"9": {"a": 9}}
        merged = core.merge_details(
            previous=previous, fetched=fetched, keep={"1"}
        )
        self.assertEqual(merged["9"], {"a": 9})

    def test_no_payloads_are_dropped_as_none(self):
        previous = {"1": None, "2": {"a": 2}}
        fetched = {"2": None}
        merged = core.merge_details(
            previous=previous, fetched=fetched, keep={"1", "2"}
        )
        self.assertEqual(merged.get("1"), None)
        self.assertEqual(merged["2"], {"a": 2})


if __name__ == "__main__":
    unittest.main()