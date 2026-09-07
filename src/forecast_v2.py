"""
forecast_v2.py — the Champion, wrapped rather than rewritten, plus a registry.

The Champion is `engine.Model` with the shipped config, and this module CALLS it
rather than reimplementing its arithmetic. That is the whole point of an adapter
here: the proven path stays the proven path, and V2 gets a common interface to
put challengers beside it without a rewrite standing between the two.

A FORECAST IS NOT A BET
-----------------------
Everything a model has an opinion about is recorded, including games a strategy
will decline. Deleting a forecast because nothing was wagered on it is how a
record silently becomes "the games we liked", and it is the reason
`strategy_evaluations` exists as a separate table rather than as an absence.

A MODEL VERSION IS IMMUTABLE
----------------------------
`register_model` refuses to redefine an existing version whose effective config
hash has changed. If it allowed it, every forecast already filed under that
version would become a claim about a model that no longer exists — and the
claim would still look perfectly consistent, because nothing in the row would
have changed.
"""

import datetime as dt
import json
import os

import db
import engine
import features_v2
import horizons
import provenance

CHAMPION_MODEL_ID = "champion-grade"

ROLE_CHAMPION = "champion"
ROLE_CHALLENGER = "challenger"
ROLE_BASELINE = "baseline"
ROLE_RETIRED = "retired"

REGISTRY_COLUMNS = ["model_version", "model_id", "role", "experiment_id", "git_sha",
                    "config_json", "config_hash", "feature_schema_version",
                    "created_at", "retired_at", "notes"]

FORECAST_COLUMNS = ["forecast_id", "sport", "game_id", "model_version",
                    "feature_snapshot_id", "market_snapshot_id", "horizon",
                    "horizon_target_at", "generated_at", "horizon_delta_seconds",
                    "snapshot_status", "pred_home_margin", "pred_total",
                    "home_win_prob", "home_cover_prob", "over_prob",
                    "margin_uncertainty", "total_uncertainty", "borrowed_fallback",
                    "provenance_quality", "created_by_run", "created_at"]


class VersionConflict(Exception):
    """A model version was redefined with different behaviour."""


def register_model(conn, *, model_version, model_id, role, config,
                   feature_schema_version=features_v2.CHAMPION_FEATURES_V1,
                   experiment_id=None, notes=None, git_sha=None, commit=True):
    """
    Record a model version, or confirm the existing one still means the same thing.

    Raises VersionConflict when a version already exists under a DIFFERENT
    effective config hash. That is not pedantry: a config edit that silently
    redefines a live version turns every forecast filed under it into a statement
    about a model nobody can reconstruct, and nothing about the stored rows would
    look wrong.

    The hash is of the FULLY MERGED config, so a change to an engine default
    counts as a change to the model even when the config file did not move.
    """
    chash = provenance.config_hash(config)
    merged = provenance.merged_config(config)
    if git_sha is None:
        git_sha, _dirty = provenance.git_sha()
    row = conn.execute("SELECT * FROM model_registry WHERE model_version=?",
                       (model_version,)).fetchone()
    if row is not None:
        if row["config_hash"] != chash:
            raise VersionConflict(
                "model version %r already exists with config hash %s; this config "
                "hashes to %s. A behaviour change needs a NEW version — redefining "
                "this one would rewrite the meaning of every forecast already "
                "recorded under it." % (model_version, row["config_hash"][:12],
                                        chash[:12]))
        return dict(row)

    rec = {
        "model_version": model_version, "model_id": model_id, "role": role,
        "experiment_id": experiment_id, "git_sha": git_sha or "unknown",
        "config_json": provenance.canonical_json(merged), "config_hash": chash,
        "feature_schema_version": feature_schema_version,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "retired_at": None, "notes": notes,
    }
    conn.execute("INSERT INTO model_registry (%s) VALUES (%s)"
                 % (",".join(REGISTRY_COLUMNS),
                    ",".join(":" + c for c in REGISTRY_COLUMNS)), rec)
    if commit:
        conn.commit()
    return rec


