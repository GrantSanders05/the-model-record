"""
fit_challengers.py — fit E003 and E004 on development data, register them, shadow.

    python3 tools/fit_challengers.py --season 2025 --dry-run
    python3 tools/fit_challengers.py --season 2025 --apply

DEVELOPMENT DATA IS 2025, AND IT IS DEVELOPMENT DATA. It has been used to choose
terms, fit scale, split coefficients, fit the edge shrink and inspect buckets, by
a human who saw each result and then changed the system. That makes it a
validation set however carefully any single script splits it, so a number
produced here is a hypothesis and not evidence.

The evidence is 2026, prospectively, from forecasts filed before kickoff by a
version that never saw those outcomes. That is what the registry and the shadow
forecasts are for, and it is why nothing here promotes anything.

WHAT THE MARKET NUMBER IS, HONESTLY. Development rows use the stored `lines` row:
one preferred provider, at an unrecorded time, which is `unknown_historical_current`
rather than an exact-horizon snapshot. A model fitted on that and forecasting at
T2 is a change of data regime. Every row carries its timing quality so the
limitation travels with the data instead of being rediscovered later.
"""

import argparse
import datetime as dt
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import db                                    # noqa: E402
import grade_snapshots                       # noqa: E402
import provenance                            # noqa: E402
from models_v2 import residual_grade, matchup_residual   # noqa: E402
from models_v2.ridge import predict_ridge    # noqa: E402

TIMING_UNKNOWN = "unknown_historical_current"


def development_rows(conn, sport, season):
    """
    One row per completed, lined, fully graded game. -> [dict]

    Grades come from `grade_asof` at the game's own kickoff, so a row cannot
    contain a grade published after the game it describes.
    """
    games = conn.execute(
        "SELECT g.game_id, g.season, g.week, g.home_team, g.away_team, g.kickoff,"
        "       g.neutral_site, g.home_score, g.away_score, l.home_margin"
        "  FROM games g JOIN lines l ON l.game_id = g.game_id"
        " WHERE g.sport=? AND g.season=? AND g.home_score IS NOT NULL"
        "   AND g.away_score IS NOT NULL AND l.home_margin IS NOT NULL"
        " ORDER BY g.kickoff", (sport, season)).fetchall()

    rows = []
    for g in games:
        payload = {
            "home_grade_vector": grade_snapshots.vector_of(
                grade_snapshots.grade_asof(conn, sport, season, g["home_team"],
                                           g["kickoff"])),
            "away_grade_vector": grade_snapshots.vector_of(
                grade_snapshots.grade_asof(conn, sport, season, g["away_team"],
                                           g["kickoff"])),
            "consensus_spread": g["home_margin"],
            "neutral_site": g["neutral_site"],
        }
        base = residual_grade.features_from_payload(payload)
        if base is None:
            continue
        match = matchup_residual.features_from_payload(payload)
        actual = g["home_score"] - g["away_score"]
        row = dict(base)
        row.update({k: v for k, v in match.items() if k not in row})
        row.update({
            "game_id": g["game_id"], "week": g["week"], "kickoff": g["kickoff"],
            "actual_margin": actual,
            "residual": actual - g["home_margin"],
            "market_timing_quality": TIMING_UNKNOWN,
        })
        rows.append(row)
    return rows


def split(rows, *, valid_fraction=0.3, seed=20260906):
    """
    Time-aware split: EARLY weeks train, LATE weeks validate.

    Not random. Games in one weekend share weather, news and market conditions,
    so a random split puts near-duplicates on both sides and reports a validation
    score that is partly a memory test. Splitting on the calendar also matches how
    the model is used: fitted on what has happened, applied to what has not.
    """
    ordered = sorted(rows, key=lambda r: (r["week"] or 0, r["kickoff"] or ""))
    cut = int(len(ordered) * (1 - valid_fraction))
    return ordered[:cut], ordered[cut:]


