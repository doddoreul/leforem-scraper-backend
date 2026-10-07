# -*- coding: utf-8 -*-
"""End-to-end check of the four pages in a real browser.

The pages are ES modules: nothing here proves they load, that every import
resolves and that the page actually draws something. Headless Edge loads each
page against the real server, seeded with one small search, and the resulting
DOM is inspected. A module that fails to parse or to load never runs, so its
markers are missing.

The test is skipped when Edge is not installed (set EDGE_PATH to point at
another Chromium-based browser). Nothing from the Forem is requested.

Run from the repository root:
    python -m unittest discover -s tests -v
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

os.environ.setdefault("LEFOREM_STORAGE", "json")

from python import config  # noqa: E402
from python import server  # noqa: E402

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/microsoft-edge",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)

VIRTUAL_TIME_BUDGET = "5000"

# One search, two offers: enough for the table, the dashboard, the employer
# index and the offer sheet to have something to draw.
OFFERS = [
    {
        "number": "1902",
        "offer_title": "Electromecanicien industriel",
        "company": "Ateliers du Sud",
        "location": "Liege",
        "contract_type": "CDI",
        "schedule": "Temps plein Travail de jour",
        "salary": '{"min": 2800, "max": 3400}',
        "pay": '{"min": 2800, "max": 3400}',
        "published_on": "2026-09-25",
        "removed_on": "",
        # Fields derived by python/salary.py, shown on the offer sheet.
        "salary_kind": "monthly",
        "salary_min": 2800.0,
        "salary_max": 3400.0,
        "salary_gross": True,
        "salary_hourly_estimate": 17.0,
        "salary_confidence": "high",
        "meal_voucher_amount": 8.0,
        "meal_voucher_period": "day",
        "meal_voucher_mentioned": True,
        "modified_at": "2026-09-26T08:15:00",
        "modified": True,
        "date_fin_diffusion": "2026-11-30",
        "description": "<p>Tien de poste au sein d'une equipe.</p>",
        "email": "jobs@example.be",
        "is_new": True,
        "offer_state": "new",
        "diff": {
            "contract_type": ["CDD", "CDI"],
            "description": [
                "<p>Tien de poste au sein d'une equipe de jour.</p>",
                "<p>Tien de poste au sein d'une equipe de nuit.</p>",
            ],
        },
    },
    {
        "number": "1903",
        "offer_title": "Technicien de maintenance",
        "company": "Fonderie du Nord",
        "location": "Charleroi",
        "contract_type": "CDD",
        "schedule": "Temps plein Travail de jour",
        "salary": "",
        "pay": "",
        "published_on": "2026-08-02",
        "removed_on": "",
        "modified_at": "2026-08-02T09:00:00",
        "modified": "2026-08-02",
        "date_fin_diffusion": "",
        "description": "<p>Maintenance preventive.</p>",
        "email": "",
        "is_new": False,
        "offer_state": "old",
        "diff": {},
    },
    {
        "number": "1904",
        "offer_title": "Technicien disparu",
        "company": "Fonderie du Nord",
        "location": "Charleroi",
        "contract_type": "CDI",
        "schedule": "temps plein",
        "salary": "",
        "pay": "",
        "published_on": "2026-07-01",
        "removed_on": "2026-09-30T10:00:00",
        "modified_at": "2026-07-01T09:00:00",
        "modified": False,
        "date_fin_diffusion": "",
        "description": "<p>Offre retiree du site.</p>",
        "email": "",
        "is_new": False,
        "removed": True,
        "offer_state": "deleted",
        "diff": {},
    },
    {
        "number": "1905",
        "offer_title": "Offre de retour",
        "company": "Ateliers du Sud",
        "location": "Liege",
        "contract_type": "CDI",
        "schedule": "Temps plein Travail de jour",
        "salary": "",
        "pay": "",
        "published_on": "2026-06-01",
        "removed_on": "",
        "modified_at": "2026-06-01T09:00:00",
        "modified": False,
        "date_fin_diffusion": "",
        "description": "<p>De nouveau en ligne.</p>",
        "email": "",
        "is_new": True,
        "offer_state": "reappeared",
        "diff": {},
    },
    {
        "number": "1906",
        "offer_title": "Poste modifie A",
        "company": "Ateliers du Sud",
        "location": "Liege",
        "contract_type": "CDI",
        "schedule": "temps plein",
        "salary": "",
        "pay": "",
        "published_on": "2026-09-01",
        "removed_on": "",
        "modified_at": "2026-09-27T08:00:00",
        "modified": True,
        "date_fin_diffusion": "",
        "description": "<p>Description A.</p>",
        "email": "",
        "is_new": False,
        "offer_state": "old",
        "diff": {},
    },
    {
        "number": "1907",
        "offer_title": "Poste modifie B",
        "company": "Ateliers du Sud",
        "location": "Liege",
        "contract_type": "CDI",
        "schedule": "temps plein",
        "salary": "",
        "pay": "",
        "published_on": "2026-09-02",
        "removed_on": "",
        "modified_at": "2026-09-28T08:00:00",
        "modified": True,
        "date_fin_diffusion": "",
        "description": "<p>Description B.</p>",
        "email": "",
        "is_new": False,
        "offer_state": "old",
        "diff": {},
    },
    {
        "number": "1908",
        "offer_title": "Poste modifie C",
        "company": "Ateliers du Sud",
        "location": "Liege",
        "contract_type": "CDI",
        "schedule": "temps plein",
        "salary": "",
        "pay": "",
        "published_on": "2026-09-03",
        "removed_on": "",
        "modified_at": "2026-09-29T08:00:00",
        "modified": True,
        "date_fin_diffusion": "",
        "description": "<p>Description C.</p>",
        "email": "",
        "is_new": False,
        "offer_state": "old",
        "diff": {},
    },
]

DETAIL = {
    "numero": "1902",
    "titreOffre": "Electromecanicien industriel",
    "nomEmployeur": "Ateliers du Sud",
    # A string, as the Forem really publishes it: all 176 stored offers carry
    # a plain string here, not an object.
    "typeContrat": "Durée indéterminée",
    "lieuxTravail": [{"libelle": "Liege"}],
    "datePublication": "25-09-26",
    "dateDebutDiffusion": "25-09-26",
    "dateFinDiffusion": "30-11-26",
    "dateModification": "26-09-26",
    "nombrePostes": 1,
    "descriptionJob": (
        '<p><span style="color:rgb(89,89,89);background-color:rgb(255,255,255);">'
        "Une formation technique de type bachelier en electromecanique.</span>"
        '<span style="font-family:\'Tahoma\';font-size:16px;">second bloc</span></p>'
    ),
    "descriptionEmployeur": (
        '<p style="text-align: justify;">PME industrielle.'
        '<span style="background-color:rgb(253,253,253);">coll&eacute;e</span>'
        "</p>"
    ),
    "experience": {"libelle": "3 ans"},
    "etudes": [{"libelle": "Bac technique"}],
    "competencies": [{"libelle": "Electricite"}],
    "softSkills": [{"libelle": "Rigueur"}],
    "langues": [{"libelle": "Francais"}],
    "certifications": [],
    "permisConduire": [{"libelle": "B"}],
    "isDeplacementRequired": False,
    "benefits": ["Assurance"],
    "benefitsComments": "",
    "howToApply": "jobs@example.be",
    "officeSkills": [],
    # Both are plain strings, as the Forem publishes them.
    "regimeTravail": "Temps plein",
    "regimeTravailPrecision": "Travail de jour",
    "secteurActiviteEmployeur": {"libelle": "Industrie"},
    "travel": "",
    "logoMimeType": "",
    "logoEmployeur": "",
    "metier": {"libelle": "Electromecanicien"},
    "idOffreEmploi": "1902",
}

HISTORY = {
    "1902": {"timestamp": "2026-09-26T08:15:00", "offer_state": "new"},
    "1903": {"timestamp": "2026-08-02T09:00:00", "offer_state": "old"},
}

COMPANIES = {
    "version": 1,
    "employers": {
        "Ateliers du Sud": {
            "nomEmployeur": "Ateliers du Sud",
            "nombreOffres": 1,
            "emails": ["jobs@example.be"],
            "contacts": [],
            "adresses": [{"rue": "Rue du Moulin 1",
                           "codePostal": "4000",
                           "municipalite": "Liege"}],
            "sites": [],
            "secteurs": [],
            "partenaires": [],
            "description": "PME industrielle.",
            "offers": [{"numero": "1902",
                        "titre": "Electromecanicien industriel"}],
            "premierePublication": "2026-09-25",
            "dernieresModifications": ["2026-09-26"],
        }
    },
    "stats": {"total": 1, "avecEmail": 1},
}

# Loads the offer sheet in an iframe and clicks its diff button, so a
# --dump-dom run shows what the user gets after asking for the diff.
DIFF_PROBE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<iframe id="frame" src="/detail.html?number=1902&base=metier_liege"
        width="900" height="600"></iframe>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\\n"); }

const frame = document.getElementById("frame");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// The profile is reused by every test, and the choice is remembered on
// purpose: start from a clean slate so the first state is the default one.
try { localStorage.removeItem("forem_plain_styles"); } catch (e) {}
frame.src = "/detail.html?number=1902&base=metier_liege&reload=1";

for (let i = 0; i < 60; i += 1) {
    await sleep(250);
    const doc = frame.contentDocument;
    if (!doc) continue;

    // --- The pasted styles, before the button is used -----------------
    const rich = doc.querySelector(".rich-html");
    const pasted = rich && rich.querySelector("[style]");
    log("styledBefore=" + (pasted ? pasted.getAttribute("style") : "none"));
    const styleToggle = doc.querySelector(".style-toggle");
    log("styleButtonBefore=" + (styleToggle
        ? styleToggle.textContent.trim() + "/" + styleToggle.getAttribute("aria-pressed")
        : "absent"));

    if (styleToggle) {
        styleToggle.click();
        await sleep(120);
        const after = rich.querySelector("[style]");
        log("styledAfter=" + (after ? after.getAttribute("style") : "none"));
        log("styleButtonAfter=" + styleToggle.textContent.trim() + "/"
            + styleToggle.getAttribute("aria-pressed"));
        log("stored=" + (frame.contentWindow.localStorage
            .getItem("forem_plain_styles") || "absent"));

        // And back on: the styles the employer pasted must return.
        styleToggle.click();
        await sleep(120);
        const back = rich.querySelector("[style]");
        log("restyled=" + (back ? back.getAttribute("style") : "none"));
        log("styleButtonBack=" + styleToggle.textContent.trim() + "/"
            + styleToggle.getAttribute("aria-pressed"));
        log("storedBack=" + (frame.contentWindow.localStorage
            .getItem("forem_plain_styles") || "absent"));
    }

    // --- The diff, before the button is used --------------------------
    const toggle = doc.querySelector(".diff-toggle");
    if (!toggle) { log("DIFF-NOT-FOUND"); break; }

    const body = doc.querySelector(".diff-body");
    log("button=" + toggle.textContent.trim());
    log("hiddenBefore=" + body.classList.contains("hidden"));
    log("blocksBefore=" + doc.querySelectorAll(".diff-block").length);

    toggle.click();
    await sleep(120);
    log("buttonAfter=" + toggle.textContent.trim());
    log("hiddenAfter=" + body.classList.contains("hidden"));
    log("blocksAfter=" + doc.querySelectorAll(".diff-block").length);
    log("fields=" + Array.from(doc.querySelectorAll(".diff-field"))
        .map(n => n.textContent).join("|"));
    log("oldText=" + (doc.querySelector(".diff-old .diff-text") || {}).textContent);
    log("newText=" + (doc.querySelector(".diff-new .diff-text") || {}).textContent);
    log("DIFF-OK");
    break;
}
log("done");
</script>
</body></html>
"""

