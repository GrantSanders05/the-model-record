"""
signals.py — deciding what to bet is a policy, and policies have versions.

A model forecasts. A strategy decides. Those were one step, spread between
`predict.generate` (which produces a side for essentially every priced
disagreement) and `best_bets.rank` (which then declines blowouts and unrated
games) — with the LEDGER fed from the first and the BOARD shown from the second.
So "the model went X-Y" could mean every forecast, every lined game, every game
inside the band, every fully graded game, or the actual board, and the published
record was fed by whichever one happened to be upstream.

EVERY DECISION IS RECORDED, INCLUDING NO
----------------------------------------
`evaluate` writes a row whether or not it produces a signal, with reason codes.
A declined game must leave a trace, or a strategy's record silently becomes the
record of the games it liked and there is no way to audit what it passed on.

THE STRATEGY CONFIG IS DATA
---------------------------
Not comments, not constants scattered across modules: a dict, hashed, with a
version string. Changing a threshold changes the version, which is what makes
"this strategy went X-Y" a statement about one rule rather than about whatever
the rule happened to be that week.
"""

import datetime as dt
import json

import grading
import horizons
import provenance

# ── reason codes ─────────────────────────────────────────────────────────────
UNRATED_TEAM = "UNRATED_TEAM"
BORROWED_RATER = "BORROWED_RATER"
MISSING_MARKET = "MISSING_MARKET"
MISSING_PRICE = "MISSING_PRICE"
BLOWOUT_OUT_OF_DOMAIN = "BLOWOUT_OUT_OF_DOMAIN"
OUTSIDE_HORIZON = "OUTSIDE_HORIZON"
POST_KICKOFF = "POST_KICKOFF"
STALE_MARKET = "STALE_MARKET"
DEGRADED_AVAILABILITY = "DEGRADED_AVAILABILITY"
UNVALIDATED_PROBABILITY = "UNVALIDATED_PROBABILITY"
EDGE_BELOW_THRESHOLD = "EDGE_BELOW_THRESHOLD"
MARKET_DISABLED = "MARKET_DISABLED"
INCOMPLETE_GRADE = "INCOMPLETE_GRADE"
NO_MODEL_NUMBER = "NO_MODEL_NUMBER"

# ── the strategy ─────────────────────────────────────────────────────────────
#
# S0 is the shipped board's rules, written down. Nothing here is new behaviour:
# the +/-28 domain limit and the unrated exclusion are what `best_bets` already
# applies. What is new is that they are a versioned object rather than two ifs.
STRATEGY_V0 = {
    "strategy_version": "S0-2026.09.06",
    "official_horizon": horizons.OFFICIAL_HORIZON,
    # A game the film could not answer was answered by Elo, which holds every
    # non-FBS team at one constant rating. Measured on 2026 week 1 those games
    # went 4-6 while fully graded ones went 9-5, and the disagreement they
    # generate is the constant talking.
    "allow_borrowed": False,
    "require_complete_grades": True,
    # A grade sheet spans about 25 rating points end to end, so it cannot express
    # a 45-point spread. Outside this the model is not wrong, it is out of domain.
    "max_abs_spread": 28.0,
    "spread_min_edge": 0.0,
    "spread_enabled": True,
    # Totals are a different prediction problem -- pace times efficiency, not
    # relative strength -- and this model does not solve it. Off until a totals
    # model is validated on its own.
    "totals_enabled": False,
    # The repository already found that the largest claimed moneyline EVs
    # performed worst. Off until a prospectively calibrated probability exists.
    "moneyline_enabled": False,
    "require_exact_price_for_roi": True,
    "market_policy": "consensus_v1",
    "max_market_staleness_minutes": 90,
}


def strategy_hash(strategy=None):
    return provenance.payload_hash(strategy or STRATEGY_V0)


EVAL_COLUMNS = ["evaluation_id", "forecast_id", "strategy_version", "evaluated_at",
                "eligible", "reason_codes_json", "calculated_edge", "calculated_ev",
                "decision_market", "decision_side", "decision_line", "decision_price",
                "created_at"]

