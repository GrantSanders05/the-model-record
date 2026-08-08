"""
fetch_teamcrafters.py — pull player-level roster ratings from TeamCrafters.

TeamCrafters hosts the community roster files for EA's College Football games.
Each roster release carries every team's full 85-man roster with per-player
overalls, positions, class year and dev traits. That is a genuinely useful
ratings input: it reflects transfers, recruiting and departures for the coming
season, which last year's results cannot.

WHAT THIS IS AND IS NOT
These are VIDEO GAME ratings. They are not film grades and they are not an
objective measurement of football ability. They are, however, a large,
consistently-produced, roster-aware talent estimate -- and unlike a placeholder
carried forward from last season, they know who actually left. Whether they
carry real signal is not assumed anywhere in this codebase: `bakeoff_ratings.py`
backtests them against a real season before they are used for anything.

HOW THE DATA IS OBTAINED
The pages are Next.js app-router routes. The roster JSON is embedded in the
server-rendered flight payload (`self.__next_f.push`), so no API key, no
browser and no JavaScript engine is needed -- just fetch the HTML and pull the
payload out. Everything is cached to disk on first fetch, because re-scraping
138 pages to answer the same question twice is rude and slow.

    python3 src/fetch_teamcrafters.py --game CFB27 --roster launch-6-30-26
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://www.teamcrafters.net"

# The site 403s the default urllib agent. A normal browser UA is what the site
# serves to any visitor; nothing here is behind a login or a paywall.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def cache_dir(game, roster):
    d = os.path.join(ROOT, "data", "cache", "teamcrafters", game, roster)
    os.makedirs(d, exist_ok=True)
    return d


def _get(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def _flight_payloads(html):
    """Every `self.__next_f.push([1, "..."])` string, unescaped."""
    out = []
    for raw in re.findall(r"self\.__next_f\.push\((.*?)\)</script>", html, re.S):
        try:
            part = json.loads(raw)
        except ValueError:
            continue
        if isinstance(part, list) and len(part) > 1 and isinstance(part[1], str):
            out.append(part[1])
    return out


def _extract_array(payload, key):
    """
    Pull the JSON array that follows `"key":` out of a flight payload.

    The payload is one enormous string of concatenated React output, so it
    cannot be parsed as a whole. Scanning for balanced brackets from the key is
    both sufficient and robust to whatever surrounds it.
    """
    i = payload.find('"%s":[' % key)
    if i < 0:
        return None
    start = payload.index("[", i)
    depth, in_str, esc = 0, False, False
    for j in range(start, len(payload)):
        ch = payload[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(payload[start:j + 1])
                except ValueError:
                    return None
    return None


def _find(html, key):
    for payload in sorted(_flight_payloads(html), key=len, reverse=True):
        got = _extract_array(payload, key)
        if got:
            return got
    return None


def teams(game, roster, refresh=False):
    """The roster index: every team with its id, conference and team-level OVRs."""
    path = os.path.join(cache_dir(game, roster), "_teams.json")
    if os.path.exists(path) and not refresh:
        return json.load(open(path))
    html = _get("%s/rosters/%s/%s" % (BASE, game, roster))
    got = _find(html, "teams")
    if not got:
        raise RuntimeError("no team list found at /rosters/%s/%s" % (game, roster))
    json.dump(got, open(path, "w"))
    return got


def roster(game, roster_slug, team_id, refresh=False, delay=0.4):
    """One team's player list. Cached per team id."""
    path = os.path.join(cache_dir(game, roster_slug), "%s.json" % team_id)
    if os.path.exists(path) and not refresh:
        return json.load(open(path))
    try:
        html = _get("%s/rosters/%s/%s/%s" % (BASE, game, roster_slug, team_id))
        players = _find(html, "players") or []
    except urllib.error.HTTPError as e:
        # One dead page must not abandon the other 137. The team is reported as
        # missing by fetch_all and the caller decides whether that is tolerable;
        # silently returning [] here would look like an empty roster instead.
        print("  HTTP %s on team %s — skipping" % (e.code, team_id))
        return []
    json.dump(players, open(path, "w"))
    time.sleep(delay)
    return players


def fetch_all(game, roster_slug, refresh=False, delay=0.4):
    """Index + every roster. Returns {team_name: {"meta": ..., "players": [...]}}."""
    idx = teams(game, roster_slug, refresh=refresh)
    out, missing = {}, []
    for n, t in enumerate(idx, start=1):
        players = roster(game, roster_slug, t["id"], refresh=refresh, delay=delay)
        if not players:
            missing.append(t["name"])
        out[t["name"]] = {"meta": t, "players": players}
        if n % 25 == 0 or n == len(idx):
            print("  %3d/%d teams" % (n, len(idx)), flush=True)
    if missing:
        print("  WARNING: no players parsed for %d team(s): %s"
              % (len(missing), ", ".join(missing[:8])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="CFB27")
    ap.add_argument("--roster", default="launch-6-30-26")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    print("Fetching %s / %s" % (args.game, args.roster))
    data = fetch_all(args.game, args.roster, refresh=args.refresh, delay=args.delay)

    n_players = sum(len(v["players"]) for v in data.values())
    filler = sum(1 for v in data.values() for p in v["players"] if p.get("isFiller"))
    print("\n%d teams | %d players | %d filler (%.0f%%)"
          % (len(data), n_players, filler, 100.0 * filler / max(1, n_players)))
    print("cached in %s" % cache_dir(args.game, args.roster))


if __name__ == "__main__":
    main()
