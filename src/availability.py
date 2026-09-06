"""
availability.py — §22. Who is not playing, recorded as observations.

WHY THIS IS NOT A STATUS COLUMN. College football has no mandatory injury
report. What exists is a stream of claims of wildly varying reliability, each of
which is true at the moment it is made and may be superseded an hour later. A
single mutable `status` field answers "what do we believe now" and destroys the
only question worth asking: **what was said, by whom, and how long before
kickoff** — which is the training data for how much a Thursday "questionable" is
actually worth. §22.2 is explicit that Questionable at T72, Probable at T24 and
Out at T2 are three rows.

WHAT THIS DELIBERATELY DOES NOT DO. It does not adjust any model. §22.3 asks for
`P(absent | status, source, timing) * full_absence_impact`, and that first term
is a calibration nobody has yet — this table is what will eventually make it
estimable. Turning an uncalibrated "questionable" into a point adjustment is
exactly the invented number §22.3 ends by forbidding: *do not turn Questionable
into Out*.

So the layer runs shadow: it records, it exposes an as-of view to the feature
snapshot, and it measures whether the market moved after a status changed. No
strategy consumes it. Wiring `DEGRADED_AVAILABILITY` into the decision rule
changes what gets published and is therefore a STRATEGY VERSION change, made
deliberately, not a switch flipped inside a data module.
"""

import datetime as dt
import json
import os

import provenance

# ── §22.1 source hierarchy ───────────────────────────────────────────────────
#
# Tier is a property of the SOURCE, recorded on every row, because "the model
# moved off a rumour" and "the model moved off a conference report" must be
# distinguishable after the fact. Only 1-3 would ever be eligible to move a
# serious model; nothing is eligible today.
TIER_CONFERENCE_REPORT = 1
TIER_SCHOOL_ANNOUNCEMENT = 2
TIER_VERIFIED_FEED = 3
TIER_BEAT_REPORTER = 4
TIER_AGGREGATOR = 5
TIER_RUMOUR = 6

TIERS = {
    TIER_CONFERENCE_REPORT: "official conference availability report",
    TIER_SCHOOL_ANNOUNCEMENT: "official school announcement",
    TIER_VERIFIED_FEED: "verified roster/status feed",
    TIER_BEAT_REPORTER: "credible beat reporter",
    TIER_AGGREGATOR: "generic aggregator",
    TIER_RUMOUR: "rumour or social",
}
AUTO_ADJUST_ELIGIBLE = (TIER_CONFERENCE_REPORT, TIER_SCHOOL_ANNOUNCEMENT,
                        TIER_VERIFIED_FEED)

SOURCE_ESPN = "espn_roster"
SOURCE_TIER = {SOURCE_ESPN: TIER_VERIFIED_FEED}

# ── statuses ─────────────────────────────────────────────────────────────────
OUT = "OUT"
SUSPENDED = "SUSPENDED"
DOUBTFUL = "DOUBTFUL"
QUESTIONABLE = "QUESTIONABLE"
PROBABLE = "PROBABLE"
ACTIVE = "ACTIVE"
UNKNOWN = "UNKNOWN"

# Ordered by how much absence each implies. Used for ordering and reporting only
# — NOT as a probability. There is no calibrated P(absent | status) yet and this
# module refuses to pretend otherwise.
SEVERITY = {OUT: 5, SUSPENDED: 5, DOUBTFUL: 4, QUESTIONABLE: 3,
            PROBABLE: 2, ACTIVE: 1, UNKNOWN: 0}

_RAW = {
    "out": OUT, "injured reserve": OUT, "injuredreserveorout": OUT,
    "season-ending": OUT, "suspension": SUSPENDED, "suspended": SUSPENDED,
    "doubtful": DOUBTFUL, "questionable": QUESTIONABLE, "day-to-day": QUESTIONABLE,
    "day to day": QUESTIONABLE, "probable": PROBABLE, "active": ACTIVE,
}