SIGNAL_COLUMNS = ["signal_id", "evaluation_id", "forecast_id", "game_id",
                  "strategy_version", "market", "side", "line", "price", "provider",
                  "market_snapshot_id", "created_at", "official_horizon",
                  "is_official", "locked_result", "close_result", "close_line",
                  "line_clv", "profit_units", "graded_at", "voided_at", "void_reason"]


def evaluate_spread(*, forecast, payload, strategy=None):
    """
    May this forecast become a spread signal? -> (eligible, reasons, decision)

    Pure: no database, no clock. Every rule that decides whether money moves is
    testable with a dict, which is the point.
    """
    s = strategy or STRATEGY_V0
    reasons = []
    decision = None

    if not s.get("spread_enabled", True):
        reasons.append(MARKET_DISABLED)

    if forecast.get("snapshot_status") == horizons.POST_KICK:
        reasons.append(POST_KICKOFF)
    elif forecast.get("snapshot_status") != horizons.ACCEPTED:
        reasons.append(OUTSIDE_HORIZON)
    if forecast.get("horizon") != s["official_horizon"]:
        reasons.append(OUTSIDE_HORIZON)

    if forecast.get("borrowed_fallback") and not s.get("allow_borrowed", False):
        reasons.append(BORROWED_RATER)
    for role in ("home", "away"):
        vec = payload.get("%s_grade_vector" % role)
        if vec is None:
            reasons.append(UNRATED_TEAM)
        elif s.get("require_complete_grades") and any(v is None for v in vec.values()):
            reasons.append(INCOMPLETE_GRADE)

    market_status = payload.get("market_status")
    line = payload.get("consensus_spread")
    if line is None or market_status == "missing":
        reasons.append(MISSING_MARKET)
    elif market_status == "stale_fallback":
        reasons.append(STALE_MARKET)

    model = forecast.get("pred_home_margin")
    if model is None:
        reasons.append(NO_MODEL_NUMBER)

    if line is not None and abs(line) > s["max_abs_spread"]:
        reasons.append(BLOWOUT_OUT_OF_DOMAIN)

    edge = None
    if model is not None and line is not None:
        edge = model - line
        if abs(edge) <= s.get("spread_min_edge", 0.0):
            reasons.append(EDGE_BELOW_THRESHOLD)

    reasons = sorted(set(reasons))
    if not reasons and edge is not None:
        decision = {
            "market": "spread",
            "side": (payload["home_team"] if edge > 0 else payload["away_team"]),
            "line": line,
            # No spread price is recorded by the feed. NULL, and the strategy
            # says so rather than letting a -110 become a published return.
            "price": None,
            "provider": "consensus:%s" % payload.get("market_policy_version"),
            "market_snapshot_id": payload.get("market_snapshot_id"),
        }
    return (not reasons), reasons, {"edge": edge, "decision": decision}


def evaluate(conn, *, forecast, payload, strategy=None, now=None, commit=True):
    """
    Record a decision for every enabled market on one forecast. -> [eval rows]

    A market that is disabled still produces an evaluation saying so. That is
    what keeps "we do not bet totals" a recorded policy rather than an absence
    somebody has to infer.
    """
    s = strategy or STRATEGY_V0
    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    out = []
    for market in ("spread", "total", "moneyline"):
        if market == "spread":
            eligible, reasons, extra = evaluate_spread(
                forecast=forecast, payload=payload, strategy=s)
        else:
            enabled = s.get("%ss_enabled" % market, False) if market == "total" \
                else s.get("moneyline_enabled", False)
            if enabled:
                # Neither has a validated model or a recorded price yet, so
                # neither can be made eligible without inventing one.
                eligible, reasons, extra = False, [UNVALIDATED_PROBABILITY], {
                    "edge": None, "decision": None}
            else:
                eligible, reasons, extra = False, [MARKET_DISABLED], {
                    "edge": None, "decision": None}

        d = extra.get("decision") or {}
        ev_id = provenance.stable_id("strategy_evaluation", {
            "f": forecast["forecast_id"], "s": s["strategy_version"], "m": market})
        row = {
            "evaluation_id": ev_id, "forecast_id": forecast["forecast_id"],
            "strategy_version": s["strategy_version"], "evaluated_at": now,
            "eligible": 1 if eligible else 0,
            "reason_codes_json": provenance.canonical_json(reasons),
            "calculated_edge": extra.get("edge"),
            "calculated_ev": None,
            "decision_market": d.get("market"), "decision_side": d.get("side"),
            "decision_line": d.get("line"), "decision_price": d.get("price"),
            "created_at": now,
        }
        conn.execute("INSERT OR IGNORE INTO strategy_evaluations (%s) VALUES (%s)"
                     % (",".join(EVAL_COLUMNS),
                        ",".join(":" + c for c in EVAL_COLUMNS)), row)
        row["_decision"] = d or None
        row["_reasons"] = reasons
        out.append(row)
    if commit:
        conn.commit()
    return out