def champion_version(config, *, date=None, conn=None):
    """
    The Champion's version string: C0-<date>.<config hash prefix>.

    The hash is in the name on purpose. Two configs that differ cannot share a
    version string even by accident, and a version string alone is enough to see
    that something moved.

    THE DATE IS THE DAY THE CONFIG FIRST APPEARED, NOT TODAY. It was today, and
    that minted a new Champion version at every midnight UTC from an unchanged
    config: the same model, forecasting under a fresh version string each morning,
    scattering the prospective record across as many versions as days. A version
    exists to hold a record together, so when `conn` is given the registry is
    asked first and an existing version for this exact config hash is reused.

    Without `conn` it still stamps today, which is right for the one case that
    has no registry to ask: minting a version for a config nobody has seen.
    """
    chash = provenance.config_hash(config)
    if conn is not None:
        row = conn.execute(
            "SELECT model_version FROM model_registry"
            " WHERE role='champion' AND config_hash=? AND retired_at IS NULL"
            " ORDER BY created_at LIMIT 1", (chash,)).fetchone()
        if row:
            return row["model_version"]
    stamp = date or dt.datetime.now(dt.timezone.utc).strftime("%Y.%m.%d")
    return "C0-%s.%s" % (stamp, chash[:8])


def dedupe_champions(conn, *, commit=True):
    """
    Retire duplicate Champion versions that share one config. -> [retired]

    They should not exist, and they did: the version string carried today's date,
    so an unchanged config minted a fresh Champion every midnight UTC. RETIRED,
    not deleted — the forecasts filed under a duplicate are real forecasts by an
    identical model, and erasing the row would orphan them. Retiring says "this
    is not the version to file under any more", which is exactly true.

    The EARLIEST row for a config hash wins, because that is when the config
    actually started.
    """
    seen, retired = {}, []
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for r in conn.execute(
            "SELECT model_version, config_hash, created_at FROM model_registry"
            " WHERE role='champion' AND retired_at IS NULL"
            " ORDER BY created_at, model_version"):
        keep = seen.get(r["config_hash"])
        if keep is None:
            seen[r["config_hash"]] = r["model_version"]
            continue
        # ROLE TOO, not just the timestamp. `retired` is a role this registry
        # already uses for superseded champions, and a row left at role='champion'
        # with a retirement date reads as a second live Champion to anything that
        # groups by role — which is exactly how the page rendered it.
        conn.execute(
            "UPDATE model_registry SET retired_at=?, role='retired',"
            " notes=COALESCE(notes,'') || ? WHERE model_version=?",
            (now, " | superseded by %s: same config, a version string that "
                  "carried the calendar rather than the config" % keep,
             r["model_version"]))
        retired.append((r["model_version"], keep))
    if retired and commit:
        conn.commit()
    return retired


class ChampionAdapter:
    """
    The shipped grade model, behind the V2 interface.

    It holds one `engine.Model` and walks it forward exactly as `predict.generate`
    does, so the quality-points state a forecast sees is the state the production
    path would have had. It does not recompute a rating of its own.
    """

    model_id = CHAMPION_MODEL_ID

    def __init__(self, config, grades, stats=None):
        self.config = dict(config)
        self.model = engine.Model(dict(config), grades, stats)
        self._season = None

    def advance_to(self, games, upto_game_id):
        """
        Replay games in order, observing every finished one, stopping BEFORE the
        target game. This is what gives the rater its accrued quality points
        without ever observing the game being predicted.
        """
        for g in games:
            if g["game_id"] == upto_game_id:
                return g
            if g["season"] != self._season:
                self._season = g["season"]
                self.model.new_season(g["season"])
            self.model.observe(g)
        return None

    def state_for(self, game):
        """The rater's accrued state for these two teams, for the payload."""
        rater = getattr(self.model, "rater", None)
        rec = getattr(rater, "record", None)
        if rec is None:
            return None
        try:
            return {"home": rec(game["home_team"]), "away": rec(game["away_team"])}
        except Exception:                          # noqa: BLE001 - a rater without a record
            return None

    def forecast(self, game):
        """-> the normalized model output dict."""
        if game["season"] != self._season:
            self._season = game["season"]
            self.model.new_season(game["season"])
        p = self.model.predict(game)
        import predict as _predict
        margin = p["pred_margin"]
        wp = _predict.margin_to_win_prob(margin, game.get("sport") or "cfb")
        return {
            "pred_home_margin": margin,
            "pred_total": p.get("pred_total"),
            "home_win_prob": wp,
            # The Champion has no calibrated cover or total probability and does
            # not pretend to. None is the honest value, and the strategy layer
            # refuses to price anything from a probability that was never fitted.
            "home_cover_prob": None,
            "over_prob": None,
            "margin_uncertainty": None,
            "total_uncertainty": None,
            "borrowed_fallback": 1 if p.get("borrowed") else 0,
        }