def normalize_status(raw):
    """
    A source's own wording, mapped to one of the known statuses. -> str

    UNKNOWN rather than a guess. An unrecognised word is a source saying
    something this module has not seen before, and quietly filing it as OUT
    would put a fabricated absence into the record under a real player's name.
    """
    if raw is None:
        return UNKNOWN
    t = str(raw).strip().lower()
    if not t:
        return UNKNOWN
    if t in _RAW:
        return _RAW[t]
    for k, v in _RAW.items():
        if k in t:
            return v
    return UNKNOWN


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def latest_for(conn, sport, season, player_key, *, as_of=None):
    """The most recent observation of one player at or before `as_of`. -> row"""
    q = ("SELECT * FROM availability_events WHERE sport=? AND season=?"
         "  AND player_key=?")
    args = [sport, season, player_key]
    if as_of:
        q += " AND observed_at <= ?"
        args.append(as_of)
    q += " ORDER BY observed_at DESC, event_id DESC LIMIT 1"
    return conn.execute(q, args).fetchone()


def record(conn, *, sport, season, team, player, player_key, position, status_raw,
           detail=None, source=SOURCE_ESPN, source_tier=None, impact_points=None,
           observed_at=None, commit=True):
    """
    Append one observation, if it says something new. -> "appended" | "unchanged"

    Deduplicated on MEANING, not on time: an unchanged status observed hourly is
    the same claim restated, and 700 identical rows a week would bury the four
    that are transitions. What counts as new is the status or a material change
    in the modelled impact — never the wording of the detail string, which
    reshuffles constantly for the same fact.
    """
    status = normalize_status(status_raw)
    tier = source_tier if source_tier is not None else SOURCE_TIER.get(source,
                                                                       TIER_AGGREGATOR)
    observed_at = observed_at or _now()
    prev = latest_for(conn, sport, season, player_key)
    if prev is not None and prev["status"] == status:
        a, b = prev["impact_points"], impact_points
        same_impact = (a is None and b is None) or (
            a is not None and b is not None and abs(a - b) < 0.05)
        if same_impact and prev["source_tier"] == tier:
            return "unchanged"

    payload = {"sport": sport, "season": season, "team": team,
               "player_key": player_key, "status": status, "source": source,
               "source_tier": tier, "impact_points": impact_points,
               "observed_at": observed_at}
    ph = provenance.payload_hash(payload)
    conn.execute(
        "INSERT OR IGNORE INTO availability_events (event_id, sport, season, team,"
        " player, player_key, position, status, status_raw, detail, source,"
        " source_tier, impact_points, observed_at, payload_hash, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (provenance.stable_id("availability_event", ph), sport, season, team,
         player, player_key, position, status, status_raw, detail, source, tier,
         impact_points, observed_at, ph, _now()))
    if commit:
        conn.commit()
    return "appended"


def team_status_asof(conn, sport, season, team, as_of):
    """
    Every player's latest observation for one team at `as_of`. -> [row]

    Exactly-as-of by construction: it cannot see an observation made after the
    instant asked about, which is what makes it safe to put in a feature payload.
    """
    return conn.execute(
        """SELECT e.* FROM availability_events e
             JOIN (SELECT player_key, MAX(observed_at) m
                     FROM availability_events
                    WHERE sport=? AND season=? AND team=? AND observed_at <= ?
                    GROUP BY player_key) t
               ON t.player_key = e.player_key AND t.m = e.observed_at
            WHERE e.sport=? AND e.season=? AND e.team=?
            ORDER BY e.observed_at DESC, e.event_id DESC""",
        (sport, season, team, as_of, sport, season, team)).fetchall()


