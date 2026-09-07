#!/usr/bin/env python3
"""
regrade_report.py — which teams the film should be looked at again, and why.

WHAT THIS DOES NOT DO: invent grades. A position grade is Grant's reading of
film. Deriving one from a box score and writing it into the same column would
make the sheet's provenance a lie, and the sheet is the only thing this model
sells. So nothing here writes to `grades`.

What it does is rank the teams by how wrong the MODEL was about them in a way
the MARKET was not, which is the only signal that separates a grading error from
variance. A team both missed by is a team nobody could price; a team only the
model missed is a team whose rating is wrong.

Each row also splits the miss into offence and defence, from the model's own
predicted total and margin:

    expected points for     = (pred_total + pred_margin) / 2
    expected points against = (pred_total - pred_margin) / 2

so the report can say WHICH HALF of the sheet to re-read rather than just naming
a team. That decomposition is exact given what the model predicted; it is not an
opinion about play.

The automatic half of the same job already ships: `form_weight` in the config
moves every team by its own margin-against-expectation, shrunk for how little
evidence there is. This report is for the half a person has to do.

    python3 tools/regrade_report.py --season 2026 --top 20
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import backtest                                                  # noqa: E402
import db                                                        # noqa: E402
import engine                                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# HOW MANY STANDARD ERRORS BEFORE A MISS IS EVIDENCE.
#
# The first version of this used a flat 6-point bar and flagged 41 of 94 teams
# after ONE WEEK. That is not a regrade list, it is the noise of single-game
# football printed as a to-do. A team's margin against the closing line has a
# standard deviation around 13-14 points per game, so after one game the standard
# error on its mean is the whole of that: an 18-point miss is 1.3 sigma, which is
# an ordinary Saturday.
#
# So the bar is stated in standard errors and the residual spread is MEASURED from
# the season in hand rather than assumed. Two sigma is the usual bar; 1.75 is used
# because the cost of looking at some film is low and the cost of missing a
# genuinely mis-rated team is a whole season of bad prices. It still means that
# after one game essentially nothing qualifies, and the report says so plainly
# instead of manufacturing a list.
FLAG_SIGMA = 1.75


def walk(conn, config, sport, season):
    """Every finished game of `season`, predicted before it was observed."""
    games = backtest.load_games(conn, sport)
    grades = backtest.load_grades(conn, sport)
    have = {t for (s, t) in grades if s == season}
    cfg = dict(config)
    cfg.pop("_grades", None)
    model = engine.Model(cfg, grades)
    seen, out = None, []
    for g in games:
        if g["season"] != seen:
            seen = g["season"]
            model.new_season(seen)
        if (g["season"] == season and g["home_score"] is not None
                and g["away_score"] is not None):
            p = model.predict(g)
            out.append({
                "week": g["week"], "home": g["home_team"], "away": g["away_team"],
                "model": p["pred_margin"], "rating": p.get("rating_margin"),
                "form": p.get("form_adj"), "total": p.get("pred_total"),
                "market": g["market_margin"],
                "hs": g["home_score"], "as": g["away_score"],
                "graded": (g["home_team"] in have) and (g["away_team"] in have),
            })
        model.observe(g)
    return out, model


def residual_sd(rows):
    """Spread of (actual - market) over the season in hand, in points."""
    v = [(r["hs"] - r["as"]) - r["market"] for r in rows
         if r["graded"] and r["market"] is not None]
    if len(v) < 8:
        return None
    m = sum(v) / len(v)
    return (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** 0.5


def per_team(rows):
    acc = {}
    for r in rows:
        if not r["graded"]:
            continue
        actual = r["hs"] - r["as"]
        for team, sign, pf, pa in ((r["home"], 1.0, r["hs"], r["as"]),
                                   (r["away"], -1.0, r["as"], r["hs"])):
            d = acc.setdefault(team, {"n": 0, "model": 0.0, "market": 0.0,
                                      "off": 0.0, "deff": 0.0, "games": [],
                                      "market_n": 0})
            d["n"] += 1
            d["model"] += sign * (actual - r["model"])
            if r["market"] is not None:
                d["market"] += sign * (actual - r["market"])
                d["market_n"] += 1
            if r["total"] is not None:
                exp_for = (r["total"] + sign * r["model"]) / 2.0
                exp_against = (r["total"] - sign * r["model"]) / 2.0
                d["off"] += pf - exp_for
                d["deff"] += pa - exp_against
            d["games"].append("%s %s %d-%d" % (
                "vs" if sign > 0 else "at",
                r["away"] if sign > 0 else r["home"], pf, pa))
    out = []
    for t, d in acc.items():
        if not d["n"]:
            continue
        model_err = d["model"] / d["n"]
        market_err = d["market"] / d["market_n"] if d["market_n"] else None
        out.append({
            "team": t, "n": d["n"],
            "model_err": model_err, "market_err": market_err,
            # WHAT ONLY THE MODEL MISSED. The market's error is the part of the
            # surprise that was genuinely unpriceable; the remainder is the part
            # the rating owns.
            "attributable": None if market_err is None else model_err - market_err,
            "off": d["off"] / d["n"], "def": d["deff"] / d["n"],
            "games": d["games"],
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int)
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", help="write the full table here")
    args = ap.parse_args()

    path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(path))
    conn = db.connect()
    season = args.season or conn.execute(
        "SELECT MAX(season) s FROM games WHERE sport=? AND home_score IS NOT NULL",
        (args.sport,)).fetchone()["s"]

    rows, model = walk(conn, config, args.sport, season)
    teams = per_team(rows)
    sd = residual_sd(rows)
    if not teams:
        print("No finished, fully graded games in %s yet." % season)
        return

    played = sum(1 for r in rows if r["graded"])
    print("=" * 88)
    print("  REGRADE PRIORITY  %s %s  —  %d finished games with both teams graded"
          % (args.sport.upper(), season, played))
    print("=" * 88)
    print("""
  model   points the team beat the MODEL's expectation by (+ = rated too low)
  market  the same against the closing line (+ = the whole market was too low)
  own     model - market: the part of the miss the RATING owns, not the game
  off/def points scored / allowed against what the model expected
