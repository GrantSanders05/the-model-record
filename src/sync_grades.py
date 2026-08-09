"""
sync_grades.py — read the film grades straight out of the live Google Sheet.

This is the step that makes the whole loop hands-off. Grant grades film in the
spreadsheet, exactly as he always has. Everything after that — pulling those
grades, refreshing results and lines, regenerating picks, locking them, grading
them, republishing the record — happens on a schedule with nobody involved.

Before this existed the grades reached the model via a manually exported .xlsx,
which meant the "automated" pipeline quietly depended on Grant remembering to
export a file every week.

Parsing is shared with the .xlsx importer (`import_workbook.parse_rows`) rather
than reimplemented, so a grade loaded from the live sheet is identical to the
same grade loaded from a download. Two parsers would eventually disagree.

Week semantics are the sheet's own: a tab named "Week N Data" holds the state
AFTER N weeks, so it is stamped effective week N+1 and can never be used to
predict a game it already saw.

Usage:
    python3 src/sync_grades.py --sheet <id> --sport cfb --season 2026
    python3 src/sync_grades.py --sheet <id> --sport cfb --season 2026 --dry-run
"""

import argparse
import os
import re
import sys

import db
import import_workbook as iw

WEEK_RE = re.compile(r"week\s*(\d+)\s*data", re.I)


def list_week_tabs(sheet_id):
    """Tab titles that look like weekly grade snapshots, with their week number."""
    import sheets
    s = sheets._session()
    r = s.get("%s/%s" % (sheets.API, sheet_id),
              params={"fields": "sheets.properties.title"}, timeout=60)
    r.raise_for_status()
    out = []
    for sh in r.json().get("sheets", []):
        title = sh["properties"]["title"]
        m = WEEK_RE.match(title.strip())
        if m:
            out.append((title, int(m.group(1))))
    return sorted(out, key=lambda t: t[1])


def _providers(sheet_id):
    """
    How to list and read tabs, for whichever access method is configured.

    Two ways in: a service account (private sheet, read+write) or a link-shared
    sheet read as CSV (no Google Cloud project, read-only). They differ only in
    how bytes are obtained, so they are reduced to a pair of callables here and
    everything after this point is identical -- which is the point. Two ingest
    paths that parse rows differently would produce grades that disagree
    depending on how they arrived.
    """
    if os.environ.get("GOOGLE_SERVICE_ACCOUNT") or os.environ.get(
            "GOOGLE_APPLICATION_CREDENTIALS"):
        import sheets
        return ("service account",
                lambda: list_week_tabs(sheet_id),
                lambda title: sheets.read_range(sheet_id, "'%s'!A1:Z200" % title))

    import grades_link
    sid = grades_link.sheet_id(sheet_id)
    return ("link-shared CSV",
            lambda: grades_link.list_week_tabs(sid),
            lambda title: grades_link.read_tab(sid, title))


def sync(conn, sheet_id, sport, season, week_offset=1, dry_run=False, verbose=True):
    how, list_tabs, read_tab = _providers(sheet_id)
    # Printed even when quiet. Which door the grades came through decides whether
    # writeback exists and whether the sheet is public, so it is not a detail.
    print("  reading via %s" % how)

    try:
        tabs = list_tabs()
    except PermissionError as e:
        raise SystemExit(str(e))

    if not tabs:
        raise SystemExit(
            "No tabs matching 'Week N Data' found in that sheet.\n"
            "Check the sheet ID, and that this method can actually see it.")

    total, imported_tabs = 0, 0
    for title, wk in tabs:
        eff = wk + week_offset
        rows = read_tab(title)
        recs, teams = iw.parse_rows(rows, sport, season, eff, label=title)
        if not recs:
            if verbose:
                print("  %-16s -> skipped (no recognizable grade columns)" % title)
            continue
        if verbose:
            print("  %-16s -> effective week %-3d  %4d rows, %d teams"
                  % (title, eff, len(recs), teams))
        if not dry_run:
            conn.executemany(
                """INSERT INTO grades (sport, season, week, team, position, grade)
                   VALUES (:sport, :season, :week, :team, :position, :grade)
                   ON CONFLICT(sport, season, week, team, position)
                   DO UPDATE SET grade = excluded.grade""", recs)
            conn.commit()
        total += len(recs)
        imported_tabs += 1
    return total, imported_tabs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True, help="Google Sheet ID (from the URL)")
    ap.add_argument("--sport", default="cfb", choices=["cfb", "nfl", "nba"])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week-offset", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    total, tabs = sync(conn, args.sheet, args.sport, args.season,
                       args.week_offset, args.dry_run)
    print("\n%s %d grade rows from %d weekly tab(s)."
          % ("Would import" if args.dry_run else "Imported", total, tabs))

    if not args.dry_run and total:
        checked, bad = iw.verify_formula(conn, args.sport, args.season)
        print("Formula check: re-derived TOTAL for %d team-weeks, %d mismatches -> %s"
              % (checked, bad, "OK" if bad == 0 else "INVESTIGATE"))
        if bad:
            sys.exit("Synced grades do not reproduce the sheet's own TOTAL column.")


if __name__ == "__main__":
    main()
