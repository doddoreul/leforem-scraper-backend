"""The local web server: static files, the data API and the scrape route.

Everything is served from ``http://localhost:8123``; the page is plain HTML,
CSS and ES modules, so there is no build step and nothing to install beyond
``requests``.

Routes:

===========================  ==========================================
``GET  /...``                 static files and the shared JSON files
``GET  /api/scrapings``       the searches present in ``data/``
``GET  /api/nomenclature/...`` professions and locations, proxied to Forem
``POST /api/scraper/run``     run a scrape, streaming NDJSON events
``POST /delete-scraping``     move a search's files to ``data/trash/``
``POST /companies.json``      save the employer index edited by hand
===========================  ==========================================
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import requests

from python import config
from python import scraper
from python.employers import refresh_index, summarize

logger = logging.getLogger(__name__)
from python.storage import get_storage

BASE_DIR = config.BASE_DIR

PORT = 8123

OCCUPATIONS_ENDPOINT = (
    "https://www.leforem.be/recherche-offres/"
    "api/Nomenclature/RechercheMetiers/{}"
)
LOCATIONS_ENDPOINT = (
    "https://www.leforem.be/recherche-offres/"
    "api/Nomenclature/Localisations"
)

# The root folder holds only index.html and the entry-point scripts; the other
# pages and the shared partial live in html/, the stylesheet in css/.
HTML_DIR = os.path.join(BASE_DIR, "html")
CSS_DIR = os.path.join(BASE_DIR, "css")

STATIC_FILES = {
    "": ("index.html", "text/html; charset=utf-8"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
}

HTML_MIME = "text/html; charset=utf-8"

# The pages served out of html/, including the partial the navbar injects.
HTML_FILES = {
    "/insights.html": "insights.html",
    "/companies.html": "companies.html",
    "/detail.html": "detail.html",
    "/profil.html": "profil.html",
    "/navbar_include.html": "navbar_include.html",
}

# The pages are plain ES modules: one entry per page in js/pages/ and the
# modules they share in js/shared/ and js/boot/.
JS_DIR = os.path.join(BASE_DIR, "js")
JS_PATH = re.compile(r"^/js/(?:boot|shared|pages)/[A-Za-z0-9_-]+\.js$")
JAVASCRIPT_MIME = "application/javascript; charset=utf-8"

CSS_PATH = re.compile(r"^/css/[A-Za-z0-9_-]+\.css$")
CSS_MIME = "text/css; charset=utf-8"

# The routes that answer a POST.
SCRAPER_RUN_PATH = "/api/scraper/run"
DELETE_SCRAPING_PATH = "/delete-scraping"
COMPANIES_PATH = "/companies.json"
PROFILE_PATH = "/api/profil"
RAW_JSON_PATH = "/" + config.RAW_JSON_FILE_NAME

# Synchronous scraping: the browser blocks on SCRAPER_RUN_PATH until the
# scraper has finished writing its files.
_scraper_lock = threading.Lock()

os.makedirs(config.trash_dir(), exist_ok=True)

MAX_EDIT_BODY = 8 * 1024 * 1024

SESSION = requests.Session()
SESSION.headers.update(scraper.HEADERS)


class StreamReporter:
    """Turns the real scraper events into NDJSON lines.

    The scraper runs in this very request thread; each line it logs is
    forwarded to the browser while the request stays open. Nothing runs in
    the background: the response is finished once scraper.py returns.
    """

    def __init__(self, handler: Any) -> None:
        self.handler = handler

    def log(self, message: str = "") -> None:
        lines = str(message).splitlines() or [""]
        for line in lines:
            self.handler.log_message("scraper | %s", line)
            self.handler._write_event({"type": "log", "line": line})

    def progress(self, done: int, total: int) -> None:
        self.handler._write_event(
            {"type": "progress", "done": done, "total": total}
        )

    def progress_done(self) -> None:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "LeForemScraper/1.0"
    # Required to stream the scraper log while the request is still open.
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self._stream_broken = False

    def _send_json(self, code: int, content: Any) -> None:
        body = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path: str) -> None:
        if self._serve_data(path):
            return
        if JS_PATH.match(path):
            file_name = path[len("/js/"):]
            file_root = JS_DIR
            mime_type = JAVASCRIPT_MIME
        elif CSS_PATH.match(path):
            file_name = path[len("/css/"):]
            file_root = CSS_DIR
            mime_type = CSS_MIME
        elif path in HTML_FILES:
            file_name = HTML_FILES[path]
            file_root = HTML_DIR
            mime_type = HTML_MIME
        elif path in STATIC_FILES:
            file_name, mime_type = STATIC_FILES[path]
            file_root = BASE_DIR
        else:
            self.send_error(404)
            return

        file_dir = os.path.normpath(file_root)
        file_path = os.path.normpath(os.path.join(file_dir, file_name))
        # The modules live in sub-directories: only their own tree is served.
        if os.path.commonpath([file_dir, file_path]) != file_dir:
            self.send_error(404)
            return
        try:
            with open(file_path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_data(self, path: str) -> bool:
        """Serve the JSON data routes from the storage layer.

        Returns True when the path belongs to the data API (even if the
        answer is a 404), False to let the static-file handler take over.
        """
        name = os.path.basename(path)
        store = get_storage()

        if path == "/" + config.SCRAPES_FILE_NAME:
            self._send_json(200, {
                "version": config.VERSION,
                "scrapes": store.read_history_scrapes(),
            })
            return True

        if path == "/" + config.MODIFICATIONS_FILE_NAME:
            payload = store.read_history_modifications()
            self._send_json(200, payload if isinstance(payload, dict) else {})
            return True

        if path == COMPANIES_PATH:
            payload = store.read_companies()
            self._send_json(200, payload or {"employers": {}})
            return True

        if path == RAW_JSON_PATH:
            # The export the static site reads. Built per request rather than
            # read from a file, so it always describes the current state of the
            # database and the page can be developed without publishing it.
            from python.export import build_export

            self._send_json(200, build_export(store))
            return True

        details_prefix = config.DETAILS_PREFIX
        data_prefix = config.DATA_PREFIX
        history_prefix = config.HISTORY_PREFIX
        suffix = config.JSON_SUFFIX

        if name.startswith(details_prefix) and name.endswith(suffix):
            base = name[len(details_prefix):-len(suffix)]
            self._send_json(200, {
                "version": config.VERSION,
                "details": store.read_details(base),
            })
            return True

        if name.startswith(data_prefix) and name.endswith(suffix):
            scraping_base = config.scrape_base(name)
            if scraping_base is None:
                return False
            data_payload = store.read_scraping(scraping_base)
            if data_payload is None:
                self.send_error(404)
            else:
                self._send_json(200, data_payload)
            return True

        if name.startswith(history_prefix) and name.endswith(suffix):
            base = name[len(history_prefix):-len(suffix)]
            payload = store.read_history_offers(base)
            self._send_json(200, payload or {"offers": []})
            return True

        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/scrapings":
            self._handle_scrapings()
            return

        if path == "/api/nomenclature/locations":
            self._handle_locations()
            return

        if path == "/api/nomenclature/occupations":
            params = dict(
                p.split("=", 1) for p in parsed.query.split("&") if "=" in p
            )
            q = params.get("q", "").strip()
            self._handle_occupations(q)
            return

        if path.startswith("/api/tracking/"):
            base = path[len("/api/tracking/"):]
            if base:
                self._handle_tracking_get(base)
                return

        if path == PROFILE_PATH:
            self._send_json(200, get_storage().read_profile())
            return

        self._serve_file(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == SCRAPER_RUN_PATH:
            self._handle_scraper_run()
        elif path == DELETE_SCRAPING_PATH:
            self._handle_delete_scraping()
        elif path == COMPANIES_PATH:
            self._handle_companies_save()
        elif path == PROFILE_PATH:
            self._handle_profile_save()
        else:
            self.send_error(404)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/tracking/"):
            parts = path[len("/api/tracking/"):].split("/")
            if len(parts) == 2 and parts[0] and parts[1]:
                self._handle_tracking_put(parts[0], parts[1])
                return
        self._drain_body()
        self.send_error(404)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/tracking/"):
            parts = path[len("/api/tracking/"):].split("/")
            if len(parts) == 2 and parts[0] and parts[1]:
                self._handle_tracking_delete(parts[0], parts[1])
                return
        self._drain_body()
        self.send_error(404)

    def _drain_body(self) -> None:
        """Consume an unread request body before answering.

        The server speaks HTTP/1.1 with keep-alive, so a body left in the
        socket is read as the start of the next request and desynchronises the
        connection. Answering 404 on a PUT without reading it aborted the
        client's next call at random.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_EDIT_BODY:
            self.close_connection = True
            return
        self.rfile.read(length)

    def _handle_tracking_get(self, base: str) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin refused"})
            return
        tracking = get_storage().read_tracking(base)
        self._send_json(200, tracking)

    def _handle_tracking_put(self, base: str, offer_id: str) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin refused"})
            return
        payload, error = self._read_json_body()
        if error:
            self._send_json(400, {"error": error})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "invalid payload"})
            return
        get_storage().write_tracking(base, offer_id, payload)
        self._send_json(200, {"ok": True})

    def _handle_tracking_delete(self, base: str, offer_id: str) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin refused"})
            return
        get_storage().delete_tracking(base, offer_id)
        self._send_json(200, {"ok": True})

    def _handle_profile_save(self) -> None:
        """Save the single candidate profile sent by the Profil page."""
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin refused"})
            return

        payload, error = self._read_json_body()
        if error:
            self._send_json(400, {"error": error})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "invalid payload"})
            return

        get_storage().write_profile(payload)
        self._send_json(200, {"ok": True})

    def _handle_companies_save(self) -> None:
        """Save the employer index as edited by hand on the Employers page."""
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin refused"})
            return

        payload, error = self._read_json_body()
        if error:
            self._send_json(400, {"error": error})
            return

        if not isinstance(payload.get("employers"), dict):
            self._send_json(400, {"error": "missing employers map"})
            return

        payload["version"] = payload.get("version") or 1
        payload["updated_timestamp"] = scraper.now_iso_timestamp()
        payload["stats"] = summarize(payload["employers"])
        try:
            get_storage().write_companies(payload)
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})
            return

        self._send_json(200, {
            "ok": True,
            "employeurs": len(payload["employers"]),
        })

    def _handle_scrapings(self) -> None:
        store = get_storage()
        results = []
        for name in store.get_scraping_names():
            data = store.read_scraping(name)
            if not isinstance(data, dict):
                continue
            offers = data.get("offers", [])
            results.append({
                "name": name,
                "file": config.data_file_name(name),
                "history": config.history_file_name(name),
                "details": config.details_file_name(name),
                "label": data.get("label", "") or "",
                "scrape_timestamp": data.get("scrape_timestamp", "") or "",
                "occupationGuid": data.get("occupation_guid", "") or "",
                "locationGuid": data.get("location_guid", "") or "",
                "offerCount": len(offers) if isinstance(offers, list) else 0,
            })
        results.sort(key=lambda e: e["label"] or e["name"])
        self._send_json(200, results)

    def _handle_occupations(self, q: str) -> None:
        if not q:
            self._send_json(200, [])
            return
        try:
            response = SESSION.get(OCCUPATIONS_ENDPOINT.format(q), timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self._send_json(502, {"success": False, "error": str(e)})
            return
        results = [
            {"key": item.get("key", ""), "value": item.get("value", "")}
            for item in data
            if isinstance(item, dict)
        ]
        self._send_json(200, results)

    def _handle_locations(self) -> None:
        try:
            response = SESSION.get(LOCATIONS_ENDPOINT, timeout=30)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            self._send_json(502, {"success": False, "error": str(e)})
            return
        results = [
            {
                "gufid": item.get("gufid", ""),
                "label": item.get("libelle", ""),
                "code": item.get("code", ""),
            }
            for item in data
            if isinstance(item, dict) and item.get("gufid")
        ]
        self._send_json(200, results)

    # ============================================================
    # SYNCHRONOUS SCRAPING (POST /api/scraper/run)
    # ============================================================

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host") or ""
        return not origin or urlparse(origin).netloc == host

    def _read_json_body(self, required: bool = True) -> tuple[Any, str | None]:
        """Return (payload, error_message)."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return (None, "empty body") if required else ({}, None)
        if length > MAX_EDIT_BODY:
            return None, "invalid body size"
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, "invalid JSON"
        if not isinstance(payload, dict):
            return None, "invalid JSON"
        return payload, None

    def _start_stream(self) -> None:
        self.send_response(200)
        self.send_header(
            "Content-Type", "application/x-ndjson; charset=utf-8"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _write_event(self, event: Any) -> None:
        if self._stream_broken:
            return
        body = json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n"
        try:
            self.wfile.write(b"%X\r\n" % len(body))
            self.wfile.write(body)
            self.wfile.write(b"\r\n")
            self.wfile.flush()
        except OSError as exc:
            # Browser closed the page: the scrape still runs to the end,
            # only the live log is dropped.
            self._stream_broken = True
            self.log_message("scraper stream closed: %s", exc)

    def _end_stream(self) -> None:
        if self._stream_broken:
            self.close_connection = True
            return
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            self.close_connection = True

    def _read_scraping_target(self, name: str, payload: Any) -> dict[str, str] | None:
        """GUIDs and label of one scraping. The stored values win, so the
        request cannot scrape another search than the selected one."""
        stored = get_storage().read_scraping(name) or {}

        occupation_guid = (
            stored.get("occupation_guid") or payload.get("occupation_guid") or ""
        )
        location_guid = (
            stored.get("location_guid") or payload.get("location_guid") or ""
        )
        if not occupation_guid or not location_guid:
            return None

        return {
            "occupation_guid": str(occupation_guid),
            "location_guid": str(location_guid),
            "label": str(stored.get("label") or payload.get("label") or ""),
        }

    def _handle_scraper_run(self) -> None:
        """Run scraper.py and answer only once it is finished.

        The response is streamed (one NDJSON line per real scraper event) so
        the terminal follows the run, but the request stays open and blocking
        for the whole duration: no task, queue or job is created.
        """
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin refused"})
            return

        payload, error = self._read_json_body(required=False)
        if error:
            self._send_json(400, {"error": error})
            return

        name = str(payload.get("name") or "").strip()
        if not config.valid_search_name(name):
            self._send_json(
                400,
                {"error": "Sélectionnez un scraping précis à actualiser."},
            )
            return

        target = self._read_scraping_target(name, payload)
        if target is None:
            self._send_json(
                404, {"error": "Recherche inconnue ou incomplète : " + name}
            )
            return

        if not _scraper_lock.acquire(blocking=False):
            self._send_json(
                409, {"error": "Un scraping est déjà en cours."}
            )
            return

        try:
            self._run_scraper(
                name, target, refresh=bool(payload.get("refresh"))
            )
        finally:
            _scraper_lock.release()

    def _run_scraper(self, name: str, target: Any, refresh: bool) -> None:
        self.log_message("scraper run started for %s", name)
        self._start_stream()
        reporter = StreamReporter(self)

        try:
            summary = scraper.run_scrape(
                occupation_guid=target["occupation_guid"],
                location_guid=target["location_guid"],
                base=name,
                label=target["label"],
                refresh=refresh,
                reporter=reporter,
                # The user already confirmed in the web interface.
                confirm=lambda question: True,
            )
        except Exception as exc:
            self.log_message("scraper run failed for %s: %s", name, exc)
            self._write_event({
                "type": "error",
                "message": str(exc) or exc.__class__.__name__,
                "details": traceback.format_exc(limit=8),
            })
            self._end_stream()
            return

        # The employer index is rebuilt from the offers just written, so the
        # "Entreprises" page never lags behind the scraping. A failure here
        # must not turn a successful scrape into an error.
        self._refresh_employer_index()

        # Export raw.json after the scrape, so the frontend always has a fresh
        # mirror of SQLite. A failure here must not turn a successful scrape into
        # an error: the data is safe in the database and the export can be re-run.
        try:
            from python import export
            export.main([])
        except Exception as exc:
            self.log_message("export failed: %s", exc)

        self.log_message(
            "scraper run finished for %s: %s offer(s), %s new, %s modified, "
            "%s error(s) in %ss",
            name,
            summary.get("total_offres"),
            summary.get("nouvelles"),
            summary.get("modifiees"),
            summary.get("erreurs"),
            summary.get("duration_seconds"),
        )
        self._write_event({"type": "done", "result": summary})
        self._end_stream()

    def _refresh_employer_index(self) -> Any:
        """Rebuild data/companies.json after a scrape.

        Best effort: an unreadable or partial index is logged and the scrape
        result is left untouched, because the offers themselves are fine and
        the index can be rebuilt again later.
        """
        try:
            stats = refresh_index()
        except Exception as exc:
            self.log_message(
                "employer index not refreshed: %s: %s",
                exc.__class__.__name__,
                exc,
            )
            return None

        self.log_message(
            "employer index refreshed: %s employer(s), %s conserved",
            stats.get("employeurs"),
            stats.get("conserves"),
        )
        return stats

    def _handle_delete_scraping(self) -> None:
        """Move all files for a scraping to trash.

        The files are moved aside rather than removed, so a mistaken deletion
        can still be undone by hand from data/trash/.
        """
        if not self._origin_allowed():
            self._send_json(403, {"error": "origin refused"})
            return

        payload, error = self._read_json_body()
        if error:
            self._send_json(400, {"error": error})
            return

        name = payload.get("name")
        if not name or not isinstance(name, str):
            self._send_json(400, {"error": "invalid name"})
            return

        # The allowed characters are what keeps the name from escaping data/.
        if not config.valid_search_name(name):
            self._send_json(400, {"error": "invalid name format"})
            return

        try:
            moved = get_storage().delete_scraping(name)
        except Exception as exc:
            self._send_json(500, {"error": str(exc)})
            return

        self._send_json(200, {"ok": True, "moved": moved})

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(
            "%s - %s\n" % (self.log_date_time_string(), format % args)
        )


def open_browser(url: str) -> bool:
    """Ouvre l'adresse dans le navigateur par défaut (Edge, Chrome, Firefox).

    Le serveur écoute déjà quand cette fonction est appelée : la requête
    du navigateur est donc servie même si elle arrive avant la boucle.
    Retourne True si un navigateur est bien lancé.
    """
    try:
        controller = webbrowser.get()
    except webbrowser.Error:
        # Aucun navigateur enregistré sur ce poste.
        return False

    # Petit délai : la page a le temps d'être servie, et l'utilisateur
    # voit d'abord la fenêtre du serveur.
    def launch() -> None:
        time.sleep(0.4)
        try:
            controller.open(url, new=1)
        except Exception:  # pragma: no cover - dépend du poste
            pass

    threading.Thread(target=launch, daemon=True).start()
    return True


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    opened = open_browser(url)
    logger.info("=" * 58)
    logger.info("  Le scraper Le Forem est démarré.")
    logger.info("")
    if opened:
        logger.info("  Le navigateur par défaut s'ouvre sur :")
    else:
        logger.info("  Ouvre cette adresse dans ton navigateur :")
    logger.info("    %s", url)
    logger.info("")
    logger.info("  Ctrl+C pour arrêter le serveur.")
    logger.info("=" * 58)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Serveur arrêté.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()