"""
sheets.py — read and write Google Sheets with a service account.

This is the ONE place in the project that needs third-party packages
(google-auth, requests). The model, backtester and optimizer are all pure
stdlib on purpose, so nothing about the analysis can break because of a
dependency upgrade. Only the sheet bridge carries that risk.

Why a service account and not OAuth: a service account has no human in the
loop, so a scheduled job can refresh the sheet at 6am with nobody logged in.
That is the whole point of "without me touching it".

Auth comes from a JSON key supplied EITHER as a file path in
GOOGLE_APPLICATION_CREDENTIALS or as the raw JSON in GOOGLE_SERVICE_ACCOUNT
(which is how GitHub Actions passes a secret). Never commit the key.

Setup instructions: see SETUP.md.
"""

import json
import os
import sys

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
API = "https://sheets.googleapis.com/v4/spreadsheets"


def _credentials():
    try:
        from google.oauth2 import service_account
    except ImportError:
        sys.exit("Missing dependency. Run:  pip3 install -r requirements.txt")

    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if raw:
        info = json.loads(raw)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    sys.exit(
        "No Google credentials found.\n"
        "  Local : export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json\n"
        "  CI    : set the GOOGLE_SERVICE_ACCOUNT secret to the key's JSON contents\n"
        "  See SETUP.md for how to create the key and share the sheets with it."
    )


def _session():
    try:
        from google.auth.transport.requests import AuthorizedSession
    except ImportError:
        sys.exit("Missing dependency. Run:  pip3 install -r requirements.txt")
    return AuthorizedSession(_credentials())


def read_range(spreadsheet_id, a1_range):
    """Return the range as a list of rows (list of lists)."""
    s = _session()
    r = s.get("%s/%s/values/%s" % (API, spreadsheet_id, a1_range),
              params={"valueRenderOption": "UNFORMATTED_VALUE"}, timeout=60)
    r.raise_for_status()
    return r.json().get("values", [])


def write_range(spreadsheet_id, a1_range, rows, value_input="USER_ENTERED"):
    """Overwrite a range with `rows`."""
    s = _session()
    r = s.put("%s/%s/values/%s" % (API, spreadsheet_id, a1_range),
              params={"valueInputOption": value_input},
              json={"values": rows}, timeout=60)
    r.raise_for_status()
    return r.json()


def clear_range(spreadsheet_id, a1_range):
    s = _session()
    r = s.post("%s/%s/values/%s:clear" % (API, spreadsheet_id, a1_range),
               json={}, timeout=60)
    r.raise_for_status()
    return r.json()


def ensure_tab(spreadsheet_id, title):
    """Create a tab if it isn't there. Safe to call every run."""
    s = _session()
    meta = s.get("%s/%s" % (API, spreadsheet_id),
                 params={"fields": "sheets.properties.title"}, timeout=60)
    meta.raise_for_status()
    existing = {sh["properties"]["title"] for sh in meta.json().get("sheets", [])}
    if title in existing:
        return False
    r = s.post("%s/%s:batchUpdate" % (API, spreadsheet_id),
               json={"requests": [{"addSheet": {"properties": {"title": title}}}]},
               timeout=60)
    r.raise_for_status()
    return True


def write_picks(spreadsheet_id, tab, picks):
    """
    Replace `tab` with the current picks table.

    Writes to a dedicated tab rather than editing the existing layout, so an
    automated run can never overwrite hand-entered film grades. The automation
    owns this tab; Grant owns the rest of the workbook.
    """
    if not picks:
        return 0
    ensure_tab(spreadsheet_id, tab)
    cols = ["week", "kickoff", "away", "home", "model_margin", "market_margin",
            "edge", "ats_pick", "ml_pick", "ml_win_prob",
            "model_total", "market_total", "ou_pick"]
    header = ["Week", "Kickoff", "Away", "Home", "Model", "Market", "Edge",
              "ATS Pick", "ML Pick", "ML Win %", "Model Total", "Market Total", "O/U"]
    rows = [header]
    for p in picks:
        rows.append([("" if p.get(c) is None else p.get(c)) for c in cols])
    clear_range(spreadsheet_id, "%s!A1:Z2000" % tab)
    write_range(spreadsheet_id, "%s!A1" % tab, rows)
    return len(rows) - 1


def write_accuracy(spreadsheet_id, tab, metrics_by_label):
    """Write the running accuracy summary. `metrics_by_label` is {label: metrics dict}."""
    ensure_tab(spreadsheet_id, tab)
    header = ["Scope", "Games", "ATS %", "ATS Record", "ROI %", "SU %",
              "Market Fav SU %", "MAE", "Calib Slope", "O/U %", "Verdict"]
    rows = [header]
    for label, m in metrics_by_label.items():
        verdict = ("PROVEN" if m.get("ats_significant")
                   else "above break-even, unproven" if m.get("ats_beats_breakeven")
                   else "below break-even")
        rows.append([
            label, m.get("n_games"),
            round(m["ats_pct"], 2) if m.get("ats_pct") is not None else "",
            "%d-%d-%d" % (m.get("ats_w", 0), m.get("ats_l", 0), m.get("ats_push", 0)),
            round(m["roi"], 2) if m.get("roi") is not None else "",
            round(m["su_pct"], 2) if m.get("su_pct") is not None else "",
            round(m["su_baseline_pct"], 2) if m.get("su_baseline_pct") is not None else "",
            round(m["mae"], 2) if m.get("mae") is not None else "",
            round(m["calib_slope"], 3) if m.get("calib_slope") is not None else "",
            round(m["total_pct"], 2) if m.get("total_pct") is not None else "",
            verdict,
        ])
    clear_range(spreadsheet_id, "%s!A1:Z200" % tab)
    write_range(spreadsheet_id, "%s!A1" % tab, rows)
    return len(rows) - 1
