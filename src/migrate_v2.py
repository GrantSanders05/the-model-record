"""
migrate_v2.py — carry the existing record into the V2 tables without touching it.

    python3 src/migrate_v2.py --dry-run
    python3 src/migrate_v2.py --apply
    python3 src/migrate_v2.py --apply --report output/v2_migration_report.json
    python3 src/migrate_v2.py --fixture-test     # self-contained, used by CI

WHAT THIS DOES NOT DO
---------------------
It does not edit a single existing row. `picks_log` comes out byte-identical
except for the V2 result columns added in Phase 0A, which are computed from
columns that were already there. Nothing is deleted, nothing is reinterpreted in
place, and the legacy `ats_result` keeps its original close-based meaning.

WHAT IT DERIVES, AND WHAT IT REFUSES TO
---------------------------------------
For every historical pick it can build a forecast, an evaluation and a signal,
because the pick recorded a side, a number and a time. It CANNOT build:

  * a spread or total price -- CFBD supplies moneylines and not juice, so those
    stay NULL and no ROI is computed from them;
  * a market snapshot -- one preferred quote was stored, not the providers behind
    it, so there is nothing to take a consensus of;
  * a real feature hash -- the grade vector as of that instant was not preserved.

Those rows are stamped `provenance_quality = 'legacy'` and stop there. A
manufactured hash would make an unreproducible forecast look reproducible, which
is worse than a gap that says so.

THE MONEYLINE COLUMN IS NOT PROMOTED TO A SIGNAL. `predict.generate` documents
`ml_pick` as "STRAIGHT-UP winner, which is not the same thing as the moneyline
bet on the Best Bets board". Turning it into a wager record would invent a
betting history nobody had.

RECONCILIATION IS THE POINT. The report counts where the new locked-line result
disagrees with the legacy close-based one and prints examples. Those differences
are not corruption: they are the side-recomputation bug, made visible.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db            # noqa: E402
import grading       # noqa: E402
import provenance    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MIGRATION_ID = "v2-legacy-picks-001"
# Historical picks are their own strategy: they were produced by the pre-V2 lock
# path, with its own eligibility rules (or lack of them). Filing them under the
# live strategy version would let old decisions contaminate the new record.
LEGACY_STRATEGY = "S-legacy"
LEGACY_HORIZON = "legacy"
LEGACY_FEATURE_SCHEMA = "legacy_pick_v1"


def _now():
    return dt.datetime.now(dt.timezone.utc)


def source_hash(path):
    """SHA-256 of the database file, so a report says which database it read."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def already_applied(conn):
    row = conn.execute("SELECT applied_at FROM v2_migrations WHERE migration=?",
                       (MIGRATION_ID,)).fetchone()
    return row["applied_at"] if row else None


def table_counts(conn):
    names = ["games", "lines", "picks_log", "picks_voided", "grades",
             "market_quotes", "grade_snapshots", "model_registry", "forecast_log",
             "strategy_evaluations", "signal_log", "game_results_v2"]
    out = {}
    for n in names:
        try:
            out[n] = conn.execute("SELECT COUNT(*) c FROM %s" % n).fetchone()["c"]
        except Exception:                          # noqa: BLE001 - table may not exist yet
            out[n] = None
    return out


def _model_version_for(config_label, git_sha):
    """One registry entry per distinct historical config label."""
    return "C-legacy:%s" % (config_label or "unknown")