# Loads the offers page in an iframe and clicks "Voir plus" on the tracked
# alert panel, so a --dump-dom run shows the unfolded state.
TRACKED_PROBE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<iframe id="frame" src="/" width="1000" height="800"></iframe>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\\n"); }

const frame = document.getElementById("frame");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

for (let i = 0; i < 80; i += 1) {
    await sleep(250);
    const doc = frame.contentDocument;
    if (!doc) continue;
    const list = doc.getElementById("trackedAlertList");
    const button = doc.getElementById("trackedAlertToggleBtn");
    if (!list || !button) continue;
    if (button.classList.contains("hidden")) continue;
    if (!list.classList.contains("is-limited")) continue;

    log("beforeLimited=" + list.classList.contains("is-limited"));
    log("beforeText=" + button.textContent);
    button.click();
    await sleep(150);
    log("afterLimited=" + list.classList.contains("is-limited"));
    log("afterText=" + button.textContent);
    log("TRACKED-OK");
    break;
}
</script>
</body></html>
"""

# Dismisses the tracked panel, opens the page again in the same origin and
# checks the dismissal survives until a new scrape date replaces it.
DISMISS_PROBE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const KEY = "forem_tracked_alerts_dismissed";

// A dismissal recorded for an older scrape must not hide the panel.
localStorage.setItem(KEY, "1999-01-01T00:00:00");

async function openPage() {
    const frame = document.createElement("iframe");
    frame.width = 1200;
    frame.height = 900;
    const loaded = new Promise(resolve => { frame.onload = resolve; });
    frame.src = "/";
    document.body.appendChild(frame);
    await loaded;
    const doc = frame.contentDocument;
    for (let i = 0; i < 40; i += 1) {
        const panel = doc.getElementById("trackedAlertPanel");
        const items = panel ? panel.querySelectorAll(".tracked-alert-item") : [];
        if (items.length > 0) return { frame: frame, doc: doc, panel: panel };
        await sleep(250);
    }
    return { frame: frame, doc: doc, panel: doc.getElementById("trackedAlertPanel") };
}

let page = await openPage();
log("staleVisible=" + !page.panel.classList.contains("hidden"));

page.doc.getElementById("trackedAlertDismissBtn").click();
await sleep(100);
log("afterClickHidden=" + page.panel.classList.contains("hidden"));

// A fresh render of the same scrape re-reads the marker and stays hidden.
page.doc.dispatchEvent(new Event("foremsuiviimported"));
await sleep(100);
log("rerenderHidden=" + page.panel.classList.contains("hidden"));

const stored = localStorage.getItem(KEY) || "";
log("storedForScrape=" + (stored !== "" && stored !== "1999-01-01T00:00:00"));
page.frame.remove();

localStorage.removeItem(KEY);
log("DISMISS-OK");
</script>
</body></html>
"""

# Proves the follow-up lives in the browser and survives a reload: a
# follow-up is seeded, written through the module, then the cache is dropped
# and rebuilt from localStorage. If the reads came from anything but
# localStorage the rebuilt cache would be empty.
PERSIST_PROBE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\\n"); }

import {
    migrateLegacyStorage,
    loadAllTracking,
    readTrackedMap,
    writeTrackedMap,
} from "/js/shared/storage.js";

const BASE = "persiste";
const PREFIX = "forem_" + BASE + "_";

// A follow-up that only exists in the browser so far.
localStorage.setItem(PREFIX + "statuts", JSON.stringify({ "9001": "postule" }));
localStorage.setItem(PREFIX + "remarques", JSON.stringify({ "9001": "a relancer" }));

await migrateLegacyStorage();

// 1. The seeded follow-up is readable through the module.
log("seededStatut=" + (readTrackedMap(PREFIX, "statuts")["9001"] || ""));
log("seededRemarque=" + (readTrackedMap(PREFIX, "remarques")["9001"] || ""));

// 2. A status set through the module is readable again straight away: the
//    cache updates before the caller returns.
await writeTrackedMap(PREFIX, "statuts", { "9001": "contacte", "9002": "refuse" });
log("immediateRead=" + (readTrackedMap(PREFIX, "statuts")["9001"] || ""));

// 3. Nothing left the machine: the raw key holds the edit.
const stored = JSON.parse(localStorage.getItem(PREFIX + "statuts") || "{}");
log("storedStatut=" + (stored["9001"] || ""));
log("storedSecond=" + (stored["9002"] || ""));

// 4. Dropping the cache and rebuilding it from localStorage gives the same
//    follow-up back: that is what a page reload does.
await loadAllTracking();
log("reloadStatut=" + (readTrackedMap(PREFIX, "statuts")["9001"] || ""));
log("reloadSecond=" + (readTrackedMap(PREFIX, "statuts")["9002"] || ""));
log("reloadRemarque=" + (readTrackedMap(PREFIX, "remarques")["9001"] || ""));

// 5. Clearing a field really removes it.
await writeTrackedMap(PREFIX, "statuts", { "9001": "contacte" });
await loadAllTracking();
log("afterClearSecond=" + (readTrackedMap(PREFIX, "statuts")["9002"] || ""));

localStorage.removeItem(PREFIX + "statuts");
localStorage.removeItem(PREFIX + "remarques");
log("PERSIST-OK");
</script>
</body></html>
"""

# Reads the rendered offer sheet and reports the remuneration lines, so a
# --dump-dom run shows what the user actually gets.
REMUN_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<iframe id="frame" src="/detail.html?number=1902&base=metier_liege"
        width="1000" height="900"></iframe>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }

const frame = document.getElementById("frame");
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

for (let i = 0; i < 80; i += 1) {
    await sleep(250);
    const doc = frame.contentDocument;
    if (!doc) continue;
    const root = doc.getElementById("detailRoot");
    if (!root) continue;

    const titles = Array.from(root.querySelectorAll(".card-title"))
        .map(node => node.textContent);
    if (titles.indexOf("Remuneration") === -1
            && titles.indexOf("Rémunération") === -1) {
        if (i === 79) {
            log("cards=" + titles.join("|"));
            log("NO-RUN");
        }
        continue;
    }

    const items = Array.from(root.querySelectorAll(".card .profile-list li"))
        .map(node => node.textContent);
    log("titles=" + titles.join("|"));
    items.forEach((item, index) => log("item" + index + "=" + item));

    // The diff card must still work after findOffer changed shape.
    const diff = root.querySelector(".diff-card .diff-meta");
    log("diffMeta=" + (diff ? diff.textContent : "absent"));

    log("RUN-OK");
    break;
}
</script>
</body></html>
"""

