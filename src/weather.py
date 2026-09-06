"""
weather.py — §23. What the forecast said, when it said it.

THE VARIABLE THIS LAYER WANTS IS WIND, AND IT DOES NOT HAVE IT. §23 names
sustained wind and gusts as the initially most plausible variable, and every
public account of weather in football agrees: wind moves kicking and the passing
game, temperature mostly does not. **The one free source already wired into this
repository — ESPN's scoreboard — publishes temperature, a condition string and a
dome flag, and no wind at all.** The columns exist and stay NULL until a source
that carries wind is added; nothing here fills them with a proxy.

So what this layer honestly delivers today is:

  * `indoor` — a verified fact, not a forecast, and the single largest weather
    effect there is: a dome removes wind and rain entirely. §20.8 lists it as a
    totals input, and it is the one weather variable available with certainty;
  * `temperature_f` and `condition` — recorded because they are free and because
    a stream that starts now is a stream that has history in a year;
  * the append-only snapshot shape, so when a wind source arrives the schema and
    the as-of reader do not have to be redesigned around it.

WHAT IT DOES NOT DO. It adjusts nothing, and §23's own instruction is why: the
evaluation target is totals and score residuals, the totals model (C6) is shadow
and currently loses to the market total by 0.74 points, and adding an unvalidated
input to an unvalidated model measures nothing about either.

FAILURE IS CONTAINED, deliberately and per §23: a weather fetch that fails must
degrade only models that require weather. Nothing here raises into the pipeline,
and a missing snapshot reads as missing, never as fair and still.
"""

import datetime as dt
import json

import provenance

SOURCE_ESPN = "espn_scoreboard"
SCOREBOARD = ("https://site.api.espn.com/apis/site/v2/sports/football/"
              "college-football/scoreboard")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _parse(value):
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def parse_event(ev):
    """
    One ESPN scoreboard event -> the fields this layer stores, or None.

    None when the event carries no weather at all, which is a real state and not
    an error: ESPN publishes a forecast for games close enough to have one.
    """
    comp = (ev.get("competitions") or [{}])[0]
    wx = ev.get("weather") or comp.get("weather")
    venue = comp.get("venue") or {}
    indoor = venue.get("indoor")
    if not wx and indoor is None:
        return None
    wx = wx or {}
    temp = wx.get("temperature")
    if temp is None:
        temp = wx.get("highTemperature")
    return {
        "espn_event_id": ev.get("id"),
        "venue": venue.get("fullName"),
        "indoor": None if indoor is None else int(bool(indoor)),
        "temperature_f": None if temp is None else float(temp),
        "condition": wx.get("displayValue"),
        "condition_id": (None if wx.get("conditionId") is None
                         else str(wx.get("conditionId"))),
        # ESPN publishes neither. NULL, not zero: a still day and an unmeasured
        # day are not the same day, and zero would read as the former.
        "wind_mph": None,
        "gust_mph": None,
        "kickoff": ev.get("date"),
        # `location`, NOT `displayName`. ESPN's display name carries the mascot
        # -- "Washington Huskies" -- and this database stores "Washington", so
        # every single row went unmatched and the layer stored nothing while
        # reporting a successful fetch.
        "teams": sorted(
            ((c.get("team") or {}).get("location")
             or (c.get("team") or {}).get("displayName"))
            for c in (comp.get("competitors") or []) if c.get("team")),
    }


def fetch_day(date_yyyymmdd, *, fetch=None):
    """
    Every game on one date, with whatever weather ESPN has. -> [dict] or None

    None means the fetch failed, and it is deliberately distinguishable from an
    empty list, which means the day had no games. `fetch` is injectable so the
    tests exercise the parsing without a network.
    """
    if fetch is None:
        import roster_watch                    # shares the working ESPN headers
        fetch = roster_watch._get_json
    payload = fetch("%s?dates=%s&limit=200" % (SCOREBOARD, date_yyyymmdd))
    if payload is None:
        return None
    out = []
    for ev in payload.get("events") or []:
        row = parse_event(ev)
        if row is not None:
            out.append(row)
    return out


def match_game(conn, sport, row):
    """
    ESPN's event to this database's game_id, by kickoff date and both teams.

    Returns None rather than a best guess. A weather row filed against the wrong
    game is worse than one not filed at all: it is wrong in a way nothing
    downstream can detect.
    """
    k = _parse(row.get("kickoff"))
    if k is None or len(row.get("teams") or []) != 2:
        return None
    day = k.date().isoformat()
    import team_aliases
    want = sorted(team_aliases.canonical(sport, t) for t in row["teams"])
    for g in conn.execute(
            "SELECT game_id, home_team, away_team FROM games"
            " WHERE sport=? AND date(kickoff)=?", (sport, day)):
        have = sorted(team_aliases.canonical(sport, t)
                      for t in (g["home_team"], g["away_team"]))
        if have == want:
            return g["game_id"]
    return None


