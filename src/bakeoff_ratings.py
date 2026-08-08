"""
bakeoff_ratings.py — are EA roster ratings better than the hand grades?

THE EXPERIMENT
The CFB26 roster released 2 July 2025 was published BEFORE a single game of the
2025 season. So it can be turned into position grades and replayed against the
season that actually happened, with no hindsight available to it at all. That is
a real out-of-sample test, not a plausibility argument.

WHAT IS HELD CONSTANT
Only the eight position columns change. Win points, loss points, the ranking
lookups, home-field, scale -- every other input stays exactly as it is in
Grant's sheet. So a difference in the result is attributable to the grades and
to nothing else.

WHY EVERY ARM SCORES THE SAME GAMES
An arm that cannot rate both teams falls back to Elo, silently. If the arms were
each scored on whatever games they happened to cover, a difference between them
could just be a difference in which games they played -- which is exactly the
trap that once made 30% of a "grade model" backtest secretly Elo. So the game
set is intersected first: only games every arm can rate on its own are scored,
and the count is printed so the sample is never taken on trust.

    python3 src/bakeoff_ratings.py
"""

import argparse
import copy
import json
import os
import sys
from collections import defaultdict

import backtest
import db
import fetch_teamcrafters as tc
import game_ratings
import metrics
import team_aliases

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUALITY_KEYS = ("_win_points", "_loss_points", "_wins", "_losses")


def ea_grades(game, roster_slug, target, season, **kw):
    """EA roster -> {(season, team): [(0, {pos: grade})]}, canonical team names."""
    rosters = tc.fetch_all(game, roster_slug)
    graded = game_ratings.build(rosters, target, **kw)
    out = {}
    for team, gmap in graded.items():
        canon = team_aliases.canonical("cfb", team)
        out[(season, canon)] = [(0, dict(gmap))]
    return out


def splice(hand, ea, season):
    """
    Grant's sheet with ONLY the position columns replaced.

    His win/loss points are preserved week by week, so the arm under test is
    'his model, EA's talent estimate' rather than a different model entirely.
    """
    out = {}
    for key, entries in hand.items():
        s, team = key
        if s != season:
            out[key] = entries
            continue
        src = ea.get((season, team))
        if not src:
            continue                       # no EA rating -> arm cannot rate it
        ea_pos = src[0][1]
        merged = []
        for week, gmap in entries:
            m = {k: v for k, v in gmap.items() if k in QUALITY_KEYS}
            m.update(ea_pos)
            merged.append((week, m))
        out[key] = merged
    return out


def freeze(hand, season, at_week=1):
    """
    Grant's position columns frozen at his earliest snapshot.

    His real grades are re-cut every couple of weeks, so comparing them against
    a single static roster file is comparing a human WITH in-season information
    to a file without any. Freezing his position columns at week 1 -- while
    still letting his win/loss points accrue weekly, exactly as in the EA arm --
    puts both on the same footing: one preseason talent estimate, held all year.

    That is also the decision actually being made. A sheet for a season that has
    not started has no film grades in it yet, and the only question is which
    preseason estimate should be sitting there when Grant opens it.
    """
    out = {}
    for key, entries in hand.items():
        s, team = key
        if s != season:
            out[key] = entries
            continue
        base = None
        for w, gmap in sorted(entries):
            if w >= at_week:
                base = {k: v for k, v in gmap.items() if k not in QUALITY_KEYS}
                break
        if base is None:
            continue
        out[key] = [(w, dict(base, **{k: v for k, v in gmap.items() if k in QUALITY_KEYS}))
                    for w, gmap in entries]
    return out


def blend(hand_static, ea, season, weight=0.5):
    """
    Average the two preseason estimates on the rank scale, then re-map.

    Two noisy independent estimates of the same quantity usually beat either
    one. Averaging the GRADES rather than the ranks would be fine here too --
    both are already on the same scale by construction -- and is simpler to
    reason about, so that is what this does.
    """
    out = {}
    for key, entries in hand_static.items():
        s, team = key
        if s != season:
            out[key] = entries
            continue
        src = ea.get((season, team))
        if not src:
            continue
        ea_pos = src[0][1]
        merged = []
        for week, gmap in entries:
            m = {k: v for k, v in gmap.items() if k in QUALITY_KEYS}
            for p in game_ratings.POSITIONS:
                a, b = gmap.get(p), ea_pos.get(p)
                if a is None and b is None:
                    continue
                if a is None:
                    m[p] = b
                elif b is None:
                    m[p] = a
                else:
                    m[p] = round(a * (1 - weight) + b * weight, 2)
            merged.append((week, m))
        out[key] = merged
    return out


