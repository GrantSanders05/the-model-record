"""
check_sheets.py — prove the Google Sheets connection works before trusting it.

Every failure mode here looks the same from the outside: the sync reports zero
rows and the run carries on. So this checks each link in the chain separately
and says which one broke, because "it didn't work" is not a diagnosis.

The overwhelmingly common failure is the last one people think of: the service
account is a separate identity with its own email address, and a sheet in your
Drive is invisible to it until you SHARE the sheet with that address. Nothing
about that is obvious, and the API's error for it is a flat 404 that reads as
"wrong ID".

    python3 tools/check_sheets.py                      # uses MODEL_GRADES_SHEET_ID
    python3 tools/check_sheets.py --sheet <id-or-url>
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))


def sheet_id(value):
    """Accept a bare ID or a full Sheets URL — people paste the URL."""
    if not value:
        return value
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
    return m.group(1) if m else value.strip()


def service_account_email():
    """Who the automation will be acting as. This is the address to share with."""
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if raw:
        try:
            return json.loads(raw).get("client_email")
        except ValueError:
            return None
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path and os.path.exists(path):
        try:
            return json.load(open(path)).get("client_email")
        except ValueError:
            return None
    return None


def check_link(args):
    """The no-Google-Cloud path: a link-shared sheet read as CSV."""
    import grades_link

    sid = grades_link.sheet_id(args.sheet)
    if not sid:
        print("[FAIL] no sheet. Pass --sheet <url-or-id> or set MODEL_GRADES_SHEET_ID.")
        return 1
    print("[ok]   sheet id: %s" % sid)
    print("       probing for 'Week N Data' tabs…")

    res = grades_link.probe(sid)
    if not res["ok"]:
        print("[FAIL] %s" % res["reason"])
        return 1

    print("[ok]   %d weekly tab(s): %s"
          % (len(res["tabs"]), ", ".join(t for t, _ in res["tabs"])))

    title, _ = res["tabs"][0]
    rows = grades_link.read_tabs(sid).get(title)
    print("[ok]   read %d row(s) from %r" % (len(rows or []), title))

    import import_workbook as iw
    recs, teams = iw.parse_rows(rows, "cfb", args.season, 1, label=title)
    if not recs:
        print("[FAIL] the tab was readable but no grade columns were recognised.")
        print("       Headers must still look like 'QB Score 15', 'RB Score 10', …")
        return 1
    print("[ok]   parsed %d grade rows across %d teams" % (len(recs), teams))

    print("=" * 60)
    print("Connection is good, and no Google Cloud project was needed.")
    print("Set ONE secret and the daily job takes it from here:")
    print("  gh secret set MODEL_GRADES_SHEET_ID    # %s" % sid)
    print()
    print("Note: this path is READ-ONLY, so the automation will not write")
    print("its Model Picks / Model Accuracy tabs back into your workbook.")
    print("The website shows both regardless.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=os.environ.get("MODEL_GRADES_SHEET_ID"))
    ap.add_argument("--season", type=int, default=2026)
    args = ap.parse_args()

    print("Google Sheets connection check\n" + "=" * 60)
    ok = True

    # 1. the library
    try:
        import google.auth          # noqa: F401
        import requests             # noqa: F401
        print("[ok]   google-auth and requests are installed")
    except ImportError as e:
        print("[FAIL] %s" % e)
        print("       pip install -r requirements.txt")
        return 1

    # 2. the credential — or the no-credential path
    email = service_account_email()
    if not (os.environ.get("GOOGLE_SERVICE_ACCOUNT")
            or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")):
        print("[--]   no service-account credential set — checking the")
        print("       link-shared CSV path instead (no Google Cloud needed)")
        return check_link(args)
    if not email:
        print("[FAIL] the credential is set but is not valid JSON with a client_email.")
        print("       Paste the WHOLE key file, braces included.")
        return 1
    print("[ok]   credential parses — acting as:\n         %s" % email)

    # 3. the sheet id
    sid = sheet_id(args.sheet)
    if not sid:
        print("[FAIL] no sheet. Pass --sheet or set MODEL_GRADES_SHEET_ID.")
        return 1
    print("[ok]   sheet id: %s" % sid)

    # 4. can we actually open it
    import sheets
    try:
        meta = sheets._session().get(
            "%s/%s?fields=properties.title,sheets.properties.title"
            % (sheets.API, sid), timeout=30)
    except Exception as e:                       # noqa: BLE001 - report anything
        print("[FAIL] could not reach the Sheets API: %s" % e)
        return 1

    if meta.status_code == 404:
        print("[FAIL] 404 — the service account cannot SEE this sheet.")
        print("       This nearly always means it has not been shared, not that")
        print("       the ID is wrong. Open the sheet, press Share, and add:")
        print("         %s" % email)
        print("       Viewer is enough to read grades; Editor is needed for the")
        print("       automation to write its Model Picks / Model Accuracy tabs.")
        return 1
    if meta.status_code == 403:
        print("[FAIL] 403 — the Sheets API is probably not enabled on the project.")
        print("       Google Cloud console -> APIs & Services -> enable 'Google Sheets API'.")
        return 1
    if meta.status_code != 200:
        print("[FAIL] HTTP %s: %s" % (meta.status_code, meta.text[:300]))
        return 1

    data = meta.json()
    title = data.get("properties", {}).get("title", "(untitled)")
    tabs = [s["properties"]["title"] for s in data.get("sheets", [])]
    print("[ok]   opened %r" % title)
    print("       tabs: %s" % ", ".join(tabs))

    # 5. the tabs the sync actually looks for
    import sync_grades
    week_tabs = [t for t in tabs if sync_grades.WEEK_RE.match(t)] \
        if hasattr(sync_grades, "WEEK_RE") else \
        [t for t in tabs if re.match(r"^Week\s+(\d+)\s+Data$", t.strip())]
    if not week_tabs:
        print("[FAIL] no tab named 'Week N Data'. The sync reads ONLY those —")
        print("       'Team Data' alone is not enough, because the week number is")
        print("       what stops a grade being used to predict a game it already saw.")
        print("       Duplicate 'Team Data' and name the copy 'Week 0 Data'.")
        ok = False
    else:
        print("[ok]   %d weekly tab(s) the sync will read: %s"
              % (len(week_tabs), ", ".join(week_tabs)))

    # 6. can we read a cell
    try:
        vals = sheets.read_range(sid, "%s!A1:D3" % week_tabs[0]) if week_tabs else None
        if vals:
            print("[ok]   read %d row(s) — header is: %s"
                  % (len(vals), vals[0][:4] if vals else "?"))
    except Exception as e:                       # noqa: BLE001
        print("[FAIL] could not read a range: %s" % e)
        ok = False

    print("=" * 60)
    if ok:
        print("Connection is good. Set these as repo secrets and the daily job")
        print("will pick the grades up on its own:")
        print("  gh secret set GOOGLE_SERVICE_ACCOUNT   # the whole JSON key file")
        print("  gh secret set MODEL_GRADES_SHEET_ID    # %s" % sid)
    else:
        print("Fix the FAIL lines above, then run this again.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
