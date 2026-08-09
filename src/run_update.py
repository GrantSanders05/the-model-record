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
    """Score every completed game this season, walk-forward."""
    games = backtest.load_games(conn, sport)
    cfg = dict(config)
    preds = backtest.run(games, cfg, [season])
    return preds, metrics.evaluate(preds)


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
    locked, already_started = ledger.lock(conn, args.sport, picks, label, now=started)
    graded = ledger.grade(conn, args.sport, now=started)
    print("  locked %d new pick(s); skipped %d already under way; graded %d"
          % (locked, already_started, graded))
    rec = ledger.record(conn, args.sport)
    if rec.get("ats_pct") is not None:
        lo, hi = rec["ats_ci95"]
        print("  LIVE ledger: %d-%d-%d  %.2f%% ATS  %+.2fu  ROI %+.2f%%  (95%% CI %.1f-%.1f)"
              % (rec["ats_w"], rec["ats_l"], rec["ats_push"], rec["ats_pct"],
                 rec["units"], rec["roi"], lo, hi))
    else:
        print("  LIVE ledger: no graded picks yet")

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