def evaluate(artifact, rows):
    """RMSE and MAE of the residual prediction, plus what the market alone scores."""
    import math
    errs, base = [], []
    for r in rows:
        if r.get("residual") is None:
            continue
        pred = predict_ridge(artifact, r)
        errs.append((pred - r["residual"]) ** 2)
        base.append(r["residual"] ** 2)          # the market alone predicts 0
    if not errs:
        return None
    return {
        "n": len(errs),
        "rmse": round(math.sqrt(sum(errs) / len(errs)), 3),
        "market_rmse": round(math.sqrt(sum(base) / len(base)), 3),
        "improvement": round(math.sqrt(sum(base) / len(base))
                             - math.sqrt(sum(errs) / len(errs)), 3),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    random.seed(20260906)
    conn = db.connect()
    rows = development_rows(conn, args.sport, args.season)
    print("\n── development data: %s %d ──" % (args.sport, args.season))
    print("  usable rows (lined, both teams fully graded): %d" % len(rows))
    if len(rows) < 200:
        raise SystemExit("too few rows to fit anything worth registering.")
    train, valid = split(rows)
    print("  train weeks 1-%s: %d rows   validate: %d rows"
          % (max(r["week"] for r in train), len(train), len(valid)))
    print("  market timing quality: %s (development rows are not T2 snapshots)"
          % TIMING_UNKNOWN)

    out = {}
    for name, mod, cls in (("E003 residual grade", residual_grade,
                            residual_grade.ResidualGrade),
                           ("E004 matchup residual", matchup_residual,
                            matchup_residual.MatchupResidual)):
        print("\n── %s ──" % name)
        model = cls().fit(train, valid=valid)
        art = model.artifact()
        print("  lambda chosen on the held-out split: %g" % art["lambda"])
        ev_t = evaluate(art, train)
        ev_v = evaluate(art, valid)
        print("  train : rmse %.3f vs market %.3f  (%+.3f)"
              % (ev_t["rmse"], ev_t["market_rmse"], ev_t["improvement"]))
        print("  valid : rmse %.3f vs market %.3f  (%+.3f)"
              % (ev_v["rmse"], ev_v["market_rmse"], ev_v["improvement"]))
        if ev_v["improvement"] <= 0:
            print("  -> NO IMPROVEMENT on held-out data. Registered as a shadow")
            print("     challenger anyway, because a negative result recorded is")
            print("     worth more than one quietly dropped.")
        top = sorted(art["coefficients"].items(), key=lambda kv: -abs(kv[1]))[:5]
        print("  largest standardized coefficients: %s"
              % ", ".join("%s %+.3f" % (k, v) for k, v in top))
        out[name] = {"artifact": art, "train": ev_t, "valid": ev_v}

    if not args.apply:
        print("\nDRY RUN. Re-run with --apply to register these as shadow challengers.")
        return

    import forecast_v2

    # C1 FIRST, AND ALWAYS. The market baseline has nothing to fit and is
    # registered unconditionally, because every challenger's improvement is
    # reported against it as well as against the Champion — "better than the
    # Champion" and "better than the line" are different claims and only the
    # second means anything to anyone else. Registering it on the same run means
    # it forecasts the same games from the same payload, so the comparison is
    # paired rather than a comparison of two game sets.
    from models_v2 import MarketBaseline
    _mb = MarketBaseline()
    forecast_v2.register_model(
        conn, model_version="C1-market-baseline",
        model_id=_mb.model_id, role=forecast_v2.ROLE_BASELINE,
        config=_mb.artifact(), experiment_id="E000",
        notes="the market consensus at each forecast's own decision time; the "
              "number every other model has to beat")
    print("  registered C1-market-baseline (%s)" % _mb.model_id)

    for name, res in out.items():
        art = res["artifact"]
        art["artifact_hash"] = provenance.payload_hash(
            {k: v for k, v in art.items() if k != "lambda_scored"})
        version = "%s-%s.%s" % (art["experiment_id"],
                                dt.datetime.now(dt.timezone.utc).strftime("%Y.%m.%d"),
                                art["artifact_hash"][:8])
        forecast_v2.register_model(
            conn, model_version=version, model_id=art["model_id"],
            role=forecast_v2.ROLE_CHALLENGER, config=art,
            feature_schema_version=art["feature_schema"],
            experiment_id=art["experiment_id"],
            notes="fitted on %s %d development data; lambda chosen on a held-out "
                  "late-season split; SHADOW ONLY — never promoted automatically"
                  % (args.sport, args.season))
        print("  registered %s (%s)" % (version, art["model_id"]))
        path = os.path.join(ROOT, "output", "models", version + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(art, fh, indent=2, sort_keys=True, default=str)
    print("\n  artifacts written to output/models/")


if __name__ == "__main__":
    main()