# The profile keywords must be highlighted in the listing and on the offer
# sheet. The iframe skeleton is the one the other probes here use.
KEYWORD_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// The profile must exist before the page reads it, so it is stored here and
// the frame is only built afterwards.
const KEYWORDS = "Mecanicien, equipe";
const profile = {
    version: 1,
    keywordsText: KEYWORDS,
    keywords: KEYWORDS.split(",").map(s => s.trim()),
    hourlyRate: null,
    contractTypes: [],
    maxDistanceKm: null,
    updatedAt: "2026-10-06",
};
localStorage.setItem("forem_profil", JSON.stringify(profile));
await fetch("/api/profil", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
});

async function openPage(url, ready) {
    const frame = document.createElement("iframe");
    frame.width = 1200;
    frame.height = 900;
    const loaded = new Promise(resolve => { frame.onload = resolve; });
    frame.src = url;
    document.body.appendChild(frame);
    await loaded;
    for (let i = 0; i < 100; i += 1) {
        await sleep(200);
        const doc = frame.contentDocument;
        if (doc && ready(doc)) return doc;
    }
    return frame.contentDocument;
}

const listDoc = await openPage(
    "/",
    doc => doc.querySelectorAll("#currentRows tr").length > 1
);
await sleep(400);

const listMarks = listDoc.querySelectorAll("#currentRows mark.keyword-hit");
log("listRows=" + listDoc.querySelectorAll("#currentRows tr").length);
log("listMarks=" + listMarks.length);
log("listMarkTexts=" + Array.from(listMarks).map(m => m.textContent).join("|"));
log("marksInLink=" + listDoc.querySelectorAll("#currentRows a[data-number] mark").length);
log("listTextIntact=" + (listDoc.querySelector("#currentRows a[data-number]")
    ? listDoc.querySelector("#currentRows a[data-number]").textContent : "NONE"));

const detailDoc = await openPage(
    "/detail.html?number=1902&base=metier_liege",
    doc => doc.getElementById("detailRoot")
        && doc.querySelectorAll("#detailRoot .card").length > 1
);
await sleep(400);

const root = detailDoc.getElementById("detailRoot");
const detailMarks = root ? root.querySelectorAll("mark.keyword-hit") : [];
log("detailCards=" + (root ? root.querySelectorAll(".card").length : 0));
log("detailMarks=" + detailMarks.length);
log("detailMarkTexts=" + Array.from(detailMarks).map(m => m.textContent).join("|"));
log("marksInTextarea=" + (root ? root.querySelectorAll("textarea mark").length : -1));
log("nestedTagsInMark=" + (root ? root.querySelectorAll("mark *").length : -1));

log("KEYWORD-OK");
</script>
</body></html>
"""

# Accent and case folding, for the filters and for the highlighting. The
# letters NFD cannot decompose (oe, ae, o-slash, sharp s) are handled
# explicitly, and a ligature must not shift the indexes of what follows.
FOLD_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
import { matchRanges, highlightIn } from "/js/shared/highlight.js";
import { normalizeText } from "/js/shared/text.js";

const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\\n"); }

const failed = [];
function check(label, got, expected) {
    const same = JSON.stringify(got) === JSON.stringify(expected);
    if (same) {
        log("ok " + label);
    } else {
        log("FAIL " + label + " got=" + JSON.stringify(got)
            + " expected=" + JSON.stringify(expected));
        failed.push(label);
    }
}

// normalizeText: what the filters rely on.
check("filter folds accents and case",
    normalizeText("Électromécanicien"), "electromecanicien");
check("filter folds the oe ligature",
    normalizeText("Manœuvre"), "manoeuvre");
check("filter folds oe in coeur",
    normalizeText("Cœur"), "coeur");
check("filter folds oe uppercase",
    normalizeText("ŒUVRE"), "oeuvre");
check("filter folds the ae ligature",
    normalizeText("Cæsar"), "caesar");
check("filter folds the o-slash",
    normalizeText("Møller"), "moller");
check("filter folds the sharp s",
    normalizeText("Straße"), "strasse");

// matchRanges: the highlighting. "Manœuvre" is 8 characters even though the
// oe counts for 2 once folded.
check("highlight finds a plain keyword in accented text",
    matchRanges("Électromécanicien", ["electromecanicien"]), [[0, 17]]);
check("highlight finds an accented keyword",
    matchRanges("électromécanicien", ["ÉLECTROMÉCANICIEN"]), [[0, 17]]);
check("highlight finds the oe ligature",
    matchRanges("Manœuvre", ["manoeuvre"]), [[0, 8]]);
check("highlight finds an oe keyword in accented text",
    matchRanges("Manœuvre en électromécanicien", ["manoeuvre"]), [[0, 8]]);
check("highlight ignores case",
    matchRanges("maintenance", ["MAINTENANCE"]), [[0, 11]]);

// The text after a ligature must still be cut at the right place.
const host = document.createElement("div");
host.textContent = "";
host.appendChild(document.createTextNode("Manœuvre en électromécanicien"));
highlightIn(host, ["manoeuvre", "electromecanicien"]);
const marks = Array.from(host.querySelectorAll("mark"));
check("two marks", marks.length, 2);
check("mark texts", marks.map(m => m.textContent),
    ["Manœuvre", "électromécanicien"]);
check("the whole text is unchanged",
    host.textContent, "Manœuvre en électromécanicien");

log(failed.length ? "FOLD-FAILED:" + failed.join("|") : "FOLD-OK");
</script>
</body></html>
"""

# The box must search every field of the listing, not a hand-written list:
# these words live in fields no list mentioned.
FIELDS_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const frame = document.createElement("iframe");
frame.width = 1200;
frame.height = 900;
const loaded = new Promise(resolve => { frame.onload = resolve; });
frame.src = "/";
document.body.appendChild(frame);
await loaded;

let doc = null;
for (let i = 0; i < 120; i += 1) {
    await sleep(200);
    doc = frame.contentDocument;
    if (doc && doc.querySelectorAll("#currentRows tr[data-number]").length > 1) break;
}
await sleep(500);

const rows = Array.from(doc.querySelectorAll("#currentRows tr[data-number]"));
const input = doc.getElementById("currentSearch");
const visible = () => rows.filter(r => r.style.display !== "none").length;
log("total=" + rows.length);
log("baseline=" + visible());

async function type(term) {
    input.value = term;
    input.dispatchEvent(new InputEvent("input", { bubbles: true }));
    await sleep(320);
    return visible();
}

// Fields no hand-written list mentioned.
log("endDate=" + await type("2026-11-30"));   // date_fin_diffusion
log("hourly=" + await type("17"));             // salary_hourly_estimate
log("voucherPeriod=" + await type("day"));     // meal_voucher_period
log("offerState=" + await type("new"));        // offer_state
log("confidence=" + await type("high"));       // salary_confidence
log("published=" + await type("2026-09-25"));  // published_on

// A nested object: the diff sits one level down.
const payload = await (await fetch("/data_metier_liege.json"))
    .json().catch(() => null);
const offers = (payload && payload.offers) || [];
const diff = (offers[0] && offers[0].diff) || {};
const diffKeys = Object.keys(diff);
log("diffKeys=" + diffKeys.join(","));
const nested = (diff[diffKeys[0]] || [])[0];
log("nestedValue=" + nested);
log("nestedMatch=" + await type(String(nested).trim()));

// A markup tag must still not match every offer.
log("markupWord=" + await type("div"));
log("cleared=" + await type(""));

log("FIELDS-OK");
</script>
</body></html>
"""

# The search box must follow the keyboard. Dispatching keydown/keyup with no
# input event at all is the case that used to do nothing, and the offer
# description must stay searchable after the filter moved off the rendered row.
KEYS_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const frame = document.createElement("iframe");
frame.width = 1200;
frame.height = 900;
const loaded = new Promise(resolve => { frame.onload = resolve; });
frame.src = "/";
document.body.appendChild(frame);
await loaded;

let doc = null;
for (let i = 0; i < 120; i += 1) {
    await sleep(200);
    doc = frame.contentDocument;
    if (doc && doc.querySelectorAll("#currentRows tr[data-number]").length > 1) break;
}
await sleep(500);

const rows = Array.from(doc.querySelectorAll("#currentRows tr[data-number]"));
const input = doc.getElementById("currentSearch");
const visible = () => rows.filter(r => r.style.display !== "none").length;
log("total=" + rows.length);
log("baseline=" + visible());

// keydown, value changes, keyup: no input event is ever sent.
function press(key) {
    input.dispatchEvent(new KeyboardEvent("keydown", {
        key, bubbles: true, cancelable: true,
    }));
    input.value += key;
    input.dispatchEvent(new KeyboardEvent("keyup", {
        key, bubbles: true, cancelable: true,
    }));
}

input.value = "";
input.dispatchEvent(new InputEvent("input", { bubbles: true }));
await sleep(300);
for (const ch of "Electromecanicien") press(ch);
await sleep(450);
log("keysOnly=" + visible());

// A word that lives only in the description.
const payload = await (await fetch("/data_metier_liege.json"))
    .json().catch(() => null);
const offers = Array.isArray(payload) ? payload : (payload.offers || []);
const html = (offers[0] && offers[0].description) || "";
const word = html.replace(/<[^>]*>/g, " ").split(/\s+/)
    .map(w => w.toLowerCase().replace(/[^a-z]/g, ""))
    .find(w => w.length > 3) || "";
log("descWord=" + word);
input.value = word;
input.dispatchEvent(new InputEvent("input", { bubbles: true }));
await sleep(400);
log("descMatch=" + visible());

// A markup word must not match: the description is stripped first.
input.value = "div";
input.dispatchEvent(new InputEvent("input", { bubbles: true }));
await sleep(400);
log("markupWord=" + visible());

input.value = "";
input.dispatchEvent(new InputEvent("input", { bubbles: true }));
await sleep(400);
log("cleared=" + visible());

log("KEYS-OK");
</script>
</body></html>
"""