def emit_signal(conn, *, evaluation, forecast, strategy=None, is_official=True,
                now=None, commit=True):
    """
    Turn an eligible evaluation into a signal. -> row | None

    Content-addressed on (evaluation, market), so a retried workflow re-derives
    the same id and collides instead of publishing the same opinion twice. The
    partial unique index in the schema is the second guard: one official signal
    per game per market per strategy, enforced by the database rather than by
    remembering to check.
    """
    if not evaluation.get("eligible"):
        return None
    d = evaluation.get("_decision")
    if not d:
        return None
    s = strategy or STRATEGY_V0
    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    row = {
        "signal_id": provenance.stable_id("signal", {
            "e": evaluation["evaluation_id"], "m": d["market"]}),
        "evaluation_id": evaluation["evaluation_id"],
        "forecast_id": forecast["forecast_id"], "game_id": forecast["game_id"],
        "strategy_version": s["strategy_version"], "market": d["market"],
        "side": d["side"], "line": d["line"], "price": d["price"],
        "provider": d.get("provider"),
        "market_snapshot_id": d.get("market_snapshot_id"),
        "created_at": now, "official_horizon": s["official_horizon"],
        "is_official": 1 if is_official else 0,
        "locked_result": None, "close_result": None, "close_line": None,
        "line_clv": None, "profit_units": None, "graded_at": None,
        "voided_at": None, "void_reason": None,
    }
    try:
        conn.execute("INSERT OR IGNORE INTO signal_log (%s) VALUES (%s)"
                     % (",".join(SIGNAL_COLUMNS),
                        ",".join(":" + c for c in SIGNAL_COLUMNS)), row)
    except Exception:                              # noqa: BLE001 - unique index fired
        return None
    if commit:
        conn.commit()
    return row


def grade_signals(conn, *, sport="cfb", now=None, commit=True):
    """
    Grade every signal whose game is final. -> count

    At the LINE THE SIGNAL LOCKED, using the SIDE THE SIGNAL RECORDED, and
    separately at the close. Neither is ever inferred from a model number.
    """
    import market_policy
    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    rows = conn.execute(
        """SELECT s.*, g.home_team, g.away_team, g.home_score, g.away_score,
                  g.kickoff
             FROM signal_log s JOIN games g ON g.game_id = s.game_id
            WHERE s.graded_at IS NULL AND s.voided_at IS NULL
              AND g.sport = ? AND g.home_score IS NOT NULL
              AND g.away_score IS NOT NULL""", (sport,)).fetchall()
    n = 0
    for r in rows:
        r = dict(r)
        actual_margin = r["home_score"] - r["away_score"]
        actual_total = r["home_score"] + r["away_score"]
        close = market_policy.build_close(conn, r["game_id"], r["kickoff"])
        if r["market"] == "spread":
            close_line = close.get("consensus_spread")
            try:
                locked = grading.grade_spread_pick(
                    side=r["side"], home_team=r["home_team"], away_team=r["away_team"],
                    home_margin_line=r["line"], actual_home_margin=actual_margin)
                at_close = grading.grade_spread_pick(
                    side=r["side"], home_team=r["home_team"], away_team=r["away_team"],
                    home_margin_line=close_line, actual_home_margin=actual_margin)
            except ValueError:
                locked = at_close = None
            clv = grading.line_clv(
                side="home" if r["side"] == r["home_team"] else "away",
                locked=r["line"], closing=close_line)
        else:
            close_line = close.get("consensus_total")
            locked = grading.grade_total_pick(
                side=r["side"], total_line=r["line"], actual_total=actual_total)
            at_close = grading.grade_total_pick(
                side=r["side"], total_line=close_line, actual_total=actual_total)
            clv = grading.total_clv(side=r["side"], locked=r["line"], closing=close_line)
        conn.execute(
            "UPDATE signal_log SET locked_result=?, close_result=?, close_line=?,"
            " line_clv=?, profit_units=?, graded_at=? WHERE signal_id=?",
            (locked, at_close, close_line, clv,
             grading.american_profit_units(locked, r["price"]), now, r["signal_id"]))
        n += 1
    if commit and n:
        conn.commit()
    return n


