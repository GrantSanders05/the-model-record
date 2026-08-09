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

        def all_titles():
            s = sheets._session()
            r = s.get("%s/%s" % (sheets.API, sheet_id),
                      params={"fields": "sheets.properties.title"}, timeout=60)
            r.raise_for_status()
            return [sh["properties"]["title"] for sh in r.json().get("sheets", [])]

        return ("service account",
                lambda: list_week_tabs(sheet_id),
                lambda title: sheets.read_range(sheet_id, "'%s'!A1:Z200" % title),
                all_titles)

    import grades_link
    sid = grades_link.sheet_id(sheet_id)
    # One download of the whole workbook, reused for the listing and every read.
    # Per-tab fetching here was what allowed 26 imaginary tabs to be "found":
    # asking Google for a tab by name cannot tell you whether that tab exists.
    cache = {}

    def tabs():
        cache.update(grades_link.read_all_tabs(sid))
        return sorted(
            ((t, int(grades_link.WEEK_RE.match(t.strip()).group(1)))
             for t in cache if grades_link.WEEK_RE.match(t.strip())),
            key=lambda x: x[1])

    return ("link-shared workbook export", tabs, lambda title: cache.get(title),
            lambda: list(cache))


def live_week(conn, sport, season):
    """
    The effective week the LIVE tab represents.

    "Team Data" is the tab Grant actually edits; a "Week N Data" tab is a frozen
    copy of it. So its state is "after every game played so far", which is the same
    anchor best_bets uses for preseason: the number of weeks with a completed game,
    plus one. Deriving it rather than trusting a tab name means a mid-week edit is
    stamped at the week it can legitimately predict, and never earlier.
    """
    row = conn.execute(
        "SELECT MAX(week) w FROM games WHERE sport=? AND season=? AND home_score IS NOT NULL",
        (sport, season)).fetchone()
    return (row["w"] or 0) + 1


LIVE_TAB = "team data"
SYNC_REPORT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "output", "grades_sync.json")


def write_sync_report(report, how_many, path=SYNC_REPORT):
    """
    Leave a record of which tabs were read and when, for the site to display.

    "I updated the spreadsheet and the site did not change" has several causes that
    look identical from the outside -- the run has not happened yet, the tab is not
    one the sync reads, the edit did not parse. A timestamped list of tabs and row
    counts on the page tells them apart without anyone reading a CI log.
    """
    import datetime as _dt
    import json as _json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        _json.dump({"synced_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                    "rows": how_many, "tabs": report}, fh, indent=2)


def sync(conn, sheet_id, sport, season, week_offset=1, dry_run=False, verbose=True):
    """
    -> (rows, tabs, report). `report` names every tab and what became of it.

    Both kinds of tab are read, and the distinction matters:

      "Week N Data"  a FROZEN copy of the sheet after N weeks -> effective week N+1.
      "Team Data"    the tab Grant actually edits, right now  -> effective live_week().

    Only the weekly copies used to be read. That is a sane-sounding rule that made
    the ordinary action -- open the sheet, change a grade -- do nothing at all, with
    no error and no missing-data warning anywhere in the run: the sync reported
    success, the row count was unchanged, and the site kept showing the old number.
    The live tab is at least as current as any frozen copy of it, so it is written
    LAST and wins the upsert where they collide.
    """
    how, list_tabs, read_tab, all_titles = _providers(sheet_id)
    # Printed even when quiet. Which door the grades came through decides whether
    # writeback exists and whether the sheet is public, so it is not a detail.
    print("  reading via %s" % how)

    try:
        tabs = list_tabs()
    except PermissionError as e:
        raise SystemExit(str(e))

    live = next((t for t in all_titles() if t.strip().lower() == LIVE_TAB), None)
    if not tabs and not live:
        raise SystemExit(
            "No 'Week N Data' or 'Team Data' tab found in that sheet.\n"
            "Saw: %s\n"
            "Check the sheet ID, and that this method can actually see it."
            % (", ".join(all_titles()) or "(nothing)"))

    plan = [(title, wk + week_offset, "weekly snapshot") for title, wk in tabs]
    if live:
        plan.append((live, live_week(conn, sport, season), "live tab"))

    total, imported_tabs, report = 0, 0, []
    for title, eff, kind in plan:
        rows = read_tab(title)
        recs, teams = iw.parse_rows(rows, sport, season, eff, label=title)
        if not recs:
            report.append({"tab": title, "kind": kind, "week": eff,
                           "rows": 0, "teams": 0, "note": "no recognizable grade columns"})
            if verbose:
                print("  %-16s -> skipped (no recognizable grade columns)" % title)
            continue
        report.append({"tab": title, "kind": kind, "week": eff,
                       "rows": len(recs), "teams": teams, "note": ""})
        if verbose:
            print("  %-16s -> effective week %-3d  %4d rows, %d teams  [%s]"
                  % (title, eff, len(recs), teams, kind))
        if not dry_run:
            conn.executemany(
                """INSERT INTO grades (sport, season, week, team, position, grade)
                   VALUES (:sport, :season, :week, :team, :position, :grade)
                   ON CONFLICT(sport, season, week, team, position)
                   DO UPDATE SET grade = excluded.grade""", recs)
            conn.commit()
        total += len(recs)
        imported_tabs += 1
    return total, imported_tabs, report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True, help="Google Sheet ID (from the URL)")
    ap.add_argument("--sport", default="cfb", choices=["cfb", "nfl", "nba"])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week-offset", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = db.connect()
    total, tabs, report = sync(conn, args.sheet, args.sport, args.season,
                               args.week_offset, args.dry_run)
    print("\n%s %d grade rows from %d tab(s)."
          % ("Would import" if args.dry_run else "Imported", total, tabs))
    for r in report:
        print("   %-18s wk %-3s %5d rows  %s%s"
              % (r["tab"], r["week"], r["rows"], r["kind"],
                 (" — " + r["note"]) if r["note"] else ""))
    # Written here too, not only from run_update, because the fast refresh workflow
    # invokes this module directly. A freshness stamp that only exists on the slow
    # path would go stale exactly when the fast path is doing the work.
    if not args.dry_run:
        write_sync_report(report, how_many=total)

    if not args.dry_run and total:
        checked, bad = iw.verify_formula(conn, args.sport, args.season)
        print("Formula check: re-derived TOTAL for %d team-weeks, %d mismatches -> %s"
              % (checked, bad, "OK" if bad == 0 else "INVESTIGATE"))
        if bad:
            sys.exit("Synced grades do not reproduce the sheet's own TOTAL column.")


if __name__ == "__main__":
    main()
