"""
build_season_sheet.py — generate the new season's Power Rankings workbook.

Produces an .xlsx that Grant uploads to Drive once. It is built to the exact
schema the automation reads, so the sync works on day one instead of being
debugged in week 3 because a header drifted by one character.

WHAT IS AND ISN'T REAL HERE
The starting grades are DATA-DERIVED PLACEHOLDERS, not film grades. They carry
2025's final grades forward, regressed toward the league mean, so the sheet
opens with a sensible ordering instead of 138 blank rows. They are Grant's to
overwrite — the whole edge of this model is that a human watched the tape, and
nothing in this file substitutes for that. Every pre-filled grade is flagged in
the NOTES column until it is replaced.

THE TOTAL FORMULA NOW MATCHES THE MODEL
The 2025 sheet computed:
    TOTAL = 2*(seven groups) + Coach/ST + WinPts - LossPts + Wins - Losses
Backtesting showed the trailing `+ Wins - Losses` was double-counting the
quality points; removing it took the model from 53.19% to 54.78% ATS. So the
new sheet computes:
    TOTAL = 2*(seven groups) + Coach/ST + WinPts - LossPts
which is exactly what the engine now uses. What Grant sees in the sheet and
what the model bets are finally the same number.

The `- LossPts` sign is preserved deliberately. Loss Points are entered as
negative and subtracted, so a bad loss nudges the rating UP. That looks like a
bug and tests as a feature: "correcting" it drops the model to 51.00%.

Usage:
    python3 src/build_season_sheet.py --season 2026 --out "exports/College Football PR 26.xlsx"
"""

import argparse
import json
import os
import urllib.request

import db
import xlsx_write as xw
from xlsx_write import Formula

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

POSITIONS = ["qb", "rb", "wr", "ol", "dl", "lb", "db", "coach_st"]
HEADERS = ["", "Team Name", "Wins", "Losses",
           "QB Score 15", "RB Score 10", "WR Score 10", "OL Score 15",
           "DL Score 15", "LB Score 10", "DB Score 10", "Coach/ST Score 15",
           "Win Points", "Loss Points", "Player Totals", "TOTAL",
           "Conference", "NOTES"]
WIDTHS = [4, 24, 6, 7, 11, 11, 11, 11, 11, 11, 11, 16, 11, 11, 13, 9, 18, 30]

# Regression toward the mean from one season to the next. 0.35 = keep 35% of
# last year's deviation. Taken from the efficiency model's fitted season
# carryover rather than guessed -- college rosters turn over hard.
CARRYOVER = 0.35