def official_record(conn, *, strategy_version=None, sport="cfb"):
    """
    The strategy's own record. Locked line, published side, priced honestly.

    `strategy_version` is REQUIRED in spirit: mixing two strategies' signals into
    one percentage is the ambiguity this whole module exists to end. It defaults
    to S0 rather than to "all".
    """
    sv = strategy_version or STRATEGY_V0["strategy_version"]
    rows = [dict(r) for r in conn.execute(
        "SELECT s.* FROM signal_log s JOIN games g ON g.game_id=s.game_id"
        " WHERE s.strategy_version=? AND s.is_official=1 AND s.voided_at IS NULL"
        "   AND s.graded_at IS NOT NULL AND g.sport=?", (sv, sport))]
    out = {"strategy_version": sv, "n": len(rows)}
    for label, col in (("locked", "locked_result"), ("close", "close_result")):
        w = sum(1 for r in rows if r[col] == "W")
        l = sum(1 for r in rows if r[col] == "L")
        p = sum(1 for r in rows if r[col] == "P")
        out["%s_w" % label], out["%s_l" % label], out["%s_p" % label] = w, l, p
        out["%s_pct" % label] = round(100.0 * w / (w + l), 2) if (w + l) else None
    # ROI NEEDS THE PRICE, AND A LOSS DOES NOT REVEAL IT. A losing spread bet
    # costs exactly one unit whatever the juice was, so `profit_units` is known
    # for every loss and unknown for every win where no price was recorded.
    # Averaging over "rows with a known profit" therefore averages over LOSSES
    # ONLY and produces a confident -100%.
    #
    # That is the -110 trap wearing different clothes: an invented number that
    # looks like a measurement. ROI is reported only when the PRICE was recorded,
    # which for spreads in this feed is currently never.
    priced = [r for r in rows if r["price"] is not None]
    out["priced_n"] = len(priced)
    out["unpriced_n"] = len(rows) - len(priced)
    if priced and all(r["profit_units"] is not None for r in priced):
        out["roi"] = round(
            100.0 * sum(r["profit_units"] for r in priced) / len(priced), 2)
    else:
        out["roi"] = None
    clvs = [r["line_clv"] for r in rows if r["line_clv"] is not None]
    out["clv_n"] = len(clvs)
    out["clv_mean"] = round(sum(clvs) / len(clvs), 3) if clvs else None
    out["clv_beat_pct"] = (round(100.0 * sum(1 for c in clvs if c > 0) / len(clvs), 1)
                           if clvs else None)
    return out


def reason_counts(conn, *, strategy_version=None):
    """How often each reason declined a game. The audit of what was passed on."""
    sv = strategy_version or STRATEGY_V0["strategy_version"]
    counts = {}
    for r in conn.execute(
            "SELECT reason_codes_json FROM strategy_evaluations"
            " WHERE strategy_version=? AND eligible=0", (sv,)):
        for code in json.loads(r["reason_codes_json"]):
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
