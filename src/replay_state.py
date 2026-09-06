"""
replay_state.py — rebuild the V2 tables from the journal, and prove they match.

    python3 src/replay_state.py --state-dir state --db /tmp/rebuilt.db
    python3 src/replay_state.py --verify-against data/model.db
    python3 src/replay_state.py --self-test

WHAT THIS PROVES
----------------
That the Actions cache is an accelerator and not the database of record. If the
cache is evicted tomorrow, everything unique — every quote observation, every
grade snapshot, every forecast, every decision, every signal — comes back from
the journal. The games, lines and rankings do not need to: those are public and
re-fetchable, and duplicating them in the journal would make it enormous for no
gain.

RECONCILIATION IS THE POINT, not the rebuild. A replay that silently produces
slightly different rows is worse than no replay, so `--verify-against` compares
counts AND per-table content hashes between the rebuilt database and the live
one, and reports the first rows that differ.
"""

import argparse
import datetime as dt
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db             # noqa: E402
import provenance     # noqa: E402
import state_events   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# event type -> (table, primary key). The tables a replay owns.
APPLY = {
    "market_quote": ("market_quotes", "quote_id"),
    "grade_snapshot": ("grade_snapshots", "grade_snapshot_id"),
    "model_registry": ("model_registry", "model_version"),
    "forecast": ("forecast_log", "forecast_id"),
    "strategy_evaluation": ("strategy_evaluations", "evaluation_id"),
    "signal": ("signal_log", "signal_id"),
    "game_result": ("game_results_v2", "game_id"),
    "snapshot_miss": ("snapshot_misses", "miss_id"),
    "void": ("v2_void_events", "void_id"),
    "availability": ("availability_events", "event_id"),
    "weather": ("weather_snapshots", "snapshot_id"),
}

RECONCILED_TABLES = [t for t, _k in APPLY.values()]


def apply_events(conn, events, *, verbose=False):
    """
    Apply events to a database in order. -> {table: rows written}

    INSERT OR REPLACE on the primary key: a later event for the same key is a
    correction and supersedes, which is why `read_all` orders by occurred_at.
    Applying the same journal twice is a no-op.
    """
    counts = {}
    for ev in events:
        spec = APPLY.get(ev.get("event_type"))
        if spec is None:
            continue
        table, _key = spec
        payload = ev.get("payload") or {}
        cols = [c["name"] for c in conn.execute("PRAGMA table_info(%s)" % table)]
        row = {c: payload.get(c) for c in cols if c in payload}
        if not row:
            continue
        conn.execute("INSERT OR REPLACE INTO %s (%s) VALUES (%s)"
                     % (table, ",".join(row), ",".join(":" + c for c in row)), row)
        counts[table] = counts.get(table, 0) + 1
    conn.commit()
    if verbose:
        for t in sorted(counts):
            print("  %-24s %d" % (t, counts[t]))
    return counts


def rebuild(state_dir, db_path, *, verbose=True):
    """Create a database from nothing and replay the journal into it."""
    events = state_events.read_all(state_dir)
    problems = state_events.verify(events)
    if problems:
        raise SystemExit("the journal does not verify:\n  " + "\n  ".join(problems[:10]))
    conn = db.connect(db_path)
    if verbose:
        print("  %d event(s) read from %s" % (len(events), state_dir))
    counts = apply_events(conn, events, verbose=verbose)
    return conn, counts


def table_fingerprint(conn, table):
    """
    (count, hash) over a table's rows, ordered and canonicalized. -> tuple

    The hash is over the rows themselves, so two databases with the same counts
    and different contents do not compare equal. Volatile bookkeeping columns are
    excluded: `created_at` records when a row was WRITTEN, and a rebuild writes
    it at a different moment without the fact having changed.
    """
    try:
        cols = [c["name"] for c in conn.execute("PRAGMA table_info(%s)" % table)]
    except Exception:                              # noqa: BLE001 - table absent
        return (None, None)
    if not cols:
        return (None, None)
    keep = [c for c in cols if c != "created_at"]
    rows = [tuple(r) for r in conn.execute(
        "SELECT %s FROM %s ORDER BY %s" % (",".join(keep), table, keep[0]))]
    return (len(rows), provenance.payload_hash([list(r) for r in rows]))


def reconcile(live_conn, replay_conn, tables=None):
    """Compare two databases table by table. -> [{table, ...}] differences."""
    out = []
    for t in (tables or RECONCILED_TABLES):
        ln, lh = table_fingerprint(live_conn, t)
        rn, rh = table_fingerprint(replay_conn, t)
        out.append({"table": t, "live_rows": ln, "replay_rows": rn,
                    "match": (ln == rn and lh == rh),
                    "live_hash": (lh or "")[:12], "replay_hash": (rh or "")[:12]})
    return out


def first_differences(live_conn, replay_conn, table, limit=3):
    """The first rows that differ, for a report that names the problem."""
    cols = [c["name"] for c in live_conn.execute("PRAGMA table_info(%s)" % table)]
    keep = [c for c in cols if c != "created_at"]
    key = keep[0]
    live = {r[0]: dict(zip(keep, r)) for r in live_conn.execute(
        "SELECT %s FROM %s" % (",".join(keep), table))}
    rep = {r[0]: dict(zip(keep, r)) for r in replay_conn.execute(
        "SELECT %s FROM %s" % (",".join(keep), table))}
    diffs = []
    for k in sorted(set(live) | set(rep)):
        if k not in rep:
            diffs.append({key: k, "problem": "missing from the replay"})
        elif k not in live:
            diffs.append({key: k, "problem": "present only in the replay"})
        elif live[k] != rep[k]:
            changed = {c: (live[k][c], rep[k][c]) for c in keep
                       if live[k][c] != rep[k][c]}
            diffs.append({key: k, "problem": "differs", "columns": changed})
        if len(diffs) >= limit:
            break
    return diffs