# The listing search must look at the offer data, not at the rendered row.
# The row text also carries the status option labels, the state badges and the
# remarks textarea, so typing "postule" used to match every row and looked like
# the search did nothing.
SEARCH_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const frame = document.createElement("iframe");
frame.width = 1200;
frame.height = 900;
const loaded = new Promise(resolve => { frame.onload = resolve; });
frame.src = "/";
document.body.appendChild(frame);
await loaded;

let doc = null;
for (let i = 0; i < 120; i += 1) {
    await sleep(200);
    doc = frame.contentDocument;
    if (doc && doc.querySelectorAll("#currentRows tr[data-number]").length > 1) break;
}
await sleep(500);

const rows = Array.from(doc.querySelectorAll("#currentRows tr[data-number]"));
const input = doc.getElementById("currentSearch");
const visible = () => rows.filter(r => r.style.display !== "none").length;
const total = rows.length;

log("total=" + total);

// The rendered row carries the whole status menu.
log("rowHasOptionLabels=" + rows[0].querySelectorAll("option").length);

async function type(term) {
    input.value = term;
    input.dispatchEvent(new InputEvent("input", { bubbles: true }));
    await sleep(350);
    return visible();
}

log("title=" + await type("Electromecanicien"));
log("location=" + await type("liege"));
// Chrome words: they must not match every row.
log("statusLabel=" + await type("postule"));
log("stateBadge=" + await type("nouvelle"));
log("starButton=" + await type("favori"));
log("cleared=" + await type(""));

// One offer is marked "Postulé", so the word must now find exactly it.
const select = rows[0].querySelector("select.status-select");
if (select) {
    select.value = "postule";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(450);
    log("markedThenSearched=" + await type("postule"));
}

log("SEARCH-OK");
</script>
</body></html>
"""

# validateProfile is pure, so the browser is only needed to load the module
# and check the real rejections on the real page.
PROFILE_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
import {
    validateProfile,
    parseKeywords,
    emptyProfile,
    normaliseProfile,
} from "/js/shared/profile.js";

const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }

try {
// An empty profile is valid: every field is optional.
log("emptyOk=" + validateProfile(emptyProfile()).ok);

// A negative hourly rate is refused.
log("rateNegRejected=" + Boolean(validateProfile({ hourlyRate: "-5" }).errors.hourlyRate));

// A distance over 500 km is refused.
log("distanceBigRejected=" + Boolean(validateProfile({ maxDistanceKm: "900" }).errors.maxDistanceKm));

// Valid values pass and the keyword list is built: "Nuit" is a duplicate of
// "nuit" once case and accents are ignored.
const good = validateProfile({
    keywordsText: "nuit, maintenance\nNuit , electricite",
    hourlyRate: "15,50",
    maxDistanceKm: "25",
    contractTypes: ["CDI"],
});
log("goodOk=" + good.ok);
log("rate=" + good.value.hourlyRate);
log("distance=" + good.value.maxDistanceKm);
log("keywords=" + good.value.keywords.join("|"));

// Commas and newlines are both separators, and only the empties are dropped.
log("parseEmpty=" + JSON.stringify(parseKeywords("  ,  ,  ")));
log("parseMixed=" + JSON.stringify(
    parseKeywords("nuit\n  ," + String.fromCharCode(10) + " electricite")));

// The postal code is gone: the field no longer exists in the empty profile, is
// not validated, and a stored profile that still carries one drops it.
log("emptyHasPostal=" + ("postalCode" in emptyProfile()));
log("storedPostalDropped=" + (
    normaliseProfile({ version: 1, postalCode: "4000", keywordsText: "nuit" })
        .postalCode === undefined));
log("postalIgnored=" + (
    validateProfile({ postalCode: "abcd" }).ok === true));

log("PROFILE-OK");
} catch (error) {
    log("PROBE-ERROR=" + (error && error.message ? error.message : String(error)));
}
</script>
</body></html>
"""

# Imports every module and calls the shared helpers: the import errors a
# --dump-dom run cannot show are reported here.
PROBE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\\n"); }

const MODULES = [
    "api.js", "dates.js", "dom.js", "links.js", "navbar.js", "paths.js",
    "scraping-selector.js", "statuses.js", "storage.js", "suivi.js",
    "text.js", "theme.js",
];

try {
    const loaded = {};
    for (const name of MODULES) {
        loaded[name] = await import("/js/shared/" + name);
    }
    const api = loaded["api.js"];
    const dates = loaded["dates.js"];
    const dom = loaded["dom.js"];
    const links = loaded["links.js"];
    const statuses = loaded["statuses.js"];
    const storage = loaded["storage.js"];
    const suivi = loaded["suivi.js"];
    const text = loaded["text.js"];
    const selector = loaded["scraping-selector.js"];

    log("imported=" + MODULES.length);
    log("dates=" + dates.parseForemDate("25-09-26").getFullYear());
    log("dateTime=" + dates.formatDateTime("2026-09-26T08:15:00"));
    log("detailHref=" + links.detailHref(1902, "metier-liege"));
    log("offerUrl=" + links.offerUrl(1902));
    log("statuses=" + statuses.STATUS_OPTIONS.length + "/"
        + statuses.PRIORITY_OPTIONS.length + " " + statuses.statusLabel(""));
    log("prefix=" + storage.storagePrefixFor("metier-liege"));
    log("gears=" + suivi.TRACKING_GEAR_ACTIONS.length);
    log("el=" + dom.el("p", "x", "y").outerHTML);
    log("normalize=" + text.normalizeText("Electromecanicien"));
    log("selector=" + selector.STORAGE_KEY + " "
        + typeof selector.createScrapingSelector);
    log("scrapings=" + (await api.fetchScrapings()).length);
    log("PROBE-OK");
} catch (error) {
    log("PROBE-FAIL " + (error && error.stack ? error.stack : error));
}
</script>
</body></html>
"""


def find_browser():
    candidates = [os.environ.get("EDGE_PATH") or ""]
    candidates.extend(EDGE_CANDIDATES)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


BROWSER = find_browser()


# The locality menu is built from the loaded offers, not from the Forem
# nomenclature: lieuxTravail is free text typed by the employer, so the same
# city arrives spelled "LIÈGE" from one offer and "Liège" from the next.
LOCATION_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// Show the fixture written by this test class, not the default scraping.
// The selector keys on the dataset name, which is what the export carries.
localStorage.setItem("forem_scraping_select", "casse");

const frame = document.createElement("iframe");
frame.width = 1400;
frame.height = 900;
const loaded = new Promise(resolve => { frame.onload = resolve; });
frame.src = "/";
document.body.appendChild(frame);
await loaded;

let doc = null;
for (let i = 0; i < 120; i += 1) {
    await sleep(200);
    doc = frame.contentDocument;
    if (doc && doc.querySelectorAll("#currentRows tr[data-number]").length > 4) break;
}
await sleep(500);

const rows = Array.from(doc.querySelectorAll("#currentRows tr[data-number]"));
const select = doc.getElementById("locationFilter");
const visible = () => rows.filter(r => r.style.display !== "none").length;
const options = Array.from(select.options).map(o => o.textContent);

log("total=" + rows.length);
log("baseline=" + visible());
// Sorted for the log: the menu order follows the French collation, which is
// not what this test is about.
log("options=" + options.slice().sort().join(" | "));
log("liegeOptions=" + options.filter(o => /li/i.test(o)).length);
log("herstalOptions=" + options.filter(o => /herstal/i.test(o)).length);
// Nothing shouted survives.
log("shoutedLeft=" + options.filter(o => /[A-Z]{2}/.test(o)).length);

async function pick(label) {
    select.value = label;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(320);
    return visible();
}

["Liège", "Herstal", "Namur", "Grâce-hollogne", "4000",
    "Arrondissement de Namur"].forEach(function (label) {
    log("has[" + label + "]=" + options.includes(label));
});
for (const label of options.slice(1)) {
    log("pick[" + label + "]=" + await pick(label));
}

select.value = "";
select.dispatchEvent(new Event("change", { bubbles: true }));
await sleep(300);
log("cleared=" + visible());

log("LOC-OK");
</script>
</body></html>
"""

