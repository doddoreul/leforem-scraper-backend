# -*- coding: utf-8 -*-
"""Unit tests for py (employer index).

Runs offline on fictional data. No network, no real files.

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

os.environ.setdefault("LEFOREM_STORAGE", "json")

from python import config
from python import scraper
from python.employers import (
    build_index,
    collapse_websites,
    company_key,
    compose_address,
    emails_from_offer,
    main,
    normalize_url,
    observe_offer,
    refresh_index,
)


def write_data(directory, file_name, payload):
    path = os.path.join(directory, file_name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return path


def make_offer(number, company="Start People", email=""):
    return {
        "number": number,
        "offer_title": "Electromecanicien H/F/X",
        "company": company,
        "email": email,
        "location": "HERSTAL",
        "date_publication": "16/09/2026",
    }


def make_detail(name="Start People", email="", phone="",
                postal=None, web="", contact="", fonction="",
                description="", sector="", partner="",
                published="2026-09-16T10:00:00+02:00",
                modified="2026-09-18T10:00:00+02:00"):
    how = {
        "formattedName": contact,
        "fonctionPersonneContact": fonction,
    }
    if email:
        how["email"] = email
    if phone:
        how["telephone"] = phone
    if postal:
        how["postalAddress"] = postal
    if web:
        how["webAddress"] = web
    return {
        "number": "",
        "titreOffre": "Electromecanicien H/F/X",
        "nomEmployeur": name,
        "datePublication": published,
        "dateModification": modified,
        "secteurActiviteEmployeur": sector,
        "nomPartenaire": partner,
        "descriptionEmployeur": description,
        "lieuxTravail": {"libelle": "HERSTAL"},
        "howToApply": how,
    }


class TempDataDir(unittest.TestCase):
    """Runs build_index() against a temporary data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = self.tmp.name
        self._saved = config.DATA_DIR
        config.DATA_DIR = self.data_dir

    def tearDown(self):
        config.DATA_DIR = self._saved
        self.tmp.cleanup()

    def seed(self, offers, details=None, history=None, base="test"):
        suffix = f"_{base}" if base else ""
        write_data(self.data_dir, f"data{suffix}.json", {"offers": offers})
        if details:
            write_data(
                self.data_dir,
                scraper.details_file(base),
                {"details": details},
            )
        if history:
            write_data(
                self.data_dir,
                scraper.scraper_files(base)[1],
                history,
            )


class TestKeys(unittest.TestCase):
    def test_whitespace_is_collapsed(self):
        self.assertEqual(
            company_key("  Start   People \n "), "Start People"
        )

    def test_similar_names_stay_distinct(self):
        # No fuzzy matching: two different names, two entries.
        self.assertNotEqual(
            company_key("Accent"),
            company_key("ACCENT CONSTRUCT - Accent"),
        )
        self.assertNotEqual(
            company_key("Actief Interim Liege"),
            company_key("Actief Interim Wavre"),
        )

    def test_case_is_preserved(self):
        self.assertEqual(company_key("Equans"), "Equans")
        self.assertNotEqual(
            company_key("Equans"), company_key("EQUANS")
        )


class TestEmails(unittest.TestCase):
    def test_structured_and_free_text(self):
        detail = make_detail(email="Info@StartPeople.be")
        detail["descriptionJob"] = (
            "Ecrire a jobs@startpeople.be ou 04 220 97 40."
        )
        emails = emails_from_offer({}, detail)
        self.assertEqual(
            emails, ["info@startpeople.be", "jobs@startpeople.be"]
        )

    def test_summary_email_is_split_and_validated(self):
        offer = make_offer("1", email="a@b.be, c@d.be, pas-un-email")
        self.assertEqual(
            emails_from_offer(offer, {}),
            ["a@b.be", "c@d.be"],
        )

    def test_postal_organisation_holding_an_email(self):
        detail = make_detail(postal={"organisation": "info@dhfields.be"})
        self.assertEqual(
            emails_from_offer({}, detail), ["info@dhfields.be"]
        )

    def test_postal_organisation_name_is_not_an_email(self):
        detail = make_detail(postal={"organisation": "DH Fields Service"})
        self.assertEqual(emails_from_offer({}, detail), [])

    def test_no_duplicates(self):
        detail = make_detail(email="info@startpeople.be")
        detail["descriptionJob"] = "Contact : info@startpeople.be."
        self.assertEqual(
            emails_from_offer(
                make_offer("1", email="info@startpeople.be"), detail
            ),
            ["info@startpeople.be"],
        )


