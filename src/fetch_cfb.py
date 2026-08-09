"""
fetch_cfb.py — pull College Football data from the CollegeFootballData API into SQLite.

Budget discipline
-----------------
The CFBD key is capped at 1,000 requests per calendar month. Two guards:

  1. DISK CACHE. Every raw response is written to data/cache/. A cached call
     costs zero requests. Backtesting and optimization therefore never touch
     the network, no matter how many thousands of iterations they run.
  2. BUDGET LEDGER. data/api_budget.json tracks spend per calendar month and
     the fetcher refuses to exceed MONTHLY_CAP.

Cost is 3 requests per season (games, lines, rankings) plus 1 if postseason is
pulled separately — so a full 15-season history costs ~60 requests, once.

Usage:
    python3 src/fetch_cfb.py --seasons 2015-2025
    python3 src/fetch_cfb.py --seasons 2025 --refresh    # bypass cache for live weeks
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "data", "cache")
BUDGET_FILE = os.path.join(ROOT, "data", "api_budget.json")
ENV_FILE = "/Users/haileyclark/Downloads/S2-Media/_s2-media/config/cfbd.env"

BASE = "https://api.collegefootballdata.com"
MONTHLY_CAP = 900          # leave 100 in reserve for live in-season updates
PROVIDER_PRIORITY = ["consensus", "DraftKings", "Bovada", "ESPN Bet",
                     "Caesars Sportsbook (Colorado)", "William Hill (New Jersey)"]


# ── credentials ────────────────────────────────────────────────────────────────

def load_key():
    key = os.environ.get("CFBD_API_KEY")
    if key:
        return key
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("CFBD_API_KEY="):
                    return line.split("=", 1)[1].strip()
    sys.exit("No CFBD_API_KEY found (env var or %s)" % ENV_FILE)


# ── budget ledger ──────────────────────────────────────────────────────────────

def _month_key():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _read_budget():
    if os.path.exists(BUDGET_FILE):
        with open(BUDGET_FILE) as fh:
            return json.load(fh)
    return {}


def _spend(n=1):
    b = _read_budget()
    mk = _month_key()
    used = b.get(mk, 0)
    if used + n > MONTHLY_CAP:
        sys.exit(
            "CFBD monthly budget exhausted: %d/%d used for %s.\n"
            "Cached data still works — only new pulls are blocked."
            % (used, MONTHLY_CAP, mk)
        )
    b[mk] = used + n
    os.makedirs(os.path.dirname(BUDGET_FILE), exist_ok=True)
    with open(BUDGET_FILE, "w") as fh:
        json.dump(b, fh, indent=2)
    return b[mk]


def budget_status():
    return _read_budget().get(_month_key(), 0), MONTHLY_CAP


# ── HTTP with cache ────────────────────────────────────────────────────────────

def api_get(path, params, key, refresh=False):
    """GET an endpoint, serving from disk cache unless refresh=True."""
    qs = "&".join("%s=%s" % (k, v) for k, v in sorted(params.items()))
    slug = ("%s_%s" % (path.strip("/").replace("/", "-"), qs)).replace("&", "_").replace("=", "-")
    cache_path = os.path.join(CACHE_DIR, slug + ".json")

    if not refresh and os.path.exists(cache_path):
        with open(cache_path) as fh:
            return json.load(fh), True

    url = "%s%s?%s" % (BASE, path, qs)
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
    })
    used = _spend(1)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    else:
        raise RuntimeError("failed to fetch %s" % url)

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path, "w") as fh:
        json.dump(data, fh)
    print("    [api %d/%d] %s -> %d records" % (used, MONTHLY_CAP, url, len(data)))
    return data, False


# ── transforms ─────────────────────────────────────────────────────────────────

def games_rows(raw, season, season_type):
    rows = []
    for g in raw:
        hs, as_ = g.get("homePoints"), g.get("awayPoints")
        rows.append({
            "game_id": "cfb-%s" % g["id"],
            "sport": "cfb",
            "season": season,
            "week": g.get("week"),
            "season_type": season_type,
            "kickoff": g.get("startDate"),
            "home_team": g.get("homeTeam"),
            "away_team": g.get("awayTeam"),
            "home_score": hs,
            "away_score": as_,
            "neutral_site": 1 if g.get("neutralSite") else 0,
            "home_conf": g.get("homeConference"),
            "away_conf": g.get("awayConference"),
            "home_div": g.get("homeClassification"),
            "away_div": g.get("awayClassification"),
        })
    return rows


def _pick_line(lines):
    """Choose the most trustworthy provider available for a game."""
    by_provider = {l.get("provider"): l for l in (lines or [])}
    for p in PROVIDER_PRIORITY:
        if p in by_provider:
            return p, by_provider[p]
    if by_provider:
        p = next(iter(by_provider))
        return p, by_provider[p]
    return None, None


def lines_rows(raw):
    """
    Normalize CFBD spreads to the house convention.

    CFBD `spread` is negative when the HOME team is favored
    ("Auburn -2" with Auburn at home  ->  spread = -2).
    House convention is home_margin POSITIVE when home is favored.
    Therefore: home_margin = -spread.
    """
    rows = []
    for g in raw:
        provider, line = _pick_line(g.get("lines"))
        if line is None:
            continue
        spread = line.get("spread")
        spread_open = line.get("spreadOpen")
        rows.append({
            "game_id": "cfb-%s" % g["id"],
            "provider": provider,
            "home_margin": -float(spread) if spread is not None else None,
            "home_margin_open": -float(spread_open) if spread_open is not None else None,
            "total": line.get("overUnder"),
            "total_open": line.get("overUnderOpen"),
            "home_ml": line.get("homeMoneyline"),
            "away_ml": line.get("awayMoneyline"),
        })
    return rows


def rankings_rows(raw, season):
    rows = []
    for wk in raw:
        week = wk.get("week")
        for poll in wk.get("polls", []):
            pname = poll.get("poll")
            for r in poll.get("ranks", []):
                rows.append({
                    "sport": "cfb",
                    "season": season,
                    "week": week,
                    "poll": pname,
                    "team": r.get("school"),
                    "rank": r.get("rank"),
                })
    return rows


# ── driver ─────────────────────────────────────────────────────────────────────

def fetch_season(conn, season, key, refresh=False, postseason=True):
    print("  season %d" % season)
    cached_all = True

    for st in (["regular", "postseason"] if postseason else ["regular"]):
        raw, hit = api_get("/games", {"year": season, "seasonType": st}, key, refresh)
        cached_all &= hit
        rows = games_rows(raw, season, st)
        if rows:
            db.upsert_games(conn, rows)

    raw, hit = api_get("/lines", {"year": season}, key, refresh)
    cached_all &= hit
    lrows = lines_rows(raw)
    # Only keep lines whose game we actually have, or the FK will reject them.
    known = {r["game_id"] for r in conn.execute(
        "SELECT game_id FROM games WHERE sport='cfb' AND season=?", (season,))}
    lrows = [r for r in lrows if r["game_id"] in known]
    if lrows:
        db.upsert_lines(conn, lrows)
        # Capture the movement history too -- upsert_lines overwrites, this appends.
        db.snapshot_lines(conn, lrows)

    raw, hit = api_get("/rankings", {"year": season}, key, refresh)
    cached_all &= hit
    rrows = rankings_rows(raw, season)
    if rrows:
        db.upsert_rankings(conn, rrows)

    # PPA. This call existed and was never wired to anything, so team_game_stats was
    # empty everywhere except the one laptop that had run it by hand -- which is why
    # the research app's efficiency chart was blank in production and nowhere else,
    # and why nothing looked broken locally. One request per season, cached like the
    # rest. Allowed to fail: efficiency is a supporting view, and losing it must not
    # cost the games and lines the picks are actually made from.
    try:
        n_ppa, hit = fetch_ppa(conn, season, key, refresh)
        cached_all &= hit
    except Exception as e:                          # noqa: BLE001 - report anything
        n_ppa = 0
        print("     PPA unavailable for %d (%s: %s)" % (season, type(e).__name__, e))

    print("     %s  games+lines+rankings+ppa loaded (%d lines, %d ppa)"
          % ("(cached)" if cached_all else "(fetched)", len(lrows), n_ppa))


def parse_seasons(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2015-2025")
    ap.add_argument("--refresh", action="store_true",
                    help="bypass disk cache (spends API budget)")
    ap.add_argument("--no-postseason", action="store_true")
    args = ap.parse_args()

    key = load_key()
    conn = db.connect()
    used, cap = budget_status()
    print("CFBD budget this month: %d/%d" % (used, cap))

    for s in parse_seasons(args.seasons):
        fetch_season(conn, s, key, args.refresh, not args.no_postseason)

    print("\nDatabase now holds:")
    print(db.summary(conn))
    slope, r, n = db.verify_conventions(conn, "cfb")
    if slope is not None:
        print("\nConvention check (actual margin ~ market margin), n=%d:" % n)
        print("  slope %.3f  r %.3f   -> %s"
              % (slope, r, "OK" if 0.7 < slope < 1.3 else "SUSPICIOUS - investigate"))


# ── advanced efficiency (PPA) ──────────────────────────────────────────────────
#
# This section used to sit BELOW the `if __name__ == "__main__"` guard, which is why
# fetch_ppa could never be called from a script run: python executes top to bottom,
# main() ran at the guard, and these defs had not been evaluated yet. Calling it
# raised NameError at runtime -- from inside a try/except that turned it into one
# quiet "PPA unavailable" line per season. Module-level code after the guard runs
# only on import, so this was invisible to every `import fetch_cfb` too.
# Keep the guard last.

def ppa_rows(raw, season):
    """
    CFBD PPA per team per game. One request covers a whole season.

    PPA is predicted points added — the college equivalent of EPA, and the
    closest thing in free data to what Grant's position grades estimate by eye.
    Passing/rushing splits on both sides of the ball map loosely onto his
    QB/WR/OL and RB/OL groups (offense) and DL/LB/DB groups (defense).
    """
    out = []
    for r in raw:
        o = r.get("offense") or {}
        d = r.get("defense") or {}
        out.append({
            "game_id": "cfb-%s" % r.get("gameId"),
            "sport": "cfb",
            "season": season,
            "week": r.get("week"),
            "team": r.get("team"),
            "opponent": r.get("opponent"),
            "off_ppa": o.get("overall"), "off_pass_ppa": o.get("passing"),
            "off_rush_ppa": o.get("rushing"),
            "def_ppa": d.get("overall"), "def_pass_ppa": d.get("passing"),
            "def_rush_ppa": d.get("rushing"),
        })
    return out


def fetch_ppa(conn, season, key, refresh=False):
    raw, hit = api_get("/ppa/games", {"year": season}, key, refresh)
    rows = ppa_rows(raw, season)
    if rows:
        conn.executemany(
            """INSERT INTO team_game_stats
               (game_id, sport, season, week, team, opponent,
                off_ppa, off_pass_ppa, off_rush_ppa, def_ppa, def_pass_ppa, def_rush_ppa)
               VALUES (:game_id,:sport,:season,:week,:team,:opponent,
                       :off_ppa,:off_pass_ppa,:off_rush_ppa,:def_ppa,:def_pass_ppa,:def_rush_ppa)
               ON CONFLICT(game_id, team) DO UPDATE SET
                 off_ppa=excluded.off_ppa, def_ppa=excluded.def_ppa,
                 off_pass_ppa=excluded.off_pass_ppa, off_rush_ppa=excluded.off_rush_ppa,
                 def_pass_ppa=excluded.def_pass_ppa, def_rush_ppa=excluded.def_rush_ppa""", rows)
        conn.commit()
    return len(rows), hit


if __name__ == "__main__":
    main()
