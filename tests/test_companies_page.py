# -*- coding: utf-8 -*-
"""Quick static sanity check for the web files (no browser, no network).

Verifies that the ids used by js/pages/companies.js exist in
html/companies.html and that the braces/parentheses of the script are
balanced.
"""

import io
import os
import re
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with io.open(os.path.join(BASE, name), encoding="utf-8") as handle:
        return handle.read()


class TestCompaniesJs(unittest.TestCase):
    def setUp(self):
        self.js = read(os.path.join("js", "pages", "companies.js"))
        self.html = read(os.path.join("html", "companies.html"))

    def test_balanced_delimiters(self):
        for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(
                self.js.count(opener), self.js.count(closer),
                "{} / {} déséquilibrés".format(opener, closer),
            )

    def test_ids_used_exist_in_the_page(self):
        # Ids created by the script itself (modal) are excluded.
        created = set(re.findall(r'\.id = "([^"]+)"', self.js))
        created |= set(re.findall(r'id = "([^"]+)"', self.js))
        used = set(re.findall(r'getElementById\("([^"]+)"\)', self.js))
        missing = sorted(used - created)
        for name in missing:
            self.assertIn('id="{}"'.format(name), self.html)

    def test_script_and_stylesheet_are_linked(self):
        self.assertIn('type="module"', self.html)
        self.assertIn("/js/pages/companies.js", self.html)
        self.assertIn("/js/boot/theme-boot.js", self.html)
        self.assertIn("/css/theme.css", self.html)

    def test_no_inner_html_with_data(self):
        # Company names come from the API: never injected as HTML.
        cleaned = self.js \
            .replace('root.innerHTML = ""', "") \
            .replace('body.innerHTML = ""', "") \
            .replace('grid.innerHTML = ""', "") \
            .replace('head.innerHTML = ""', "")
        self.assertNotIn("innerHTML =", cleaned)

    def test_table_columns_match_the_request(self):
        for label in ("Société", "Offres", "E-mails", "Contacts", "Détails"):
            self.assertIn("<th", self.html)
            self.assertIn(label, self.html)

    def test_readonly_offer_list_without_editing(self):
        self.assertIn("function offerList(", self.js)
        self.assertNotIn("function offerGroup(", self.js)
        self.assertIn('"Offres liées"', self.js)
        self.assertIn('"company-offer-button", "Détails"', self.js)
        self.assertNotIn("fieldEl(", self.js)

    def test_removed_sections_are_gone(self):
        for label in ('"Secteurs"', '"Partenaires"', "Description de l'employeur",
                      "Première publication", "Dernière modification"):
            self.assertNotIn(label, self.js)

    def test_hidden_data_still_saved_untouched(self):
        saved = self.js.split("function buildSavedIndex()", 1)[1] \
            .split("function saveModal", 1)[0]
        for fragment in ("sectors: list(draft.sectors)",
                         "partners: list(draft.partners)",
                         "description: String(draft.description",
                         "firstPublished: String(draft.firstPublished",
                         "lastModified: String(draft.lastModified",
                         "offers: offers"):
            self.assertIn(fragment, saved)

    def test_no_orphan_css(self):
        css = ""
        for name in ("theme.css", "base.css", "components.css", "pages.css", "widgets.css"):
            css += read(os.path.join("css", name))
        for block in (".company-offer-head", ".company-offer-meta",
                      ".company-offer-fields", ".company-offer-label",
                      ".company-dates-row", ".company-textarea",
                      ".company-offer-link", "--bg-input", "--text-main"):
            self.assertNotIn(block, css)


if __name__ == "__main__":
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