def plan(conn, sport="cfb"):
    """
    Work out every row the migration would write. Returns (rows, report).

    Pure with respect to the database: it reads and computes, it does not write.
    `--dry-run` and `--apply` run the identical planner, so what is reported is
    exactly what gets applied.
    """
    picks = [dict(r) for r in conn.execute(
        "SELECT * FROM picks_log WHERE sport=? ORDER BY kickoff, game_id", (sport,))]
    games = {r["game_id"]: dict(r) for r in conn.execute(
        "SELECT game_id, home_score, away_score FROM games WHERE sport=?", (sport,))}
    closes = {r["game_id"]: dict(r) for r in conn.execute(
        "SELECT l.game_id, l.home_margin, l.total, l.home_ml, l.away_ml "
        "FROM lines l JOIN games g USING(game_id) WHERE g.sport=?", (sport,))}
    voided = [dict(r) for r in conn.execute("SELECT * FROM picks_voided")]

    sha, dirty = provenance.git_sha(ROOT)
    now = _now().isoformat()

    rep = {
        "migration": MIGRATION_ID,
        "sport": sport,
        "picks_examined": len(picks),
        "picks_with_results": 0,
        "locked_results_derivable": 0,
        "close_results_derivable": 0,
        "locked_vs_legacy_differences": 0,
        "close_vs_legacy_differences": 0,
        "missing_at_pick_lines": 0,
        "missing_closes": 0,
        "missing_spread_prices": 0,
        "missing_total_prices": 0,
        "voided_rows_migrated": len(voided),
        "model_versions_created": 0,
        "forecast_rows_created": 0,
        "strategy_evaluations_created": 0,
        "signals_created": 0,
        "signals_spread": 0,
        "signals_total": 0,
        "moneyline_columns_not_promoted": 0,
        "rows_skipped": 0,
        "skip_reasons": {},
        "examples_locked_vs_legacy": [],
        "examples_close_vs_legacy": [],
    }

    models, features, forecasts, evals, signals, results = {}, [], [], [], [], {}

    for p in picks:
        gid = p["game_id"]
        g = games.get(gid)
        if g is None:
            rep["rows_skipped"] += 1
            rep["skip_reasons"]["no game row"] = rep["skip_reasons"].get("no game row", 0) + 1
            continue

        mv = _model_version_for(p.get("config_label"), sha)
        if mv not in models:
            models[mv] = {
                "model_version": mv, "model_id": "champion-grade", "role": "retired",
                "experiment_id": None,
                # The commit that ran at the time was not recorded. Saying so is the
                # only honest option; stamping today's sha would claim this code
                # produced a forecast it never saw.
                "git_sha": "unknown-legacy",
                "config_json": provenance.canonical_json(
                    {"config_label": p.get("config_label")}),
                "config_hash": provenance.payload_hash(
                    {"config_label": p.get("config_label")}),
                "feature_schema_version": LEGACY_FEATURE_SCHEMA,
                "created_at": now, "retired_at": now,
                "notes": "reconstructed from picks_log during the V2 migration; "
                         "config and code state at the time were not preserved",
            }

        final = g["home_score"] is not None and g["away_score"] is not None
        actual_margin = (g["home_score"] - g["away_score"]) if final else None
        actual_total = (g["home_score"] + g["away_score"]) if final else None
        if final:
            rep["picks_with_results"] += 1
            results[gid] = {
                "game_id": gid, "final_home_score": g["home_score"],
                "final_away_score": g["away_score"],
                "finalized_at": p.get("graded_at") or now,
                "close_policy_version": "legacy_stored_line",
                "closing_market_snapshot_id": None,
                "result_hash": provenance.payload_hash(
                    {"g": gid, "h": g["home_score"], "a": g["away_score"]}),
            }

        locked_m = p.get("market_margin_at_pick")
        locked_t = p.get("market_total_at_pick")
        close_row = closes.get(gid, {})
        close_m = p.get("closing_margin")
        if close_m is None:
            close_m = close_row.get("home_margin")
        close_t = p.get("closing_total")
        if close_t is None:
            close_t = close_row.get("total")

        if locked_m is None:
            rep["missing_at_pick_lines"] += 1
        if close_m is None:
            rep["missing_closes"] += 1
        rep["missing_spread_prices"] += 1        # never stored, for any row
        if p.get("ou_pick"):
            rep["missing_total_prices"] += 1
        if p.get("ml_pick"):
            rep["moneyline_columns_not_promoted"] += 1

        # The forecast: what the model said, when it said it.
        fpayload = {
            "schema": LEGACY_FEATURE_SCHEMA, "game_id": gid,
            "home_team": p["home_team"], "away_team": p["away_team"],
            "as_of": p.get("published_at"),
            "market_margin_at_pick": locked_m, "market_total_at_pick": locked_t,
            "config_label": p.get("config_label"),
            "note": "reconstructed from picks_log; the grade vector and market "
                    "quotes as of this instant were not preserved",
        }
        fsid = provenance.stable_id("feature_snapshot", fpayload)
        features.append({
            "feature_snapshot_id": fsid, "game_id": gid, "created_at": now,
            "feature_schema_version": LEGACY_FEATURE_SCHEMA,
            "payload_json": provenance.canonical_json(fpayload),
            "payload_hash": provenance.payload_hash(fpayload),
        })
        fc_key = {"gid": gid, "mv": mv, "at": p.get("published_at"), "h": LEGACY_HORIZON}
        fcid = provenance.stable_id("forecast", fc_key)
        forecasts.append({
            "forecast_id": fcid, "sport": sport, "game_id": gid, "model_version": mv,
            "feature_snapshot_id": fsid, "market_snapshot_id": None,
            "horizon": LEGACY_HORIZON, "horizon_target_at": None,
            "generated_at": p.get("published_at") or now,
            "horizon_delta_seconds": None, "snapshot_status": "legacy",
            "pred_home_margin": p.get("model_margin"),
            "pred_total": p.get("model_total"),
            "home_win_prob": None, "home_cover_prob": None, "over_prob": None,
            "margin_uncertainty": None, "total_uncertainty": None,
            "borrowed_fallback": 0,
            "provenance_quality": provenance.LEGACY,
            "created_by_run": "migrate_v2", "created_at": now,
        })
        rep["forecast_rows_created"] += 1

        # One evaluation per market, so a market that produced no side is a
        # recorded decision rather than an absence.
        for market, side, line, price in (
                ("spread", p.get("ats_pick"), locked_m, None),
                ("total", p.get("ou_pick"), locked_t, None)):
            eligible = 1 if (side and line is not None) else 0
            reasons = []
            if not side:
                reasons.append("NO_SIDE_RECORDED")
            if line is None:
                reasons.append("MISSING_MARKET")
            ev_key = {"fc": fcid, "sv": LEGACY_STRATEGY, "m": market}
            evid = provenance.stable_id("strategy_evaluation", ev_key)
            evals.append({
                "evaluation_id": evid, "forecast_id": fcid,
                "strategy_version": LEGACY_STRATEGY,
                "evaluated_at": p.get("published_at") or now,
                "eligible": eligible,
                "reason_codes_json": provenance.canonical_json(reasons),
                "calculated_edge": (
                    (p["model_margin"] - line) if market == "spread"
                    and p.get("model_margin") is not None and line is not None
                    else (p["model_total"] - line) if market == "total"
                    and p.get("model_total") is not None and line is not None
                    else None),
                "calculated_ev": None,
                "decision_market": market if eligible else None,
                "decision_side": side if eligible else None,
                "decision_line": line if eligible else None,
                "decision_price": price,
                "created_at": now,
            })
            rep["strategy_evaluations_created"] += 1
            if not eligible:
                continue

            # THE SIDE IS READ, NEVER RE-DERIVED. This is the whole reason the
            # migration exists rather than a recompute-from-model-margin script.
            if market == "spread":
                lock_res = _safe_spread(side, p, line, actual_margin)
                close_res = _safe_spread(side, p, close_m, actual_margin)
                clv = grading.line_clv(
                    side="home" if side == p["home_team"] else "away",
                    locked=line, closing=close_m)
            else:
                lock_res = grading.grade_total_pick(
                    side=side, total_line=line, actual_total=actual_total)
                close_res = grading.grade_total_pick(
                    side=side, total_line=close_t, actual_total=actual_total)
                clv = grading.total_clv(side=side, locked=line, closing=close_t)

            if lock_res is not None:
                rep["locked_results_derivable"] += 1
            if close_res is not None:
                rep["close_results_derivable"] += 1

            legacy = p.get("ats_result") if market == "spread" else p.get("ou_result")
            if legacy and lock_res and legacy != lock_res:
                rep["locked_vs_legacy_differences"] += 1
                if len(rep["examples_locked_vs_legacy"]) < 8:
                    rep["examples_locked_vs_legacy"].append({
                        "game": "%s @ %s" % (p["away_team"], p["home_team"]),
                        "market": market, "side": side,
                        "locked_line": line, "closing_line":
                            close_m if market == "spread" else close_t,
                        "actual": actual_margin if market == "spread" else actual_total,
                        "legacy_close_based": legacy, "locked_line_result": lock_res,
                    })
            if legacy and close_res and legacy != close_res:
                rep["close_vs_legacy_differences"] += 1
                if len(rep["examples_close_vs_legacy"]) < 8:
                    rep["examples_close_vs_legacy"].append({
                        "game": "%s @ %s" % (p["away_team"], p["home_team"]),
                        "market": market, "side": side,
                        "legacy_close_based": legacy,
                        "side_preserving_close": close_res,
                        "note": "same close, different answer: the legacy value "
                                "graded a side recomputed from the model number",
                    })

            sg_key = {"ev": evid, "m": market}
            signals.append({
                "signal_id": provenance.stable_id("signal", sg_key),
                "evaluation_id": evid, "forecast_id": fcid, "game_id": gid,
                "strategy_version": LEGACY_STRATEGY, "market": market, "side": side,
                "line": line, "price": None, "provider": None,
                "market_snapshot_id": None,
                "created_at": p.get("published_at") or now,
                "official_horizon": LEGACY_HORIZON,
                "is_official": 1,
                "locked_result": lock_res, "close_result": close_res,
                "close_line": close_m if market == "spread" else close_t,
                "line_clv": clv,
                # No price was ever recorded, so there is no profit to state.
                "profit_units": None,
                "graded_at": p.get("graded_at"),
                "voided_at": p.get("voided_at"), "void_reason": p.get("void_reason"),
            })
            rep["signals_created"] += 1
            rep["signals_%s" % market] += 1

    rep["model_versions_created"] = len(models)
    void_events = [{
        "void_id": provenance.stable_id("event", {"v": v["game_id"], "t": v["voided_at"]}),
        "object_type": "legacy_pick", "object_id": v["game_id"],
        "voided_at": v["voided_at"], "reason": v.get("void_reason") or "no reason recorded",
        "payload": v.get("payload"),
    } for v in voided]

    rows = {"model_registry": list(models.values()), "feature_snapshots": features,
            "forecast_log": forecasts, "strategy_evaluations": evals,
            "signal_log": signals, "game_results_v2": list(results.values()),
            "v2_void_events": void_events}
    return rows, rep