# One offer may name several workplaces, "Arrondissement de Waremme,
# Arrondissement de Liège, Hannut". The menu must offer three places, not one
# joined string, and picking any of them must keep that offer. The fixture only
# carries the joined string, so this also covers offers scraped before the
# scraper started storing the list.
MULTI_LOCATIONS_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

localStorage.setItem("forem_scraping_select", "multi");

const frame = document.createElement("iframe");
frame.width = 1400;
frame.height = 900;
const loaded = new Promise(resolve => { frame.onload = resolve; });
frame.src = "/";
document.body.appendChild(frame);
await loaded;

let doc = null;
for (let i = 0; i < 120; i += 1) {
    await sleep(200);
    doc = frame.contentDocument;
    if (doc && doc.querySelectorAll("#currentRows tr[data-number]").length > 4) break;
}
await sleep(500);

const rows = Array.from(doc.querySelectorAll("#currentRows tr[data-number]"));
const select = doc.getElementById("locationFilter");
const visible = () => rows.filter(r => r.style.display !== "none").length;
const values = Array.from(select.options).map(o => o.value);

log("total=" + rows.length);
log("baseline=" + visible());
log("values=" + values.slice().sort().join(" | "));
// No entry may still hold a comma.
log("joinedEntryLeft=" + values.filter(v => v.indexOf(",") !== -1).length);

async function pick(place) {
    select.value = place;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await sleep(320);
    return visible();
}

log("pick[Hannut]=" + await pick("Hannut"));
log("pick[Arrondissement de Liège]=" + await pick("Arrondissement de Liège"));
log("pick[Arrondissement de Waremme]=" + await pick("Arrondissement de Waremme"));
log("pick[Bruxelles]=" + await pick("Bruxelles"));
// Liège sits in two offers written "LIÈGE" plus the one joined with Bruxelles.
log("pick[Liège]=" + await pick("Liège"));
log("cleared=" + await pick(""));

log("MULTI-OK");
</script>
</body></html>
"""

# The profile carries two keyword lists: the wanted ones, marked in yellow, and
# the excluded ones, marked in red. Both must be found, and the excluded one
# must win when a stretch is in both lists.
EXCLUDED_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const KEYWORDS = "mecanicien";
// "poste" is really in the seeded offers, so the marking is exercised.
const EXCLUDED = "poste";
const profile = {
    version: 1,
    keywordsText: KEYWORDS,
    keywords: KEYWORDS.split(","),
    excludedText: EXCLUDED,
    excluded: EXCLUDED.split(","),
    hourlyRate: null,
    contractTypes: [],
    maxDistanceKm: null,
    updatedAt: "2026-10-06",
};
localStorage.setItem("forem_profil", JSON.stringify(profile));
await fetch("/api/profil", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
});

const frame = document.createElement("iframe");
frame.width = 1200;
frame.height = 900;
const loaded = new Promise(resolve => { frame.onload = resolve; });
frame.src = "/";
document.body.appendChild(frame);
await loaded;

let doc = null;
for (let i = 0; i < 120; i += 1) {
    await sleep(200);
    doc = frame.contentDocument;
    if (doc && doc.querySelectorAll("#currentRows tr[data-number]").length > 1) break;
}
await sleep(700);

log("wanted=" + doc.querySelectorAll("#currentRows mark.keyword-hit:not(.keyword-hit--danger)").length);
log("excluded=" + doc.querySelectorAll("#currentRows mark.keyword-hit--danger").length);

// The red mark really holds the excluded word, and carries no yellow class.
const red = doc.querySelector("#currentRows mark.keyword-hit--danger");
log("redText=" + (red ? red.textContent.trim() : "aucun"));
// The base class must come along, or the mark loses its padding and weight.
log("redHasPlainClass=" + (
    red ? red.className.split(" ").indexOf("keyword-hit") !== -1 : false));

// The offer sheet marks them too.
const detail = document.createElement("iframe");
detail.width = 1200;
detail.height = 900;
const loadedDetail = new Promise(resolve => { detail.onload = resolve; });
detail.src = "/detail.html?number=1902";
document.body.appendChild(detail);
await loadedDetail;
let dd = null;
for (let i = 0; i < 100; i += 1) {
    await sleep(200);
    dd = detail.contentDocument;
    if (dd && dd.querySelectorAll("mark").length) break;
}
await sleep(600);
log("detailExcluded=" + (dd
    ? dd.querySelectorAll("mark.keyword-hit--danger").length : "page absente"));

log("EXCL-OK");
</script>
</body></html>
"""

# A tick beside each value the candidate asked for: the contract, the working
# regime and the mention. The friendly labels have to reach the wording the
# Forem really publishes, and the listing stores regime and mention in one
# "schedule" string, so both must answer to it.
WANTED_TICK_PROBE = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head><body>
<pre id="out">pending</pre>
<script type="module">
const out = document.getElementById("out");
const lines = [];
function log(line) { lines.push(line); out.textContent = lines.join("\n"); }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const profile = {
    version: 1,
    keywordsText: "", keywords: [], excludedText: "", excluded: [],
    hourlyRate: null,
    contractTypes: ["CDI"],
    scheduleTypes: ["Temps plein"],
    mentionTypes: ["jour"],
    maxDistanceKm: null,
    updatedAt: "2026-10-06",
};
localStorage.setItem("forem_profil", JSON.stringify(profile));
await fetch("/api/profil", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
});

const mod = await import("/js/shared/profile.js");
const wanted = { contracts: ["CDI"], schedules: ["Temps plein"], mentions: ["jour"] };

// The contract wording. Only CDI is ticked in this profile, so Intérimaire
// must not match.
log("cdi=" + mod.contractIsWanted("Durée indéterminée", wanted.contracts));
log("cdd=" + mod.contractIsWanted("Durée déterminée", wanted.contracts));
log("interim=" + mod.contractIsWanted("Intérimaire", wanted.contracts));
// An offer that is both still counts, because CDI is ticked.
log("lesDeux=" + mod.contractIsWanted(
    "Intérimaire avec option sur durée indéterminée", wanted.contracts));
log("remplacement=" + mod.contractIsWanted("Remplacement", wanted.contracts));

// regimeTravail.
log("regimePlein=" + mod.scheduleIsWanted("Temps plein", wanted.schedules));
log("regimePartiel=" + mod.scheduleIsWanted("Temps partiel", wanted.schedules));

// regimeTravailPrecision.
log("mentionJour=" + mod.mentionIsWanted("Travail de jour", wanted.mentions));
log("mentionNuit=" + mod.mentionIsWanted("Travail de nuit", wanted.mentions));
log("mentionPauses=" + mod.mentionIsWanted("Travail posté 3 pauses", wanted.mentions));
log("mentionVide=" + mod.mentionIsWanted("", wanted.mentions));

// The listing packs regime and mention into one "schedule" string.
const merged = "Temps plein Travail de jour";
log("fusionRegime=" + mod.scheduleIsWanted(merged, wanted.schedules));
log("fusionMention=" + mod.mentionIsWanted(merged, wanted.mentions));

const loaded = await mod.loadWantedTypes();
log("chargeRegime=" + loaded.schedules.join("|"));
log("chargeMention=" + loaded.mentions.join("|"));

const frame = document.createElement("iframe");
frame.width = 1400;
frame.height = 900;
const loadedFrame = new Promise(resolve => { frame.onload = resolve; });
frame.src = "/";
document.body.appendChild(frame);
await loadedFrame;

let doc = null;
for (let i = 0; i < 120; i += 1) {
    await sleep(200);
    doc = frame.contentDocument;
    if (doc && doc.querySelectorAll("#currentRows tr[data-number]").length > 1) break;
}
await sleep(800);

const rows = doc.querySelector("#currentRows");
log("ticksContrat=" + rows.querySelectorAll(".wanted-tick--contract").length);
log("ticksRegime=" + rows.querySelectorAll(".wanted-tick--schedule").length);
log("ticksMention=" + rows.querySelectorAll(".wanted-tick--mention").length);

function onLine(selector, expectedLabel) {
    const tick = rows.querySelector(selector);
    if (!tick) return "absent";
    return tick.parentElement.textContent.indexOf(expectedLabel) === 0;
}
log("surLigneContrat=" + onLine(".wanted-tick--contract", "Contrat"));
log("surLigneRegime=" + onLine(".wanted-tick--schedule", "Horaire"));
log("surLigneMention=" + onLine(".wanted-tick--mention", "Horaire"));