def rateable(grades, season):
    return {team for (s, team) in grades if s == season}


def score(games, config, grades, season, label, common):
    cfg = copy.deepcopy(config)
    cfg["_grades"] = grades
    subset = [g for g in games
              if g["season"] != season
              or g["game_id"] in common]
    preds = backtest.run(subset, cfg, test_seasons=[season])
    preds = [p for p in preds if p["game_id"] in common]
    return label, metrics.evaluate(preds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--game", default="CFB26")
    ap.add_argument("--roster", default="initial-070225")
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--variants", action="store_true",
                    help="also test TE/FB placement and depth-weighting choices")
    ap.add_argument("--seed-test", action="store_true",
                    help="compare preseason estimates like-for-like (both static)")
    args = ap.parse_args()

    path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(path)) if os.path.exists(path) else {}
    conn = db.connect()

    target = game_ratings.target_from_grades(conn, "cfb", args.season)
    hand = backtest.load_grades(conn, "cfb")
    games = backtest.load_games(conn, "cfb")

    print("Target distribution taken from Grant's %d grades:" % args.season)
    for p in game_ratings.POSITIONS:
        if p in target:
            mean, sd = game_ratings._mean_sd(target[p])
            print("   %-9s n=%d  mean %5.2f  sd %4.2f  range %.1f-%.1f"
                  % (p, len(target[p]), mean, sd, target[p][0], target[p][-1]))

    arms = {}
    ea = ea_grades(args.game, args.roster, target, args.season)
    arms["sheet (Grant's hand grades)"] = hand
    arms["EA %s %s" % (args.game, args.roster)] = splice(hand, ea, args.season)

    if args.seed_test:
        # The comparison that decides what goes in the new sheet: two preseason
        # estimates, both held static all season, neither updated by results.
        hs = freeze(hand, args.season, at_week=1)
        arms["hand, FROZEN at week 1 (preseason only)"] = hs
        arms["blend 50/50 EA + frozen hand"] = blend(hs, ea, args.season, 0.5)
        arms["blend 70/30 EA + frozen hand"] = blend(hs, ea, args.season, 0.7)

    if args.variants:
        for label, kw in (
                ("EA  TE->ol", dict(te="ol")),
                ("EA  TE->rb", dict(te="rb")),
                ("EA  FB->ol", dict(fb="ol")),
                ("EA  flat mean (no depth weight)", dict(mode="flat")),
                ("EA  starters only", dict(mode="starters")),
        ):
            arms[label] = splice(hand, ea_grades(args.game, args.roster, target,
                                                 args.season, **kw), args.season)

    # --- the common game set -------------------------------------------------
    rate = {k: rateable(v, args.season) for k, v in arms.items()}
    common_teams = set.intersection(*rate.values())
    common = {g["game_id"] for g in games
              if g["season"] == args.season
              and g["market_margin"] is not None
              and g["home_score"] is not None
              and g["home_team"] in common_teams
              and g["away_team"] in common_teams}
    print("\nTeams rateable by every arm: %d | games scored by every arm: %d"
          % (len(common_teams), len(common)))
    for k, v in rate.items():
        missing = common_teams - v
        if missing:
            print("  !! %s missing %s" % (k, sorted(missing)[:5]))

    results = []
    for label, grades in arms.items():
        results.append(score(games, config, grades, args.season, label, common))
    # Elo needs no grades at all; it is the floor every arm must clear.
    elo_cfg = copy.deepcopy(config)
    elo_cfg["rater"] = "elo"
    elo_preds = backtest.run(
        [g for g in games if g["season"] != args.season or g["game_id"] in common],
        elo_cfg, test_seasons=[args.season])
    results.append(("elo (no grades)",
                    metrics.evaluate([p for p in elo_preds if p["game_id"] in common])))

    print("\n%-38s %5s %7s %14s %7s %7s" % (
        "arm", "n", "ATS%", "95% CI", "MAE", "calib"))
    print("-" * 84)
    for label, m in results:
        ci = m.get("ats_ci95") or (0.0, 0.0)
        print("%-38s %5d %7.2f  %5.1f-%5.1f %7.2f %7.3f%s" % (
            label, m.get("ats_n", 0), m.get("ats_pct") or 0.0, ci[0], ci[1],
            m.get("mae", 0.0), m.get("calib_slope") or 0.0,
            "  *" if m.get("ats_significant") else ""))

    print("\nBreak-even at -110 is 52.38%. Nothing here is significant on one")
    print("season; the number that matters is whether EA's ordering beats the")
    print("hand grades' ordering by more than a season of noise can explain.")


if __name__ == "__main__":
    main()
