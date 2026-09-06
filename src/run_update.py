"""
run_update.py — the one command the scheduler runs. Does everything, touches nothing by hand.

  1. Refresh the current season (results, lines, rankings) from the free APIs
  2. Re-grade every completed game against the closing line
  3. Generate picks for games not yet played
  4. LOCK those picks into the append-only ledger, and grade any that finished
  5. Leave local JSON artifacts
  6. Render the public track record page
  7. Push picks + accuracy back to Google Sheets (if configured)

Steps 4 and 6 are what make the public record trustworthy: a pick is written
before kickoff and never revised, and the page is generated from that ledger
rather than hand-maintained.

Runs fine with no Google credentials at all — it just skips step 4 and writes
files. That means the pipeline can be verified end to end before any of the
Sheets plumbing exists.

Usage:
    python3 src/run_update.py --sport cfb
    python3 src/run_update.py --sport cfb --no-fetch        # rebuild from cache
    python3 src/run_update.py --sport cfb --sheet <id>      # also push to Sheets
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys

import backtest
import db
import ledger
import metrics
import predict
import publish
import research_export
import sync_grades

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output")

# ── the cutover ───────────────────────────────────────────────────────────────
#
# True: V2 writes the official signals, and the legacy `ledger.lock` path stops
# creating picks of its own. False: V2 runs in shadow and the legacy path is the
# official writer.
#
# ONE OF THEM, NEVER BOTH. Two pipelines independently publishing official picks
# for the same game is the single failure mode a transition must not have — the
# record would contain two opinions and no way to say which was the one that
# counted. So this is a switch and not two flags.
#
# The legacy ledger is still GRADED and still READ: every settled pick keeps its
# row, and the historical record it holds was carried into signal_log by
# migrate_v2 under strategy_version 'S-legacy'. What stops is new picks.
V2_OFFICIAL = os.environ.get("MODEL_V2_OFFICIAL", "1") not in ("0", "false", "no")


def refresh(sport, season):
    """Pull the current season fresh. Cached history is left alone."""
    script = os.path.join(ROOT, "src", "fetch_%s.py" % sport)
    if not os.path.exists(script):
        print("  no fetcher for %s — skipping refresh" % sport)
        return
    cmd = [sys.executable, script]
    if sport == "cfb":
        cmd += ["--seasons", str(season), "--refresh"]
    print("  $ %s" % " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        # A failed refresh must not destroy the run: cached data is still good,
        # and silently reporting success on stale data would be worse.
        print("  WARNING: refresh failed (using cached data)\n%s"
              % (r.stderr or r.stdout)[-800:])
    else:
        print("  refreshed.")


def season_grade(conn, sport, season, config):
    """
    Score every completed game this season, walk-forward.

    THE GRADES HAVE TO BE PASSED IN. Without them a `rater: grades` config produced
    an Elo prediction for every game and reported it as the model's own season
    grade — the number printed at step [3/9] and used to judge whether the film is
    working. engine.Model now refuses outright rather than substituting quietly.
    """
    games = backtest.load_games(conn, sport)
    cfg = dict(config)
    if cfg.get("rater") in ("grades", "blend"):
        cfg["_grades"] = backtest.load_grades(conn, sport)
        import pro_models
        cfg["_stats"] = pro_models.load_stats(conn, sport)
    preds = backtest.run(games, cfg, [season])
    return preds, metrics.evaluate(preds)


def validation_backtest(conn, sport, current_season, config):
    """
    Walk-forward replay of the most recent COMPLETE season that has film grades.

    This is the number the public page leads its evidence with, so it is derived
    here every run rather than read from a file somebody once made — and it carries
    its confidence interval, because a percentage without one invites exactly the
    reading the page spends a paragraph warning against.
    """
    row = conn.execute(
        "SELECT MAX(season) s FROM grades WHERE sport=? AND season < ?",
        (sport, current_season)).fetchone()
    season = row and row["s"]
    if not season:
        # Said out loud. CI restores a database holding only the CURRENT season's
        # grades — the historical ones were imported from a workbook on Grant's
        # machine — so this returns None there every run, and returning it quietly
        # is how the public page lost its whole evidence section without a word.
        print("  no completed season with grades in this database — using the "
              "committed validation summary instead (see data/validation/).")
        return None
    try:
        preds, m = season_grade(conn, sport, season, config)
    except Exception as e:                         # noqa: BLE001 - never block a run
        print("  WARNING: validation backtest failed — %s" % e)
        return None
    if not m.get("ats_pct"):
        return None
    lo, hi = m["ats_ci95"]
    out = {"season": season, "n": m["ats_n"], "ats_pct": m["ats_pct"],
           "roi": m["roi"], "ci_lo": lo, "ci_hi": hi,
           "vs_baseline": round(m.get("su_edge_vs_baseline") or 0.0, 2),
           "calib_slope": m.get("calib_slope"), "mae": m.get("mae")}

    # AND THE SAME REPLAY OVER THE GAMES THE MODEL WOULD ACTUALLY HAVE OFFERED.
    # best_bets marks everything outside +/-BLOWOUT_LINE `no_bet` and gives it no
    # stake, so the headline above is the record of a strategy nobody runs: it bets
    # a hundred and twenty games a season that the board declines. Both are carried
    # because either one alone is a half-truth -- the wide number understates what
    # the model does, and the narrow one, published alone, would look like the
    # blowouts had been dropped for being losers.
    import best_bets
    offered = [p for p in preds
               if p.get("market_margin") is not None
               and abs(p["market_margin"]) <= best_bets.BLOWOUT_LINE]
    if offered:
        om = metrics.evaluate(offered)
        if om.get("ats_pct"):
            out.update({"offered_n": om["ats_n"], "offered_ats_pct": om["ats_pct"],
                        "offered_roi": om["roi"],
                        "blowout_line": best_bets.BLOWOUT_LINE})
    return out


def resolve_config(explicit, sport):
    """
    Which config this run uses, in one place.

    Prefers the grade model, because that is the one with a measured edge, and
    falls back to Elo only if no grade config exists for the sport.
    """
    if explicit:
        return explicit if os.path.isabs(explicit) else os.path.join(ROOT, explicit)
    for name in ("config/%s_grades.json" % sport, "config/%s_elo.json" % sport):
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            return path
    return os.path.join(ROOT, "config/%s_grades.json" % sport)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", default="cfb")
    # No hard-coded default. It was "config/cfb_elo.json", so any run that did
    # not name a config used the Elo rater -- 50.52% ATS against the grade
    # model's 54.66%, i.e. the one model measured to have no edge. The scheduled
    # job passes --config explicitly and was never affected, so nothing was
    # published wrong; but every manual run silently produced Elo picks and
    # looked identical while doing it. A default that quietly selects the worst
    # model is a trap whether or not it has been sprung yet.
    # The config is now derived from --sport and announced on every run.
    ap.add_argument("--config", default=None,
                    help="defaults to config/<sport>_grades.json, "
                         "falling back to config/<sport>_elo.json")
    ap.add_argument("--season", type=int)
    ap.add_argument("--sheet", help="Google Sheet ID to write to")
    ap.add_argument("--picks-tab", default="Model Picks")
    ap.add_argument("--accuracy-tab", default="Model Accuracy")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--grades-sheet",
                    help="Google Sheet ID holding the weekly film grades "
                         "(or set MODEL_GRADES_SHEET_ID)")
    ap.add_argument("--min-edge", type=float, default=0.0)
    args = ap.parse_args()

    started = dt.datetime.now(dt.timezone.utc)
    print("The Model — update run %s UTC | sport=%s" % (started.strftime("%Y-%m-%d %H:%M"), args.sport))

    cfg_path = resolve_config(args.config, args.sport)
    config = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
    if not config:
        print("  NOTE: no config at %s — using un-optimized defaults." % cfg_path)
    # Say which model is about to run, every time. The rater is the single most
    # consequential setting here and it used to be invisible.
    print("  config: %s   rater=%s"
          % (os.path.relpath(cfg_path, ROOT), config.get("rater", "(default)")))

    conn = db.connect()
    season = args.season or (dt.date.today().year if dt.date.today().month >= 7
                             else dt.date.today().year - 1)

    # ── step 0: pull the film grades straight from the live sheet ──
    # This is what makes the loop hands-off. Grant grades film; nothing else
    # requires a human. If no sheet is configured the run continues on whatever
    # grades are already in the database rather than failing.
    print("\n[1/9] sync film grades from Google Sheets")
    grades_sheet = args.grades_sheet or os.environ.get("MODEL_GRADES_SHEET_ID")
    if not grades_sheet:
        n_existing = conn.execute(
            "SELECT COUNT(*) c FROM grades WHERE sport=?", (args.sport,)).fetchone()["c"]
        print("  skipped — no --grades-sheet / MODEL_GRADES_SHEET_ID set.")
        print("  using %d grade rows already in the database." % n_existing)
    else:
        try:
            total, tabs, report = sync_grades.sync(
                conn, grades_sheet, args.sport, season, verbose=False)
            print("  synced %d grade rows from %d tab(s)" % (total, tabs))
            # Name every tab and the week it landed on. "1 weekly tab" was true and
            # useless: it could not distinguish a sheet whose live edits are being
            # read from one where they are silently ignored.
            for r in report:
                print("    %-18s -> week %-3s %5d rows  [%s]%s"
                      % (r["tab"], r["week"], r["rows"], r["kind"],
                         "  " + r["note"] if r["note"] else ""))
            sync_grades.write_sync_report(report, how_many=total)
            checked, bad = sync_grades.iw.verify_formula(conn, args.sport, season)
            print("  formula check: %d team-weeks, %d mismatches%s"
                  % (checked, bad, "" if bad == 0 else "  <-- INVESTIGATE"))
        except SystemExit as e:
            print("  skipped — %s" % e)
        except Exception as e:
            # Stale grades still produce picks; a failed sync must not abort the run.
            print("  FAILED: %s: %s (continuing on existing grades)" % (type(e).__name__, e))

    print("\n[2/9] refresh %s %d" % (args.sport, season))
    if args.no_fetch:
        print("  skipped (--no-fetch)")
    else:
        refresh(args.sport, season)

    print("\n[3/9] grade completed games")
    preds, m_season = season_grade(conn, args.sport, season, config)
    if m_season.get("n_games"):
        print(metrics.format_report(m_season, "%s %d season to date" % (args.sport, season)))
    else:
        print("  no completed games with lines yet this season.")

    # The validation backtest, regenerated every run. It used to be a file somebody
    # produced by hand once: `publish` read output/cfb_backtest_2025.json, nothing in
    # CI ever wrote it, and so the public page silently omitted the model's ONLY
    # piece of validation evidence. A renderer with no producer, which is the same
    # failure as a reader with no writer and just as quiet.
    val = validation_backtest(conn, args.sport, season, config)
    if val:
        vp = os.path.join(OUT, "%s_backtest_%d.json" % (args.sport, val["season"]))
        os.makedirs(OUT, exist_ok=True)
        with open(vp, "w") as fh:
            json.dump(val, fh, indent=2)
        print("  validation season %d: %.2f%% ATS on %d games (95%% CI %.1f-%.1f)"
              % (val["season"], val["ats_pct"], val["n"], val["ci_lo"], val["ci_hi"]))

    print("\n[4/9] generate picks")
    picks = predict.generate(conn, args.sport, config, season=season)
    picks = [p for p in picks if p["edge"] is None or abs(p["edge"]) >= args.min_edge]
    print("  %d upcoming games" % len(picks))

    print("\n[5/9] lock picks + grade finished ones")

    # REFUSE to lock if the configured rater has nothing to rate with.
    #
    # GradeRater returns None when a team has no grades and the engine quietly
    # falls back to Elo. That is the right behaviour for one missing team and
    # completely wrong for all of them: on a runner with an empty grades table
    # every pick is an Elo pick, and the ledger is APPEND-ONLY, so they are
    # recorded permanently as this model's picks. It happened -- 888 of them,
    # published to the public track record, from the model measured to have no
    # edge at all. Nothing failed, because substituting a different model is not
    # an error anywhere in the code.
    if config.get("rater") == "grades":
        n_grades = conn.execute(
            "SELECT COUNT(*) n FROM grades WHERE sport=? AND season=?",
            (args.sport, season)).fetchone()["n"]
        if n_grades == 0:
            print("  REFUSING TO LOCK — the config asks for the grade model and")
            print("  there are no %d grades in the database. Every pick would be" % season)
            print("  an Elo fallback recorded permanently as a grade-model pick.")
            print("  Sync the film grades (MODEL_GRADES_SHEET_ID) and re-run.")
            picks = []
        else:
            print("  %d grade rows for %d — grade model is live." % (n_grades, season))

    label = "%s@%s" % (os.path.basename(cfg_path).replace(".json", ""),
                       started.strftime("%Y-%m-%d"))
    if V2_OFFICIAL:
        # AFTER THE CUTOVER the legacy path grades and reports; it does not lock.
        # Its settled picks stay exactly where they are and are still graded as
        # games finish, so nothing already recorded moves. New official picks are
        # V2 signals, taken at T2 with a strategy version attached.
        locked, already_started, deferred = 0, 0, 0
        graded = ledger.grade(conn, args.sport, now=started)
        print("  legacy ledger: no new picks (V2 is the official writer); graded %d"
              % graded)
    else:
        locked, already_started, deferred = ledger.lock(
            conn, args.sport, picks, label, now=started)
        graded = ledger.grade(conn, args.sport, now=started)
        print("  locked %d new pick(s); skipped %d already under way; graded %d"
              % (locked, already_started, graded))
        print("  held back %d pick(s) beyond the %d-day window — they are locked closer"
              % (deferred, ledger.LOCK_WINDOW_DAYS))
        print("  to kickoff, from that week's grades and a line near its close.")
    missed, examples = ledger.missed_locks(conn, args.sport, season, now=started)
    if missed:
        print("  WARNING: %d priced game(s) kicked off with NO pick locked." % missed)
        print("  A pick that was never recorded leaves the record looking better")
        print("  than the model was. Most recent:")
        for e in examples:
            print("    wk %-3s %s  %s @ %s"
                  % (e["week"], (e["kickoff"] or "")[:10], e["away_team"], e["home_team"]))
    stale, stale_ex = ledger.stale_locks(conn, args.sport, season, now=started)
    if stale:
        print("  WARNING: %d pick(s) are locked far outside the %d-day window."
              % (stale, ledger.LOCK_WINDOW_DAYS))
        print("  They cannot be replaced — lock() writes INSERT OR IGNORE, so these")
        print("  win over every better pick the model makes between now and kickoff.")
        print("  Retire them with:  python3 src/void_picks.py --sport %s --before "
              "<date> --reason '...' --apply" % args.sport)
        for e in stale_ex:
            print("    wk %-3s kicks %s, locked %s  %s @ %s"
                  % (e["week"], (e["kickoff"] or "")[:10], (e["published_at"] or "")[:10],
                     e["away_team"], e["home_team"]))
    rec = ledger.record(conn, args.sport)
    if rec.get("ats_pct") is not None:
        lo, hi = rec["ats_ci95"]
        print("  LIVE ledger: %d-%d-%d  %.2f%% ATS  %+.2fu  ROI %+.2f%%  (95%% CI %.1f-%.1f)"
              % (rec["ats_w"], rec["ats_l"], rec["ats_push"], rec["ats_pct"],
                 rec["units"], rec["roi"], lo, hi))
    else:
        print("  LIVE ledger: no graded picks yet")

    # THE MIGRATION RUNS ITSELF, and it has to.
    #
    # It was a one-off command a human was supposed to remember, and the first
    # production run after the cutover proved why that is not good enough: the
    # code was V2-official, `MODEL_V2_OFFICIAL` was on, the legacy writer had
    # stood down — and `picks_log.ats_result_at_pick` was NULL on every row and
    # `signal_log` held no legacy signals, because nothing had ever migrated that
    # database. The public page would have quietly fallen back to the legacy
    # headline: the close-based, side-recomputed record this whole project exists
    # to replace, shown under a page that now claims otherwise.
    #
    # It is idempotent by construction -- `v2_migrations` has a primary key and a
    # test asserts running it twice changes nothing -- so applying it on every run
    # costs one indexed lookup and closes the gap permanently. A database cannot
    # be V2-official and unmigrated at the same time any more.
    if V2_OFFICIAL:
        try:
            import migrate_v2
            if migrate_v2.already_applied(conn):
                pass
            else:
                _dest = migrate_v2.backup(db.DB_PATH)
                print("  V2 migration has never been applied to this database.")
                print("  backup written to %s" % os.path.basename(_dest))
                _rows, _rep = migrate_v2.plan(conn, args.sport)
                migrate_v2.apply_migration(conn, _rows, _rep,
                                           src_hash=migrate_v2.source_hash(db.DB_PATH))
                print("  migrated: %d pick(s) examined, %d with results, "
                      "%d locked-line vs legacy difference(s), %d signal(s)"
                      % (_rep.get("picks_examined", 0),
                         _rep.get("picks_with_results", 0),
                         _rep.get("locked_vs_legacy_differences", 0),
                         _rep.get("signals_created", 0)))
        except Exception as e:                     # noqa: BLE001 - never block a run
            # Reported loudly rather than swallowed: an unmigrated database is a
            # record integrity problem, and the acceptance gate fails on it.
            print("  ERROR: the V2 migration did not apply — %s: %s"
                  % (type(e).__name__, e))

    # THE JOURNAL IS THE HAND-OFF between workflows. Only one job may write the
    # database cache, so the frequent forecast-snapshot job records its work to
    # the state journal instead and this run replays it in. Without this step a
    # T2 forecast taken at 4pm by a job that cannot save the cache would simply
    # vanish, which is the failure the whole horizon apparatus exists to prevent.
    try:
        import replay_state
        import state_events as _se
        _sdir = _se.DEFAULT_STATE_DIR
        if os.path.isdir(_sdir):
            _events = _se.read_all(_sdir)
            _bad = _se.verify(_events)
            if _bad:
                print("  WARNING: the state journal does not verify; not replaying")
                for _p in _bad[:3]:
                    print("    %s" % _p)
            else:
                _applied = replay_state.apply_events(conn, _events)
                if _applied:
                    print("  replayed %d row(s) from the state journal: %s"
                          % (sum(_applied.values()),
                             ", ".join("%s %d" % kv for kv in sorted(_applied.items()))))
    except Exception as e:                         # noqa: BLE001 - never block a run
        print("  WARNING: could not replay the state journal — %s: %s"
              % (type(e).__name__, e))

    print("\n[5b/9] V2 forecasts and official signals")
    try:
        import forecast_v2
        forecast_v2.run_snapshots(conn, sport=args.sport, config=config,
                                  official=V2_OFFICIAL,
                                  run_id=os.environ.get("GITHUB_RUN_ID"))
        # And grade the V2 signals: at the line each one locked, using the side
        # it recorded, and separately at the close.
        import signals as _sig
        n_sig = _sig.grade_signals(conn, sport=args.sport)
        if n_sig:
            print("  graded %d V2 signal(s)" % n_sig)
        rec_v2 = _sig.official_record(conn)
        if rec_v2["n"]:
            print("  V2 official (%s): %d-%d-%d at the locked line, %d-%d-%d at the close"
                  % (rec_v2["strategy_version"], rec_v2["locked_w"], rec_v2["locked_l"],
                     rec_v2["locked_p"], rec_v2["close_w"], rec_v2["close_l"],
                     rec_v2["close_p"]))
    except Exception as e:                         # noqa: BLE001 - never block the run
        print("  WARNING: the V2 pass failed — %s: %s" % (type(e).__name__, e))
        import traceback
        traceback.print_exc()


    print("\n[6/9] write local artifacts")
    os.makedirs(OUT, exist_ok=True)
    picks_path = os.path.join(OUT, "%s_picks.json" % args.sport)
    with open(picks_path, "w") as fh:
        json.dump({"generated_utc": started.isoformat(), "sport": args.sport,
                   "season": season, "picks": picks}, fh, indent=2)
    acc_path = os.path.join(OUT, "%s_accuracy.json" % args.sport)
    with open(acc_path, "w") as fh:
        json.dump({"generated_utc": started.isoformat(), "season": season,
                   "metrics": {k: v for k, v in m_season.items()}}, fh, indent=2, default=str)
    print("  %s\n  %s" % (picks_path, acc_path))

    print("\n[7/9] render public track record page")
    try:
        site_dir = os.path.join(OUT, "site")
        os.makedirs(site_dir, exist_ok=True)
        page = publish.render(conn, args.sport, publish._backtest_summary())
        with open(os.path.join(site_dir, "index.html"), "w") as fh:
            fh.write(page)
        print("  %s" % os.path.join(site_dir, "index.html"))
    except Exception as e:
        print("  FAILED: %s: %s" % (type(e).__name__, e))

    # ── V2, in shadow ────────────────────────────────────────────────────────
    #
    # Forecasts, feature snapshots, market snapshots and strategy evaluations for
    # every game inside a horizon window, plus a recorded miss for every window
    # that closed empty. Signals are written with is_official=0.
    #
    # SHADOW UNTIL THE CUTOVER, deliberately. The legacy lock path above is still
    # the official writer, and two pipelines both publishing official picks is
    # the one failure mode a transition must not have. Phase 1C flips this, once
    # the shadow forecasts have been compared side by side with the legacy path.
    print("\n[8/9] build private research bundle")
    try:
        import subprocess as _sp
        r = _sp.run([sys.executable, os.path.join(ROOT, "src", "research_export.py"),
                     "--sport", args.sport, "--config", cfg_path],
                    capture_output=True, text=True, cwd=ROOT)
        # ALL of it, not just the first line. Taking [0] kept the one-line "bundle ->
        # path (size)" and threw away every count and warning underneath it — which
        # is how "no PPA data at all, the efficiency view will be empty for every
        # team" was emitted on each run and read by nobody. A step that summarises a
        # child process by discarding everything except its first line is not
        # summarising, it is hiding.
        if r.returncode == 0:
            for line in (r.stdout.strip().splitlines() or ["(no output)"]):
                print("  " + line)
        else:
            print("  FAILED: %s" % r.stderr[-300:])
    except Exception as e:
        print("  FAILED: %s: %s" % (type(e).__name__, e))

    print("\n[9/9] push to Google Sheets")
    sheet_id = args.sheet or os.environ.get("MODEL_SHEET_ID")
    if not sheet_id:
        print("  skipped — no --sheet and no MODEL_SHEET_ID set.")
    else:
        try:
            import sheets
            n = sheets.write_picks(sheet_id, args.picks_tab, picks)
            sheets.write_accuracy(sheet_id, args.accuracy_tab,
                                  {"%s %d season" % (args.sport.upper(), season): m_season})
            print("  wrote %d picks to '%s' and accuracy to '%s'"
                  % (n, args.picks_tab, args.accuracy_tab))
        except SystemExit as e:
            print("  skipped — %s" % e)
        except Exception as e:
            # Never fail the whole run because the sheet push broke; the
            # artifacts above are already written and are the source of truth.
            print("  FAILED: %s: %s" % (type(e).__name__, e))

    print("\ndone in %.1fs" % (dt.datetime.now(dt.timezone.utc) - started).total_seconds())


if __name__ == "__main__":
    main()
