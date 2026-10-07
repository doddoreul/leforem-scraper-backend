# -*- coding: utf-8 -*-
"""Table-driven tests for the remuneration and meal-voucher parsers.

The cases are the wordings actually found in Forem offers, plus the traps:
working hours, bare amounts, benefits sitting next to a wage, and French
number formatting. Run from the repository root:

    python -m unittest tests.test_salary -v
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent)
)

from python import core  # noqa: E402
from python.salary import (  # noqa: E402
    HOURS_PER_MONTH,
    HOURS_PER_WEEK,
    parse_meal_vouchers,
    parse_remuneration,
    to_number,
)


class TestToNumber(unittest.TestCase):
    """A dot is a thousands mark with three digits, else a decimal point."""

    def test_french_thousands(self):
        for text, expected in [
            ("2.700", 2700.0),
            ("36.000", 36000.0),
            ("16.800", 16800.0),
        ]:
            with self.subTest(text=text):
                self.assertEqual(to_number(text), expected)

    def test_decimal_point(self):
        for text, expected in [
            ("18.00", 18.0),
            ("16.80", 16.8),
            ("0.5", 0.5),
        ]:
            with self.subTest(text=text):
                self.assertEqual(to_number(text), expected)

    def test_decimal_comma(self):
        for text, expected in [
            ("14,5", 14.5),
            ("14,50", 14.5),
            ("1,09", 1.09),
            ("2,700", 2700.0),
            ("2.700,50", 2700.5),
        ]:
            with self.subTest(text=text):
                self.assertEqual(to_number(text), expected)

    def test_space_thousands(self):
        self.assertEqual(to_number("2 800"), 2800.0)
        self.assertEqual(to_number("36 000"), 36000.0)

    def test_unreadable(self):
        for text in ("", None, "abc"):
            with self.subTest(text=text):
                self.assertIsNone(to_number(text))


class TestHourly(unittest.TestCase):
    """Every French wording of an hourly rate, and the traps around it."""

    def check(self, text, low, high=None, gross=None, hourly=None):
        got = parse_remuneration(text)
        self.assertEqual(got["kind"], "hourly", text)
        self.assertAlmostEqual(got["min"], low, places=2, msg=text)
        self.assertAlmostEqual(got["max"], low if high is None else high,
                               places=2, msg=text)
        if gross is not None:
            self.assertEqual(got["gross"], gross, text)
        if hourly is not None:
            self.assertAlmostEqual(got["hourly_estimate"], hourly, places=4,
                                   msg=text)

    def test_simple_forms(self):
        self.check("14,5 €/h", 14.5)
        self.check("14.50 € de l'heure", 14.5)
        self.check("16,55€/h", 16.55)
        self.check("18-24€/h", 18.0, 24.0)
        self.check("de 16 à 18 € par heure", 16.0, 18.0)
        self.check("environ 17€/h", 17.0)

    def test_gross_is_reported(self):
        self.check("14,5 €/h brut", 14.5, gross=True)
        self.check("14,5 €/h net", 14.5, gross=False)
        self.assertIsNone(parse_remuneration("14,5 €/h")["gross"])

    def test_between_and_range(self):
        self.check("entre 14 et 16 € brut/heure", 14.0, 16.0, gross=True,
                   hourly=14.0)
        self.check("Salaire : entre 16.80€ et 20€ par heure brut", 16.8, 20.0,
                   gross=True, hourly=16.8)
        self.check("Salaire horaire : Entre 22 et 24€ de l'heure", 22.0, 24.0,
                   hourly=22.0)

    def test_open_range_keeps_min_only(self):
        got = parse_remuneration("salaire à partir de 20,50€/h brut")
        self.assertEqual(got["kind"], "hourly")
        self.assertAlmostEqual(got["min"], 20.5)
        self.assertAlmostEqual(got["max"], 20.5)

    def test_hourly_estimate_is_the_amount(self):
        self.assertAlmostEqual(parse_remuneration("20 €/h")["hourly_estimate"],
                               20.0)

    def test_confidence_is_high_with_a_unit(self):
        self.assertEqual(parse_remuneration("20 €/h")["confidence"], "high")

    def test_working_hours_are_not_a_wage(self):
        # "horaire de travail" mentions the unit word without being one.
        got = parse_remuneration("horaire de travail : 9h à 17h, du lundi au vendredi")
        self.assertEqual(got["kind"], "unknown")
        self.assertIsNone(got["min"])
        self.assertIsNone(got["hourly_estimate"])

    def test_a_bonus_percentage_is_not_a_wage(self):
        got = parse_remuneration("Salaire horaire : Entre 22 et 24€ de l'heure "
                                 "Primes d'équipe : 20 %")
        self.assertEqual(got["kind"], "hourly")
        self.assertAlmostEqual(got["min"], 22.0)
        self.assertAlmostEqual(got["max"], 24.0)


class TestMonthly(unittest.TestCase):
    def check(self, text, low, high=None, gross=None):
        got = parse_remuneration(text)
        self.assertEqual(got["kind"], "monthly", text)
        self.assertAlmostEqual(got["min"], low, places=2, msg=text)
        self.assertAlmostEqual(got["max"], low if high is None else high,
                               places=2, msg=text)
        if gross is not None:
            self.assertEqual(got["gross"], gross, text)

    def test_simple_forms(self):
        self.check("2.500 € brut/mois", 2500.0, gross=True)
        self.check("2500 euros bruts par mois", 2500.0, gross=True)
        self.check("salaire brut de 2.700 € par mois", 2700.0, gross=True)
        self.check("2 800 € par mois", 2800.0)

    def test_range(self):
        self.check("de 2 800 à 3 200 € bruts mensuels", 2800.0, 3200.0,
                   gross=True)

    def test_hourly_estimate_divides_by_hours_per_month(self):
        got = parse_remuneration("2 500 € brut par mois")
        self.assertAlmostEqual(got["hourly_estimate"], 2500.0 / HOURS_PER_MONTH,
                               places=4)
        # The documented assumption, spelled out.
        self.assertAlmostEqual(HOURS_PER_MONTH, 38.0 * 52.0 / 12.0, places=4)


class TestAnnual(unittest.TestCase):
    def test_brut_per_year(self):
        got = parse_remuneration("36.000 € brut/an")
        self.assertEqual(got["kind"], "annual")
        self.assertAlmostEqual(got["min"], 36000.0)
        self.assertTrue(got["gross"])

    def test_spaces_and_no_unit_word(self):
        got = parse_remuneration("36 000 € annuels")
        self.assertEqual(got["kind"], "annual")
        self.assertAlmostEqual(got["min"], 36000.0)

    def test_hourly_estimate_divides_by_twelve_then_by_month_hours(self):
        got = parse_remuneration("36.000 € brut/an")
        expected = 36000.0 / 12.0 / HOURS_PER_MONTH
        self.assertAlmostEqual(got["hourly_estimate"], expected, places=4)


class TestUnknown(unittest.TestCase):
    """No usable wage: the parser must say so rather than invent a figure."""

    def check(self, text):
        got = parse_remuneration(text)
        self.assertEqual(got["kind"], "unknown", text)
        self.assertIsNone(got["min"], text)
        self.assertIsNone(got["max"], text)
        self.assertIsNone(got["hourly_estimate"], text)
        self.assertEqual(got["confidence"], "none", text)

    def test_no_amount_at_all(self):
        self.check("salaire selon barème CP 200")
        self.check("à discuter")
        self.check("barème en vigueur")
        self.check("")
        self.check(None)

    def test_a_thirteenth_month_alone(self):
        self.check("13e mois")
        self.check("prime")
        self.check("prime d'embauche")

    def test_a_benefit_alone_is_not_a_wage(self):
        self.check("chèques-repas de 5€/jour")
        self.check("tickets restaurant")


class TestMagnitudeOnly(unittest.TestCase):
    """A bare amount is guessed from its size, with confidence low."""

    def check(self, text, kind, low):
        got = parse_remuneration(text)
        self.assertEqual(got["kind"], kind, text)
        self.assertAlmostEqual(got["min"], low, places=2, msg=text)
        self.assertEqual(got["confidence"], "low", text)

    def test_below_hundred_is_hourly(self):
        self.check("45", "hourly", 45.0)

    def test_thousand_to_ten_thousand_is_monthly(self):
        self.check("2797", "monthly", 2797.0)

    def test_above_ten_thousand_is_annual(self):
        self.check("45000", "annual", 45000.0)

    def test_hourly_estimate_is_still_computed(self):
        got = parse_remuneration("2797")
        self.assertAlmostEqual(got["hourly_estimate"], 2797.0 / HOURS_PER_MONTH,
                               places=4)


class TestAnalyzeSalariesRegressions(unittest.TestCase):
    """core.analyze_salaries keeps working unchanged on every kind.

    The derived fields are additive: analyze_salaries reads the ``salary`` and
    ``pay`` text, so a new ``kind`` must not change its result.
    """

    def analyze(self, offer):
        return core.analyze_salaries([offer])

    def test_each_kind_gives_the_same_answer(self):
        text = "Salaire brut de 2 500 € par mois"
        parsed = parse_remuneration(text)
        base = {"number": "1", "salary": text, "pay": ""}

        for kind in ("hourly", "monthly", "annual", "unknown"):
            with self.subTest(kind=kind):
                offer = dict(base, salary_kind=kind)
                got = self.analyze(offer)
                self.assertEqual(got["total"], 1)
                self.assertEqual(got["renseignees"], 1)
                # A monthly wage yields no hourly value, as before.
                self.assertEqual(got["hourly"], [])

    def test_an_hourly_wage_is_still_reported(self):
        offer = {"number": "1", "salary": "18 €/h", "pay": "",
                 "salary_kind": "hourly"}
        got = self.analyze(offer)
        self.assertIn(18.0, got["hourly"])
        self.assertEqual(got["renseignees"], 1)

    def test_the_derived_fields_do_not_create_an_hourly_value(self):
        # salary_hourly_estimate exists but analyze_salaries must not suddenly
        # count it: it keeps reading the text only.
        offer = {"number": "1", "salary": "2 500 € par mois", "pay": "",
                 "salary_kind": "monthly",
                 "salary_hourly_estimate": 15.18}
        got = self.analyze(offer)
        self.assertEqual(got["hourly"], [])
        self.assertAlmostEqual(parse_remuneration(offer["salary"])["min"], 2500.0)

    def test_no_offer(self):
        got = core.analyze_salaries([])
        self.assertEqual(got, {"renseignees": 0, "total": 0, "hourly": []})


class TestSeveralFields(unittest.TestCase):
    """parse_remuneration takes several texts, as build_offer does."""

    def test_the_wage_wins_over_a_benefit(self):
        got = parse_remuneration("chèques-repas de 5€/jour",
                                 "Salaire : environ 17€/h")
        self.assertEqual(got["kind"], "hourly")
        self.assertAlmostEqual(got["min"], 17.0)

    def test_empty_inputs(self):
        self.assertEqual(parse_remuneration()["kind"], "unknown")


class TestMealVouchers(unittest.TestCase):
    def check(self, text, amount, period, mentioned=True):
        got = parse_meal_vouchers(text)
        self.assertEqual(got["mentioned"], mentioned, text)
        self.assertEqual(got["amount"], amount, text)
        self.assertEqual(got["period"], period, text)

    def test_named_without_an_amount(self):
        for text in ("chèques-repas", "chèques repas", "titres-repas",
                     "ticket restaurant", "tickets restaurant",
                     "repas sur place"):
            with self.subTest(text=text):
                self.check(text, None, "unknown")

    def test_amount_per_day(self):
        self.check("chèques-repas de 8 €/jour", 8.0, "day")
        self.check("chèques-repas de 5EUR/jour", 5.0, "day")
        self.check("8 €/jour", 8.0, "day")

    def test_amount_per_month(self):
        self.check("chèques-repas 8 € par mois", 8.0, "month")

    def test_face_value(self):
        self.check("chèques-repas (valeur faciale 8,00 €)", 8.0, "unknown")

    def test_employer_share(self):
        self.check("1,09 € part travailleur", 1.09, "unknown")

    def test_a_wage_in_the_same_sentence_is_never_the_voucher(self):
        # The amount closest to the meal keyword wins: 5, not 2500 and not 17.
        self.check("salaire 2 500 € + chèques-repas", None, "unknown")
        self.check("Salaire : environ 17€/h + chèques-repas de 5€/jour",
                   5.0, "day")

    def test_explicitly_absent(self):
        self.check("pas de chèques-repas", None, "unknown", mentioned=False)

    def test_nothing_about_meals(self):
        self.check("poste électromécanicien", None, "unknown", mentioned=False)

    def test_several_fields(self):
        got = parse_meal_vouchers("chèques-repas de 9 EUR", "Salaire : 20 EUR/h")
        self.assertTrue(got["mentioned"])
        self.assertEqual(got["amount"], 9.0)

    def test_empty_input(self):
        self.check(None, None, "unknown", mentioned=False)


if __name__ == "__main__":
    unittest.main()