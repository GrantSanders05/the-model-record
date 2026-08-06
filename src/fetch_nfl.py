"""
fetch_nfl.py — pull NFL data from nflverse into SQLite.

nflverse publishes a single CSV covering every NFL game since 1999 with closing
spreads, totals, moneylines, rest days and starting QBs. No API key, no rate
limit, no cost.

This is the project's VALIDATION set. It is deep (7,000+ games with lines),
completely free to re-pull, and independent of Grant's model, which makes it
the right place to prove the backtester is honest before trusting any number
it reports about college football.

nflverse `spread_line` is already positive-when-home-favored, matching the
house convention, so it passes through unchanged. `verify_conventions()`
re-proves that rather than assuming it.

Usage:
    python3 src/fetch_nfl.py
"""

import csv
import io
import os
import urllib.request

import db

SOURCE = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_SNAPSHOT = os.path.join(ROOT, "analysis", "nflverse_games.csv")


def _num(v, cast=float):
    if v is None:
        return None
    v = v.strip()
    if v in ("", "NA", "NaN"):
        return None
    try:
        return cast(v)
    except ValueError:
        return None


def load_rows(use_local=False):
    if use_local and os.path.exists(LOCAL_SNAPSHOT):
        with open(LOCAL_SNAPSHOT) as fh:
            return list(csv.DictReader(fh))
    with urllib.request.urlopen(SOURCE, timeout=90) as resp:
        text = resp.read().decode("utf-8")
    # Keep a local snapshot so a network outage never blocks a backtest.
    os.makedirs(os.path.dirname(LOCAL_SNAPSHOT), exist_ok=True)
    with open(LOCAL_SNAPSHOT, "w") as fh:
        fh.write(text)
    return list(csv.DictReader(io.StringIO(text)))


def to_games(rows):
    out = []
    for r in rows:
        gid = r.get("game_id") or "%s_%s_%s_%s" % (
            r["season"], r["week"], r["away_team"], r["home_team"])
        out.append({
            "game_id": "nfl-%s" % gid,
            "sport": "nfl",
            "season": _num(r["season"], int),
            "week": _num(r["week"], int),
            "season_type": r.get("game_type") or "REG",
            "kickoff": (r.get("gameday") or "") + (
                "T" + r["gametime"] if r.get("gametime") not in (None, "", "NA") else ""),
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "home_score": _num(r["home_score"], int),
            "away_score": _num(r["away_score"], int),
            "neutral_site": 1 if (r.get("location") or "").lower() == "neutral" else 0,
            "home_conf": None,
            "away_conf": None,
            "home_div": "nfl",
            "away_div": "nfl",
        })
    return out


def to_lines(rows):
    """
    nflverse `spread_line` is already POSITIVE when the home team is favored,
    which is the house convention -- pass through with no sign change.
    """
    out = []
    for r in rows:
        spread = _num(r.get("spread_line"))
        total = _num(r.get("total_line"))
        if spread is None and total is None:
            continue
        gid = r.get("game_id") or "%s_%s_%s_%s" % (
            r["season"], r["week"], r["away_team"], r["home_team"])
        out.append({
            "game_id": "nfl-%s" % gid,
            "provider": "nflverse_close",
            "home_margin": spread,
            "home_margin_open": None,
            "total": total,
            "total_open": None,
            "home_ml": _num(r.get("home_moneyline"), int),
            "away_ml": _num(r.get("away_moneyline"), int),
        })
    return out


def main():
    conn = db.connect()
    print("Downloading nflverse games.csv ...")
    rows = load_rows()
    print("  %d rows" % len(rows))

    games = to_games(rows)
    db.upsert_games(conn, games)
    print("  %d games upserted" % len(games))

    known = {r["game_id"] for r in conn.execute(
        "SELECT game_id FROM games WHERE sport='nfl'")}
    lines = [l for l in to_lines(rows) if l["game_id"] in known]
    db.upsert_lines(conn, lines)
    print("  %d lines upserted" % len(lines))

    print("\nDatabase now holds:")
    print(db.summary(conn))

    slope, r, n = db.verify_conventions(conn, "nfl")
    print("\nConvention check (actual margin ~ market margin), n=%d:" % n)
    print("  slope %.3f  r %.3f   -> %s"
          % (slope, r, "OK" if 0.7 < slope < 1.3 else "SUSPICIOUS - investigate"))


if __name__ == "__main__":
    main()
