"""
market.py — every quote, from every provider, exactly as it was seen.

    INGEST preserves facts.  POLICY decides which facts to use.

Those two layers were mixed. `fetch_cfb._pick_line` walks a provider priority
list and keeps ONE quote per game, at ingest, before anything has asked a
question. Everything the discarded quotes could have answered is gone with them:

  * consensus, and how wide the providers were
  * best available price
  * whether a disagreement is with the market or with one slow book
  * provider-specific movement
  * the dispersion that says whether a number is contested or settled

This module holds the facts. `market_policy` decides what to make of them, under
a named and versioned rule, at a stated time.

APPEND-ONLY, AND NULL MEANS UNKNOWN. A quote row is never updated: a new
observation is a new row, which is what makes a line-movement history exist at
all. A price that the feed did not supply stays NULL forever. CFBD carries
moneylines but not spread or total juice, so most rows here have no spread
price, and filling in -110 would turn "we do not know what this cost" into a
published ROI.
"""

import datetime as dt

import db
import provenance

# Canonical provider names. A book that renames itself, or arrives with different
# capitalisation from a different endpoint, must not look like a new book or like
# market movement. Raw names are kept in `source` so nothing is lost.
PROVIDER_ALIASES = {
    "draftkings": "DraftKings",
    "draft kings": "DraftKings",
    "dk": "DraftKings",
    "caesars": "Caesars",
    "caesars sportsbook": "Caesars",
    "william hill (caesars)": "Caesars",
    "espn bet": "ESPN BET",
    "espnbet": "ESPN BET",
    "bovada": "Bovada",
    "consensus": "Consensus",
    "teamrankings": "TeamRankings",
    "numberfire": "numberfire",
}

# Beyond these a quote is not a quote, it is a parse failure wearing one. The
# bounds are deliberately WIDE: college football produces genuine 50-point
# spreads, and rejecting a real extreme line as implausible loses exactly the
# games the model most needs to know it should not bet.
MAX_ABS_SPREAD = 75.0
MIN_TOTAL, MAX_TOTAL = 20.0, 130.0
MAX_ABS_ML = 100000

VALID = "valid"
SUSPECT = "suspect"          # stored, flagged, excluded from consensus


def canonical_provider(name):
    """A stable name for a book. Unknown providers pass through, trimmed."""
    if not name:
        return None
    key = str(name).strip().lower()
    return PROVIDER_ALIASES.get(key, str(name).strip())


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def validate(quote):
    """
    Is this a usable observation? -> (status, [problems])

    Returns SUSPECT rather than dropping, because a book posting nonsense is
    itself an observation and deleting it hides a data problem. Only a quote
    with no market values at all is refused outright, since it says nothing.
    """
    problems = []
    if not quote.get("game_id"):
        problems.append("no game_id")
    if not quote.get("provider"):
        problems.append("no provider")
    if not quote.get("observed_at"):
        problems.append("no observation time")

    spread, total = quote.get("home_spread"), quote.get("total")
    if spread is None and total is None and quote.get("home_ml") is None:
        problems.append("no market values at all")
    if spread is not None and abs(spread) > MAX_ABS_SPREAD:
        problems.append("spread %.1f is outside +/-%.0f" % (spread, MAX_ABS_SPREAD))
    if total is not None and not (MIN_TOTAL <= total <= MAX_TOTAL):
        problems.append("total %.1f is outside %.0f-%.0f" % (total, MIN_TOTAL, MAX_TOTAL))
    for k in ("home_ml", "away_ml", "home_spread_price", "away_spread_price",
              "over_price", "under_price"):
        v = quote.get(k)
        if v is not None and (abs(v) > MAX_ABS_ML or -100 < v < 100):
            # American odds do not live between -100 and +100. A 0 or a 50 in
            # that field is a null or a percentage that took a wrong turn.
            problems.append("%s=%s is not American odds" % (k, v))

    # A two-way market whose implied probabilities sum below 1 is arbitrage, and
    # a real book does not offer it. Flagged, not rejected: it is usually a stale
    # side rather than a parse error, and either way it is worth being able to see.
    hm, am = quote.get("home_ml"), quote.get("away_ml")
    if hm is not None and am is not None:
        import grading
        ph, pa = grading.implied_probability(hm), grading.implied_probability(am)
        if ph and pa and (ph + pa) < 0.98:
            problems.append("two-way overround %.3f is below 1" % (ph + pa))

    fatal = any(p.startswith("no ") for p in problems)
    if fatal:
        return None, problems              # not storable
    return (SUSPECT if problems else VALID), problems