class _ModelAdapter:
    """Wraps a models_v2 model in the interface make_forecast expects."""

    def __init__(self, model):
        self.model = model

    def state_for(self, game):
        return None                    # challengers read the payload, not a rater

    def forecast(self, game):
        return self.model.predict(self._payload)

    def with_payload(self, payload):
        self._payload = payload
        return self


def load_challengers(conn):
    """
    Every registered challenger, rehydrated from its artifact. -> [(version, model)]

    A challenger whose artifact is missing or unreadable is SKIPPED WITH A
    MESSAGE rather than silently omitted: a shadow model that quietly stops
    forecasting accumulates a record of the games it happened to be alive for.
    """
    import json as _json
    out = []
    try:
        import models_v2
    except Exception as e:                         # noqa: BLE001
        print("  challengers unavailable: %s" % e)
        return out
    classes = {"residual-grade": models_v2.ResidualGrade,
               "matchup-residual": models_v2.MatchupResidual,
               "form-quality": models_v2.FormQuality,
               "totals-scoring": models_v2.TotalsScoring,
               "market-baseline": models_v2.MarketBaseline}
    for r in conn.execute(
            "SELECT model_version, model_id, config_json FROM model_registry"
            # BASELINE TOO. C1 is not a challenger in the promotion sense and it
            # must still forecast every game, or there is nothing to compare a
            # challenger's error against at the same instant.
            " WHERE role IN ('challenger','baseline') AND retired_at IS NULL"
            " ORDER BY model_version"):
        cls = classes.get(r["model_id"])
        if cls is None:
            print("  challenger %s: no class for model_id %r — skipped"
                  % (r["model_version"], r["model_id"]))
            continue
        if r["model_id"] == "market-baseline":
            out.append((r["model_version"], cls()))
            continue
        # THE REGISTRY IS THE ARTIFACT, not a pointer to one. `config_json` holds
        # the fitted coefficients that `register_model` hashed to establish this
        # version's identity, so rehydrating from it is the only way to be certain
        # the model that forecasts is the model the version NAMES.
        #
        # It also fixes a silent production failure: the fitted artifacts were
        # written to output/, output/ is in .gitignore, and the Actions cache
        # carries data/ and not output/. Every scheduled run therefore found no
        # artifact and skipped every challenger — the exact "accumulates a record
        # of the games it happened to be alive for" failure this function's
        # docstring warns about, happening on every run since they were fitted.
        try:
            art = _json.loads(r["config_json"] or "{}")
        except ValueError as e:
            print("  challenger %s: registry config unreadable — %s"
                  % (r["model_version"], e))
            continue
        if not cls.is_fitted(art):
            print("  challenger %s: registry row carries no fitted parameters "
                  "— skipped" % r["model_version"])
            continue
        out.append((r["model_version"], cls(art)))
    return out


