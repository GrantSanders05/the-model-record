"""
horizons.py — when a forecast was supposed to be made, and whether it was.

The old three-day lock window created the problem this replaces: a Saturday game
could receive an official pick on Wednesday and then sit frozen while the grades,
the injuries and the line all moved. The pick was made before kickoff, which is
the integrity property, but it was made from information nobody would choose.

So forecasts are taken at STANDARDIZED DISTANCES FROM KICKOFF instead, and a run
records which distance it was aiming at, how far off it landed, and whether that
is close enough to count.

    T72  three days out    the schedule is known, little else has moved
    T24  the day before    most of the week's information has arrived
    T6   game morning      availability reports are in
    T2   two hours out     THE OFFICIAL LOCK. Late enough that the grades,
                           the availability and the market have developed;
                           early enough to be unambiguously pregame.
    T30  thirty minutes    shadow only, for measuring late movement

WHY THE TARGET AND THE ACTUAL TIME ARE BOTH RECORDED
----------------------------------------------------
A scheduler does not run when it is asked to. GitHub drops throttled cron runs
and a */30 schedule has delivered a 53-minute median in this repository's own
history. If a forecast recorded only "T2", a run that fired 90 minutes late would
be indistinguishable from one that fired on time, and the whole point of a
standardized horizon — that forecasts across games are comparable — would be
quietly false.

So a forecast stores its target, its real generated time, the delta, and a status.
A late run is recorded as late. It is never relabelled as the horizon it missed.
"""

import datetime as dt

T72, T24, T6, T2, T30 = "T72", "T24", "T6", "T2", "T30"

# Hours before kickoff. One place, not scattered between YAML and Python.
OFFSET_HOURS = {T72: 72.0, T24: 24.0, T6: 6.0, T2: 2.0, T30: 0.5}

# How far from the target a run may land and still be that horizon. Wider far
# out, where an hour changes little, and tight near kickoff, where it changes
# everything.
TOLERANCE_MINUTES = {T72: 60, T24: 45, T6: 30, T2: 20, T30: 10}

# The horizon whose forecasts a strategy may turn into official signals. A
# research policy, frozen for the evaluation window rather than true forever.
OFFICIAL_HORIZON = T2

ALL = [T72, T24, T6, T2, T30]
# T30 is collected but never official: too close to kickoff to act on reliably,
# and its value is measuring how much the market moved after the official lock.
SHADOW_ONLY = {T30}

POLICY_VERSION = "horizons_v1"

ACCEPTED = "accepted"
EARLY = "early"
LATE = "late"
POST_KICK = "post_kick"


def _parse(ts):
    if ts is None:
        return None
    if isinstance(ts, dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def target_time(kickoff, horizon):
    """When a forecast at `horizon` was due. -> datetime | None"""
    ko = _parse(kickoff)
    if ko is None or horizon not in OFFSET_HOURS:
        return None
    return ko - dt.timedelta(hours=OFFSET_HOURS[horizon])


def classify(*, kickoff, generated_at, horizon, tolerance_minutes=None):
    """
    Was this run close enough to the horizon it was aiming at?

    -> {horizon, target_at, delta_seconds, status, tolerance_minutes}

    `delta_seconds` is positive when the run happened AFTER its target, which is
    the ordinary case for a scheduler that fires on a fixed clock.

    POST_KICK IS ABSOLUTE. A forecast generated at or after kickoff is not a
    forecast, whatever its delta says, and no tolerance widens far enough to
    make it one.
    """
    ko, gen = _parse(kickoff), _parse(generated_at)
    target = target_time(kickoff, horizon)
    if ko is None or gen is None or target is None:
        return {"horizon": horizon, "target_at": None, "delta_seconds": None,
                "status": None, "tolerance_minutes": None}

    tol = tolerance_minutes
    if tol is None:
        tol = TOLERANCE_MINUTES.get(horizon, 30)
    delta = (gen - target).total_seconds()

    if gen >= ko:
        status = POST_KICK
    elif abs(delta) <= tol * 60:
        status = ACCEPTED
    elif delta < 0:
        status = EARLY
    else:
        status = LATE

    return {"horizon": horizon, "target_at": target.isoformat(),
            "delta_seconds": int(delta), "status": status,
            "tolerance_minutes": tol}


def due_horizons(*, kickoff, now, horizons=None, tolerance_minutes=None):
    """
    Which horizons a run at `now` can legitimately fill for this game. -> [str]

    Only horizons whose acceptance window contains `now` and whose kickoff has
    not passed. A run outside every window fills nothing rather than filling the
    nearest one.
    """
    out = []
    for h in (horizons or ALL):
        c = classify(kickoff=kickoff, generated_at=now, horizon=h,
                     tolerance_minutes=tolerance_minutes)
        if c["status"] == ACCEPTED:
            out.append(h)
    return out


def is_official(horizon):
    return horizon == OFFICIAL_HORIZON and horizon not in SHADOW_ONLY


def missed(*, kickoff, now, horizon, tolerance_minutes=None):
    """
    Has this horizon's window closed with nothing in it? -> bool

    True once `now` is past the end of the acceptance window. The caller checks
    whether a forecast exists; this only says whether the chance has gone.
    """
    target = target_time(kickoff, horizon)
    n = _parse(now)
    if target is None or n is None:
        return False
    tol = tolerance_minutes
    if tol is None:
        tol = TOLERANCE_MINUTES.get(horizon, 30)
    return n > target + dt.timedelta(minutes=tol)


def record_miss(conn, *, game_id, model_version, horizon, kickoff, now,
               reason="acceptance window closed with no accepted forecast"):
    """
    Write a snapshot_miss. Returns True if it was new.

    A gap that is recorded can never later be filled with a mislabelled forecast,
    because the miss and the forecast would both exist and disagree. A gap that
    is NOT recorded is indistinguishable from a healthy game, which is the state
    the whole horizon apparatus exists to make visible.
    """
    import provenance
    target = target_time(kickoff, horizon)
    mid = provenance.stable_id("event", {"g": game_id, "m": model_version, "h": horizon})
    cur = conn.execute("SELECT COUNT(*) c FROM snapshot_misses WHERE miss_id=?",
                       (mid,)).fetchone()["c"]
    conn.execute(
        "INSERT OR IGNORE INTO snapshot_misses"
        " (miss_id, game_id, model_version, horizon, target_at, detected_at, reason)"
        " VALUES (?,?,?,?,?,?,?)",
        (mid, game_id, model_version, horizon,
         target.isoformat() if target else "", _parse(now).isoformat(), reason))
    conn.commit()
    return cur == 0