def team_summary(conn, sport, season, team, as_of):
    """
    What the feature snapshot carries. -> dict, or None when nothing was observed.

    None, not a zeroed summary. "Nobody is listed out" and "nobody has looked"
    produce identical-looking numbers and mean opposite things, and this module
    exists because that distinction is the whole point.
    """
    rows = team_status_asof(conn, sport, season, team, as_of)
    if not rows:
        return None
    by_status = {}
    impact = 0.0
    tiers = set()
    latest = None
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        tiers.add(r["source_tier"])
        if r["status"] in (OUT, SUSPENDED) and r["impact_points"]:
            impact += r["impact_points"]
        if latest is None or r["observed_at"] > latest:
            latest = r["observed_at"]
    return {
        "by_status": by_status,
        "listed": len(rows),
        "out_impact_points": round(impact, 3),
        "best_source_tier": min(tiers) if tiers else None,
        "observed_at": latest,
        "staleness_minutes": _minutes_between(latest, as_of),
        # Stated on every payload so no downstream reader has to remember it.
        "adjusts_model": False,
        "why": "no calibrated P(absent | status, source, timing) exists yet; "
               "§22.3 forbids turning Questionable into Out",
    }


def _minutes_between(a, b):
    def _p(s):
        if not s:
            return None
        s = str(s).replace("Z", "+00:00")
        try:
            d = dt.datetime.fromisoformat(s)
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    x, y = _p(a), _p(b)
    if x is None or y is None:
        return None
    return round((y - x).total_seconds() / 60.0, 1)


def transitions(conn, sport, season, *, player_key=None, team=None):
    """
    Each player's ordered status history. -> {player_key: [row, ...]}

    This is the shape §22.2 asks for and the reason the table is append-only.
    """
    q = ("SELECT * FROM availability_events WHERE sport=? AND season=?")
    args = [sport, season]
    if player_key:
        q += " AND player_key=?"
        args.append(player_key)
    if team:
        q += " AND team=?"
        args.append(team)
    q += " ORDER BY player_key, observed_at, event_id"
    out = {}
    for r in conn.execute(q, args):
        out.setdefault(r["player_key"], []).append(r)
    return out


def line_movement_after(conn, sport, season, *, window_hours=48, min_impact=0.75):
    """
    §22.4. Did the market move after a status changed? -> [dict]

    The primary shadow metric, and the only one this layer can honestly report
    today: it needs no calibration, only the append-only quote history the market
    layer already keeps. For each transition it finds the last home spread quoted
    before the observation and the first quoted after it, for the team's next
    game within the window.

    A move is not proof the status caused it. The market absorbs a hundred things
    at once, and a paired sample over a season is what would eventually make this
    an estimate rather than an anecdote — which is why the rows are returned and
    nothing is summed into a headline here.
    """
    out = []
    for pk, rows in transitions(conn, sport, season).items():
        for i in range(1, len(rows)):
            prev, cur = rows[i - 1], rows[i]
            if SEVERITY.get(cur["status"], 0) == SEVERITY.get(prev["status"], 0):
                continue
            if cur["impact_points"] is not None and cur["impact_points"] < min_impact:
                continue
            g = conn.execute(
                "SELECT game_id, kickoff FROM games"
                " WHERE sport=? AND season=? AND (home_team=? OR away_team=?)"
                "   AND kickoff >= ? ORDER BY kickoff LIMIT 1",
                (sport, season, cur["team"], cur["team"],
                 cur["observed_at"])).fetchone()
            if g is None:
                continue
            before = conn.execute(
                "SELECT home_spread, observed_at FROM market_quotes"
                " WHERE game_id=? AND observed_at <= ? AND home_spread IS NOT NULL"
                " ORDER BY observed_at DESC LIMIT 1",
                (g["game_id"], cur["observed_at"])).fetchone()
            after = conn.execute(
                "SELECT home_spread, observed_at FROM market_quotes"
                " WHERE game_id=? AND observed_at > ? AND home_spread IS NOT NULL"
                " ORDER BY observed_at LIMIT 1",
                (g["game_id"], cur["observed_at"])).fetchone()
            if before is None or after is None:
                continue
            out.append({
                "player_key": pk, "team": cur["team"], "game_id": g["game_id"],
                "from_status": prev["status"], "to_status": cur["status"],
                "observed_at": cur["observed_at"],
                "hours_to_kickoff": (
                    None if not g["kickoff"]
                    else round((_minutes_between(cur["observed_at"], g["kickoff"]) or 0)
                               / 60.0, 2)),
                "modelled_impact": cur["impact_points"],
                "spread_before": before["home_spread"],
                "spread_after": after["home_spread"],
                "move": round(after["home_spread"] - before["home_spread"], 3),
                "source_tier": cur["source_tier"],
            })
    return out


