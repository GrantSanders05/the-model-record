"""
import_grades.py — load Grant's position group grades from exported CSVs.

Expects the common shape of a power-rankings sheet: one row per team, one
column per position group.

    Team,QB,RB,WR,OL,DL,LB,DB,Coach,ST
    Georgia,13.5,8.2,8.8,12.1,13.9,8.0,8.4,13.2,11.0
    ...

Column names are matched case-insensitively against a synonym table, so
"Quarterback", "QB Rating" and "qb" all land in the same slot. Anything it
cannot map is reported rather than silently dropped — a quietly ignored column
is a quietly missing input to the model.

Grades are stamped with the (season, week) they became effective. That is what
lets the backtester enforce walk-forward: a grade published in week 7 is never
visible to a week 5 prediction. If your export has no week column, pass --week;
if the sheet only holds current-season end-state grades, import them at the
week they were written, NOT week 1, or the backtest will be optimistic.

Usage:
    python3 src/import_grades.py --csv exports/cfb_pr_2025.csv --sport cfb --season 2025 --week 1
    python3 src/import_grades.py --csv exports/*.csv --sport cfb --season 2025 --dry-run
"""

import argparse
import csv
import glob
import os
import re
import sys

import db

# Maps whatever the sheet calls a column to the engine's canonical position key.
SYNONYMS = {
    "qb": ["qb", "quarterback", "quarterbacks", "passing"],
    "rb": ["rb", "runningback", "running back", "rushing", "backs"],
    "wr": ["wr", "receiver", "receivers", "wideout", "wr/te", "wrte", "receiving"],
    "ol": ["ol", "oline", "o line", "offensive line", "olinemen", "o-line"],
    "dl": ["dl", "dline", "d line", "defensive line", "d-line", "front seven"],
    "lb": ["lb", "linebacker", "linebackers"],
    "db": ["db", "secondary", "defensive back", "defensive backs", "dbs"],
    "coach": ["coach", "coaching", "coaches", "staff", "hc"],
    "st": ["st", "special teams", "specialteams", "sp teams", "special"],
}
TEAM_KEYS = ["team", "school", "teams", "name", "opponent"]
WEEK_KEYS = ["week", "wk"]
SEASON_KEYS = ["season", "year"]

_norm_re = re.compile(r"[^a-z0-9 ]+")


def _norm(s):
    return _norm_re.sub("", (s or "").strip().lower())


def build_column_map(fieldnames):
    """Return (position_columns, team_col, week_col, season_col, unmapped)."""
    pos_cols, unmapped = {}, []
    team_col = week_col = season_col = None
    lookup = {}
    for canon, alts in SYNONYMS.items():
        for a in alts:
            lookup[_norm(a)] = canon

    for f in fieldnames or []:
        n = _norm(f)
        if not n:
            continue
        if n in TEAM_KEYS and team_col is None:
            team_col = f
        elif n in WEEK_KEYS and week_col is None:
            week_col = f
        elif n in SEASON_KEYS and season_col is None:
            season_col = f
        elif n in lookup:
            pos_cols[f] = lookup[n]
        else:
            unmapped.append(f)
    return pos_cols, team_col, week_col, season_col, unmapped


def parse_file(path, sport, season_default, week_default):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        pos_cols, team_col, week_col, season_col, unmapped = build_column_map(reader.fieldnames)

        if not pos_cols:
            raise SystemExit(
                "%s: no position-group columns recognized.\nColumns found: %s\n"
                "Add the sheet's own names to SYNONYMS in this file."
                % (path, reader.fieldnames))
        if team_col is None:
            raise SystemExit("%s: no team column found (looked for %s). Columns: %s"
                             % (path, TEAM_KEYS, reader.fieldnames))

        rows, skipped = [], 0
        for rec in reader:
            team = (rec.get(team_col) or "").strip()
            if not team:
                continue
            season = rec.get(season_col) if season_col else None
            week = rec.get(week_col) if week_col else None
            try:
                season = int(str(season).strip()) if season not in (None, "") else season_default
                week = int(str(week).strip()) if week not in (None, "") else week_default
            except ValueError:
                skipped += 1
                continue
            if season is None or week is None:
                raise SystemExit(
                    "%s: no season/week column and none supplied. Pass --season and --week."
                    % path)

            for col, pos in pos_cols.items():
                raw = (rec.get(col) or "").strip()
                if raw in ("", "-", "NA", "N/A"):
                    continue
                try:
                    grade = float(raw.replace(",", ""))
                except ValueError:
                    skipped += 1
                    continue
                rows.append({"sport": sport, "season": season, "week": week,
                             "team": team, "position": pos, "grade": grade})
    return rows, pos_cols, unmapped, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, nargs="+", help="CSV path(s); globs allowed")
    ap.add_argument("--sport", required=True, choices=["cfb", "nfl", "nba"])
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int,
                    help="week these grades became effective (required if the CSV has no week column)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    paths = []
    for pattern in args.csv:
        paths.extend(sorted(glob.glob(pattern)) or ([pattern] if os.path.exists(pattern) else []))
    if not paths:
        sys.exit("No CSV files matched: %s" % args.csv)

    conn = db.connect()
    total = 0
    for path in paths:
        rows, pos_cols, unmapped, skipped = parse_file(path, args.sport, args.season, args.week)
        print("%s" % path)
        print("   mapped columns : %s" % ", ".join(
            "%s->%s" % (k, v) for k, v in sorted(pos_cols.items(), key=lambda kv: kv[1])))
        if unmapped:
            print("   NOT MAPPED     : %s" % ", ".join(unmapped))
            print("                    (ignored — add to SYNONYMS if these are grades)")
        teams = len({r["team"] for r in rows})
        print("   %d grade rows across %d teams%s"
              % (len(rows), teams, ", %d cells skipped" % skipped if skipped else ""))

        if not args.dry_run and rows:
            conn.executemany(
                """INSERT INTO grades (sport, season, week, team, position, grade)
                   VALUES (:sport, :season, :week, :team, :position, :grade)
                   ON CONFLICT(sport, season, week, team, position)
                   DO UPDATE SET grade = excluded.grade""", rows)
            conn.commit()
        total += len(rows)

    print("\n%s %d grade rows." % ("Would import" if args.dry_run else "Imported", total))
    if not args.dry_run:
        n = conn.execute(
            "SELECT COUNT(*) c, COUNT(DISTINCT team) t, COUNT(DISTINCT season) s "
            "FROM grades WHERE sport=?", (args.sport,)).fetchone()
        print("grades table now holds %d rows, %d teams, %d seasons for %s."
              % (n["c"], n["t"], n["s"], args.sport))
        print("\nNext: compare grades against the results-only baseline —")
        print("    python3 src/optimize.py --sport %s --rater blend \\" % args.sport)
        print("        --train <seasons> --test <seasons> --save config/%s_blend.json" % args.sport)
        print("  blend_weight near 0 means the grades add nothing over results;")
        print("  near 1 means they carry the signal. That is the number that matters.")


if __name__ == "__main__":
    main()