const detail = document.createElement("iframe");
detail.width = 1200;
detail.height = 900;
const loadedDetail = new Promise(resolve => { detail.onload = resolve; });
detail.src = "/detail.html?number=1902&base=metier_liege";
document.body.appendChild(detail);
await loadedDetail;
let dd = null;
for (let i = 0; i < 100; i += 1) {
    await sleep(200);
    dd = detail.contentDocument;
    if (dd && dd.querySelector(".detail-facts")) break;
}
await sleep(900);
log("ficheRegime=" + (dd ? dd.querySelectorAll(".wanted-tick--schedule").length : "absente"));
log("ficheMention=" + (dd ? dd.querySelectorAll(".wanted-tick--mention").length : "absente"));
// The sheet shows these values twice: as chips in the banner and again in
// "Informations pratiques". Both places carry the tick.
const info = dd ? dd.querySelector(".detail-info") : null;
log("puceRegime=" + (dd
    ? dd.querySelectorAll(".detail-facts .wanted-tick--schedule").length : "absente"));
log("infoRegime=" + (info ? info.querySelectorAll(".wanted-tick--schedule").length : "absente"));
log("infoMention=" + (info ? info.querySelectorAll(".wanted-tick--mention").length : "absente"));
log("infoContrat=" + (info ? info.querySelectorAll(".wanted-tick--contract").length : "absente"));