def _safe_spread(side, pick, line, actual):
    """grade_spread_pick, but an unknown team name is reported, not fatal."""
    try:
        return grading.grade_spread_pick(
            side=side, home_team=pick["home_team"], away_team=pick["away_team"],
            home_margin_line=line, actual_home_margin=actual)
    except ValueError:
        return None


def _insert(conn, table, rows):
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (
        table, ",".join(cols), ",".join(":" + c for c in cols))
    conn.executemany(sql, rows)
    return len(rows)


def backfill_pick_columns(conn, rows):
    """
    Fill the V2 result columns on historical `picks_log` rows. Returns the count.

    Those columns were added in Phase 0A and are written by `ledger.grade`, which
    only looks at rows it has not graded yet -- so every pick settled before the
    fix has them NULL. This fills them from the signals just planned, which were
    computed from the immutable at-pick line and the side that was published.

    IT WRITES ONLY THE NEW COLUMNS. `ats_result` and `ou_result` are not in the
    UPDATE at all: they keep their original close-based value, and the pick
    itself -- side, line, model number, timestamps -- is never touched.
    """
    by_game = {}
    for sg in rows.get("signal_log", []):
        if sg["locked_result"] is None and sg["close_result"] is None:
            continue
        d = by_game.setdefault(sg["game_id"], {"game_id": sg["game_id"]})
        if sg["market"] == "spread":
            d["ats_result_at_pick"] = sg["locked_result"]
            d["ats_result_at_close"] = sg["close_result"]
        elif sg["market"] == "total":
            d["ou_result_at_pick"] = sg["locked_result"]
            d["ou_result_at_close"] = sg["close_result"]
    updates = []
    for gid, d in by_game.items():
        updates.append({
            "game_id": gid,
            "ats_result_at_pick": d.get("ats_result_at_pick"),
            "ats_result_at_close": d.get("ats_result_at_close"),
            "ou_result_at_pick": d.get("ou_result_at_pick"),
            "ou_result_at_close": d.get("ou_result_at_close"),
            "grading_version": db.GRADING_VERSION,
        })
    if updates:
        conn.executemany(
            "UPDATE picks_log SET"
            "  ats_result_at_pick = COALESCE(:ats_result_at_pick, ats_result_at_pick),"
            "  ats_result_at_close = COALESCE(:ats_result_at_close, ats_result_at_close),"
            "  ou_result_at_pick = COALESCE(:ou_result_at_pick, ou_result_at_pick),"
            "  ou_result_at_close = COALESCE(:ou_result_at_close, ou_result_at_close),"
            "  grading_version = :grading_version"
            " WHERE game_id = :game_id AND graded_at IS NOT NULL", updates)
    return len(updates)


