"""
make_fixture.py — a research bundle with a FULL season of results in it.

The live ledger is empty until games are played, which is correct and which makes
the tracking views impossible to build or test against reality. Shipping a results
screen that has only ever been rendered with zero rows is how you find out in
September that the weekly table breaks on its second week.

So this replays 2025 through the real engine, writes the predictions into a
TEMPORARY database as graded picks, and runs the real tracking code over them. The
output is a bundle identical in shape to production with ~800 graded picks and a
sample bet log.

    python3 tools/make_fixture.py            -> output/research/fixture.json

IT NEVER TOUCHES data/model.db. The whole reason the published record is worth
anything is that nothing can write to it except a pick locked before kickoff; a
fixture generator with access to the real ledger would be the exact hole that
rule exists to close. The temp database is created, used and deleted.

Used by the QA suite via QA_BUNDLE, and by nothing that publishes.
"""

import json
import os
import random
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import backtest      # noqa: E402
import db            # noqa: E402
import research_export  # noqa: E402
import tracking      # noqa: E402
import bet_log       # noqa: E402

SEASON = 2025


def graded_rows(conn, season):
    """Replay the season and score every pick, exactly as the ledger would."""
    cfg = json.load(open(os.path.join(ROOT, "config", "cfb_elo.json")))
    games = backtest.load_games(conn, "cfb")
    preds = backtest.run(games, dict(cfg), test_seasons=[season])
    by_id = {g["game_id"]: g for g in games}

    rng = random.Random(7)          # deterministic: a fixture that moves is useless
    out = []
    for p in preds:
        if p["market_margin"] is None:
            continue
        g = by_id[p["game_id"]]
        close = p["market_margin"]
        # A plausible pick-time line: the closing number plus the sort of drift a
        # real week produces. This is what makes CLV non-degenerate in the fixture.
        at_pick = round((close + rng.choice([-1.5, -1, -0.5, 0, 0, 0.5, 1, 1.5])) * 2) / 2
        edge = p["pred_margin"] - close
        actual = p["actual_margin"]
        diff = actual - close
        ats = None if edge == 0 else ("P" if diff == 0 else
                                      ("W" if (edge > 0) == (diff > 0) else "L"))
        ml_pick = g["home_team"] if p["pred_margin"] > 0 else g["away_team"]
        ml_res = ("P" if actual == 0 else
                  "W" if ((actual > 0) == (ml_pick == g["home_team"])) else "L")
        ou = None
        if p.get("pred_total") is not None and p.get("market_total") is not None:
            oe = p["pred_total"] - p["market_total"]
            od = p["actual_total"] - p["market_total"]
            ou = None if oe == 0 else ("P" if od == 0 else
                                       ("W" if (oe > 0) == (od > 0) else "L"))
        out.append({
            "game_id": p["game_id"], "sport": "cfb", "season": p["season"],
            "week": p["week"], "home_team": g["home_team"], "away_team": g["away_team"],
            "kickoff": g["kickoff"], "published_at": g["kickoff"],
            "config_label": "cfb_elo (2025 replay)",
            "model_margin": round(p["pred_margin"], 1),
            "market_margin_at_pick": at_pick,
            "model_total": p.get("pred_total"), "market_total_at_pick": p.get("market_total"),
            "ats_pick": None if edge == 0 else (g["home_team"] if edge > 0 else g["away_team"]),
            "ml_pick": ml_pick,
            "ou_pick": None if not ou else ("OVER" if p["pred_total"] > p["market_total"] else "UNDER"),
            "ml_odds_at_pick": g.get("home_ml") if ml_pick == g["home_team"] else g.get("away_ml"),
            "closing_margin": close, "closing_total": p.get("market_total"),
            "actual_margin": actual, "actual_total": p.get("actual_total"),
            "ats_result": ats, "ou_result": ou, "ml_result": ml_res,
            "closing_ml": None, "graded_at": g["kickoff"],
        })
    return out


def sample_bets(conn, rows, week_labels):
    """A believable personal book: ~40 bets, varied markets, sizes and prices."""
    rng = random.Random(11)
    picked = [r for r in rows if r["ats_pick"]][:400]
    rng.shuffle(picked)
    sheet = [["Date", "Week", "Team", "Market", "Side", "Line", "Odds", "Units",
              "Book", "Notes"]]
    for r in picked[:40]:
        wk = week_labels.get(r["game_id"], r["week"])
        home = r["ats_pick"] == r["home_team"]
        line = -r["closing_margin"] if home else r["closing_margin"]
        line += rng.choice([-0.5, 0, 0, 0.5])
        market = rng.choices(["spread", "moneyline", "total"], [7, 2, 2])[0]
        if market == "total" and r["closing_total"] is None:
            market = "spread"
        row = [str(r["kickoff"] or "")[:10], wk, r["ats_pick"], market]
        if market == "spread":
            row += ["", round(line * 2) / 2, rng.choice(["-110", "-105", "-115"])]
        elif market == "moneyline":
            row += ["", "", rng.choice(["+150", "-130", "+220", "-160"])]
        else:
            row += [rng.choice(["over", "under"]), r["closing_total"], "-110"]
        row += [rng.choice([0.5, 1, 1, 1, 1.5, 2]), rng.choice(["DK", "FD", "MGM"]), ""]
        sheet.append(row)
    # Two rows that cannot be graded, so the "problems" path is exercised in the UI
    # rather than only in a unit test.
    sheet.append(["", 3, "Nonexistent State", "spread", "", "-3", "-110", 1, "DK", ""])
    sheet.append(["", 4, "Alabama", "total", "", "51", "-110", 1, "DK", "no side given"])
    return sheet