class TestAddresses(unittest.TestCase):
    def test_street_number_and_city(self):
        postal = {
            "street": "Avenue Eiffel",
            "numero": "8",
            "codePostal": "1300",
            "municipalite": "Wavre",
            "pays": "Belgique",
        }
        self.assertEqual(
            compose_address(postal),
            "Avenue Eiffel 8, 1300 Wavre",
        )

    def test_house_range(self):
        postal = {"street": "Rue de Cockerill", "numero": "44/46",
                  "codePostal": "4100", "municipalite": "Seraing"}
        self.assertEqual(
            compose_address(postal),
            "Rue de Cockerill 44/46, 4100 Seraing",
        )

    def test_address_list_used_without_street(self):
        postal = {"adresse": ["Zone Verte", "Batiment C"],
                  "codePostal": "4040", "municipalite": "Herstal"}
        self.assertEqual(
            compose_address(postal),
            "Zone Verte, Batiment C, 4040 Herstal",
        )

    def test_foreign_country_is_kept(self):
        postal = {"street": "Rue de Paris", "codePostal": "75001",
                  "municipalite": "Paris", "pays": "France"}
        self.assertEqual(
            compose_address(postal), "Rue de Paris, 75001 Paris, France"
        )

    def test_not_a_dict(self):
        self.assertEqual(compose_address("nope"), "")


class TestWebsites(unittest.TestCase):
    def test_only_the_domain_is_kept(self):
        self.assertEqual(
            normalize_url(
                "https://www.jobat.be/fr/emplois/electricien/job_6293847"
                "?utm_source=forem&utm_medium=jobs#top"
            ),
            "https://www.jobat.be",
        )
        self.assertEqual(
            normalize_url("http://www.forumjobs.be/fr/vacature-2646675"),
            "http://www.forumjobs.be",
        )

    def test_host_is_lowercased(self):
        self.assertEqual(
            normalize_url("https://WWW.DHFIELDS.be/Be/"), "https://www.dhfields.be"
        )

    def test_bare_domain_gets_a_scheme(self):
        self.assertEqual(
            normalize_url("dhfields.be/offres"), "https://dhfields.be"
        )

    def test_rejected_values(self):
        self.assertEqual(normalize_url(""), "")
        self.assertEqual(normalize_url("appelez nous"), "")
        self.assertEqual(normalize_url("mailto:info@dhfields.be"), "")
        self.assertEqual(normalize_url("javascript:alert(1)"), "")

    def test_one_entry_per_domain(self):
        self.assertEqual(
            collapse_websites([
                "https://www.jobat.be/fr/emplois/electricien/job_1?utm_source=forem",
                "https://www.jobat.be/fr/emplois/electricien/job_2",
                "https://www.jobat.be",
                "https://www.technolease.be",
                "https://www.technolease.be/autres-offres",
                "pas une url",
            ]),
            ["https://www.jobat.be", "https://www.technolease.be"],
        )