def make_forecast(conn, *, sport, game, adapter, model_version, horizon,
                  generated_at, feature_snapshot=None, run_id=None,
                  provenance_quality=provenance.COMPLETE, commit=True):
    """
    Build, classify and store one forecast. -> forecast row dict

    The classification is stored whatever it says. A run that fired late is
    recorded as late; it is never relabelled as the horizon it missed, because
    the comparability of forecasts across games is the only thing a standardized
    horizon buys and a silent relabel spends it.
    """
    fs = feature_snapshot
    if fs is None:
        fs = features_v2.build_feature_snapshot(
            conn, sport=sport, game_id=game["game_id"], as_of=generated_at,
            model_version=model_version,
            champion_state=adapter.state_for(game) if adapter else None)
    if fs is None:
        return None
    features_v2.store(conn, fs, commit=False)

    cls = horizons.classify(kickoff=game["kickoff"], generated_at=generated_at,
                            horizon=horizon)
    # A challenger reads the feature payload; the Champion adapter reads its own
    # walked-forward rater. `with_payload` exists so both satisfy one call.
    if hasattr(adapter, "with_payload"):
        adapter = adapter.with_payload(fs["payload"])
    out = adapter.forecast(game)
    fid = provenance.stable_id("forecast", {
        "g": game["game_id"], "m": model_version, "h": horizon,
        "t": generated_at, "f": fs["feature_snapshot_id"]})
    row = {
        "forecast_id": fid, "sport": sport, "game_id": game["game_id"],
        "model_version": model_version,
        "feature_snapshot_id": fs["feature_snapshot_id"],
        "market_snapshot_id": fs["payload"].get("market_snapshot_id"),
        "horizon": horizon, "horizon_target_at": cls["target_at"],
        "generated_at": generated_at,
        "horizon_delta_seconds": cls["delta_seconds"],
        "snapshot_status": cls["status"] or "unknown",
        "provenance_quality": provenance_quality,
        "created_by_run": run_id, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    row.update({k: out.get(k) for k in (
        "pred_home_margin", "pred_total", "home_win_prob", "home_cover_prob",
        "over_prob", "margin_uncertainty", "total_uncertainty", "borrowed_fallback")})
    conn.execute("INSERT OR IGNORE INTO forecast_log (%s) VALUES (%s)"
                 % (",".join(FORECAST_COLUMNS),
                    ",".join(":" + c for c in FORECAST_COLUMNS)),
                 {k: row[k] for k in FORECAST_COLUMNS})
    if commit:
        conn.commit()
    row["_feature_snapshot"] = fs
    return row


def forecasts_for(conn, game_id, *, model_version=None, horizon=None):
    q = "SELECT * FROM forecast_log WHERE game_id=?"
    args = [game_id]
    if model_version:
        q += " AND model_version=?"
        args.append(model_version)
    if horizon:
        q += " AND horizon=?"
        args.append(horizon)
    return [dict(r) for r in conn.execute(q + " ORDER BY generated_at", args)]


def run_snapshots(conn, *, sport="cfb", config, now=None, horizons_wanted=None,
                  season=None, model_version=None, strategy=None,
                  official=False, run_id=None, verbose=True):
    """
    Take every forecast that is due right now, and evaluate the official ones.

    -> report dict

    `official=False` is Phase 1A's shadow mode: forecasts, feature snapshots,
    market snapshots and strategy evaluations are all written, and signals are
    marked is_official=0. Nothing competes with the legacy pick path until the
    cutover, so the two cannot both publish.

    THE WALK-FORWARD ORDER IS THE SAME AS PRODUCTION. Games are replayed in
    chronological order and observed as they finish, so the rater arrives at an
    upcoming game with exactly the accrued state `predict.generate` would have
    had. It stops before every game it forecasts and never observes one it is
    about to predict.
    """
    import backtest
    import pro_models
    import signals as sig

    now = now or dt.datetime.now(dt.timezone.utc).isoformat()
    strategy = strategy or sig.STRATEGY_V0
    wanted = horizons_wanted or horizons.ALL

    grades = backtest.load_grades(conn, sport)
    stats = pro_models.load_stats(conn, sport)
    games = backtest.load_games(conn, sport)
    if season is None:
        season = max(g["season"] for g in games)

    # Any duplicate minted by the old calendar-stamped version string is retired
    # before a version is chosen, so the choice cannot land on one of them.
    for _dup, _keep in dedupe_champions(conn):
        print("  retired duplicate champion %s (same config as %s)" % (_dup, _keep))
    model_version = model_version or champion_version(config, conn=conn)
    register_model(conn, model_version=model_version, model_id=CHAMPION_MODEL_ID,
                   role=ROLE_CHAMPION, config=config,
                   notes="the shipped grade model, frozen as the V2 Champion")

    adapter = ChampionAdapter(config, grades, stats)

    # EVERY REGISTERED CHALLENGER FORECASTS THE SAME GAMES, at the same instant,
    # from the same feature payload. That is what makes a later comparison PAIRED
    # rather than a comparison of two game sets.
    #
    # They are shadow without exception: `emit_signal` is only ever called for
    # the Champion below. A challenger accumulates a prospective record and is
    # promoted, if ever, by the checklist in §30 — never by a threshold.
    challengers = load_challengers(conn)
    rep = {"now": now, "model_version": model_version,
           "strategy_version": strategy["strategy_version"],
           "official": bool(official), "games_considered": 0, "forecasts": 0,
           "by_horizon": {}, "evaluations": 0, "signals": 0, "misses": 0,
           "declined": {}}

    season_seen = None
    for g in games:
        if g["season"] != season_seen:
            season_seen = g["season"]
            adapter.model.new_season(g["season"])
            adapter._season = g["season"]

        played = g["home_score"] is not None and g["away_score"] is not None
        if not played and g["season"] == season:
            due = horizons.due_horizons(kickoff=g["kickoff"], now=now,
                                        horizons=wanted)
            if due:
                rep["games_considered"] += 1
                fs = features_v2.build_feature_snapshot(
                    conn, sport=sport, game_id=g["game_id"], as_of=now,
                    model_version=model_version,
                    champion_state=adapter.state_for(g))
                for h in due:
                    row = make_forecast(
                        conn, sport=sport, game=g, adapter=adapter,
                        model_version=model_version, horizon=h, generated_at=now,
                        feature_snapshot=fs, run_id=run_id, commit=False)
                    if row is None:
                        continue
                    rep["forecasts"] += 1
                    rep["by_horizon"][h] = rep["by_horizon"].get(h, 0) + 1

                    for ch_version, ch_model in challengers:
                        ch_row = make_forecast(
                            conn, sport=sport, game=g, adapter=_ModelAdapter(ch_model),
                            model_version=ch_version, horizon=h, generated_at=now,
                            feature_snapshot=fs, run_id=run_id, commit=False)
                        if ch_row is not None:
                            rep["challenger_forecasts"] = \
                                rep.get("challenger_forecasts", 0) + 1
                    if h != strategy["official_horizon"]:
                        continue
                    evals = sig.evaluate(conn, forecast=row, payload=fs["payload"],
                                         strategy=strategy, now=now, commit=False)
                    rep["evaluations"] += len(evals)
                    for e in evals:
                        for code in e["_reasons"]:
                            rep["declined"][code] = rep["declined"].get(code, 0) + 1
                        s = sig.emit_signal(conn, evaluation=e, forecast=row,
                                            strategy=strategy,
                                            is_official=official, commit=False)
                        if s:
                            rep["signals"] += 1
            else:
                # A horizon whose window has closed with nothing in it is
                # RECORDED. An unrecorded gap is indistinguishable from a healthy
                # game, and can later be filled with a mislabelled forecast.
                for h in wanted:
                    if h in horizons.SHADOW_ONLY:
                        continue
                    if not horizons.missed(kickoff=g["kickoff"], now=now, horizon=h):
                        continue
                    have = conn.execute(
                        "SELECT COUNT(*) c FROM forecast_log WHERE game_id=?"
                        " AND model_version=? AND horizon=? AND snapshot_status=?",
                        (g["game_id"], model_version, h, horizons.ACCEPTED)
                    ).fetchone()["c"]
                    if not have and horizons.record_miss(
                            conn, game_id=g["game_id"], model_version=model_version,
                            horizon=h, kickoff=g["kickoff"], now=now):
                        rep["misses"] += 1
        adapter.model.observe(g)

    conn.commit()
    if verbose:
        print("  model %s  strategy %s  %s"
              % (model_version, strategy["strategy_version"],
                 "OFFICIAL" if official else "shadow"))
        print("  %d game(s) in a horizon window -> %d forecast(s) %s"
              % (rep["games_considered"], rep["forecasts"],
                 dict(sorted(rep["by_horizon"].items()))))
        print("  %d evaluation(s) -> %d signal(s)" % (rep["evaluations"], rep["signals"]))
        if rep["declined"]:
            top = sorted(rep["declined"].items(), key=lambda kv: -kv[1])[:5]
            print("  declined: %s" % ", ".join("%s x%d" % (k, v) for k, v in top))
        if rep["misses"]:
            print("  %d horizon(s) recorded as missed" % rep["misses"])
    return rep


def main():
    import argparse

    ap = argparse.ArgumentParser(
        description="Take every forecast that is due at a standardized horizon.")
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--config", default="config/cfb_grades.json")
    ap.add_argument("--season", type=int)
    ap.add_argument("--now", help="ISO time to pretend it is (for testing)")
    ap.add_argument("--horizons", help="comma-separated subset, e.g. T24,T2")
    ap.add_argument("--shadow", action="store_true",
                    help="write signals with is_official=0")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = (args.config if os.path.isabs(args.config)
                else os.path.join(root, args.config))
    with open(cfg_path) as fh:
        config = json.load(fh)
    conn = db.connect()

    # A dirty tree means the recorded SHA does not describe what ran, so the run
    # may take forecasts but not sign them as official. Said out loud rather than
    # silently downgraded.
    sha, dirty = provenance.git_sha(root)
    okay, why = provenance.official_ready(sha, dirty)
    official = (not args.shadow) and okay
    if not args.shadow and not okay:
        print("  NOT SIGNING OFFICIAL SIGNALS: %s" % why)

    run_snapshots(conn, sport=args.sport, config=config, season=args.season,
                  now=args.now,
                  horizons_wanted=(args.horizons.split(",") if args.horizons else None),
                  official=official,
                  run_id=os.environ.get("GITHUB_RUN_ID"))


if __name__ == "__main__":
    main()