def apply_migration(conn, rows, rep, src_hash=None):
    """Write the planned rows. INSERT OR IGNORE throughout, so re-running is safe."""
    order = ["model_registry", "feature_snapshots", "forecast_log",
             "strategy_evaluations", "signal_log", "game_results_v2", "v2_void_events"]
    for t in order:
        _insert(conn, t, rows.get(t, []))
    rep["pick_rows_backfilled"] = backfill_pick_columns(conn, rows)
    rep["signals_repaired"] = repair_signals(conn, rows, commit=False)
    # Historical grade vectors, reconstructed from the weekly `grades` table so
    # `grade_asof` can answer questions about past forecasts. Labelled `backfill`
    # and effective at the first kickoff of the week they were stamped for; a
    # week with no scheduled game gets no snapshot rather than a guessed time.
    import grade_snapshots
    st, seen, skipped = grade_snapshots.backfill_from_grades(conn, rep["sport"])
    rep["grade_snapshots_backfilled"] = st
    rep["grade_team_weeks_seen"] = seen
    rep["grade_team_weeks_without_a_kickoff"] = skipped
    conn.execute(
        "INSERT OR REPLACE INTO v2_migrations (migration, applied_at, source_hash, report_json)"
        " VALUES (?,?,?,?)",
        (MIGRATION_ID, _now().isoformat(), src_hash, provenance.canonical_json(rep)))
    conn.commit()