class TestObservation(unittest.TestCase):
    def test_offer_without_employer_is_skipped(self):
        offer = make_offer("1", company="")
        offer["company"] = ""
        self.assertIsNone(observe_offer(offer, {}))

    def test_full_observation(self):
        detail = make_detail(
            name="DH FIELDS SERVICE - DH FIELDS SERVICE",
            email="info@dhfields.be",
            phone="+32489301051",
            postal={"street": "Avenue Eiffel", "numero": "8",
                    "codePostal": "1300", "municipalite": "Wavre"},
            web="https://www.dhfields.be",
            contact="Laura Mahy", fonction="Coordinateur Projet",
            description="Specialiste en reseaux.",
            sector="Installation de systemes de telecommunication",
            partner="Jobat",
        )
        observation = observe_offer(
            make_offer("2053275"), detail, base="liege"
        )
        self.assertEqual(observation["number"], "2053275")
        self.assertEqual(observation["base"], "liege")
        self.assertEqual(observation["published"], "2026-09-16")
        self.assertEqual(observation["modified"], "2026-09-18")
        self.assertEqual(observation["phone"], "+32489301051")
        self.assertEqual(observation["address"], "Avenue Eiffel 8, 1300 Wavre")
        self.assertEqual(observation["website"], "https://www.dhfields.be")
        self.assertEqual(
            observation["contact"], "Laura Mahy (Coordinateur Projet)"
        )
        self.assertEqual(observation["sector"],
                         "Installation de systemes de telecommunication")
        self.assertEqual(observation["partner"], "Jobat")
        self.assertEqual(observation["emails"], ["info@dhfields.be"])

    def test_summary_only_offer_has_published_date(self):
        observation = observe_offer(
            make_offer("1", company="Vivaldis", email="contact@vivaldis.be"), {}
        )
        self.assertEqual(observation["published"], "2026-09-16")
        self.assertEqual(observation["modified"], "")
        self.assertEqual(observation["emails"], ["contact@vivaldis.be"])


