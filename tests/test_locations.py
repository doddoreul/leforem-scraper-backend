# -*- coding: utf-8 -*-
"""Several workplaces on one offer must stay several places.

An offer may name "Arrondissement de Waremme, Arrondissement de Liège, Hannut".
The scraper now keeps them apart in `locations`, so the locality filter offers
and matches each one; `location` stays the joined string for display.
"""

import unittest

from python.scraper import build_offer, extract_location, extract_locations


class ExtractLocationsTest(unittest.TestCase):
    def test_a_single_place_stays_one(self):
        self.assertEqual(extract_locations(["Seraing"]), ["Seraing"])
        self.assertEqual(extract_locations([{"libelle": "Herstal"}]), ["Herstal"])

    def test_three_places_stay_three(self):
        found = extract_locations([
            "Arrondissement de Waremme",
            "Arrondissement de Liège",
            "Hannut",
        ])
        self.assertEqual(found, [
            "Arrondissement de Waremme",
            "Arrondissement de Liège",
            "Hannut",
        ])

    def test_a_dict_and_a_string_are_both_read(self):
        found = extract_locations([{"libelle": "Hannut"}, "Seraing"])
        self.assertEqual(found, ["Hannut", "Seraing"])

    def test_the_name_wins_over_the_other_keys(self):
        self.assertEqual(
            extract_locations([{"nom": "Namur", "libelle": "ignored"}]),
            ["Namur"],
        )

    def test_repeats_are_dropped(self):
        self.assertEqual(extract_locations(["Liège", "LIEGE ", "Liège"]),
                         ["Liège", "LIEGE"])

    def test_nothing_in_nothing_out(self):
        self.assertEqual(extract_locations(None), [])
        self.assertEqual(extract_locations([]), [])
        self.assertEqual(extract_locations(""), [])

    def test_a_single_dict_is_accepted(self):
        self.assertEqual(extract_locations({"ville": "Huy"}), ["Huy"])

    def test_the_joined_string_is_unchanged(self):
        places = ["Arrondissement de Waremme", "Hannut"]
        self.assertEqual(extract_location(places),
                         "Arrondissement de Waremme, Hannut")

    def test_the_joined_string_and_the_list_agree(self):
        raw = ["Seraing", "Hannut"]
        self.assertEqual(
            extract_location(raw), ", ".join(extract_locations(raw))
        )


class BuildOfferTest(unittest.TestCase):
    def test_the_offer_carries_both_forms(self):
        offer = build_offer({
            "numero": "1902",
            "lieuxTravail": ["Arrondissement de Waremme", "Hannut"],
        })
        self.assertEqual(offer["location"],
                         "Arrondissement de Waremme, Hannut")
        self.assertEqual(offer["locations"],
                         ["Arrondissement de Waremme", "Hannut"])

    def test_no_workplace_gives_an_empty_list(self):
        offer = build_offer({"numero": "1902"})
        self.assertEqual(offer["locations"], [])
        self.assertEqual(offer["location"], "")


if __name__ == "__main__":
    unittest.main()