def store(conn, *, sport, game_id, row, horizon=None, observed_at=None,
          source=SOURCE_ESPN, commit=True):
    """Append one snapshot. Identical content at the same instant is a no-op."""
    observed_at = observed_at or _now()
    payload = dict(row)
    payload.update({"sport": sport, "game_id": game_id, "source": source,
                    "observed_at": observed_at})
    ph = provenance.payload_hash(payload)
    conn.execute(
        "INSERT OR IGNORE INTO weather_snapshots (snapshot_id, sport, game_id,"
        " horizon, observed_at, source, venue, indoor, temperature_f, condition,"
        " condition_id, wind_mph, gust_mph, payload_json, payload_hash, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (provenance.stable_id("weather_snapshot", ph), sport, game_id, horizon,
         observed_at, source, row.get("venue"), row.get("indoor"),
         row.get("temperature_f"), row.get("condition"), row.get("condition_id"),
         row.get("wind_mph"), row.get("gust_mph"),
         provenance.canonical_json(payload), ph, _now()))
    if commit:
        conn.commit()
    return ph


def weather_asof(conn, game_id, as_of):
    """
    The most recent snapshot at or before `as_of`. -> row or None

    The as-of guard, same as everywhere else: a pregame payload may not contain a
    forecast published after the instant it claims to describe.
    """
    return conn.execute(
        "SELECT * FROM weather_snapshots WHERE game_id=? AND observed_at <= ?"
        " ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1",
        (game_id, as_of)).fetchone()


def summary_asof(conn, game_id, as_of):
    """What a feature payload carries, or None when nothing was ever recorded."""
    r = weather_asof(conn, game_id, as_of)
    if r is None:
        return None
    return {
        "source": r["source"], "observed_at": r["observed_at"],
        "horizon": r["horizon"], "venue": r["venue"],
        "indoor": None if r["indoor"] is None else bool(r["indoor"]),
        "temperature_f": r["temperature_f"], "condition": r["condition"],
        "wind_mph": r["wind_mph"], "gust_mph": r["gust_mph"],
        "adjusts_model": False,
        "why": "no wind from this source, and the totals model it would inform "
               "is itself shadow and behind the market",
    }


def nearest_horizon(kickoff, now=None):
    """
    Which standardized horizon this reading is closest to. -> str or None

    A LABEL, not a claim of punctuality. §11 owns whether a forecast hit its
    window; this only says which window the reading sits nearest, so the stream
    can be read by distance-to-kickoff later. None once kickoff has passed —
    §23's snapshots are all pregame.
    """
    import horizons
    k = _parse(kickoff)
    n = _parse(now) if now else dt.datetime.now(dt.timezone.utc)
    if k is None or n is None or n >= k:
        return None
    hours = (k - n).total_seconds() / 3600.0
    best, gap = None, None
    for name, off in horizons.OFFSET_HOURS.items():
        d = abs(hours - off)
        if gap is None or d < gap:
            best, gap = name, d
    return best


def refresh(conn, *, sport="cfb", days=None, fetch=None, verbose=True):
    """
    Snapshot the next few days of games. -> dict counts

    Never raises. §23 is explicit that a weather source failing must degrade only
    the models that need weather, and this is called from the same run that
    publishes the spread board.
    """
    days = days or 3
    today = dt.datetime.now(dt.timezone.utc).date()
    counts = {"days": 0, "fetched": 0, "stored": 0, "unmatched": 0, "failed": 0}
    for i in range(days):
        d = (today + dt.timedelta(days=i)).strftime("%Y%m%d")
        try:
            rows = fetch_day(d, fetch=fetch)
        except Exception as e:                     # noqa: BLE001
            if verbose:
                print("  weather fetch raised for %s: %s" % (d, e))
            counts["failed"] += 1
            continue
        counts["days"] += 1
        if rows is None:
            counts["failed"] += 1
            continue
        counts["fetched"] += len(rows)
        for row in rows:
            gid = match_game(conn, sport, row)
            if gid is None:
                counts["unmatched"] += 1
                continue
            hz = nearest_horizon(row.get("kickoff"))
            store(conn, sport=sport, game_id=gid, row=row, horizon=hz,
                  commit=False)
            counts["stored"] += 1
    conn.commit()
    return counts


def main():
    import argparse
    import db
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--days", type=int, default=3)
    args = ap.parse_args()
    conn = db.connect()
    c = refresh(conn, sport=args.sport, days=args.days)
    print("weather: %d day(s), %d event(s) with a forecast, %d stored, "
          "%d unmatched, %d failed fetch"
          % (c["days"], c["fetched"], c["stored"], c["unmatched"], c["failed"]))
    print("  NOTE: this source publishes no wind, which is the variable §23 "
          "actually wants. The columns stay NULL rather than holding a proxy.")


if __name__ == "__main__":
    main()