def sync_from_alerts(conn, path=None, *, sport="cfb", season=None, commit=True):
    """
    Append everything roster_watch's last run listed. -> dict counts

    roster_watch already fetches ESPN and prices each absence in points of
    spread; this turns that report — which is overwritten on every run and keeps
    no history — into the observation stream §22.2 asks for.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = path or os.path.join(root, "output", "alerts.json")
    if not os.path.exists(path):
        return {"state": "no alerts file", "path": path, "appended": 0}
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except ValueError as e:
        return {"state": "unreadable: %s" % e, "appended": 0}

    season = season or payload.get("season")
    observed_at = payload.get("generated_utc") or _now()
    counts = {"appended": 0, "unchanged": 0, "skipped_unknown": 0}
    for a in payload.get("alerts") or []:
        status = normalize_status(a.get("status") or a.get("detail"))
        if status == UNKNOWN:
            # Recorded as UNKNOWN rather than dropped: a source saying something
            # unrecognised is itself information, and dropping it would make the
            # feed look quieter than it is.
            counts["skipped_unknown"] += 1
        r = record(conn, sport=sport, season=season, team=a.get("team"),
                   player=a.get("player"), player_key=a.get("key") or a.get("player"),
                   position=a.get("pos"), status_raw=a.get("status") or a.get("detail"),
                   detail=a.get("detail"), source=SOURCE_ESPN,
                   impact_points=a.get("points"), observed_at=observed_at,
                   commit=False)
        counts[r] = counts.get(r, 0) + 1
    if commit:
        conn.commit()
    counts["state"] = "read %s" % os.path.relpath(path, root)
    counts["observed_at"] = observed_at
    return counts


def main():
    import argparse
    import db
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--season", type=int)
    ap.add_argument("--sync", action="store_true",
                    help="append roster_watch's latest report as observations")
    ap.add_argument("--movement", action="store_true",
                    help="§22.4 shadow evaluation: the market after a status change")
    args = ap.parse_args()
    conn = db.connect()

    if args.sync or not args.movement:
        r = sync_from_alerts(conn, sport=args.sport, season=args.season)
        print("availability: %s" % r.get("state"))
        print("  appended %d, unchanged %d, unrecognised status %d"
              % (r.get("appended", 0), r.get("unchanged", 0),
                 r.get("skipped_unknown", 0)))
        if not r.get("appended") and not r.get("unchanged"):
            print("  NOTHING LISTED. In the preseason that is the expected answer,")
            print("  not a broken feed — but it is also what a broken feed looks")
            print("  like, so the timestamp above is the thing to read.")

    if args.movement:
        season = args.season or dt.datetime.now(dt.timezone.utc).year
        rows = line_movement_after(conn, args.sport, season)
        print("\n§22.4 line movement after a status change: %d transition(s)"
              % len(rows))
        for r in rows[:40]:
            print("  %-22s %-14s -> %-12s %+6.2f pts  (%.1fh out, impact %s)"
                  % (r["team"][:22], r["from_status"], r["to_status"], r["move"],
                     r["hours_to_kickoff"] or 0, r["modelled_impact"]))
        if not rows:
            print("  No transition has a quote on both sides of it yet. This is a")
            print("  sample-size statement, not a finding.")


if __name__ == "__main__":
    main()