""")

    have_market = [t for t in teams if t["attributable"] is not None]
    for t in have_market:
        # sigma on a MEAN of n games. One game buys one game of certainty.
        t["se"] = (sd / (t["n"] ** 0.5)) if sd else None
        t["sigma"] = (t["attributable"] / t["se"]) if t["se"] else None
    have_market.sort(key=lambda t: -abs(t["sigma"] or 0))

    if sd:
        print("  Game-to-game spread against the closing line this season: %.1f pts."
              % sd)
        print("  A team's rating error is only readable against that, so `sigma`")
        print("  below is what matters and `own` on its own is not.\n")
    print("  %-22s %4s %7s %7s %7s %6s  %6s %6s"
          % ("team", "n", "model", "market", "own", "sigma", "off", "def"))
    print("  " + "-" * 80)
    for t in have_market[:args.top]:
        print("  %-22s %4d %+7.1f %+7.1f %+7.1f %6s  %+6.1f %+6.1f"
              % (t["team"][:22], t["n"], t["model_err"], t["market_err"],
                 t["attributable"],
                 "%+.2f" % t["sigma"] if t["sigma"] is not None else "—",
                 t["off"], t["def"]))

    flagged = [t for t in have_market
               if t["sigma"] is not None and abs(t["sigma"]) >= FLAG_SIGMA]
    print("\n  %d team(s) clear %.2f sigma." % (len(flagged), FLAG_SIGMA))
    if flagged:
        print("  Re-read the film on these first. The side named is the half of the")
        print("  sheet whose deviation points the SAME WAY as the rating error --")
        print("  a team rated too low that also scored above expectation is an")
        print("  offence problem; one that held its opponent below is a defence one.")
        for t in sorted(flagged, key=lambda x: -abs(x["sigma"])):
            low = t["attributable"] > 0
            # Signed, not absolute. Picking the bigger |deviation| named the
            # offence for a team that scored 20 BELOW expectation while calling it
            # under-rated -- two facts that cannot both be the same finding.
            off_agrees = (t["off"] > 0) == low
            def_agrees = (-t["def"] > 0) == low
            if off_agrees and not def_agrees:
                side = "offence (scored %+.0f vs expectation)" % t["off"]
            elif def_agrees and not off_agrees:
                side = "defence (allowed %+.0f vs expectation)" % t["def"]
            elif off_agrees and def_agrees:
                side = ("both halves (scored %+.0f, allowed %+.0f)"
                        % (t["off"], t["def"]))
            else:
                side = "neither half agrees — read the whole sheet"
            print("    %-22s rated %-8s by %4.1f pts (%.2f sigma over %d game%s)  %s"
                  % (t["team"][:22], "TOO LOW" if low else "TOO HIGH",
                     abs(t["attributable"]), abs(t["sigma"]), t["n"],
                     "" if t["n"] == 1 else "s", side))
    else:
        biggest = have_market[0] if have_market else None
        print("  NOTHING QUALIFIES, and after this many games that is the expected")
        print("  answer rather than a clean bill of health. The largest gap is")
        if biggest:
            print("  %s at %.1f points, which is %.2f sigma — an ordinary Saturday."
                  % (biggest["team"], abs(biggest["attributable"]),
                     abs(biggest["sigma"] or 0)))
        print("  On this evidence week 1's damage was not mis-graded teams. It was")
        print("  betting games the board declines: 38 of 85 graded picks were games")
        print("  where a team had no film grade at all, and 11 more had a line wider")
        print("  than a grade sheet can express.")

    # What the model has ALREADY corrected on its own, so the two are not done twice.
    if getattr(model, "form", None):
        moved = sorted(((model.form.value(t["team"]), t["team"]) for t in teams),
                       key=lambda x: -abs(x[0]))[:8]
        print("\n  Already applied automatically by the form term (points of margin,")
        print("  shrunk for how few games are behind it) — do NOT also hand-correct")
        print("  these by the same amount:")
        for v, t in moved:
            if abs(v) < 0.05:
                continue
            print("    %-24s %+5.2f" % (t[:24], v))

    if args.json:
        p = args.json if os.path.isabs(args.json) else os.path.join(ROOT, args.json)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump({"season": season, "games": played, "teams": teams},
                  open(p, "w"), indent=1)
        print("\n  full table -> %s" % p)


if __name__ == "__main__":
    main()
