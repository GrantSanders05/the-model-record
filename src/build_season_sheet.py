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


def teamcrafters_grades(conn, teams, game, roster_slug, prev_season):
    """
    Starting grades from the EA roster file instead of last season's carryover.

    A carryover knows last season. A roster file knows THIS season -- who
    transferred in, who left, who was recruited. Backtested against the real
    2025 season it scored 53.57% ATS against Elo's 50.52%, so it carries genuine
    signal; it lost to Grant's own film grades (54.56% on the same games, same
    static footing), so it is a seed and not a replacement.

    Only the ORDERING comes from EA. The numbers are mapped onto the shape of
    Grant's own grade distribution, so the columns look like his columns and the
    engine's tuned parameters stay valid. See game_ratings.py.
    """
    import fetch_teamcrafters as tc
    import game_ratings
    import team_aliases

    target = game_ratings.target_from_grades(conn, "cfb", prev_season)
    if not target:
        raise SystemExit("No %d grades to take a target distribution from." % prev_season)

    rosters = tc.fetch_all(game, roster_slug)
    graded = game_ratings.build(rosters, target)

    by_canon = {}
    for name, gmap in graded.items():
        by_canon[team_aliases.canonical("cfb", name)] = gmap

    rows, missing = {}, []
    for t in teams:
        g = by_canon.get(t)
        if g is None:
            missing.append(t)
            continue
        rows[t] = dict(g)
        rows[t]["_new"] = False
        rows[t]["_src"] = "ea"
    return rows, missing


def team_rows(grades, conf_by_team, prev):
    """Build the grid, ranked by starting TOTAL."""
    def total(g):
        seven = sum(g[p] for p in POSITIONS if p != "coach_st")
        return 2 * seven + g["coach_st"]

    ordered = sorted(grades.items(), key=lambda kv: -total(kv[1]))
    rows = [HEADERS]
    for rank, (team, g) in enumerate(ordered, start=1):
        r = rank + 1                      # spreadsheet row (header is row 1)
        if g.get("_src") == "ea":
            note = "EA CFB27 launch rating — replace with film grade"
        elif g["_new"]:
            note = "NEW TO FBS — grade from film"
        else:
            note = "pre-filled from 2025, replace with film grade"
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
    ["1. Grade film. Overwrite the pre-filled grades in 'Team Data'. They are a"],
    ["   starting point, not a rating. The NOTES column flags every un-graded row."],
    ["2. Each week, duplicate 'Team Data' and rename the copy 'Week N Data',"],
    ["   where N is the number of weeks COMPLETED. After week 3's games, the"],
    ["   snapshot is 'Week 3 Data'."],
    ["3. That is all. The automation reads every 'Week N Data' tab on a schedule"],
    ["   and does the rest — picks, grading, and the public record."],
    [""],
    ["WHERE THE STARTING GRADES CAME FROM"],
    ["The EA College Football 27 launch roster (TeamCrafters, 30 June 2026), one"],
    ["team at a time, one position group at a time. Each group is a DEPTH-WEIGHTED"],
    ["average of its players' overalls — the starters carry the rating, because a"],
    ["team with seven halfbacks is not better at running back than one with three."],
    [""],
    ["Only the ORDER of teams is taken from EA. The numbers themselves are mapped"],
    ["onto the same distribution your 2025 grades occupied, so every column has"],
    ["your centre, your spread, and your floor and ceiling. The sheet looks like"],
    ["your sheet. What changed is who is ranked where."],
    [""],
    ["HOW GOOD ARE THEY? (measured, not asserted)"],
    ["The equivalent EA file was published before the 2025 season, so it could be"],
    ["replayed against a season that actually happened. On 785 games, scoring the"],
    ["identical game set, all of it out of sample:"],
    ["   your film grades, frozen preseason ....... 54.56% ATS"],
    ["   EA roster ratings ........................ 53.57% ATS"],
    ["   Elo, no grades at all .................... 50.52% ATS"],
    ["Break-even is 52.38%. So the roster file is real signal and clears the"],
    ["number — and YOUR EYE STILL BEAT IT by about a point. Grade over the top."],
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
    ap.add_argument("--source", default="teamcrafters",
                    choices=["teamcrafters", "carryover"],
                    help="where the starting grades come from")
    ap.add_argument("--tc-game", default="CFB27")
    ap.add_argument("--tc-roster", default="launch-6-30-26")
    args = ap.parse_args()

    import fetch_cfb
    key = fetch_cfb.load_key()
    conn = db.connect()

    teams_raw = fbs_teams(args.season, key)
    teams = sorted({t["school"] for t in teams_raw})
    conf = {t["school"]: (t.get("conference") or "") for t in teams_raw}
    prev = final_grades(conn, args.prev_season)

    if args.source == "teamcrafters":
        grades, missing = teamcrafters_grades(
            conn, teams, args.tc_game, args.tc_roster, args.prev_season)
        if missing:
            # A team the roster file does not cover still needs a row, or it
            # vanishes from the sheet and every one of its games silently falls
            # back to Elo. Fill those from the carryover path instead.
            print("  %d team(s) absent from %s, filled from %d carryover: %s"
                  % (len(missing), args.tc_game, args.prev_season, ", ".join(missing)))
            fallback = starting_grades(prev, missing)
            for t in missing:
                grades[t] = fallback[t]
    else:
        grades = starting_grades(prev, teams)

    new_teams = [t for t, g in grades.items() if g.get("_new")]

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

    n_ea = sum(1 for g in grades.values() if g.get("_src") == "ea")
    if n_ea:
        print("  %d teams graded from %s %s (depth-weighted position groups,"
              % (n_ea, args.tc_game, args.tc_roster))
        print("     mapped onto the shape of the %d grade distribution)" % args.prev_season)
    n_carry = len(grades) - n_ea - len(new_teams)
    if n_carry > 0:
        print("  %d team(s) carried forward from %d (regressed %.0f%% to the mean)"
              % (n_carry, args.prev_season, (1 - CARRYOVER) * 100))
    if new_teams:
        print("  NEW to FBS, placed at the 20th percentile and flagged: %s"
              % ", ".join(sorted(new_teams)))
    print("  tabs: Team Data | Week 0 Data | How This Works")
    print("\n  Every grade in here is a starting point. Grade film over the top of it.")


if __name__ == "__main__":
    main()