def self_test():
    """
    Export a synthetic database to a journal, replay into an empty one, compare.

    Runs in CI with no production data. The control at the end deletes one row
    from the rebuilt database and requires reconciliation to FAIL, because a
    reconciliation that cannot fail proves nothing about the journal.
    """
    p = f = 0

    def ok(name, cond, detail=""):
        nonlocal p, f
        if cond:
            p += 1
            print("  [PASS] %s" % name)
        else:
            f += 1
            print("  [FAIL] %s%s" % (name, (" — " + str(detail)) if detail else ""))

    work = tempfile.mkdtemp()
    src = db.connect(os.path.join(work, "source.db"))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    src.execute(
        "INSERT INTO market_quotes (quote_id, sport, game_id, provider, observed_at,"
        " home_spread, total, quality_status, created_at)"
        " VALUES ('mq_a','cfb','g1','DraftKings',?, -3.5, 52.5,'valid',?)", (now, now))
    src.execute(
        "INSERT INTO grade_snapshots (grade_snapshot_id, sport, season, team,"
        " observed_at, effective_at, source_type, source_hash, qb, created_at)"
        " VALUES ('gs_a','cfb',2026,'Oregon',?,?, 'live_sheet','h1', 12.0, ?)",
        (now, now, now))
    src.execute(
        "INSERT INTO model_registry (model_version, model_id, role, git_sha,"
        " config_json, config_hash, feature_schema_version, created_at)"
        " VALUES ('C0-test','champion-grade','champion','sha','{}','ch','fs',?)", (now,))
    src.execute(
        "INSERT INTO signal_log (signal_id, evaluation_id, forecast_id, game_id,"
        " strategy_version, market, side, line, created_at, official_horizon,"
        " is_official) VALUES ('sg_a','ev_a','fc_a','g1','S0','spread','H',-3.5,?, 'T2',1)",
        (now,))
    src.commit()

    events = state_events.export_from_db(src)
    ok("every V2 row becomes an event", len(events) == 4, len(events))
    state_dir = os.path.join(work, "state")
    written, skipped = state_events.append(events, state_dir)
    ok("the journal is written", written == 4 and skipped == 0, (written, skipped))
    again = state_events.append(state_events.export_from_db(src), state_dir)
    ok("exporting the same database twice appends nothing", again == (0, 4), again)

    replay_conn, counts = rebuild(state_dir, os.path.join(work, "rebuilt.db"),
                                  verbose=False)
    ok("an empty database is rebuilt from the journal alone", sum(counts.values()) == 4,
       counts)
    diffs = reconcile(src, replay_conn)
    ok("...and reconciles row for row against the original",
       all(d["match"] for d in diffs),
       [d for d in diffs if not d["match"]])

    ok("the journal verifies against its own hashes",
       not state_events.verify(state_events.read_all(state_dir)))

    # CONTROLS. A reconciliation that cannot fail proves nothing.
    replay_conn.execute("DELETE FROM signal_log WHERE signal_id='sg_a'")
    replay_conn.commit()
    d2 = reconcile(src, replay_conn)
    ok("[control] a deleted row makes reconciliation fail",
       any(not d["match"] for d in d2),
       "a missing signal was not detected")
    ok("...and the report names which row", 
       any("sg_a" in str(x) for x in first_differences(src, replay_conn, "signal_log")))

    tampered = state_events.read_all(state_dir)
    tampered[0]["payload"]["home_spread"] = -99.0
    ok("[control] an edited payload fails verification",
       bool(state_events.verify(tampered)))

    print("\n  %d passed, %d failed" % (p, f))
    return 1 if f else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state-dir", default=state_events.DEFAULT_STATE_DIR)
    ap.add_argument("--db", help="rebuild into this path")
    ap.add_argument("--verify-against", help="compare a rebuild with this database")
    ap.add_argument("--export", action="store_true",
                    help="export the live database's V2 rows into the journal")
    ap.add_argument("--source-db", default=db.DB_PATH)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        print("── state journal self-test ──")
        sys.exit(self_test())

    if args.export:
        conn = db.connect(args.source_db)
        events = state_events.export_from_db(
            conn, source_run=os.environ.get("GITHUB_RUN_ID"))
        written, skipped = state_events.append(events, args.state_dir)
        print("  %d event(s) appended, %d already present" % (written, skipped))
        return

    if args.verify_against:
        tmp = os.path.join(tempfile.mkdtemp(), "replay.db")
        replay_conn, _counts = rebuild(args.state_dir, tmp)
        live = db.connect(args.verify_against)
        diffs = reconcile(live, replay_conn)
        print("\n  %-24s %10s %10s %s" % ("table", "live", "replay", "match"))
        bad = 0
        for d in diffs:
            print("  %-24s %10s %10s %s"
                  % (d["table"], d["live_rows"], d["replay_rows"],
                     "yes" if d["match"] else "NO"))
            if not d["match"]:
                bad += 1
                for x in first_differences(live, replay_conn, d["table"]):
                    print("      %s" % json.dumps(x)[:160])
        if bad:
            raise SystemExit("\n%d table(s) do not reconcile." % bad)
        print("\n  every table reconciles.")
        return

    if args.db:
        rebuild(args.state_dir, args.db)
        print("  rebuilt into %s" % args.db)
        return
    ap.error("nothing to do: pass --export, --db, --verify-against or --self-test")


if __name__ == "__main__":
    main()