def main():
    real = db.connect()
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "fixture.db")
    # A real file, not :memory:, because the schema is applied by db.connect() and
    # the tracking queries are the production ones. Same code path, throwaway data.
    fake = db.connect(path)

    rows = graded_rows(real, SEASON)
    if not rows:
        raise SystemExit("no 2025 predictions with lines — cannot build a fixture")
    fake.executemany(
        "INSERT OR REPLACE INTO picks_log (%s) VALUES (%s)"
        % (", ".join(rows[0]), ", ".join(":" + k for k in rows[0])), rows)

    # THE GAMES AND LINES THOSE PICKS SIT ON, and then the V2 migration over the
    # top. The public page leads with the LOCKED-LINE record, which is read from
    # signal_log; a fixture holding only picks_log renders the legacy fallback
    # instead, so the branch that actually ships would never be exercised — which
    # is the exact failure this file exists to prevent, one layer up.
    gids = [r["game_id"] for r in rows]
    marks = ",".join("?" * len(gids))
    gcols = [c["name"] for c in real.execute("PRAGMA table_info(games)")]
    grows = [tuple(r) for r in real.execute(
        "SELECT %s FROM games WHERE game_id IN (%s)" % (",".join(gcols), marks), gids)]
    fake.executemany("INSERT OR REPLACE INTO games (%s) VALUES (%s)"
                     % (",".join(gcols), ",".join("?" * len(gcols))), grows)
    lcols = [c["name"] for c in real.execute("PRAGMA table_info(lines)")]
    lrows = [tuple(r) for r in real.execute(
        "SELECT %s FROM lines WHERE game_id IN (%s)" % (",".join(lcols), marks), gids)]
    fake.executemany("INSERT OR REPLACE INTO lines (%s) VALUES (%s)"
                     % (",".join(lcols), ",".join("?" * len(lcols))), lrows)
    fake.commit()

    import migrate_v2
    _plan, _rep = migrate_v2.plan(fake, "cfb")
    migrate_v2.apply_migration(fake, _plan, _rep)

    labels = research_export.display_weeks(real, "cfb", SEASON)
    summary = tracking.summary(fake, "cfb", week_labels=labels)

    sheet = sample_bets(fake, rows, labels)
    # The bet log grades against the GAMES table, which only the real database has.
    built = bet_log.build(real, "cfb", SEASON, sheet, week_labels=labels)
    built["state"] = "ok"
    built["fetched_utc"] = "2026-01-05T12:00:00+00:00"

    src = os.path.join(ROOT, "output", "research", "data.json")
    bundle = json.load(open(src))
    bundle["tracking"] = summary
    bundle["mybets"] = built
    bundle["fixture"] = True

    # THE INVARIANT: every game a bet refers to is in the bundle's schedule, because
    # the browser grades bets against that schedule and nothing else. The live half
    # of this bundle is the upcoming season and the bet log is a 2025 replay, so the
    # 2025 fixtures those bets sit on have to be spliced in or the whole graded book
    # renders as ungraded. Production holds the same invariant through
    # research_export.schedule(keep=...); this is the fixture's version of it.
    #
    # It is also the only way the fixture gets a FINAL game into `schedule`, which is
    # what lets the QA suite prove that logging a bet on a played game grades it on
    # the spot. Without this the branch is never executed anywhere.
    need = {b["game_id"] for b in built.get("bets", [])}
    have = {str(g["game_id"]) for g in bundle["schedule"]}
    extra = research_export.schedule(real, "cfb", SEASON, {}, labels,
                                     rated=(), keep=need)
    added = [g for g in extra if str(g["game_id"]) in {str(n) for n in need}
             and str(g["game_id"]) not in have]
    bundle["schedule"] = bundle["schedule"] + added
    missing = need - {str(g["game_id"]) for g in bundle["schedule"]}
    if missing:
        raise SystemExit("fixture would ship %d bet(s) with no game to grade against"
                         % len(missing))

    out = os.path.join(ROOT, "output", "research", "fixture.json")
    with open(out, "w") as fh:
        json.dump(bundle, fh, separators=(",", ":"))

    # The PUBLIC page too. Its weekly table only exists once something is graded, so
    # rendering it against the empty live ledger tests the branch that is never the
    # interesting one. Same temp database, same render function.
    import publish
    page = os.path.join(ROOT, "output", "site", "fixture.html")
    os.makedirs(os.path.dirname(page), exist_ok=True)
    with open(page, "w") as fh:
        # Pass the validation summary too. The public page has a whole section that
        # only renders when one exists, and a fixture that omits it leaves that
        # section untested on the branch that matters.
        fh.write(publish.render(fake, "cfb", publish._backtest_summary()))
    print("public fixture -> %s" % page)

    fake.close()
    for f in os.listdir(tmp):
        os.unlink(os.path.join(tmp, f))
    os.rmdir(tmp)


    o = summary["overall"]
    print("fixture -> %s" % out)
    print("  %d graded picks, %d weeks" % (summary["graded"], len(summary["weekly"])))
    print("  ATS %s (%s) %s" % (o["ats"]["pct"], o["ats"]["ci95"],
                                "clears" if o["ats"]["clears"] else "does not clear"))
    print("  CLV mean %+.2f pts on %d picks" % (o["clv"]["mean"], o["clv"]["n"]))
    t = built["totals"]
    print("  bet log: %d bets, %d settled, %+.2f units, ROI %s%%"
          % (t["n"], t["settled"], t["units_won"], t["roi"]))
    print("  %d problem row(s) reported" % len(built["problems"]))
    print("  %d game(s) spliced in so every bet has a fixture to grade against"
          % len(added))


if __name__ == "__main__":
    main()
