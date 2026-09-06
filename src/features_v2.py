"""
features_v2.py — exactly what a model was given, and when.

Every function here takes `as_of` and none of them can reach past it. That is the
whole design: there is no `latest_grade(team)` and no `current_market(game)` in
V2, because a helper that means "latest" has no way to be wrong on a Tuesday and
every way to be wrong when it is called from a backfill.

    grade_asof(team, when)          not  latest_grade(team)
    market_asof(game, when, policy) not  current_market(game)

WHAT IS IN A PAYLOAD
--------------------
Enough to prove what the model knew: the two grade vectors and their snapshot
ids, the market snapshot id and its consensus, the ranks in force, the venue
facts, and the Champion's own accrued state. Values AND identifiers, because a
value alone cannot be traced back and an id alone cannot be read.

WHAT IS NOT
-----------
The result. Not the score, not the actual margin, not a rank published after
kickoff, not a stat computed from the game being predicted. A payload is hashed
and stored before the game is played, so anything in it that depends on the
outcome is a leak that no later check can undo.
"""

import datetime as dt

import grade_snapshots
import market
import market_policy
import provenance

CHAMPION_FEATURES_V1 = "champion_features_v1"
# V2 adds team form (E005's one feature) to the payload. Additive only: every key
# v1 carried is still here and still means the same thing, so a model reading the
# v1 keys is unaffected. Snapshots already recorded keep their own schema string —
# a payload is content-addressed and an old one is not retro-fitted.
CHAMPION_FEATURES_V2 = "champion_features_v2"
# V3 adds season-to-date scoring rates, which is C6's whole input. A NEW string
# rather than a redefinition of v2: a schema version that means two different
# payload shapes is exactly what §9.3 exists to prevent, and versions are cheap.
CHAMPION_FEATURES_V3 = "champion_features_v3"


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


def rank_asof(conn, sport, season, team, when):
    """
    The most recent published rank at or before `when`. -> int | None

    Polls are published on a known day, but the `rankings` table stores a week
    rather than a timestamp, so the week's first kickoff is used as the moment
    the poll became usable. That is a reconstruction and is deliberately
    conservative: it can never make a poll available EARLIER than the games it
    was published to reflect.
    """
    row = conn.execute(
        """SELECT r.rank
             FROM rankings r
             JOIN (SELECT season, week, MIN(kickoff) k FROM games
                    WHERE sport=? AND season=? AND kickoff IS NOT NULL
                    GROUP BY season, week) w
               ON w.season = r.season AND w.week = r.week
            WHERE r.sport=? AND r.season=? AND r.team=? AND w.k <= ?
            ORDER BY r.week DESC,
                     CASE r.poll WHEN 'AP Top 25' THEN 0
                                 WHEN 'Coaches Poll' THEN 1 ELSE 2 END
            LIMIT 1""",
        (sport, season, sport, season, team, when)).fetchone()
    return row["rank"] if row else None


def _form(conn, sport, season, as_of):
    """The as-of form state, or None if the challenger module is unavailable."""
    try:
        from models_v2 import form_quality
    except Exception:                              # noqa: BLE001
        # A challenger's module failing must not stop the Champion's snapshot.
        # The field goes MISSING rather than zero: zero is a real form value
        # meaning "exactly as expected", and it is not the same as "not known".
        return None
    try:
        return form_quality.form_asof(conn, sport, season, as_of)
    except Exception as e:                         # noqa: BLE001
        print("  form unavailable for %s %s: %s" % (sport, season, e))
        return None


def _form_value(conn, sport, season, as_of, team):
    tf = _form(conn, sport, season, as_of)
    return None if tf is None else round(tf.value_for(team), 4)


def _form_diff(conn, sport, season, as_of, home_team, away_team):
    tf = _form(conn, sport, season, as_of)
    return None if tf is None else round(tf.diff(home_team, away_team), 4)


def _form_basis():
    """What the form is a residual AGAINST — §21.1 requires this to be explicit."""
    try:
        from models_v2 import form_quality
    except Exception:                              # noqa: BLE001
        return None
    return {"expected_from": form_quality.MARKET,
            "market_timing_quality": form_quality.MARKET_TIMING,
            "settle_hours": form_quality.SETTLE_HOURS,
            "params": form_quality.TeamForm().state()}


def _scoring(conn, sport, season, as_of, team):
    """Season-to-date scoring rates for one team, or None if unavailable."""
    try:
        from models_v2 import totals as _totals
    except Exception:                              # noqa: BLE001
        return None
    try:
        ts = _totals.scoring_asof(conn, sport, season, as_of)
    except Exception as e:                         # noqa: BLE001
        print("  scoring rates unavailable for %s %s: %s" % (sport, season, e))
        return None
    return {"off_pg": round(ts.offence(team), 4),
            "def_pg": round(ts.defence(team), 4),
            "games": ts.games(team),
            "league_mean": round(ts.league_mean(), 4),
            "shrink_games": ts.shrink_games}