def repair_signals(conn, rows, *, commit=True):
    """
    Restore derived columns on migrated signals that were later nulled. -> count

    NOT A REWRITE OF HISTORY. close_line and line_clv are DERIVED from columns
    that have not changed — the pick's own at-pick line, and the closing line
    already stored on the pick or in `lines`. The planner recomputes them from
    exactly those inputs, so this puts back a value that was destroyed rather
    than deciding a new one. The side, the line taken and the result are not
    touched.

    WHY IT WAS NEEDED. `signals.grade_signals` recomputed the close with
    close_policy_v1, which requires quotes observed BEFORE kickoff. Historical
    games have none, so the policy correctly returned "missing" — and the UPDATE
    wrote that missing value over a real closing number that the migration had
    already recorded. 122 signals lost their close, and nothing failed: the only
    symptom was the journal replay ceasing to reconcile.
    """
    fixed = 0
    for sg in rows.get("signal_log", []):
        if sg.get("close_line") is None and sg.get("line_clv") is None:
            continue
        cur = conn.execute(
            "SELECT close_line, line_clv FROM signal_log WHERE signal_id=?",
            (sg["signal_id"],)).fetchone()
        if cur is None:
            continue
        if cur["close_line"] is not None and cur["line_clv"] is not None:
            continue
        conn.execute(
            "UPDATE signal_log SET close_line = COALESCE(close_line, ?),"
            "  line_clv = COALESCE(line_clv, ?),"
            "  close_result = COALESCE(close_result, ?)"
            " WHERE signal_id=?",
            (sg["close_line"], sg["line_clv"], sg["close_result"], sg["signal_id"]))
        fixed += 1
    if commit and fixed:
        conn.commit()
    return fixed