def fbs_teams(season, key):
    """2026 FBS membership, straight from CFBD (cached on disk)."""
    cache = os.path.join(ROOT, "data", "cache", "teams-fbs_year-%d.json" % season)
    if os.path.exists(cache):
        return json.load(open(cache))
    req = urllib.request.Request(
        "https://api.collegefootballdata.com/teams/fbs?year=%d" % season,
        headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    json.dump(data, open(cache, "w"))
    return data


def final_grades(conn, season):
    """Each team's latest grade snapshot from `season`."""
    out = {}
    for r in conn.execute(
            "SELECT week, team, position, grade FROM grades "
            "WHERE sport='cfb' AND season=? ORDER BY week", (season,)):
        out.setdefault(r["team"], {}).setdefault(r["position"], r["grade"])
        out[r["team"]][r["position"]] = r["grade"]        # later week wins
    return out


def starting_grades(prev, teams):
    """
    Carry last season forward, regressed toward each position's league mean.

    New FBS programs have no prior grades. They are placed at the 20th
    percentile of each position rather than at the bottom: both 2026 entrants
    have been competitive with lower-tier FBS (North Dakota State lost 26-31 at
    Colorado; Sacramento State beat Stanford), so bottom-of-the-league would be
    a worse starting guess than a low-but-real one. They are flagged for
    grading regardless.
    """
    means = {}
    for p in POSITIONS:
        vals = [g[p] for g in prev.values() if p in g]
        means[p] = (sum(vals) / len(vals)) if vals else 8.0

    # Regress the returning teams FIRST.
    rows = {}
    for t in teams:
        if t in prev:
            rows[t] = {p: round(means[p] + (prev[t].get(p, means[p]) - means[p]) * CARRYOVER, 1)
                       for p in POSITIONS}
            rows[t]["_new"] = False

    # Percentiles must come from the REGRESSED distribution, not the raw one.
    # Regression compresses everyone toward the mean, so a percentile taken from
    # last season's spread lands below the entire compressed field -- which is
    # how both new teams first came out ranked 137th and 138th.
    pcts = {}
    for p in POSITIONS:
        vals = sorted(g[p] for g in rows.values())
        pcts[p] = vals[max(0, int(0.20 * len(vals)) - 1)] if vals else 7.0

    for t in teams:
        if t not in rows:
            rows[t] = {p: round(pcts[p], 1) for p in POSITIONS}
            rows[t]["_new"] = True
    return rows


def team_rows(grades, conf_by_team, prev):
    """Build the grid, ranked by starting TOTAL."""
    def total(g):
        seven = sum(g[p] for p in POSITIONS if p != "coach_st")
        return 2 * seven + g["coach_st"]

    ordered = sorted(grades.items(), key=lambda kv: -total(kv[1]))
    rows = [HEADERS]
    for rank, (team, g) in enumerate(ordered, start=1):
        r = rank + 1                      # spreadsheet row (header is row 1)
        note = ("NEW TO FBS — grade from film" if g["_new"]
                else "pre-filled from 2025, replace with film grade")
        rows.append([
            rank, team, 0, 0,
            g["qb"], g["rb"], g["wr"], g["ol"], g["dl"], g["lb"], g["db"], g["coach_st"],
            0, 0,
            Formula("SUM(E%d:K%d)" % (r, r)),
            # Matches the engine exactly: 2x(seven) + Coach/ST + WinPts - LossPts
            Formula("2*SUM(E%d:K%d)+L%d+M%d-N%d" % (r, r, r, r, r)),
            conf_by_team.get(team, ""),
            note,
        ])
    return rows


INSTRUCTIONS = [
    ["The Model — 2026 Power Rankings"],
    [""],
    ["HOW THIS WORKBOOK IS USED"],
    ["1. Grade film. Overwrite the pre-filled grades in 'Team Data'. They are"],
    ["   carried over from 2025 and regressed toward the mean — a starting point,"],
    ["   not a rating. The NOTES column flags every row still un-graded."],
    ["2. Each week, duplicate 'Team Data' and rename the copy 'Week N Data',"],
    ["   where N is the number of weeks COMPLETED. After week 3's games, the"],
    ["   snapshot is 'Week 3 Data'."],
    ["3. That is all. The automation reads every 'Week N Data' tab on a schedule"],
    ["   and does the rest — picks, grading, and the public record."],
    [""],
    ["THE TAB NAME MATTERS"],
    ["'Week N Data' is how the sync finds a snapshot, and N is what keeps the"],
    ["backtest honest: a tab named Week 3 may only predict week 4 onward. Rename"],
    ["a tab wrong and those grades are either ignored or used too early."],
    [""],
    ["DO NOT EDIT THESE COLUMNS"],
    ["Player Totals (O) and TOTAL (P) are formulas. TOTAL is:"],
    ["   2 x (QB+RB+WR+OL+DL+LB+DB) + Coach/ST + Win Points - Loss Points"],
    ["This is exactly what the model computes, so the sheet and the picks agree."],
    [""],
    ["WHY THERE IS NO 'Wins - Losses' TERM ANY MORE"],
    ["The 2025 sheet added raw Wins minus Losses on top of the quality points,"],
    ["which counted every result twice. Removing it moved the model from 53.19%"],
    ["to 54.78% ATS over 502 graded games, and fixed its calibration."],
    [""],
    ["WHY LOSS POINTS STILL LOOK BACKWARDS"],
    ["Loss Points are entered NEGATIVE and subtracted, so a bad loss nudges a"],
    ["rating up. That looks wrong and tests as right: correcting the sign drops"],
    ["the model to 51.00%. Likely because a good team's bad day is over-punished"],
    ["by the market too. Leave it as it is."],
    [""],
    ["SCORING SCALES"],
    ["QB, OL, DL, Coach/ST: 1-15 (realistic floor ~10)"],
    ["RB, WR, LB, DB: 1-10 (realistic floor ~6.5)"],
    ["Win Points: Top 5 +4 | Top 10 +3 | Top 25 +2"],
    ["Loss Points: unranked FBS -2 | FCS -4   (enter as NEGATIVE numbers)"],
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--prev-season", type=int, default=2025)
    ap.add_argument("--out", default="exports/College Football PR 26.xlsx")
    args = ap.parse_args()

    import fetch_cfb
    key = fetch_cfb.load_key()
    conn = db.connect()

    teams_raw = fbs_teams(args.season, key)
    teams = sorted({t["school"] for t in teams_raw})
    conf = {t["school"]: (t.get("conference") or "") for t in teams_raw}
    prev = final_grades(conn, args.prev_season)

    grades = starting_grades(prev, teams)
    new_teams = [t for t, g in grades.items() if g["_new"]]

    rows = team_rows(grades, conf, prev)
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    xw.write(out, [
        ("Team Data", rows),
        ("Week 0 Data", [list(r) for r in rows]),
        ("How This Works", INSTRUCTIONS),
    ], widths=WIDTHS)

    print("Built %s" % out)
    print("  %d FBS teams for %d" % (len(teams), args.season))
    print("  carried %d teams forward from %d (regressed %.0f%% toward the mean)"
          % (len(teams) - len(new_teams), args.prev_season, (1 - CARRYOVER) * 100))
    if new_teams:
        print("  NEW to FBS, placed at the 20th percentile and flagged: %s"
              % ", ".join(sorted(new_teams)))
    print("  tabs: Team Data | Week 0 Data | How This Works")
    print("\n  Every grade in here is a placeholder. Grade film over the top of it.")


if __name__ == "__main__":
    main()
