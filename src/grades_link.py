"""
grades_link.py — read the grade sheet with no Google Cloud project at all.

WHY THIS EXISTS
The service-account path in `sheets.py` is the secure one, but it costs a Google
Cloud project, an enabled API, a generated key and a share step. This path costs
one menu click: set the sheet to "Anyone with the link can view", and Google's
gviz endpoint will hand back any tab as CSV with no authentication.

    https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:csv&sheet=<TAB>

THE TRADE, STATED PLAINLY
"Anyone with the link" means exactly that. The film grades are the only part of
this model that is not public by design -- the arithmetic is on the website --
so this trades the moat's secrecy for setup convenience. The mitigations are
real but partial:

  * The sheet ID lives in a repo SECRET, never in the repo.
  * The ID is a long random token; it cannot be guessed or enumerated.
  * It is rotatable. Turning link sharing off and on issues a new document link
    only if the file is copied -- so if the ID ever leaks, the fix is to copy
    the sheet to a new file and update the secret.

It is also READ-ONLY. Nothing can write back, so the `Model Picks` and
`Model Accuracy` tabs stay empty under this path; the website shows both anyway.

If the grades ever matter more than the five minutes, switch to `sheets.py`.
Both feed the identical importer, so nothing downstream changes.
"""

import csv
import io
import re
import urllib.error
import urllib.parse
import urllib.request

WEEK_RE = re.compile(r"week\s*(\d+)\s*data", re.I)
BASE = "https://docs.google.com/spreadsheets/d"


def sheet_id(value):
    """Accept a bare ID or a pasted Sheets URL."""
    if not value:
        return value
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
    return m.group(1) if m else value.strip()


def csv_url(sid, tab):
    return "%s/%s/gviz/tq?tqx=out:csv&sheet=%s" % (
        BASE, sid, urllib.parse.quote(tab))


def read_tab(sid, tab, timeout=45):
    """
    One tab as a list of rows, or None if that tab does not exist.

    Raises PermissionError when the document is not link-shared. That case
    deserves its own signal: Google answers it with an HTML sign-in page rather
    than an error, so a naive CSV parse yields a single nonsense row and the
    import reports "0 teams" as though the sheet were empty.
    """
    try:
        with urllib.request.urlopen(csv_url(sid, tab), timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return None                      # no such tab, or no such document
        if e.code in (401, 403):
            raise PermissionError(
                "Google refused the request (HTTP %d). The sheet is not shared.\n"
                "Open it, press Share, and set General access to\n"
                "  'Anyone with the link'  ->  Viewer." % e.code)
        raise
    except urllib.error.URLError as e:
        raise RuntimeError("could not reach Google: %s" % e)

    head = body.lstrip()[:200].lower()
    if "text/html" in ctype or head.startswith("<!doctype") or "<html" in head:
        raise PermissionError(
            "Google returned a sign-in page instead of CSV, which means the "
            "sheet is private.\nOpen it, press Share, and set General access to\n"
            "  'Anyone with the link'  ->  Viewer.")

    rows = list(csv.reader(io.StringIO(body)))
    return rows or None


def list_week_tabs(sid, max_week=25):
    """
    Which 'Week N Data' tabs exist.

    There is no way to enumerate tabs without authenticating, so this probes.
    Cheap enough at once a day, and it fails in the right direction: a tab that
    is named wrongly is simply not found, which is the same outcome as under the
    authenticated path.
    """
    found = []
    for n in range(0, max_week + 1):
        title = "Week %d Data" % n
        rows = read_tab(sid, title)
        if rows:
            found.append((title, n))
    return found


def document_reachable(sid):
    """
    Is the DOCUMENT itself readable, separately from any tab in it?

    Omitting the sheet parameter returns the first tab, so this answers "does
    this ID resolve and is it shared" on its own. Without splitting the two
    questions, a nonexistent document and a correctly-shared document with
    badly-named tabs produce the identical empty result -- and the check then
    tells you to go rename tabs in a document that does not exist.
    """
    try:
        with urllib.request.urlopen(
                "%s/%s/gviz/tq?tqx=out:csv" % (BASE, sid), timeout=45) as r:
            body = r.read(4096).decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return False, ("No document with that ID, or it is not shared at all.\n"
                           "Check the ID, and that General access is set to\n"
                           "  'Anyone with the link'  ->  Viewer.")
        return False, "Google returned HTTP %d." % e.code
    except urllib.error.URLError as e:
        return False, "could not reach Google: %s" % e

    head = body.lstrip()[:200].lower()
    if "text/html" in ctype or head.startswith("<!doctype") or "<html" in head:
        return False, ("The document exists but is PRIVATE — Google served a "
                       "sign-in page.\nOpen it, press Share, and set General access to\n"
                       "  'Anyone with the link'  ->  Viewer.")
    return True, ""


def probe(sid):
    """A one-shot readiness check with a human-readable verdict."""
    sid = sheet_id(sid)

    reachable, why = document_reachable(sid)
    if not reachable:
        return {"ok": False, "reason": why, "tabs": []}

    try:
        tabs = list_week_tabs(sid)
    except PermissionError as e:
        return {"ok": False, "reason": str(e), "tabs": []}
    except RuntimeError as e:
        return {"ok": False, "reason": str(e), "tabs": []}
    if not tabs:
        return {"ok": False, "tabs": [],
                "reason": ("Reached the document, but found no tab named "
                           "'Week N Data'.\nThe sync reads only those — 'Team Data' "
                           "alone is not enough, because the week number is what "
                           "keeps a grade from predicting a game it already saw.\n"
                           "Duplicate 'Team Data' and name the copy 'Week 0 Data'.")}
    return {"ok": True, "tabs": tabs, "reason": ""}
