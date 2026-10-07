# -*- coding: utf-8 -*-
"""Tests for the static files served by the local server.

The pages are plain ES modules: one file per page in js/pages/ and the
modules they share in js/shared/ and js/boot/. The root folder holds only
index.html and the entry points: the other pages live in html/ and the
stylesheet in css/. What is checked here is that the server hands every file
over with the right type, that nothing escapes those trees, and that the old
root scripts are gone.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

import requests

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

os.environ.setdefault("LEFOREM_STORAGE", "json")

from python import config
from python import server

PAGES = ("index", "insights", "companies", "detail", "profil")

# The offer sheet is reached from the offers table, not from the navbar, so it
# has no entry there.
NAVBAR_PAGES = ("index", "insights", "companies", "profil")


class StaticFilesTestCase(unittest.TestCase):
    """Boots the real server on a temporary data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._saved = config.DATA_DIR
        config.DATA_DIR = self.tmp.name

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        config.DATA_DIR = self._saved
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def get(self, path, **kwargs):
        return requests.get(
            f"http://127.0.0.1:{self.port}{path}", timeout=10, **kwargs
        )


class TestPages(StaticFilesTestCase):
    def test_every_page_is_served(self):
        for page in PAGES:
            with self.subTest(page=page):
                response = self.get(f"/{page}.html")
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["Content-Type"])

    def test_a_page_loads_its_own_module(self):
        for page in PAGES:
            with self.subTest(page=page):
                body = self.get(f"/{page}.html").text
                self.assertIn(f'js/pages/{page}.js', body)
                self.assertIn("js/boot/theme-boot.js", body)

    def test_the_theme_boot_script_is_not_a_module(self):
        # It must run while <head> is parsed, before the first paint.
        for page in PAGES:
            with self.subTest(page=page):
                body = self.get(f"/{page}.html").text
                self.assertIn(
                    '<script src="/js/boot/theme-boot.js"></script>', body
                )

    def test_every_page_loads_the_shared_stylesheet(self):
        for page in PAGES:
            with self.subTest(page=page):
                body = self.get(f"/{page}.html").text
                self.assertIn('href="/css/theme.css', body)

    def test_the_navbar_partial_is_served(self):
        response = self.get("/navbar_include.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/index.html"', response.text)

    def test_every_internal_url_is_served(self):
        # The pages, the navbar and the modules all point at each other with
        # root-absolute URLs. One wrong prefix and the browser silently asks
        # for a path the server does not answer.
        sources = [self.get(f"/{page}.html").text for page in PAGES]
        sources.append(self.get("/navbar_include.html").text)
        sources += [source for _path, source in modules()]
        pattern = re.compile(r'(?:href|src|from)="(/[^"#?]+)')
        for source in sources:
            for url in sorted(set(pattern.findall(source))):
                with self.subTest(url=url):
                    self.assertEqual(self.get(url).status_code, 200, url)

    def test_the_navbar_links_every_page(self):
        # Each page of the app is reachable from the navbar partial, which is
        # what loadNavbar() injects everywhere.
        navbar = self.get("/navbar_include.html").text
        for page in NAVBAR_PAGES:
            with self.subTest(page=page):
                self.assertIn(f'"/{page}.html"', navbar)

    def test_the_navbar_marks_the_current_page(self):
        # navbar.js compares the href with the current file name, so every
        # link needs the data-page marker the active class keys off.
        navbar = self.get("/navbar_include.html").text
        self.assertIn('data-page="profil"', navbar)

    def test_no_page_points_at_a_moved_file(self):
        # The pages were reorganised into html/ and the stylesheet into css/;
        # the URL stays flat, so a "/html/x.html" would 404.
        for page in PAGES:
            with self.subTest(page=page):
                body = self.get(f"/{page}.html").text
                for url in re.findall(r'(?:href|src)="(/[^"#?]+)', body):
                    self.assertFalse(url.startswith("/html/"), url)
                    self.assertFalse(url.startswith("/css/../"), url)

    def test_the_removed_root_scripts_are_not_served(self):
        for name in ("script.js", "insights.js", "companies.js", "detail.js",
                     "theme.js", "suivi-io.js", "scraping-selector.js",
                     "scraper-ui.js", "navbar-loader.js", "install.ps1"):
            with self.subTest(name=name):
                self.assertEqual(self.get(f"/{name}").status_code, 404)

    def test_nothing_of_the_project_is_listed(self):
        body = self.get("/").text
        self.assertNotIn("companies.py", body)


class TestModules(StaticFilesTestCase):
    def test_the_whole_module_tree_is_served(self):
        js_root = os.path.join(server.BASE_DIR, "js")
        modules = []
        for folder, _dirs, files in os.walk(js_root):
            for name in files:
                if name.endswith(".js"):
                    relative = os.path.relpath(
                        os.path.join(folder, name), js_root
                    )
                    modules.append("/js/" + relative.replace(os.sep, "/"))

        self.assertGreaterEqual(len(modules), 10)
        for path in sorted(modules):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.headers["Content-Type"],
                    "application/javascript; charset=utf-8",
                )
                self.assertEqual(
                    response.headers["Cache-Control"], "no-store"
                )

    def test_an_unknown_module_is_a_404(self):
        self.assertEqual(self.get("/js/pages/nope.js").status_code, 404)

    def test_only_javascript_is_served_from_the_module_tree(self):
        self.assertEqual(self.get("/js/pages/index.html").status_code, 404)
        self.assertEqual(self.get("/js/server.py").status_code, 404)

    def test_the_module_tree_cannot_be_left(self):
        for path in ("/js/../server.py",
                     "/js/shared/../../core.py",
                     "/js/pages/../../../etc/passwd"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)


