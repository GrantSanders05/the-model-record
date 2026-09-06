"""
compare_champion.py — does the V2 adapter produce the Champion's own numbers?

    python3 tools/compare_champion.py --week 2

THE PRE-CUTOVER GATE. V2 must not change what the model thinks; it changes how
what the model thinks is recorded. So before the official writer moves, the same
slate is priced both ways and every difference has to be explained.

  legacy path   predict.generate -> best_bets.rank
  V2 path       forecast_v2.ChampionAdapter at the same instant

The one difference that is EXPECTED and correct: the legacy path reads the
`lines` table, which keeps one preferred provider per game, and V2 reads a
consensus across every provider. Those are different market numbers on purpose,
so the model MARGIN is compared (which must match exactly) separately from the
edge and the side (which may legitimately differ where the consensus and the
preferred book disagree).
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import backtest        # noqa: E402
import best_bets       # noqa: E402
import db              # noqa: E402
import forecast_v2     # noqa: E402
import features_v2     # noqa: E402
import predict         # noqa: E402
import pro_models      # noqa: E402


def compare(conn, sport, config, season, week, as_of):
    legacy = {p["game_id"]: p for p in
              predict.generate(conn, sport, dict(config), week=week, season=season)}

    grades = backtest.load_grades(conn, sport)
    stats = pro_models.load_stats(conn, sport)
    games = backtest.load_games(conn, sport)
    adapter = forecast_v2.ChampionAdapter(config, grades, stats)

    v2 = {}
    seen = None
    for g in games:
        if g["season"] != seen:
            seen = g["season"]
            adapter.model.new_season(g["season"])
            adapter._season = g["season"]
        played = g["home_score"] is not None and g["away_score"] is not None
        if (not played and g["season"] == season
                and (week is None or g["week"] == week)):
            v2[g["game_id"]] = adapter.forecast(g)
        adapter.model.observe(g)

    rows = []
    for gid, lp in sorted(legacy.items()):
        vf = v2.get(gid)
        if vf is None:
            rows.append({"game_id": gid, "problem": "V2 produced no forecast"})
            continue
        dm = (vf["pred_home_margin"] - lp["model_margin"])
        rows.append({
            "game_id": gid, "away": lp["away"], "home": lp["home"],
            "legacy_margin": lp["model_margin"],
            "v2_margin": round(vf["pred_home_margin"], 4),
            "margin_delta": round(dm, 4),
            "legacy_borrowed": lp.get("unrated"),
            "v2_borrowed": bool(vf["borrowed_fallback"]),
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="points of margin difference treated as rounding")
    args = ap.parse_args()

    conn = db.connect()
    config = json.load(open(os.path.join(ROOT, args.config)))
    season = args.season or conn.execute(
        "SELECT MAX(season) s FROM games WHERE sport=?", (args.sport,)).fetchone()["s"]

    rows = compare(conn, args.sport, config, season, args.week, None)
    ok = [r for r in rows if "problem" not in r
          and abs(r["margin_delta"]) <= args.tolerance]
    bad = [r for r in rows if "problem" in r
           or abs(r.get("margin_delta", 0)) > args.tolerance]
    mismatched_borrow = [r for r in rows if "problem" not in r
                         and bool(r["legacy_borrowed"]) != bool(r["v2_borrowed"])]

    print("\n── Champion, priced both ways: %s %s week %s ──"
          % (args.sport, season, args.week if args.week is not None else "all"))
    print("  games compared              : %d" % len(rows))
    print("  model margins agree         : %d" % len(ok))
    print("  model margins DIFFER        : %d" % len(bad))
    print("  fallback flag disagrees     : %d" % len(mismatched_borrow))
    if bad:
        print("\n  Differences (V2 must not change what the Champion thinks):")
        for r in bad[:12]:
            if "problem" in r:
                print("    %-26s %s" % (r["game_id"], r["problem"]))
            else:
                print("    %-22s @ %-22s legacy %+7.3f  v2 %+7.3f  delta %+.3f"
                      % (r["away"][:22], r["home"][:22], r["legacy_margin"],
                         r["v2_margin"], r["margin_delta"]))
    if mismatched_borrow:
        print("\n  Fallback disagreements:")
        for r in mismatched_borrow[:8]:
            print("    %-22s @ %-22s legacy=%s v2=%s"
                  % (r["away"][:22], r["home"][:22],
                     r["legacy_borrowed"], r["v2_borrowed"]))

    if bad or mismatched_borrow:
        raise SystemExit("\nNOT READY TO CUT OVER: the two paths disagree.")
    print("\n  Both paths produce the same Champion. Safe to cut over.")


if __name__ == "__main__":
    main()
