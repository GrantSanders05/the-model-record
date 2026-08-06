"""
import_workbook.py — import a Power Rankings .xlsx straight from Google Sheets.

Reads the per-week tabs ("Week 0 Data" ... "Week N Data"), which is what makes
an honest backtest possible: each tab is a genuine snapshot of what the model
knew at that point, so the grades carry real timestamps instead of being
back-applied from the end of the season.

WEEK SEMANTICS (verified against the file, not assumed)
    Week 0 Data -> teams average 0.00 games played
    Week 1 Data -> 1.06
    Week 2 Data -> 2.02
So "Week N Data" is the state AFTER N weeks. It may therefore only be used to
predict week N+1 onward, and is imported with effective_week = N + 1.

The "Team Data" tab is the end-of-season state. It is deliberately SKIPPED for
backtesting -- using it to predict September games is exactly the hindsight
that inflates a record.

Alongside the eight position grades it stores the sheet's own bookkeeping
(wins, losses, win points, loss points) and its computed TOTAL, under keys
prefixed with "_". Keeping the sheet's TOTAL lets the engine assert that its
reimplementation of Grant's formula still matches the spreadsheet exactly.

Usage:
    python3 src/import_workbook.py --xlsx "exports/College Football PR 25.xlsx" \
        --sport cfb --season 2025
"""

import argparse
import os
import re
import sys

import db
import team_aliases
import xlsx

# Header text in the sheet -> canonical key. Matched on a normalized form, so
# "QB Score 15", "qb score 15" and "QB  Score  15" all land in the same slot.
HEADER_MAP = {
    "team name": "_team", "team": "_team", "school": "_team",
    "wins": "_wins", "losses": "_losses",
    "qb score 15": "qb", "rb score 10": "rb", "wr score 10": "wr",
    "ol score 15": "ol", "dl score 15": "dl", "lb score 10": "lb",
    "db score 10": "db",
    "coachst score 15": "coach_st", "coachst score 1": "coach_st",
    "coach st score 15": "coach_st", "coachst": "coach_st",
    "win points": "_win_points", "loss points": "_loss_points",
    "player totals": "_player_totals", "total": "_sheet_total",
}
POSITIONS = ["qb", "rb", "wr", "ol", "dl", "lb", "db", "coach_st"]
META = ["_wins", "_losses", "_win_points", "_loss_points", "_sheet_total"]

_week_re = re.compile(r"week\s*(\d+)\s*data", re.I)


def _norm(s):
    return re.sub(r"[^a-z0-9 ]+", "", (s or "").strip().lower())


def map_header(row):
    out = {}
    for i, cell in enumerate(row):
        key = HEADER_MAP.get(_norm(xlsx.cell_str(cell)))
        if key and key not in out.values():
            out[i] = key
    return out


def parse_sheet(wb, tab, sport, season, effective_week):
    rows = wb.table(tab)
    if not rows:
        return [], 0
    hdr = map_header(rows[0])
    if "_team" not in hdr.values():
        return [], 0
    missing = [p for p in POSITIONS if p not in hdr.values()]
    if missing:
        print("     WARNING: %s is missing position columns: %s" % (tab, ", ".join(missing)))

    out, teams = [], 0
    for r in rows[1:]:
        rec = {}
        for i, key in hdr.items():
            rec[key] = r[i] if i < len(r) else None
        team = xlsx.cell_str(rec.get("_team"))
        if not team:
            continue
        # Reconcile the sheet's spelling with the data feed's BEFORE storing,
        # so an unmatched name can never silently become an Elo fallback.
        team = team_aliases.canonical(sport, team)
        teams += 1
        for key in POSITIONS + META:
            v = rec.get(key)
            if v is None or v == "":
                continue
            try:
                grade = float(v)
            except (TypeError, ValueError):
                continue
            out.append({"sport": sport, "season": season, "week": effective_week,
                        "team": team, "position": key, "grade": grade})
    return out, teams


def verify_formula(conn, sport, season):
    """
    Re-derive the sheet's TOTAL from the imported parts and confirm it matches.

    If this fails, the import mis-mapped a column and every downstream number
    is built on the wrong inputs -- worth catching loudly and immediately.
    """
    rows = conn.execute(
        """SELECT week, team, position, grade FROM grades
           WHERE sport=? AND season=?""", (sport, season)).fetchall()
    by = {}
    for r in rows:
        by.setdefault((r["week"], r["team"]), {})[r["position"]] = r["grade"]

    checked = bad = 0
    for (wk, team), g in by.items():
        if "_sheet_total" not in g:
            continue
        seven = sum(g.get(p, 0.0) for p in ["qb", "rb", "wr", "ol", "dl", "lb", "db"])
        mine = (2 * seven + g.get("coach_st", 0.0)
                + g.get("_win_points", 0.0) - g.get("_loss_points", 0.0)
                + g.get("_wins", 0.0) - g.get("_losses", 0.0))
        checked += 1
        if abs(mine - g["_sheet_total"]) > 0.06:
            bad += 1
            if bad <= 3:
                print("     mismatch wk%s %-18s sheet=%.1f reimpl=%.1f"
                      % (wk, team, g["_sheet_total"], mine))
    return checked, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--sport", required=True, choices=["cfb", "nfl", "nba"])
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week-offset", type=int, default=1,
                    help="effective week = tab week + this (default 1: "
                         "'Week 5 Data' is the state AFTER week 5, so it predicts week 6)")
    ap.add_argument("--include-team-data", action="store_true",
                    help="also import the end-of-season 'Team Data' tab (NOT walk-forward safe)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.xlsx):
        sys.exit("No such file: %s" % args.xlsx)

    wb = xlsx.Workbook(args.xlsx)
    conn = db.connect()
    print("Workbook: %s" % os.path.basename(args.xlsx))
    print("Tabs: %s\n" % ", ".join(wb.sheet_names))

    total_rows = 0
    for tab in wb.sheet_names:
        m = _week_re.match(tab.strip())
        if not m:
            if tab.strip().lower() == "team data" and args.include_team_data:
                eff = 99
            else:
                continue
        else:
            eff = int(m.group(1)) + args.week_offset

        rows, teams = parse_sheet(wb, tab, args.sport, args.season, eff)
        if not rows:
            continue
        print("  %-16s -> effective week %-3d  %4d rows, %d teams" % (tab, eff, len(rows), teams))
        if not args.dry_run:
            conn.executemany(
                """INSERT INTO grades (sport, season, week, team, position, grade)
                   VALUES (:sport, :season, :week, :team, :position, :grade)
                   ON CONFLICT(sport, season, week, team, position)
                   DO UPDATE SET grade = excluded.grade""", rows)
            conn.commit()
        total_rows += len(rows)

    print("\n%s %d rows." % ("Would import" if args.dry_run else "Imported", total_rows))
    if args.dry_run:
        return

    checked, bad = verify_formula(conn, args.sport, args.season)
    print("Formula check: re-derived TOTAL for %d team-weeks, %d mismatches -> %s"
          % (checked, bad, "OK" if bad == 0 else "INVESTIGATE"))
    if bad:
        sys.exit("Import produced inputs that do not reproduce the sheet's own TOTAL.")


if __name__ == "__main__":
    main()