class TestStyles(StaticFilesTestCase):
    def test_the_stylesheet_is_served_from_the_css_folder(self):
        for css in ("theme.css", "base.css", "components.css", "pages.css", "widgets.css"):
            with self.subTest(css=css):
                response = self.get("/css/" + css)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.headers["Content-Type"], "text/css; charset=utf-8"
                )

    def test_the_stylesheet_is_not_served_from_the_root(self):
        self.assertEqual(self.get("/style.css").status_code, 404)

    def test_an_unknown_stylesheet_is_a_404(self):
        self.assertEqual(self.get("/css/nope.css").status_code, 404)

    def test_only_css_is_served_from_the_css_folder(self):
        self.assertEqual(self.get("/css/index.html").status_code, 404)
        self.assertEqual(self.get("/css/server.py").status_code, 404)

    def test_the_css_tree_cannot_be_left(self):
        for path in ("/css/../core.py", "/css/../../.gitignore"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path).status_code, 404)


class TestDataFiles(StaticFilesTestCase):
    def test_the_shared_json_files_are_served_from_the_data_directory(self):
        expected = {
            "historique_scrapes.json": {
                "version": config.VERSION,
                "scrapes": [],
            },
            "historique_modifications.json": {},
            "companies.json": {"employers": {}},
        }
        for name, body in expected.items():
            with self.subTest(name=name):
                with open(os.path.join(self.tmp.name, name), "w",
                          encoding="utf-8") as handle:
                    handle.write("{}")
                response = self.get(f"/{name}")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), body)

    def test_an_unknown_json_file_is_a_404(self):
        self.assertEqual(self.get("/data_ghost.json").status_code, 404)


def modules():
    """Every module of js/, as a list of (path, source)."""
    js_root = os.path.join(server.BASE_DIR, "js")
    found = []
    for folder, _dirs, files in os.walk(js_root):
        for name in sorted(files):
            if name.endswith(".js"):
                full = os.path.join(folder, name)
                with open(full, encoding="utf-8") as handle:
                    found.append((full, handle.read()))
    return sorted(found)


class TestPageModules(unittest.TestCase):
    """Static checks a browser would otherwise catch the hard way."""

    def test_every_import_points_to_a_file_of_the_tree(self):
        # The server only serves js/boot, js/shared and js/pages: a module
        # imported from anywhere else would answer 404 at load time.
        served = ("../shared/", "./", "boot/", "shared/", "pages/")
        for path, source in modules():
            for specifier in re.findall(r'from "([^"]+)"', source) \
                    + re.findall(r'import "([^"]+)"', source):
                if specifier.startswith("/"):
                    target = os.path.join(server.BASE_DIR, specifier[1:])
                else:
                    target = os.path.normpath(
                        os.path.join(os.path.dirname(path), specifier)
                    )
                with self.subTest(module=os.path.basename(path), to=specifier):
                    self.assertTrue(os.path.isfile(target), target)
                    self.assertTrue(target.endswith(".js"), target)
                    self.assertTrue(
                        specifier.startswith(served) or specifier == ".",
                        specifier,
                    )

    def test_no_page_module_publishes_a_global(self):
        for path, source in modules():
            if os.sep + "pages" + os.sep not in path:
                continue
            for name in ("window.ScrapingSelector", "window.ForemScraperUi",
                         "window.FOREM_GEAR_ACTIONS"):
                with self.subTest(module=os.path.basename(path), name=name):
                    self.assertNotIn(name, source)

    def test_the_offers_table_restores_the_resolved_selection(self):
        # Without it the page opens on "Aucun scraping sélectionné" and
        # stays empty until the selector is touched.
        source = read_js(os.path.join("js", "pages", "index.js"))
        self.assertIn("applyScrapingSelection(result.current, result.scrapings)",
                      source)


def read_js(*parts):
    with open(os.path.join(server.BASE_DIR, *parts), encoding="utf-8") as handle:
        return handle.read()


if __name__ == "__main__":
    unittest.main()