def backup(path):
    """Timestamped copy beside the database. Returns the new path."""
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    dest = "%s.pre-v2-%s.db" % (os.path.splitext(path)[0], stamp)
    shutil.copy2(path, dest)
    return dest


def print_report(rep, counts_before, counts_after=None):
    print("\n── V2 migration report ──")
    print("  picks examined                    : %d" % rep["picks_examined"])
    print("  ...with a final result            : %d" % rep["picks_with_results"])
    print("  locked-line results derivable     : %d" % rep["locked_results_derivable"])
    print("  close results derivable           : %d" % rep["close_results_derivable"])
    print("  missing at-pick lines             : %d" % rep["missing_at_pick_lines"])
    print("  missing closes                    : %d" % rep["missing_closes"])
    print("  missing spread prices             : %d  (never stored by the feed)"
          % rep["missing_spread_prices"])
    print("  missing total prices              : %d" % rep["missing_total_prices"])
    print("  moneyline columns NOT promoted    : %d  (a straight-up pick is not a wager)"
          % rep["moneyline_columns_not_promoted"])
    print("\n  rows created:")
    print("    model versions                  : %d" % rep["model_versions_created"])
    print("    forecasts                       : %d" % rep["forecast_rows_created"])
    print("    strategy evaluations            : %d" % rep["strategy_evaluations_created"])
    print("    signals                         : %d  (%d spread, %d total)"
          % (rep["signals_created"], rep["signals_spread"], rep["signals_total"]))
    print("    voided picks carried as events  : %d" % rep["voided_rows_migrated"])
    if "grade_snapshots_backfilled" in rep:
        print("    grade snapshots reconstructed   : %d of %d team-weeks (%d had no"
              % (rep["grade_snapshots_backfilled"], rep["grade_team_weeks_seen"],
                 rep["grade_team_weeks_without_a_kickoff"]))
        print("                                       scheduled game to date them by)")
    if rep.get("signals_repaired"):
        print("    derived columns restored on   : %d signal(s)"
              % rep["signals_repaired"])
    if "pick_rows_backfilled" in rep:
        print("    picks_log rows given V2 columns : %d  (new columns only; the"
              % rep["pick_rows_backfilled"])
        print("                                        legacy result is untouched)")
    if rep["rows_skipped"]:
        print("    SKIPPED                         : %d  %s"
              % (rep["rows_skipped"], rep["skip_reasons"]))

    print("\n  where the new results disagree with the legacy ones:")
    print("    locked-line vs legacy           : %d" % rep["locked_vs_legacy_differences"])
    print("    side-preserving close vs legacy : %d" % rep["close_vs_legacy_differences"])
    if rep["examples_close_vs_legacy"]:
        print("\n  A difference here is the side-recomputation bug, made visible:")
        print("  the legacy value graded a side derived from the model number, not")
        print("  the side that was published. Examples:")
        for e in rep["examples_close_vs_legacy"][:5]:
            print("    %-42s %-6s side %-16s legacy %s  side-preserving %s"
                  % (e["game"][:42], e["market"], str(e["side"])[:16],
                     e["legacy_close_based"], e["side_preserving_close"]))
    if rep["examples_locked_vs_legacy"]:
        print("\n  And where the number locked differs from the number it closed at:")
        for e in rep["examples_locked_vs_legacy"][:5]:
            print("    %-38s locked %-6s closed %-6s actual %-5s  legacy %s  locked %s"
                  % (e["game"][:38], e["locked_line"], e["closing_line"], e["actual"],
                     e["legacy_close_based"], e["locked_line_result"]))
    print("\n  table counts before: %s" % json.dumps(counts_before))
    if counts_after:
        print("  table counts after : %s" % json.dumps(counts_after))


