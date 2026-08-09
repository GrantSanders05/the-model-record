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


def export_workbook(sid, timeout=90):
    """
    The whole document as .xlsx — real tab names, in one request.

    THIS REPLACED A PROBE, AND THE PROBE WAS WRONG.
    The first version asked gviz for "Week 0 Data" … "Week 25 Data" and kept
    whatever came back. gviz does not 404 on a tab name that does not exist --
    it serves the default sheet instead. So all 26 names returned data, the same
    snapshot was imported 26 times stamped as weeks 1 through 26, and the run
    reported "synced 46,644 grade rows from 26 weekly tabs" as a success.

    Fictional week numbers are not a cosmetic problem. The week is what stops a
    grade being used to predict a game it was made after; inventing 25 of them
    quietly destroys the guarantee the whole backtest rests on.

    Exporting the workbook removes the guesswork: the tab names are whatever the
    document actually contains, and the bytes are read by the same xlsx reader
    the offline importer uses.
    """
    url = "%s/%s/export?format=xlsx" % (BASE, sid)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return None
        if e.code in (401, 403):
            raise PermissionError(
                "Google refused the export (HTTP %d). The sheet is not shared.\n"
                "Open it, press Share, and set General access to\n"
                "  'Anyone with the link'  ->  Viewer." % e.code)
        raise
    except urllib.error.URLError as e:
        raise RuntimeError("could not reach Google: %s" % e)

    # A private document answers with a sign-in page, not a spreadsheet. An xlsx
    # is a zip, so it always starts "PK".
    if "html" in ctype.lower() or not body[:2] == b"PK":
        raise PermissionError(
            "Google returned a web page instead of a spreadsheet, which means "
            "the sheet is private.\nOpen it, press Share, and set General access to\n"
            "  'Anyone with the link'  ->  Viewer.")
    return body


def _workbook(sid):
    import os
    import tempfile
    sys_path_added = False
    try:
        from xlsx import Workbook
    except ImportError:                       # pragma: no cover - path setup
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from xlsx import Workbook
        sys_path_added = True
    del sys_path_added

    body = export_workbook(sid)
    if body is None:
        return None
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    return Workbook(path), path


def list_week_tabs(sid):
    """The 'Week N Data' tabs the document actually has, with their numbers."""
    got = _workbook(sid)
    if not got:
        return []
    wb, path = got
    try:
        out = []
        for title in wb.sheet_names:
            m = WEEK_RE.match(title.strip())
            if m:
                out.append((title, int(m.group(1))))
        return sorted(out, key=lambda t: t[1])
    finally:
        _cleanup(path)


def read_tabs(sid):
    """{tab title: rows} for every 'Week N Data' tab, from one download."""
    got = _workbook(sid)
    if not got:
        return {}
    wb, path = got
    try:
        return {t: list(wb.rows(t)) for t in wb.sheet_names
                if WEEK_RE.match(t.strip())}
    finally:
        _cleanup(path)


def _cleanup(path):
    import os
    try:
        os.unlink(path)
    except OSError:
        pass


def document_reachable(sid):
    """
    Is the DOCUMENT itself readable, separately from the tabs inside it?

    Splitting the two questions matters: a nonexistent document and a properly
    shared one with badly-named tabs otherwise produce the same empty result,
    and the check then tells you to go rename tabs in a document that does not
    exist.
    """
    try:
        body = export_workbook(sid)
    except PermissionError as e:
        return False, str(e)
    except RuntimeError as e:
        return False, str(e)
    if body is None:
        return False, ("No document with that ID, or it is not shared at all.\n"
                       "Check the ID, and that General access is set to\n"
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
