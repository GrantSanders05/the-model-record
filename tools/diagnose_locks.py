"""
diagnose_locks.py — what ARE the games `missed_locks` is counting?

THE QUESTION THIS ANSWERS
Production reports `health.missed_locks = 45` while only 8 games have kicked off
and all 8 are locked and graded. That number is either a five-alarm fire or a
false alarm, and the two look identical from outside:

  A. REAL. 45 games kicked off with no pick, so the published 5-3 record covers
     8 of 53 gradeable games and is a survivorship number, not a record.
  B. FALSE ALARM. The query counts any game with a line, including FCS and
     Division II fixtures the model has no rating for and never intended to
     pick. Then the metric reads 45 forever and everyone stops looking at it --
     which is worse than not having it.
  C. FALLOUT. The August mass-lock was voided on 2026-09-01; games whose picks
     were removed and which kicked off before the 3-day re-lock window could
     reach them would show up here, correctly but explicably.

It cannot be settled locally: this laptop's database is a stale copy with 53
lined games where production has 169. So it runs where the real database lives.
Read-only, prints a classification, and never fails the job it rides in.
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import db          # noqa: E402
import ledger      # noqa: E402


def classify(conn, sport, season):
    now = ledger._now()
    rows = conn.execute(
        """SELECT g.game_id, g.week, g.kickoff, g.home_team, g.away_team,
                  g.home_div, g.away_div, g.home_score, g.away_score
             FROM games g
             JOIN lines l ON l.game_id = g.game_id
        LEFT JOIN picks_log p ON p.game_id = g.game_id
            WHERE g.sport = ? AND p.game_id IS NULL
              AND l.home_margin IS NOT NULL
              AND g.kickoff IS NOT NULL AND g.kickoff <= ?
              AND g.season = ?
         ORDER BY g.kickoff""",
        (sport, now.isoformat(), season)).fetchall()

    rated = {r["team"] for r in conn.execute(
        "SELECT DISTINCT team FROM grades WHERE sport=? AND season=?", (sport, season))}
    voided = {str(r["game_id"]) for r in conn.execute(
        "SELECT game_id FROM picks_voided")}

    buckets = {"unrated": [], "voided": [], "genuinely missed": []}
    for r in rows:
        d = dict(r)
        if d["home_team"] not in rated and d["away_team"] not in rated:
            buckets["unrated"].append(d)
        elif str(d["game_id"]) in voided:
            buckets["voided"].append(d)
        else:
            buckets["genuinely missed"].append(d)
    return rows, buckets, rated, voided


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int, required=True)
    args = ap.parse_args()

    try:
        conn = db.connect()
        rows, buckets, rated, voided = classify(conn, args.sport, args.season)
    except Exception as e:                      # a diagnostic must not break a publish
        print("::warning::diagnose_locks failed: %s" % e)
        return 0

    total = len(rows)
    print("── missed_locks, classified (%s %d) ──" % (args.sport, args.season))
    print("   total counted by the metric : %d" % total)
    print("   teams with a grade this season: %d" % len(rated))
    print("   games with a voided pick      : %d" % len(voided))
    if not total:
        print("   nothing to explain — the metric reads zero.")
        return 0

    for name, games in buckets.items():
        print("\n   %-18s %3d  %s" % (name, len(games), {
            "unrated": "neither team is rated -> the model never intended a pick;"
                       " the METRIC is wrong, not the pipeline",
            "voided": "had a pick, it was voided, kickoff passed before re-lock"
                      " -> explained, one-off from the 2026-09-01 void",
            "genuinely missed": "rated, never picked, never voided -> A REAL GAP"
                                " in the lock schedule",
        }[name]))
        for d in games[:6]:
            fin = "final" if d["home_score"] is not None else "no score"
            print("       wk%-3s %s  %-26s @ %-26s [%s/%s] %s"
                  % (d["week"], str(d["kickoff"])[:10], d["away_team"], d["home_team"],
                     d["away_div"], d["home_div"], fin))
        if len(games) > 6:
            print("       ... and %d more" % (len(games) - 6))

    real = len(buckets["genuinely missed"])
    print("\n   VERDICT: %s" % (
        "%d genuinely missed. The record is incomplete and the lock schedule has "
        "a hole." % real if real else
        "none genuinely missed. Every counted game is explained by the metric "
        "counting games the model never picks, or by the voided mass-lock."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