def build_quote(*, sport, game_id, provider, observed_at, home_spread=None,
                home_spread_price=None, away_spread_price=None, total=None,
                over_price=None, under_price=None, home_ml=None, away_ml=None,
                source=None, raw=None):
    """
    One normalized, content-addressed quote row. -> dict | None

    `home_spread` is already in house convention: POSITIVE means the home team is
    favoured. Callers converting from a feed do that conversion before they get
    here, in one place, so the sign rule has exactly one home.
    """
    q = {
        "sport": sport, "game_id": game_id,
        "provider": canonical_provider(provider),
        "observed_at": observed_at,
        "home_spread": _num(home_spread),
        "home_spread_price": _int(home_spread_price),
        "away_spread_price": _int(away_spread_price),
        "total": _num(total),
        "over_price": _int(over_price), "under_price": _int(under_price),
        "home_ml": _int(home_ml), "away_ml": _int(away_ml),
        "source": source,
    }
    status, problems = validate(q)
    if status is None:
        return None
    # The hash covers the market VALUES, so re-observing an unchanged quote at a
    # new time is a new row (movement history) while an identical re-fetch of the
    # same observation collides and is ignored.
    q["raw_hash"] = provenance.payload_hash(
        {k: q[k] for k in ("home_spread", "home_spread_price", "away_spread_price",
                           "total", "over_price", "under_price", "home_ml", "away_ml")}
        if raw is None else raw)
    q["quality_status"] = status
    q["quote_id"] = provenance.stable_id("market_quote", {
        "g": game_id, "p": q["provider"], "t": observed_at, "h": q["raw_hash"]})
    q["created_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    q["_problems"] = problems
    return q


COLUMNS = ["quote_id", "sport", "game_id", "provider", "observed_at", "home_spread",
           "home_spread_price", "away_spread_price", "total", "over_price",
           "under_price", "home_ml", "away_ml", "source", "raw_hash",
           "quality_status", "created_at"]


def insert_quotes(conn, quotes):
    """Append quotes. Returns how many rows are new. Duplicates are ignored."""
    rows = [{k: q[k] for k in COLUMNS} for q in quotes if q]
    if not rows:
        return 0
    before = conn.execute("SELECT COUNT(*) c FROM market_quotes").fetchone()["c"]
    conn.executemany(
        "INSERT OR IGNORE INTO market_quotes (%s) VALUES (%s)"
        % (",".join(COLUMNS), ",".join(":" + c for c in COLUMNS)), rows)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) c FROM market_quotes").fetchone()["c"]
    return after - before


def quotes_asof(conn, game_id, when, *, include_suspect=False):
    """
    Every provider's latest quote at or before `when`. -> {provider: row}

    STRICTLY at or before. This is the function that makes "what did the model
    see" answerable, and a single `<=` is the whole of the market side of the
    leakage guard: a quote observed after the forecast cannot be returned, so it
    cannot enter a feature payload.
    """
    # ORDERED BY (time, id) AND NOT BY TIME ALONE. CFBD returns the same book
    # under more than one spelling in the same response -- "DraftKings" at -27.5
    # and "Draft Kings" at -28.5 for Alabama/East Carolina on 5 September.
    # Canonicalizing them is correct (a rename must not look like movement), but
    # it puts two different numbers under one key at one instant, and "the last
    # row wins" then depends on how SQLite happened to return them. quote_id is
    # content-addressed, so this tie-break is stable across machines and runs.
    q = ("SELECT * FROM market_quotes WHERE game_id=? AND observed_at<=?"
         + ("" if include_suspect else " AND quality_status='valid'")
         + " ORDER BY observed_at ASC, quote_id ASC")
    latest = {}
    for r in conn.execute(q, (game_id, when)):
        latest[r["provider"]] = dict(r)      # ascending, so the last write wins
    return latest


def provider_conflicts(conn, game_id, when=None):
    """
    One canonical provider quoting two different numbers at one instant.

    Returns [{provider, observed_at, spreads, totals, sources}]. Not an error and
    not filtered out -- both rows are real observations and both are kept -- but
    a book disagreeing with itself is worth being able to see, and the number a
    consensus used should never be a silent choice between them.
    """
    args = [game_id]
    q = "SELECT * FROM market_quotes WHERE game_id=?"
    if when:
        q += " AND observed_at<=?"
        args.append(when)
    groups = {}
    for r in conn.execute(q + " ORDER BY observed_at, quote_id", args):
        groups.setdefault((r["provider"], r["observed_at"]), []).append(dict(r))
    out = []
    for (prov, obs), rows in sorted(groups.items()):
        spreads = {r["home_spread"] for r in rows if r["home_spread"] is not None}
        totals = {r["total"] for r in rows if r["total"] is not None}
        if len(rows) > 1 and (len(spreads) > 1 or len(totals) > 1):
            out.append({"provider": prov, "observed_at": obs,
                        "spreads": sorted(spreads), "totals": sorted(totals),
                        "sources": sorted(r.get("source") or "" for r in rows),
                        "used_quote_id": rows[-1]["quote_id"]})
    return out


def last_quote_before_kickoff(conn, game_id, kickoff, *, include_suspect=False):
    """Each provider's final quote before the game started. -> {provider: row}"""
    return quotes_asof(conn, game_id, kickoff, include_suspect=include_suspect)


def quote_count(conn, game_id=None):
    if game_id:
        return conn.execute("SELECT COUNT(*) c FROM market_quotes WHERE game_id=?",
                            (game_id,)).fetchone()["c"]
    return conn.execute("SELECT COUNT(*) c FROM market_quotes").fetchone()["c"]