log("TICK-OK");
</script>
</body></html>
"""

class ProbeHandler(server.Handler):
    """The real handler, plus the page that exercises every module."""

    def _serve_file(self, path):
        probes = {
            "/probe.html": PROBE,
            "/diff-probe.html": DIFF_PROBE,
            "/tracked-probe.html": TRACKED_PROBE,
            "/dismiss-probe.html": DISMISS_PROBE,
            "/persist-probe.html": PERSIST_PROBE,
            "/profile-probe.html": PROFILE_PROBE,
            "/remun-probe.html": REMUN_PROBE,
            "/keyword-probe.html": KEYWORD_PROBE,
            "/fold-probe.html": FOLD_PROBE,
            "/search-probe.html": SEARCH_PROBE,
            "/keys-probe.html": KEYS_PROBE,
            "/fields-probe.html": FIELDS_PROBE,
            "/location-probe.html": LOCATION_PROBE,
            "/multi-locations-probe.html": MULTI_LOCATIONS_PROBE,
            "/excluded-probe.html": EXCLUDED_PROBE,
            "/tick-probe.html": WANTED_TICK_PROBE,
        }
        if path in probes:
            body = probes[path].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super()._serve_file(path)


@unittest.skipUnless(BROWSER, "aucun navigateur headless trouve (EDGE_PATH)")
class BrowserPagesTestCase(unittest.TestCase):
    """Boots the real server on a temporary data directory."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.profile = tempfile.TemporaryDirectory()
        seed(cls.tmp.name)

        cls._saved_data_dir = config.DATA_DIR
        config.DATA_DIR = cls.tmp.name

        # The pages read raw.json, not the per-search files. The server builds
        # it from the seeded fixtures on every request, so a test that writes
        # a new fixture is picked up without rewriting a file here.

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        config.DATA_DIR = cls._saved_data_dir
        config.set_raw_json_path(None)
        cls.profile.cleanup()
        cls.tmp.cleanup()

    def dump(self, path):
        result = subprocess.run(
            [
                BROWSER,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--user-data-dir=" + self.profile.name,
                "--virtual-time-budget=" + VIRTUAL_TIME_BUDGET,
                "--dump-dom",
                f"http://127.0.0.1:{self.port}{path}",
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        return result.stdout or ""

    def report_of(self, path):
        dom = self.dump(path)
        start = dom.find('<pre id="out">')
        end = dom.find("</pre>", start)
        self.assertGreater(start, 0, "la sonde n'a pas rendu")
        return dom[start:end]



def seed(folder):
    def write(name, payload):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    write("data_metier_liege.json", {
        "label": "Metier / Liege",
        "scrape_timestamp": "2026-09-26T08:15:00",
        "occupation_guid": "occ-guid",
        "location_guid": "loc-guid",
        "offers": OFFERS,
    })
    write("historique_metier_liege.json", HISTORY)
    write("details_metier_liege.json", {"details": {"1902": DETAIL}})
    write("historique_scrapes.json", {"scrapes": []})
    write("historique_modifications.json", {"modifications": []})
    write("companies.json", COMPANIES)


class TestModulesInBrowser(BrowserPagesTestCase):
    def test_every_shared_module_loads(self):
        dom = self.dump("/probe.html")
        start = dom.find('<pre id="out">')
        end = dom.find("</pre>", start)
        self.assertGreater(start, 0, "la page de sonde n'a pas rendu")
        report = dom[start:end]

        self.assertIn("PROBE-OK", report, report)
        self.assertIn("imported=12", report)
        self.assertNotIn("PROBE-FAIL", report)

    def test_the_excluded_keywords_are_marked_in_red(self):
        report = self.report_of("/excluded-probe.html")

        self.assertIn("EXCL-OK", report)
        # The wanted keyword is marked, and the excluded one too, but apart.
        self.assertIn("wanted=1", report)
        self.assertNotIn("excluded=0", report)
        # The red mark holds the excluded word and no other class leaks in.
        self.assertIn("redText=poste", report)
        self.assertIn("redHasPlainClass=true", report)
        # The offer sheet marks it as well.
        self.assertNotIn("detailExcluded=0", report)

    def test_the_profile_page_has_the_excluded_field(self):
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/profil.html" % self.port, timeout=10) as resp:
            page = resp.read().decode("utf-8")

        self.assertIn("excludedText", page)
        self.assertIn("Mots-clés exclus", page)
        # The field it sits next to is still there.
        self.assertIn("keywordsText", page)
    def test_each_wanted_value_gets_a_tick(self):
        report = self.report_of("/tick-probe.html")

        self.assertIn("TICK-OK", report)
        # The contract wording reaches the friendly label.
        self.assertIn("cdi=true", report)
        self.assertIn("cdd=false", report)
        # Only CDI is ticked, so Intérimaire must not match.
        self.assertIn("interim=false", report)
        self.assertIn("lesDeux=true", report)
        self.assertIn("remplacement=false", report)
        # regimeTravail.
        self.assertIn("regimePlein=true", report)
        self.assertIn("regimePartiel=false", report)
        # regimeTravailPrecision.
        self.assertIn("mentionJour=true", report)
        self.assertIn("mentionNuit=false", report)
        self.assertIn("mentionPauses=false", report)
        self.assertIn("mentionVide=false", report)
        # The merged schedule answers to both lists.
        self.assertIn("fusionRegime=true", report)
        self.assertIn("fusionMention=true", report)
        # The loader hands back the three lists.
        self.assertIn("chargeRegime=Temps plein", report)
        self.assertIn("chargeMention=jour", report)
        # In the offers table, each on the line of its own value.
        self.assertNotIn("ticksContrat=0", report)
        self.assertNotIn("ticksRegime=0", report)
        self.assertNotIn("ticksMention=0", report)
        self.assertIn("surLigneContrat=true", report)
        self.assertIn("surLigneRegime=true", report)
        self.assertIn("surLigneMention=true", report)
        # And on the offer sheet, where the values appear twice.
        self.assertNotIn("ficheRegime=0", report)
        self.assertNotIn("ficheMention=0", report)
        self.assertIn("puceRegime=1", report)
        self.assertIn("infoRegime=1", report)
        self.assertIn("infoMention=1", report)
        self.assertIn("infoContrat=1", report)

    def test_the_profile_page_has_the_schedule_and_mention_lists(self):
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/profil.html" % self.port, timeout=10) as resp:
            page = resp.read().decode("utf-8")

        self.assertIn("scheduleTypes", page)
        self.assertIn("mentionTypes", page)
        self.assertIn("Horaires souhaités", page)
        self.assertIn("Mentions souhaitées", page)
    def test_the_pasted_styles_can_be_switched_off(self):
        report = self.report_of("/diff-probe.html")

        # The button exists because the offer carries inline styles...
        self.assertIn("styleButtonBefore=", report)
        self.assertIn("Styles du texte : activés/false", report)
        # ...they are still on when the page opens...
        self.assertIn("background-color:rgb(255,255,255)", report)
        # ...and one click drops the colours, the fonts and the alignment
        # while keeping the text and the tags.
        self.assertIn("styleButtonAfter=Styles du texte : désactivés/true",
                      report)
        self.assertIn("stored=1", report)
        self.assertNotIn("styledAfter=background-color", report)
        self.assertNotIn("styledAfter=font-family", report)
        self.assertNotIn("styledAfter=text-align", report)

    def test_the_styles_come_back_when_asked_again(self):
        report = self.report_of("/diff-probe.html")

        # Second click restores exactly what Forem sent.
        self.assertIn("styleButtonBack=Styles du texte : activés/false",
                      report)
        self.assertIn(
            "restyled=color:rgb(89,89,89);background-color:rgb(255,255,255)",
            report,
        )
        self.assertIn("storedBack=0", report)

    def test_the_diff_appears_when_asked(self):
        report = self.report_of("/diff-probe.html")

        self.assertIn("DIFF-OK", report)
        self.assertIn("button=Comparer la modification", report)
        self.assertIn("hiddenBefore=true", report)
        self.assertIn("blocksBefore=0", report)
        self.assertIn("buttonAfter=Masquer la comparaison", report)
        self.assertIn("hiddenAfter=false", report)
        self.assertIn("blocksAfter=2", report)
        self.assertIn("fields=Description|Type de contrat", report)
        # The HTML of the stored descriptions is shown as text.
        self.assertIn(
            "oldText=Tien de poste au sein d'une equipe de jour.", report
        )
        self.assertIn(
            "newText=Tien de poste au sein d'une equipe de nuit.", report
        )


    def test_the_tracked_panel_unfolds_on_demand(self):
        report = self.report_of("/tracked-probe.html")

        self.assertIn("beforeLimited=true", report)
        self.assertIn("beforeText=Voir plus", report)
        self.assertIn("afterLimited=false", report)
        self.assertIn("afterText=Voir moins", report)
        self.assertIn("TRACKED-OK", report)

    def test_masking_the_tracked_panel_stays_until_the_next_scrape(self):
        report = self.report_of("/dismiss-probe.html")

        # A dismissal from an older scrape never hides the current alerts...
        self.assertIn("staleVisible=true", report)
        # ...clicking "Masquer" hides them...
        self.assertIn("afterClickHidden=true", report)
        # ...the marker is stored per scrape...
        self.assertIn("storedForScrape=true", report)
        # ...and re-rendering the same scrape keeps them hidden.
        self.assertIn("rerenderHidden=true", report)
        self.assertIn("DISMISS-OK", report)

    def test_the_search_reaches_every_field_of_the_offer(self):
        report = self.report_of("/fields-probe.html")

        self.assertIn("FIELDS-OK", report)
        self.assertIn("baseline=6", report)
        # Fields that no hand-written list mentioned.
        self.assertIn("endDate=1", report)
        self.assertIn("hourly=1", report)
        self.assertIn("voucherPeriod=1", report)
        self.assertIn("offerState=1", report)
        self.assertIn("confidence=1", report)
        self.assertIn("published=1", report)
        # The diff object sits one level down and is walked too.
        self.assertIn("nestedValue=CDD", report)
        self.assertNotEqual(report.count("nestedMatch=0"), 1)
        # Markup must not turn into a match on every offer.
        self.assertIn("markupWord=0", report)
        self.assertIn("cleared=6", report)

    def test_the_search_box_follows_the_keyboard(self):
        report = self.report_of("/keys-probe.html")

        self.assertIn("KEYS-OK", report)
        # keydown/keyup alone must filter, with no input event sent.
        self.assertIn("keysOnly=1", report)
        # The description stays searchable...
        self.assertIn("descMatch=1", report)
        # ...but its markup does not match every offer.
        self.assertIn("markupWord=0", report)
        self.assertIn("cleared=6", report)

    def test_the_listing_search_filters_on_the_offer_data(self):
        report = self.report_of("/search-probe.html")

        self.assertIn("SEARCH-OK", report)
        # The rendered row really does carry the whole status menu...
        self.assertNotIn("rowHasOptionLabels=0", report)
        # ...and the search filters on the offer instead.
        self.assertIn("title=1", report)
        self.assertIn("location=5", report)
        # A word from the status menu must not match every row.
        self.assertIn("statusLabel=0", report)
        self.assertIn("stateBadge=0", report)
        self.assertIn("starButton=0", report)
        # With a status actually set, the word finds exactly that offer.
        self.assertIn("markedThenSearched=1", report)
        self.assertIn("cleared=6", report)

    def test_the_letters_without_accents_are_folded_too(self):
        # "manœuvre" has to answer to "manoeuvre": NFD leaves a ligature alone,
        # so it needs an explicit table. Both the filters and the highlighting
        # share that rule.
        report = self.report_of("/fold-probe.html")

        self.assertIn("FOLD-OK", report)
        self.assertIn("ok filter folds the oe ligature", report)
        self.assertIn("ok highlight finds the oe ligature", report)
        self.assertIn("ok highlight finds an oe keyword in accented text", report)
        self.assertIn("ok two marks", report)
        self.assertIn("ok the whole text is unchanged", report)

    def test_the_profile_keywords_are_highlighted(self):
        report = self.report_of("/keyword-probe.html")

        self.assertIn("KEYWORD-OK", report)
        # The listing really drew offers...
        self.assertIn("listRows=", report)
        self.assertNotIn("listRows=0", report)
        self.assertNotIn("listRows=1 ", report)
        # ...and both keywords are highlighted in it. The seeded title is
        # "Electromecanicien industriel", so the hit is the lowercase stem.
        self.assertIn("listMarks=", report)
        self.assertNotIn("listMarks=0", report)
        self.assertIn("listMarkTexts=mecanicien|equipe", report)
        self.assertIn("marksInLink=", report)
        self.assertNotIn("marksInLink=0", report)
        # The surrounding text is untouched.
        self.assertIn("listTextIntact=Electromecanicien industriel", report)
        # The sheet highlights too, and never inside an editable field.
        self.assertIn("detailMarks=", report)
        self.assertNotIn("detailMarks=0", report)
        self.assertIn("detailMarkTexts=mecanicien", report)
        self.assertIn("marksInTextarea=0", report)
        # No markup was injected: a <mark> holds text only.
        self.assertIn("nestedTagsInMark=0", report)

    def test_the_offer_sheet_shows_the_remuneration(self):
        report = self.report_of("/remun-probe.html")
        # fr-BE separates thousands with U+202F, not a plain space.
        report = report.replace("\u202f", " ").replace("\u00a0", " ")

        self.assertIn("RUN-OK", report)
        # A monthly range, gross, with the hourly equivalent.
        self.assertIn("de 2 800 € à 3 400 € brut / mois", report)
        self.assertIn("estimation : 17 € brut/h", report)
        # Meal vouchers, per day.
        self.assertIn("chèques-repas : 8", report)
        self.assertIn("par jour", report)
        # The diff card still renders after findOffer started passing the
        # whole stored offer.
        self.assertIn("Dernière modification", report)

        # Nothing is injected as HTML: the euro sign is text, not markup.
        self.assertNotIn("<li>de 2", report)

    def test_validate_profile_rejects_the_impossible_values(self):
        report = self.report_of("/profile-probe.html")

        self.assertIn("PROFILE-OK", report)
        self.assertIn("emptyOk=true", report)
        self.assertIn("rateNegRejected=true", report)
        self.assertIn("distanceBigRejected=true", report)
        self.assertIn("goodOk=true", report)
        self.assertIn("rate=15.5", report)
        self.assertIn("distance=25", report)
        # "Nuit" appears twice with different case: only the first survives.
        self.assertIn("keywords=nuit|maintenance|electricite", report)
        self.assertIn("parseEmpty=[]", report)
        self.assertIn('parseMixed=["nuit","electricite"]', report)
        # The postal code is out of the profile for good.
        self.assertIn("emptyHasPostal=false", report)
        self.assertIn("storedPostalDropped=true", report)
        self.assertIn("postalIgnored=true", report)

    def test_the_profile_page_has_no_postal_code_field(self):
        url = "http://127.0.0.1:%d/profil.html" % self.port
        with urllib.request.urlopen(url, timeout=10) as response:
            page = response.read().decode("utf-8")

        self.assertNotIn("postalCode", page)
        self.assertNotIn("Code postal", page)
        self.assertNotIn("postal-code", page)
        # The fields that remain still render.
        self.assertIn("keywordsText", page)

    def test_the_follow_up_is_kept_in_the_browser(self):
        report = self.report_of("/persist-probe.html")

        self.assertIn("PERSIST-OK", report)
        # A follow-up already in localStorage is readable through the module.
        self.assertIn("seededStatut=postule", report)
        self.assertIn("seededRemarque=a relancer", report)
        # A status set through the module is visible immediately: the cache is
        # updated before the caller returns.
        self.assertIn("immediateRead=contacte", report)
        # The edit landed in localStorage, which is where it now lives.
        self.assertIn("storedStatut=contacte", report)
        self.assertIn("storedSecond=refuse", report)
        # Rebuilding the cache from localStorage, as a page reload does, gives
        # the same follow-up back.
        self.assertIn("reloadStatut=contacte", report)
        self.assertIn("reloadSecond=refuse", report)
        self.assertIn("reloadRemarque=a relancer", report)
        # Clearing a field really removes it.
        self.assertIn("afterClearSecond=", report)
        self.assertNotIn("afterClearSecond=refuse", report)


class TestPagesInBrowser(BrowserPagesTestCase):
    def assertDrawn(self, path, markers):
        dom = self.dump(path)
        self.assertTrue(dom, path + " : DOM vide")
        for marker in markers:
            with self.subTest(path=path, marker=marker):
                self.assertIn(marker, dom)

    def test_the_theme_is_applied_before_the_first_paint(self):
        for page in ("", "/insights.html", "/companies.html", "/detail.html"):
            with self.subTest(page=page):
                dom = self.dump(page)
                self.assertRegex(dom, r'data-theme="(light|dark|mono|neon|warm|pastel|jewel|vibrancy)"')

    def test_every_page_draws_its_cogwheel(self):
        for page in ("", "/insights.html", "/companies.html", "/detail.html"):
            with self.subTest(page=page):
                dom = self.dump(page)
                self.assertIn('id="themeGear"', dom)
                self.assertIn("Exporter le suivi", dom)

    def test_the_offers_table_fills_on_open(self):
        self.assertDrawn("", [
            'id="scrapingSelect"',
            "Toutes les recherches",
            "Ateliers du Sud",
            "Electromecanicien industriel",
            "Fonderie du Nord",
        ])

    def test_the_offers_page_keeps_its_controls(self):
        # Everything the offers page owns, moved or not: a split must not
        # lose a control.
        self.assertDrawn("", [
            "Exporter CSV",
            "Supprimer ce scraping",
            "Nouvelle recherche",
            "Intéressé",
            "Postulé",
            "RDV prévu",
            "Postulé ou contacté depuis plus de 7 jours.",
            'id="stateFilter"',
            'id="locationFilter"',
            'id="contractFilter"',
            'id="salaryFilter"',
            'id="currentSearch"',
            'id="exportCsvBtn"',
            'id="deleteScrapingGearBtn"',
        ])

    def test_deleted_offers_stay_in_the_single_list(self):
        dom = self.dump("")

        # The separate "Annonces supprimées" tab is gone.
        self.assertNotIn('id="tab-deleted"', dom)
        self.assertNotIn('id="deletedRows"', dom)

        # The disappeared offer is kept, grouped and badged in the list.
        self.assertIn("Supprimées (1)", dom)
        self.assertIn("Technicien disparu", dom)
        self.assertIn("state-badge state-deleted", dom)

        # A reappeared offer is badged as such.
        self.assertIn("Offre de retour", dom)
        self.assertIn("state-badge state-reappeared", dom)

        # The tracked panel lists the three kinds of change.
        self.assertIn("Disparue", dom)
        self.assertIn("De retour", dom)
        self.assertIn("Modifiée", dom)

        # With more than 5 changes, the panel starts truncated and offers
        # a "Voir plus" button to unfold it.
        self.assertIn("tracked-alert-list is-limited", dom)
        self.assertIn('id="trackedAlertToggleBtn"', dom)
        self.assertIn("Voir plus", dom)

    def test_the_listing_is_sorted_by_publication_date(self):
        import re

        dom = self.dump("")
        tbody = re.search(
            r'id="currentRows">(.*?)</tbody>', dom, re.S
        ).group(1)
        numbers = re.findall(r'<tr data-number="(\d+)"', tbody)
        # Most recent publication first; deleted offers group at the end.
        self.assertEqual(
            numbers,
            ["1902", "1908", "1907", "1906", "1903", "1905", "1904"],
        )

    def test_the_dashboard_draws_its_kpis(self):
        self.assertDrawn("/insights.html", [
            "Electromecanicien industriel",
            "Metier / Liege",
        ])

    def test_the_employer_page_draws_the_index(self):
        self.assertDrawn("/companies.html", ["Ateliers du Sud"])

    def test_the_offer_sheet_draws_the_fiche(self):
        self.assertDrawn("/detail.html?number=1902&base=metier_liege", [
            "Electromecanicien industriel",
            "Intéressé",
            "Voir l'offre sur Le Forem",
        ])

    def test_the_diff_is_hidden_until_asked(self):
        dom = self.dump("/detail.html?number=1902&base=metier_liege")
        self.assertIn("Comparer la modification", dom)
        self.assertIn("Modifications (2)", dom)
        # The offer is shown, not the diff.
        self.assertNotIn("Masquer la comparaison", dom)
        self.assertNotIn('class="diff-block"', dom)

    def test_an_offer_without_changes_has_no_diff_button(self):
        dom = self.dump("/detail.html?number=1903&base=metier_liege")
        self.assertNotIn("diff-card", dom)
        self.assertNotIn("Comparer la modification", dom)

    def test_no_page_reports_a_missing_module(self):
        for page in ("", "/insights.html", "/companies.html", "/detail.html"):
            with self.subTest(page=page):
                dom = self.dump(page)
                self.assertNotIn("Failed to load module", dom)
                self.assertNotIn("is not defined", dom)


if __name__ == "__main__":
    unittest.main()


class TestLocalityFilter(BrowserPagesTestCase):
    """The locality filter, on offers whose place is spelled several ways.

    Its own scraping and its own data directory: adding a second search to the
    shared seed would make the default listing merge both and inflate the row
    counts the other tests assert on.
    """

    MULTI_PLACES = [
        # The real case: three workplaces on one offer.
        "Arrondissement de Waremme, Arrondissement de Liège, Hannut",
        "LIÈGE",
        "Liège",
        "Herstal",
        "Namur",
        "LIÈGE, BRUXELLES",
    ]

    PLACES = [
        "LIÈGE", "Liège", "Herstal", "HERSTAL", "Namur", "GRÂCE-HOLLOGNE",
        # No letter at all: never a shouted name, so left alone.
        "4000",
        # Already mixed case: the employer shaped it, so left alone.
        "Arrondissement de Namur",
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Written after the server is up: it serves from disk on every request.
        offers = []
        for index, place in enumerate(cls.PLACES):
            offer = copy.deepcopy(OFFERS[index % len(OFFERS)])
            offer["number"] = str(7000 + index)
            offer["offer_title"] = "Offre numero %d" % index
            offer["company"] = "Societe %d" % index
            offer["location"] = place
            # The shared seed carries a deleted offer, which the state filter
            # hides by default. Nothing here is about deletion.
            offer["offer_state"] = "unchanged"
            offer["is_new"] = False
            offer["removed_on"] = ""
            offers.append(offer)

        cls._write("data_casse.json", {
            "label": "Casse",
            "scrape_timestamp": "2026-09-26T08:15:00",
            "occupation_guid": "occ-guid",
            "location_guid": "loc-guid",
            "offers": offers,
        })
        cls._write("historique_casse.json", {"scrapes": []})

        # A second scraping where one offer names three places, and another
        # names two. Only the joined string is stored, which is what offers
        # scraped before the list existed look like.
        extra = []
        for index, place in enumerate(cls.MULTI_PLACES):
            offer = copy.deepcopy(OFFERS[index % len(OFFERS)])
            offer["number"] = str(8000 + index)
            offer["offer_title"] = "Offre numero %d" % index
            offer["company"] = "Societe %d" % index
            offer["location"] = place
            offer["offer_state"] = "unchanged"
            offer["is_new"] = False
            offer["removed_on"] = ""
            extra.append(offer)

        cls._write("data_multi.json", {
            "label": "Multi",
            "scrape_timestamp": "2026-09-26T08:15:00",
            "occupation_guid": "occ-guid",
            "location_guid": "loc-guid",
            "offers": extra,
        })
        cls._write("historique_multi.json", {"scrapes": []})

    @classmethod
    def _write(cls, name, payload):
        with open(os.path.join(cls.tmp.name, name), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    def test_one_entry_per_place_and_the_right_rows_kept(self):
        report = self.report_of("/location-probe.html")

        self.assertIn("LOC-OK", report)
        self.assertIn("total=8", report)
        self.assertIn("baseline=8", report)
        # A shouted place is written the way a person would: only the very
        # first letter keeps its capital, so "GRÂCE-HOLLOGNE" becomes
        # "Grâce-hollogne".
        self.assertIn("has[Liège]=true", report)
        self.assertIn("has[Grâce-hollogne]=true", report)
        # A place without a letter, and one the employer already shaped, are
        # left exactly as they were written.
        self.assertIn("has[4000]=true", report)
        self.assertIn("has[Arrondissement de Namur]=true", report)
        # Nothing shouted is left in the menu.
        self.assertIn("shoutedLeft=0", report)
        # "LIÈGE" and "Liège" are one place, and so are "Herstal"/"HERSTAL".
        self.assertIn("liegeOptions=1", report)
        self.assertIn("herstalOptions=1", report)
        # Picking a place keeps every offer written that way.
        self.assertIn("pick[Liège]=2", report)
        self.assertIn("pick[Herstal]=2", report)
        self.assertIn("pick[Namur]=1", report)
        self.assertIn("pick[Grâce-hollogne]=1", report)
        self.assertIn("cleared=8", report)

    def test_one_offer_several_places_gives_several_entries(self):
        report = self.report_of("/multi-locations-probe.html")

        self.assertIn("MULTI-OK", report)
        self.assertIn("total=6", report)
        self.assertIn("baseline=6", report)
        # Three workplaces became three entries, and nothing still holds a comma.
        self.assertIn("joinedEntryLeft=0", report)
        self.assertIn(
            "values= | Arrondissement de Liège | Arrondissement de Waremme | "
            "Bruxelles | Hannut | Herstal | Liège | Namur",
            report,
        )
        # Picking any one of the three keeps the offer that names all three.
        self.assertIn("pick[Hannut]=1", report)
        self.assertIn("pick[Arrondissement de Liège]=1", report)
        self.assertIn("pick[Arrondissement de Waremme]=1", report)
        self.assertIn("pick[Bruxelles]=1", report)
        self.assertIn("pick[Liège]=3", report)
        self.assertIn("cleared=6", report)