class TestBuildIndex(TempDataDir):
    def test_single_scrape(self):
        self.seed(
            [make_offer("1"), make_offer("2"), make_offer("3", "Vivaldis")],
            details={"1": make_detail(email="info@startpeople.be")},
        )
        index = build_index()
        self.assertEqual(index["version"], config.VERSION)
        self.assertEqual(sorted(index["employers"]), ["Start People", "Vivaldis"])
        start = index["employers"]["Start People"]
        self.assertEqual(start["offerCount"], 2)
        self.assertEqual([o["number"] for o in start["offers"]], ["1", "2"])
        self.assertEqual(start["emails"], ["info@startpeople.be"])
        self.assertEqual(index["stats"]["avecEmail"], 1)
        self.assertEqual(index["stats"]["offresActives"], 3)

    def test_similar_names_are_not_merged(self):
        self.seed([
            make_offer("1", "Accent"),
            make_offer("2", "ACCENT CONSTRUCT - Accent"),
            make_offer("3", "Actief Interim Liege"),
            make_offer("4", "Actief Interim Wavre"),
        ], details={
            "1": make_detail("Accent", email="soignies@accentjobs.be"),
            "2": make_detail("ACCENT CONSTRUCT - Accent",
                             email="liege@accentjobs.be"),
        })
        index = build_index()
        self.assertEqual(len(index["employers"]), 4)
        self.assertEqual(
            index["employers"]["Accent"]["emails"],
            ["soignies@accentjobs.be"],
        )
        self.assertEqual(
            index["employers"]["ACCENT CONSTRUCT - Accent"]["emails"],
            ["liege@accentjobs.be"],
        )

    def test_same_name_across_scrapes_is_cumulated(self):
        self.seed([make_offer("1")],
                  details={"1": make_detail(email="liege@accents.be")})
        self.seed([make_offer("2")],
                  details={"2": make_detail(email="wavre@accents.be")},
                  base="liege")
        index = build_index()
        record = index["employers"]["Start People"]
        self.assertEqual(record["offerCount"], 2)
        # Emails are cumulated from both scrapes; order depends on scrape order
        self.assertCountEqual(
            record["emails"], ["liege@accents.be", "wavre@accents.be"]
        )
        # Scrapes are sorted alphabetically; default base is "test"
        self.assertCountEqual(index["scrapes"], ["liege", "test"])
        # Offer bases reflect the scrape they came from
        self.assertCountEqual([o["base"] for o in record["offers"]], ["test", "liege"])

    def test_removed_offers_are_counted(self):
        self.seed(
            [make_offer("1")],
            history={"offers": [dict(make_offer("2"), removed=True)]},
        )
        index = build_index()
        record = index["employers"]["Start People"]
        self.assertEqual(record["offerCount"], 1)
        self.assertEqual(record["deletedCount"], 1)
        self.assertEqual([o["number"] for o in record["offers"]], ["1"])
        self.assertEqual([o["number"] for o in record["deletedOffers"]], ["2"])

    def test_partner_is_appended_to_the_name(self):
        self.seed([make_offer("1"), make_offer("2", "Synergie Interim")],
                  details={
                      "1": make_detail("Start People", partner="Jobat"),
                      "2": make_detail("Synergie Interim",
                                       partner="Synergie Interim"),
                  })
        index = build_index()
        self.assertEqual(sorted(index["employers"]), [
            "Start People (Jobat)",
            "Synergie Interim (Synergie Interim)",
        ])
        record = index["employers"]["Start People (Jobat)"]
        self.assertEqual(record["partners"], ["Jobat"])
        self.assertEqual(record["offers"][0]["published"], "2026-09-16")

    def test_same_name_two_channels_stays_split(self):
        self.seed(
            [make_offer("1"), make_offer("2"), make_offer("3")],
            details={
                "1": make_detail("Start People", partner="Jobat"),
                "2": make_detail("Start People", partner="Start People"),
                "3": make_detail("Start People"),
            },
        )
        index = build_index()
        self.assertEqual(sorted(index["employers"]), [
            "Start People",
            "Start People (Jobat)",
            "Start People (Start People)",
        ])

    def test_old_key_does_not_survive_the_partner_arrival(self):
        # First run: no detail file yet, so no partner in the name.
        self.seed([make_offer("1")])
        first = build_index()
        self.assertEqual(list(first["employers"]), ["Start People"])

        # Second run: the details bring the channel, the old key goes away.
        self.seed([make_offer("1")],
                  details={"1": make_detail("Start People", partner="Jobat")})
        second = build_index(previous=first)
        self.assertEqual(list(second["employers"]), ["Start People (Jobat)"])
        self.assertEqual(
            second["employers"]["Start People (Jobat)"]["offerCount"], 1
        )

    def test_key_split_over_channels_leaves_no_orphan(self):
        # Before the details: one entry with three offers.
        self.seed([make_offer("1"), make_offer("2"), make_offer("3")])
        first = build_index()
        self.assertEqual(list(first["employers"]), ["Start People"])

        # The channels split those offers over three keys.
        self.seed([make_offer("1"), make_offer("2"), make_offer("3")],
                  details={
                      "1": make_detail("Start People", partner="Jobat"),
                      "2": make_detail("Start People", partner="VDAB"),
                      "3": make_detail("Start People"),
                  })
        second = build_index(previous=first)
        self.assertEqual(sorted(second["employers"]), [
            "Start People",
            "Start People (Jobat)",
            "Start People (VDAB)",
        ])
        for record in second["employers"].values():
            self.assertEqual(record["offerCount"], 1)

    def test_manual_contact_survives_the_next_run(self):
        self.seed([make_offer("1")],
                  details={"1": make_detail("Start People", partner="Jobat")})
        first = build_index()
        record = first["employers"]["Start People (Jobat)"]
        record["emails"] = list(record["emails"]) + ["saisi@main.be"]
        record["description"] = "Corrigée à la main."

        second = build_index(previous=first)
        kept = second["employers"]["Start People (Jobat)"]
        self.assertIn("saisi@main.be", kept["emails"])
        self.assertEqual(kept["description"], "Corrigée à la main.")
        self.assertEqual(kept["offerCount"], 1)

    def test_edited_name_wins_over_the_partner(self):
        self.seed([make_offer("1")],
                  details={"1": make_detail("Start People", partner="Jobat")})
        first = build_index()
        record = first["employers"]["Start People (Jobat)"]
        record["name"] = "Start People SPRL"
        record["edited"] = "2026-09-27T10:00:00+02:00"
        first = {"employers": {"Start People SPRL": record}}

        second = build_index(previous=first)
        self.assertEqual(list(second["employers"]), ["Start People SPRL"])
        self.assertEqual(
            second["employers"]["Start People SPRL"]["partners"], ["Jobat"]
        )

    def test_edited_name_survives_the_next_run(self):
        # The interface renamed "Start People" and flagged the record.
        previous = {
            "employers": {
                "Start People SPRL": {
                    "name": "Start People SPRL",
                    "edited": "2026-09-27T10:00:00+02:00",
                    "emails": ["start@startpeople.be"],
                    "offers": [{"number": "1", "base": "", "title": "H/F/X"}],
                }
            }
        }
        self.seed([make_offer("1")], details={
            "1": make_detail(email="start@startpeople.be")
        })
        index = build_index(previous=previous)
        self.assertEqual(list(index["employers"]), ["Start People SPRL"])
        record = index["employers"]["Start People SPRL"]
        self.assertEqual(record["edited"], "2026-09-27T10:00:00+02:00")
        self.assertEqual(record["offerCount"], 1)
        self.assertEqual(record["emails"], ["start@startpeople.be"])

    def test_untouched_record_keeps_the_scraped_name(self):
        previous = {
            "employers": {
                "Ancien nom": {
                    "name": "Ancien nom",
                    "emails": ["x@y.be"],
                    "offers": [{"number": "999", "base": "", "title": ""}],
                }
            }
        }
        self.seed([make_offer("1")])
        index = build_index(previous=previous)
        self.assertIn("Start People", index["employers"])
        self.assertIn("Ancien nom", index["employers"])

    def test_websites_are_reduced_to_domains(self):
        previous = {
            "employers": {
                "Jobat": {
                    "name": "Jobat",
                    "websites": [
                        "https://www.jobat.be/fr/emplois/electricien/job_1"
                        "?utm_source=forem"
                    ],
                    "emails": [],
                }
            }
        }
        self.seed([make_offer("1", "Jobat")], details={
            "1": make_detail(
                "Jobat",
                web="https://www.jobat.be/fr/emplois/electricien/job_2"
                    "?utm_source=forem",
            )
        })
        record = build_index(previous=previous)["employers"]["Jobat"]
        self.assertEqual(record["websites"], ["https://www.jobat.be"])

    def test_previous_contacts_are_kept(self):
        previous = {
            "employers": {
                "Start People": {
                    "name": "Start People",
                    "emails": ["ancien@startpeople.be"],
                    "phones": ["04 220 97 40"],
                    "addresses": ["Rue de Mieres 30, 4040 HERSTAL"],
                    "websites": ["https://startpeople.be"],
                    "sectors": ["Interim"],
                    "partners": [],
                    "contacts": [],
                    "description": "Ancienne description",
                    "locations": ["HERSTAL"],
                },
                "Ancien Employeur": {
                    "name": "Ancien Employeur",
                    "emails": ["contact@ancien.be"],
                },
            }
        }
        self.seed([make_offer("1")],
                  details={"1": make_detail(email="nouveau@startpeople.be")})
        index = build_index(previous=previous)
        record = index["employers"]["Start People"]
        self.assertEqual(
            record["emails"], ["ancien@startpeople.be", "nouveau@startpeople.be"]
        )
        self.assertEqual(record["phones"], ["04 220 97 40"])
        self.assertEqual(record["description"], "Ancienne description")
        # Employer without any offer left: contacts survive, counts do not.
        orphan = index["employers"]["Ancien Employeur"]
        self.assertEqual(orphan["emails"], ["contact@ancien.be"])
        self.assertEqual(orphan["offerCount"], 0)
        self.assertEqual(index["stats"]["conserves"], 2)

    def test_rebuild_ignores_previous(self):
        previous = {
            "employers": {
                "Ancien Employeur": {
                    "name": "Ancien Employeur",
                    "emails": ["contact@ancien.be"],
                }
            }
        }
        self.seed([make_offer("1")])
        index = build_index(previous=previous, rebuild=True)
        self.assertNotIn("Ancien Employeur", index["employers"])
        self.assertEqual(index["stats"]["conserves"], 0)

    def test_missing_details_do_not_block(self):
        self.seed([make_offer("1"), make_offer("2", "Vivaldis")])
        index = build_index()
        self.assertEqual(len(index["employers"]), 2)
        self.assertEqual(index["stats"]["employeursSansNom"], 0)

    def test_duplicate_offer_numbers_are_merged(self):
        self.seed([make_offer("1"), make_offer("1")])
        record = build_index()["employers"]["Start People"]
        self.assertEqual(record["offerCount"], 2)
        self.assertEqual([o["number"] for o in record["offers"]], ["1"])

    def test_stats_without_data(self):
        index = build_index()
        self.assertEqual(index["employers"], {})
        self.assertEqual(index["stats"]["employeurs"], 0)
        self.assertEqual(index["scrapes"], [])