def build_feature_snapshot(conn, *, sport, game_id, as_of, model_version,
                           market_policy_version=market_policy.CONSENSUS_V1,
                           champion_state=None, include_availability=False,
                           include_weather=False,
                           feature_schema=CHAMPION_FEATURES_V3):
    """
    Assemble and hash the decision-time facts for one game. -> dict

    Returns the payload, its hash, its id, and the snapshot ids it references,
    plus a `problems` list naming anything it could not obtain. A missing input
    is reported, never defaulted: a team with no grade produces None and a
    problem code, not a vector of zeros.
    """
    g = conn.execute(
        "SELECT game_id, sport, season, week, home_team, away_team, kickoff,"
        " neutral_site, home_div, away_div FROM games WHERE game_id=?",
        (game_id,)).fetchone()
    if g is None:
        return None
    g = dict(g)
    problems = []

    if _parse(as_of) is not None and _parse(g["kickoff"]) is not None \
            and _parse(as_of) >= _parse(g["kickoff"]):
        # Recorded rather than raised: the caller decides whether to store a
        # post-kick payload for research, and the strategy layer refuses to make
        # one official. Silently building it as though it were pregame is the
        # only unacceptable option.
        problems.append("AS_OF_AT_OR_AFTER_KICKOFF")

    season = g["season"]
    grades, gsids = {}, {}
    for role, team in (("home", g["home_team"]), ("away", g["away_team"])):
        snap = grade_snapshots.grade_asof(conn, sport, season, team, as_of)
        grades[role] = grade_snapshots.vector_of(snap)
        gsids[role] = snap["grade_snapshot_id"] if snap else None
        if snap is None:
            problems.append("NO_GRADE_%s" % role.upper())
        else:
            missing = [p for p in grade_snapshots.POSITIONS if snap[p] is None]
            if missing:
                problems.append("INCOMPLETE_GRADE_%s:%s"
                                % (role.upper(), ",".join(missing)))

    quotes = market.quotes_asof(conn, game_id, as_of)
    msnap = market_policy.build_market_snapshot(
        quotes, at_time=as_of, game_id=game_id, sport=sport,
        policy=market_policy_version)
    if msnap["status"] == market_policy.MISSING:
        problems.append("MISSING_MARKET")
    elif msnap["status"] == market_policy.STALE_FALLBACK:
        problems.append("STALE_MARKET")

    payload = {
        "schema": feature_schema,
        "game_id": game_id,
        "sport": sport,
        "season": season,
        "week": g["week"],
        "home_team": g["home_team"],
        "away_team": g["away_team"],
        "kickoff": g["kickoff"],
        "neutral_site": int(g["neutral_site"] or 0),
        "home_div": g["home_div"],
        "away_div": g["away_div"],
        "as_of": as_of,
        "home_grade_snapshot_id": gsids["home"],
        "away_grade_snapshot_id": gsids["away"],
        "home_grade_vector": grades["home"],
        "away_grade_vector": grades["away"],
        "market_snapshot_id": msnap.get("market_snapshot_id"),
        "market_policy_version": market_policy_version,
        "market_status": msnap["status"],
        "consensus_spread": msnap.get("consensus_spread"),
        "consensus_total": msnap.get("consensus_total"),
        "consensus_home_prob": msnap.get("consensus_home_prob"),
        "market_provider_count": msnap.get("provider_count"),
        "home_rank": rank_asof(conn, sport, season, g["home_team"], as_of),
        "away_rank": rank_asof(conn, sport, season, g["away_team"], as_of),
        # The Champion's own accrued state (quality points, record) as of this
        # instant, supplied by the caller that owns the rater. Carried so the
        # rating can be reproduced without replaying the season.
        "champion_state": champion_state,
        # E005's feature. Replayed from games that had certainly FINISHED by
        # `as_of` — kickoff plus a settle margin, not kickoff — because a game
        # that started two hours ago is still being played and its margin is a
        # fact from the future. None when the season has produced no priced,
        # finished game yet, which is the correct answer in week 1.
        "home_form": _form_value(conn, sport, season, as_of, g["home_team"]),
        "away_form": _form_value(conn, sport, season, as_of, g["away_team"]),
        "form_diff": _form_diff(conn, sport, season, as_of,
                                g["home_team"], g["away_team"]),
        "form_basis": _form_basis(),
        # C6's input: points scored and allowed per game to date, shrunk toward
        # the league mean over the same games. Same settle rule as the form
        # feature, for the same reason.
        "home_scoring": _scoring(conn, sport, season, as_of, g["home_team"]),
        "away_scoring": _scoring(conn, sport, season, as_of, g["away_team"]),
        "availability": None if not include_availability else "not_implemented",
        "weather": None if not include_weather else "not_implemented",
    }
    ph = provenance.payload_hash(payload)
    return {
        "feature_snapshot_id": provenance.stable_id("feature_snapshot", ph),
        "game_id": game_id,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "feature_schema_version": feature_schema,
        "payload_json": provenance.canonical_json(payload),
        "payload_hash": ph,
        "payload": payload,
        "market_snapshot": msnap,
        "problems": problems,
    }


FEATURE_COLUMNS = ["feature_snapshot_id", "game_id", "created_at",
                   "feature_schema_version", "payload_json", "payload_hash"]


def store(conn, snap, *, commit=True):
    """Append a feature snapshot and its market snapshot. Idempotent."""
    if snap is None:
        return None
    if snap.get("market_snapshot"):
        market_policy.store_snapshot(conn, snap["market_snapshot"])
    row = {k: snap[k] for k in FEATURE_COLUMNS}
    conn.execute(
        "INSERT OR IGNORE INTO feature_snapshots (%s) VALUES (%s)"
        % (",".join(FEATURE_COLUMNS), ",".join(":" + c for c in FEATURE_COLUMNS)), row)
    if commit:
        conn.commit()
    return snap["feature_snapshot_id"]


def load_payload(conn, feature_snapshot_id):
    """The stored payload, parsed. -> dict | None"""
    import json
    r = conn.execute("SELECT payload_json FROM feature_snapshots"
                     " WHERE feature_snapshot_id=?", (feature_snapshot_id,)).fetchone()
    return json.loads(r["payload_json"]) if r else None
