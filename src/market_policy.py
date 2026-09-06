"""
market_policy.py — turning many quotes into the one number a model saw.

Every function here is VERSIONED, because the answer depends on the rule and the
rule will change. A snapshot that does not record which policy produced it cannot
be compared with one made under a different rule, and comparing them anyway is
how a change in methodology becomes an apparent change in the market.

TWO POLICIES, NAMED
-------------------
`consensus_v1`  the market state as of an instant
`close_policy_v1`  the market state at kickoff

They are the same arithmetic over a different set of quotes, and they are
separate names because the second has a rule the first does not: nothing observed
after kickoff may take part in it, ever, for any reason.

WHY MEDIAN
----------
A median of the providers is unmoved by one book that is slow, wrong, or has a
stale side up. A mean is not. With two providers they agree by construction; with
one there is no consensus and the snapshot says so rather than pretending.
"""

import datetime as dt
import statistics

import grading
import provenance

CONSENSUS_V1 = "consensus_v1"
CLOSE_V1 = "close_policy_v1"

# A quote older than this is not evidence about the market now. Ninety minutes is
# long enough that a book which simply has not moved still counts, and short
# enough that a book which stopped updating this morning does not.
DEFAULT_STALENESS_MINUTES = 90
# For the close specifically: how far before kickoff a quote may have been taken
# and still be called closing.
CLOSE_STALENESS_MINUTES = 90

# What a snapshot is worth, said out loud rather than inferred from provider_count.
VALID_CONSENSUS = "valid_consensus"      # two or more providers agreed to be counted
SINGLE_PROVIDER = "single_provider"      # one book; a quote, not a consensus
STALE_FALLBACK = "stale_fallback"        # only quotes older than the window
MISSING = "missing"                      # nothing to build from


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


def _median(values):
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def build_market_snapshot(quotes, *, at_time, game_id=None, sport="cfb",
                          policy=CONSENSUS_V1,
                          staleness_minutes=DEFAULT_STALENESS_MINUTES):
    """
    Consensus across providers at `at_time`. -> snapshot dict (never None).

    `quotes` is {provider: quote_row}, already filtered to observations at or
    before `at_time` by `market.quotes_asof`. This function does not query and
    cannot reach past its own argument, which is what keeps the leakage guard in
    one place instead of repeated at every call site.

    A snapshot is ALWAYS returned, carrying a status. "There was no market" is a
    fact a forecast needs to record; returning None makes it indistinguishable
    from nobody having asked.
    """
    at = _parse(at_time)
    rows = [q for q in (quotes or {}).values() if q]
    fresh, stale = [], []
    for q in rows:
        obs = _parse(q.get("observed_at"))
        if obs is None:
            continue
        if at is not None and (at - obs) > dt.timedelta(minutes=staleness_minutes):
            stale.append(q)
        else:
            fresh.append(q)

    used = fresh or stale
    if not used:
        status = MISSING
    elif not fresh:
        status = STALE_FALLBACK
    elif len(fresh) == 1:
        status = SINGLE_PROVIDER
    else:
        status = VALID_CONSENSUS

    spreads = [q.get("home_spread") for q in used]
    totals = [q.get("total") for q in used]
    # De-vig each provider on its own and then take the median of the fair
    # probabilities. Averaging raw implied probabilities across books would
    # average their margins in as well.
    probs = []
    for q in used:
        ph, _ = grading.devig_pair(q.get("home_ml"), q.get("away_ml"))
        if ph is not None:
            probs.append(ph)

    present = [s for s in spreads if s is not None]
    snap = {
        "sport": sport,
        "game_id": game_id or (used[0].get("game_id") if used else None),
        "snapshot_at": at.isoformat() if at else at_time,
        "policy_version": policy,
        "consensus_spread": _median(spreads),
        "consensus_total": _median(totals),
        "consensus_home_prob": _median(probs),
        "provider_count": len(used),
        "spread_min": min(present) if present else None,
        "spread_max": max(present) if present else None,
        "quote_ids_json": provenance.canonical_json(
            sorted(q["quote_id"] for q in used if q.get("quote_id"))),
        "status": status,
        "providers": sorted(q.get("provider") for q in used if q.get("provider")),
    }
    snap["payload_hash"] = provenance.payload_hash(
        {k: snap[k] for k in ("game_id", "snapshot_at", "policy_version",
                              "consensus_spread", "consensus_total",
                              "consensus_home_prob", "quote_ids_json")})
    snap["market_snapshot_id"] = provenance.stable_id("market_snapshot", snap["payload_hash"])
    snap["created_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return snap


def build_close(conn, game_id, kickoff, *, sport="cfb",
                staleness_minutes=CLOSE_STALENESS_MINUTES, market_mod=None):
    """
    The closing market, under close_policy_v1. -> snapshot dict

    THE RULE, stated once so it is the same for every game:

        For each provider, take its last valid quote observed at or before
        KICKOFF and within the close-staleness window. Build the consensus with
        the same policy as any other snapshot. Never use an observation from
        after kickoff.

    The last clause is not a preference. A quote timestamped after kickoff knows
    something about the game, and a "closing line" that knows the score is not a
    line at all.
    """
    if market_mod is None:
        import market as market_mod
    quotes = market_mod.quotes_asof(conn, game_id, kickoff)
    snap = build_market_snapshot(quotes, at_time=kickoff, game_id=game_id,
                                 sport=sport, policy=CLOSE_V1,
                                 staleness_minutes=staleness_minutes)
    return snap


SNAPSHOT_COLUMNS = ["market_snapshot_id", "sport", "game_id", "snapshot_at",
                    "policy_version", "consensus_spread", "consensus_total",
                    "consensus_home_prob", "provider_count", "spread_min",
                    "spread_max", "quote_ids_json", "payload_hash", "created_at"]


def store_snapshot(conn, snap):
    """Append a snapshot. Content-addressed, so storing it twice is a no-op."""
    if snap is None or snap.get("status") == MISSING:
        return None
    row = {k: snap.get(k) for k in SNAPSHOT_COLUMNS}
    conn.execute(
        "INSERT OR IGNORE INTO market_snapshots (%s) VALUES (%s)"
        % (",".join(SNAPSHOT_COLUMNS), ",".join(":" + c for c in SNAPSHOT_COLUMNS)), row)
    conn.commit()
    return snap["market_snapshot_id"]