def fixture_test():
    """
    Build a miniature legacy database and migrate it. Returns exit status.

    §25.7. Runs in CI, needs no production data, and asserts the properties that
    matter: the original row survives, the locked result is right, the legacy
    value is preserved, a mismatch is reported, a void stays void, an unknown
    price stays unknown, and applying twice changes nothing.
    """
    import tempfile
    p = 0
    f = 0

    def ok(name, cond, detail=""):
        nonlocal p, f
        if cond:
            p += 1
            print("  [PASS] %s" % name)
        else:
            f += 1
            print("  [FAIL] %s%s" % (name, (" — " + str(detail)) if detail else ""))

    path = os.path.join(tempfile.mkdtemp(), "legacy.db")
    conn = db.connect(path)
    # HOME published at +2.5. Closed +3.5. Home won by 3.
    # Won the wager; lost at the close. The legacy grader, recomputing the side
    # from model_margin(6.0) - close(3.5) > 0, also says HOME and so says L.
    conn.execute(
        "INSERT INTO games (game_id, sport, season, week, home_team, away_team,"
        " kickoff, home_score, away_score, neutral_site, home_div, away_div)"
        " VALUES ('m1','cfb',2026,1,'H','A','2026-09-05T18:00:00.000Z',24,21,0,'fbs','fbs')")
    conn.execute("INSERT INTO lines (game_id, provider, home_margin, total)"
                 " VALUES ('m1','Test',3.5,44.0)")
    conn.execute(
        "INSERT INTO picks_log (game_id, sport, season, week, home_team, away_team,"
        " kickoff, published_at, config_label, model_margin, market_margin_at_pick,"
        " model_total, market_total_at_pick, ats_pick, ou_pick, ml_pick,"
        " closing_margin, closing_total, actual_margin, actual_total,"
        " ats_result, ou_result, graded_at)"
        " VALUES ('m1','cfb',2026,1,'H','A','2026-09-05T18:00:00.000Z',"
        " '2026-09-03T12:00:00+00:00','legacy-cfg',6.0,2.5,50.0,44.0,'H','OVER','H',"
        " 3.5,44.0,3,45,'L','W','2026-09-06T00:00:00+00:00')")
    conn.execute("INSERT INTO picks_voided (game_id, payload, voided_at, void_reason)"
                 " VALUES ('m9','{}','2026-09-01T00:00:00+00:00','a test void')")
    conn.commit()

    before = dict(conn.execute("SELECT * FROM picks_log WHERE game_id='m1'").fetchone())
    rows, rep = plan(conn, "cfb")
    apply_migration(conn, rows, rep)
    after = dict(conn.execute("SELECT * FROM picks_log WHERE game_id='m1'").fetchone())

    # EVERY COLUMN THAT EXISTED BEFORE V2 MUST BE BYTE-IDENTICAL. The migration is
    # allowed to fill the columns Phase 0A added and nothing else; comparing the
    # whole row would fail for the right reason and hide the wrong one.
    V2_COLUMNS = {"ats_result_at_pick", "ats_result_at_close", "ou_result_at_pick",
                  "ou_result_at_close", "ats_price_at_pick", "ou_price_at_pick",
                  "ats_closing_price", "ou_closing_price", "grading_version"}
    legacy_before = {k: v for k, v in before.items() if k not in V2_COLUMNS}
    legacy_after = {k: v for k, v in after.items() if k not in V2_COLUMNS}
    changed = [k for k in legacy_before if legacy_before[k] != legacy_after[k]]
    ok("every pre-V2 column on the original row is unchanged", not changed, changed)
    ok("...and the only columns written are the ones V2 added",
       {k for k in after if before.get(k) != after.get(k)} <= V2_COLUMNS,
       {k for k in after if before.get(k) != after.get(k)} - V2_COLUMNS)
    sig = {r["market"]: dict(r) for r in conn.execute(
        "SELECT * FROM signal_log WHERE game_id='m1'")}
    ok("a spread signal was created", "spread" in sig)
    ok("...graded W at the line it was locked at",
       sig["spread"]["locked_result"] == "W", sig["spread"]["locked_result"])
    ok("...and L at the line it closed at",
       sig["spread"]["close_result"] == "L", sig["spread"]["close_result"])
    ok("the legacy column still says what it always said", after["ats_result"] == "L")
    ok("the difference is counted in the report",
       rep["locked_vs_legacy_differences"] >= 1, rep["locked_vs_legacy_differences"])
    ok("no price was invented", sig["spread"]["price"] is None)
    ok("...so no profit is claimed", sig["spread"]["profit_units"] is None)
    ok("CLV is recorded from the side that was taken",
       abs(sig["spread"]["line_clv"] - 1.0) < 1e-9, sig["spread"]["line_clv"])
    ok("the moneyline column did not become a wager",
       conn.execute("SELECT COUNT(*) c FROM signal_log WHERE market='moneyline'"
                    ).fetchone()["c"] == 0)
    ok("a total signal was created separately", "total" in sig)
    ok("the void survived as an event",
       conn.execute("SELECT COUNT(*) c FROM v2_void_events").fetchone()["c"] == 1)
    ok("the forecast is marked legacy provenance",
       conn.execute("SELECT provenance_quality FROM forecast_log"
                    ).fetchone()["provenance_quality"] == "legacy")
    ok("the migration recorded itself", already_applied(conn) is not None)
    back = dict(conn.execute("SELECT * FROM picks_log WHERE game_id='m1'").fetchone())
    ok("the historical pick row got its V2 columns",
       back["ats_result_at_pick"] == "W" and back["ats_result_at_close"] == "L",
       (back["ats_result_at_pick"], back["ats_result_at_close"]))
    ok("...and its legacy result is still the legacy result",
       back["ats_result"] == "L")
    ok("...and the pick itself did not move",
       (back["ats_pick"], back["market_margin_at_pick"], back["model_margin"])
       == (before["ats_pick"], before["market_margin_at_pick"], before["model_margin"]))

    n1 = conn.execute("SELECT COUNT(*) c FROM signal_log").fetchone()["c"]
    rows2, rep2 = plan(conn, "cfb")
    apply_migration(conn, rows2, rep2)
    n2 = conn.execute("SELECT COUNT(*) c FROM signal_log").fetchone()["c"]
    ok("running it twice changes nothing", n1 == n2, "%d then %d" % (n1, n2))

    print("\n  %d passed, %d failed" % (p, f))
    return 1 if f else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--db", default=db.DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report")
    ap.add_argument("--fixture-test", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-run even though this migration is already recorded")
    args = ap.parse_args()

    if args.fixture_test:
        print("── migration fixture test ──")
        sys.exit(fixture_test())

    conn = db.connect(args.db)
    prev = already_applied(conn)
    if prev and not args.force:
        print("%s was already applied at %s." % (MIGRATION_ID, prev))
        print("It is idempotent, so re-running is safe — pass --force to do it anyway.")
        sys.exit(0)

    counts_before = table_counts(conn)
    try:
        slope, r, n = db.verify_conventions(conn, args.sport)
        print("  sign convention: slope %.3f on %d games (near +1 is correct)" % (slope, n))
    except Exception as e:                         # noqa: BLE001 - reported, not fatal
        print("  WARNING: could not verify the sign convention — %s" % e)

    rows, rep = plan(conn, args.sport)
    counts_after = None
    if args.apply:
        dest = backup(args.db)
        print("  backup written to %s" % dest)
        apply_migration(conn, rows, rep, src_hash=source_hash(args.db))
        counts_after = table_counts(conn)
    print_report(rep, counts_before, counts_after)

    if args.report:
        path = args.report if os.path.isabs(args.report) else os.path.join(ROOT, args.report)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(rep, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print("\n  report written to %s" % path)

    if not args.apply:
        print("\nDRY RUN. Nothing was written. Re-run with --apply.")


if __name__ == "__main__":
    main()
