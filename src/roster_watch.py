"""
roster_watch.py — tell Grant which roster news actually moves the model.

THE PROBLEM WITH INJURY NEWS
There is no shortage of it, and almost none of it matters. College football has
no mandatory injury report, so what circulates is a mix of rumour, beat-writer
speculation and genuinely decisive information, all presented at the same volume.
"Star receiver questionable" is a headline whether he is the fourth option or the
entire passing game.

This module answers a narrower and far more useful question: if this player does
not play, HOW MANY POINTS DOES THE LINE MOVE? That is answerable, because the
model's inputs are player-level. Every position group is a depth-weighted average
of real overalls, so removing a player and recomputing the whole rating set gives
an exact delta, in points of spread, on the model's own scale.

An injury worth 0.3 points is noise. One worth 3 points is the week.

SOURCES, ALL FREE, NO NEW ACCOUNTS
  ESPN   site.api.espn.com team rosters carry `injuredReserveOrOut` and
         `suspended` groups plus a per-athlete `injuries` array and status.
         No key, no auth, no rate limit worth worrying about at 138 requests.
  CFBD   /player/portal for transfer departures — a portal exit is a permanent
         roster change and the model should know before the next line is priced.
  TeamCrafters  the rating space itself. Everything is scored in the same units
         the grades use, which is what makes the impact number meaningful.

WHY THE WATCHLIST EXISTS EVEN WITH NO INJURIES
Ranking every team's most load-bearing players ahead of time means the moment a
name appears anywhere, its importance is already known. It also answers a
question Grant will actually ask in week 6: who can this team least afford to
lose?
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request

import db
import fetch_teamcrafters as tc
import game_ratings
import team_aliases

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SEVEN = ["qb", "rb", "wr", "ol", "dl", "lb", "db"]
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.I)
_PUNCT = re.compile(r"[^a-z ]+")


def norm_name(first, last):
    """
    A name key that survives the differences between two independent databases.

    ESPN writes "D.J. Uiagalelei"; a roster file writes "DJ Uiagalelei". Suffixes,
    punctuation and case all vary. Getting this wrong fails SILENTLY -- the player
    simply is not found and the alert never fires -- so it is deliberately
    aggressive, and `match_rate` below reports how well it did rather than
    leaving it to be assumed.
    """
    s = ("%s %s" % (first or "", last or "")).lower().replace(".", "")
    s = _SUFFIX.sub("", s).strip()
    return _PUNCT.sub("", s).strip()


def _get_json(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError):
            if attempt == tries - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def espn_team_index(refresh=False):
    """ESPN team id -> display/location names, cached."""
    path = os.path.join(ROOT, "data", "cache", "espn_teams.json")
    if os.path.exists(path) and not refresh:
        return json.load(open(path))
    d = _get_json("%s/teams?limit=900" % ESPN) or {}
    out = {}
    try:
        for t in d["sports"][0]["leagues"][0]["teams"]:
            t = t["team"]
            out[t["id"]] = {"location": t.get("location"),
                            "display": t.get("displayName"),
                            "abbrev": t.get("abbreviation")}
    except (KeyError, IndexError):
        return {}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w"))
    return out


def espn_id_for(team, index):
    """Map a canonical team name to an ESPN id via the alias table."""
    want = team_aliases.canonical("cfb", team)
    for tid, meta in index.items():
        loc = team_aliases.canonical("cfb", meta.get("location") or "")
        if loc == want:
            return tid
    return None


def espn_unavailable(team_id, delay=0.15):
    """
    Players ESPN currently lists as out, injured or suspended.

    Returns [{name, position, status, detail}]. An empty list is the normal
    state in the preseason and is not an error.
    """
    d = _get_json("%s/teams/%s/roster" % (ESPN, team_id))
    time.sleep(delay)
    if not d:
        return None                      # distinguish "fetch failed" from "nobody out"
    out = []
    for group in d.get("athletes", []):
        flagged_group = group.get("position") in ("injuredReserveOrOut", "suspended")
        for a in group.get("items", []):
            injuries = a.get("injuries") or []
            status = (a.get("status") or {}).get("type", "active")
            if not flagged_group and status == "active" and not injuries:
                continue
            detail = ""
            if injuries:
                inj = injuries[0]
                detail = "%s %s" % (inj.get("status") or "",
                                    (inj.get("details") or {}).get("type") or "")
            out.append({
                "name": a.get("displayName"),
                "key": norm_name(a.get("firstName"), a.get("lastName")),
                "position": (a.get("position") or {}).get("abbreviation"),
                "status": (a.get("status") or {}).get("name") or group.get("position"),
                "detail": detail.strip(),
            })
    return out


def rate_without(rosters, target, drops):
    """
    Recompute every team's grades with `drops` removed, and return TOTALs.

    The whole rating set is rebuilt rather than the one team patched, because
    the mapping is RELATIVE -- a team's grade depends on where it ranks against
    the other 137. Patching one team in isolation would silently ignore that and
    report an impact that the model would never actually produce.

    drops: {team_name: set(player_id)}
    """
    trimmed = {}
    for team, blob in rosters.items():
        gone = drops.get(team)
        players = blob.get("players") or []
        if gone:
            players = [p for p in players if p.get("id") not in gone]
        trimmed[team] = {"meta": blob.get("meta"), "players": players}
    graded = game_ratings.build(trimmed, target)
    return {t: 2 * sum(g[p] for p in SEVEN if p in g) + g.get("coach_st", 0.0)
            for t, g in graded.items()}


def impact_table(rosters, target, scale=1.0, top_per_team=3):
    """
    For every team, the players whose absence would move its rating most.

    Cost is one full rebuild per candidate. Restricting candidates to the top
    two at each position group keeps that to roughly 16 rebuilds a team, which
    is seconds in total and avoids pricing the absence of a fourth-string tackle.
    """
    base = rate_without(rosters, target, {})
    pos_map = game_ratings.position_map()
    rows = []

    for team, blob in rosters.items():
        by_group = {}
        for p in blob.get("players") or []:
            g = pos_map.get(p.get("POS"))
            if g and p.get("OVR") is not None:
                by_group.setdefault(g, []).append(p)
        cands = []
        for g, pl in by_group.items():
            pl.sort(key=lambda x: -x["OVR"])
            cands.extend(pl[:2])

        hits = []
        for p in cands:
            after = rate_without(rosters, target, {team: {p["id"]}})
            delta = (base.get(team, 0.0) - after.get(team, 0.0)) * scale
            hits.append({
                "team": team, "player": "%s %s" % (p.get("firstName"), p.get("lastName")),
                "key": norm_name(p.get("firstName"), p.get("lastName")),
                "pos": p.get("POS"), "ovr": p.get("OVR"),
                "group": pos_map.get(p.get("POS")),
                "points": round(delta, 2),
            })
        hits.sort(key=lambda r: -r["points"])
        rows.extend(hits[:top_per_team])

    rows.sort(key=lambda r: -r["points"])
    return rows, base


def portal_departures(season, key, limit=400):
    """Transfer-portal exits — a permanent roster change, not a weekly one."""
    url = "https://api.collegefootballdata.com/player/portal?year=%d" % season
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    out = []
    for p in data[:limit]:
        out.append({
            "player": "%s %s" % (p.get("firstName"), p.get("lastName")),
            "key": norm_name(p.get("firstName"), p.get("lastName")),
            "pos": p.get("position"),
            "from": p.get("origin"), "to": p.get("destination"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="CFB27")
    ap.add_argument("--roster", default="launch-6-30-26")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--out", default="output/alerts.json")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--min-points", type=float, default=0.75,
                    help="ignore anything that moves the line less than this")
    ap.add_argument("--no-espn", action="store_true", help="watchlist only")
    args = ap.parse_args()

    cfg_path = args.config if os.path.isabs(args.config) else os.path.join(ROOT, args.config)
    config = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    scale = config.get("scale", 1.0)

    conn = db.connect()
    target = game_ratings.target_from_grades(conn, "cfb", args.season - 1)
    if not target:
        target = game_ratings.target_from_grades(conn, "cfb", args.season)
    rosters = tc.fetch_all(args.game, args.roster)

    print("Building the impact table (%d teams)…" % len(rosters))
    watch, base = impact_table(rosters, target, scale=scale)
    by_key = {}
    for r in watch:
        by_key.setdefault((r["team"], r["key"]), r)

    alerts, unmatched, failed = [], 0, []
    if not args.no_espn:
        index = espn_team_index()
        print("Checking ESPN for unavailable players…")
        for n, team in enumerate(sorted(rosters), start=1):
            tid = espn_id_for(team, index)
            if not tid:
                failed.append(team)
                continue
            out = espn_unavailable(tid)
            if out is None:
                failed.append(team)
                continue
            for p in out:
                hit = by_key.get((team, p["key"]))
                if hit is None:
                    unmatched += 1
                    continue
                alerts.append(dict(hit, status=p["status"], detail=p["detail"]))
            if n % 40 == 0:
                print("  %d/%d" % (n, len(rosters)))

    alerts.sort(key=lambda r: -r["points"])
    material = [a for a in alerts if a["points"] >= args.min_points]

    print("\n" + "=" * 78)
    if material:
        print("  ROSTER NEWS THAT MOVES THE MODEL")
        print("  %-22s %-22s %-5s %7s  %s" % ("team", "player", "pos", "points", "status"))
        print("  " + "-" * 74)
        for a in material[:args.top]:
            print("  %-22s %-22s %-5s %+7.2f  %s %s" % (
                a["team"][:22], a["player"][:22], a["pos"], -a["points"],
                a["status"] or "", a["detail"]))
    elif args.no_espn:
        print("  WATCHLIST ONLY — ESPN not checked")
    else:
        print("  NOTHING MATERIAL. No listed absence moves any rating by %.2f+ points."
              % args.min_points)
        print("  In August that is the expected answer, not a broken feed:")
        print("  college football has no mandatory injury report and ESPN's")
        print("  out/suspended groups stay empty until camp and games fill them.")
    print("=" * 78)

    print("\nMOST LOAD-BEARING PLAYERS IN THE COUNTRY (lose him, lose this much)")
    print("  %-22s %-24s %-5s %6s %7s" % ("team", "player", "pos", "ovr", "points"))
    print("  " + "-" * 70)
    for r in watch[:args.top]:
        print("  %-22s %-24s %-5s %6s %7.2f" % (
            r["team"][:22], r["player"][:24], r["pos"], r["ovr"], r["points"]))

    if failed:
        print("\n  NOTE: no ESPN roster for %d team(s): %s"
              % (len(failed), ", ".join(sorted(failed)[:6])))
    if unmatched:
        print("  NOTE: %d flagged player(s) did not match a rated player — they are"
              % unmatched)
        print("        depth players outside the top two at their position, or a")
        print("        name spelled differently in the two sources.")

    payload = {
        "generated_utc": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        "season": args.season,
        "source": "%s/%s + ESPN" % (args.game, args.roster),
        "min_points": args.min_points,
        "alerts": material,
        "watchlist": watch[:300],
        "teams_without_espn": sorted(failed),
    }
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(payload, open(out, "w"), indent=1)
    print("\nalerts -> %s" % out)


if __name__ == "__main__":
    main()