class TestRefreshIndex(TempDataDir):
    """refresh_index(): what the server calls after each scraping."""

    def index_path(self):
        return config.companies_file()

    def test_the_index_is_written_and_reported(self):
        self.seed([make_offer("1", company="Acme")])

        stats = refresh_index(self.index_path())

        self.assertEqual(stats["employeurs"], 1)
        self.assertTrue(os.path.exists(self.index_path()))
        with open(self.index_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertIn("Acme", data["employers"])
        self.assertEqual(data["scrapes"], ["test"])

    def test_contacts_from_the_previous_index_are_kept(self):
        self.seed(
            [make_offer("1")],
            details={"1": make_detail(email="jobs@acme.be")},
        )
        refresh_index(self.index_path())

        # The offer disappears, but the email collected earlier must remain.
        self.seed([])
        stats = refresh_index(self.index_path())

        with open(self.index_path(), encoding="utf-8") as handle:
            data = json.load(handle)
        record = data["employers"]["Start People"]
        self.assertEqual(record["offerCount"], 0)
        self.assertEqual(record["emails"], ["jobs@acme.be"])
        self.assertEqual(stats["conserves"], 1)

    def test_an_unreadable_previous_index_does_not_crash(self):
        # A truncated file must not stop the refresh: it is simply ignored.
        with open(self.index_path(), "w", encoding="utf-8") as handle:
            handle.write("{ truncated")
        self.seed([make_offer("1", company="Acme")])

        stats = refresh_index(self.index_path())

        self.assertEqual(stats["employeurs"], 1)


class TestGeneratedFile(unittest.TestCase):
    """Invariants on data/json when it has already been built."""

    def test_keys_match_names(self):
        path = config.companies_file()
        if not os.path.exists(path):
            self.skipTest("json pas encore généré")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        for key, record in data["employers"].items():
            self.assertEqual(key, record["name"])
            self.assertNotIn("  ", key)
            self.assertEqual(
                record["offerCount"], len(record["offers"])
            )
            self.assertEqual(
                record["deletedCount"], len(record["deletedOffers"])
            )
            self.assertEqual(
                record["emails"],
                sorted(set(record["emails"]), key=record["emails"].index),
            )
        self.assertEqual(
            data["stats"]["employeurs"], len(data["employers"])
        )


if __name__ == "__main__":
    unittest.main()
