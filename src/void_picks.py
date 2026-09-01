"""
void_picks.py — withdraw picks from the record without erasing them.

A ledger that can be edited is not a record, so nothing here deletes a row. Voiding
marks a pick with a timestamp and a reason, every reader excludes it, and the row
stays exactly where it was — auditable, and reversible with --restore.

THE CASE THIS WAS BUILT FOR. `ledger.lock` had no window, so the first run of the
2026 season locked all 788 remaining games at once, on 6 August, stamped with
ratings whose quality-points term is literally zero and against lines for games no
book had priced yet. `INSERT OR IGNORE` made them permanent, which meant every week
of film graded after August was structurally unable to reach the record. Those picks
were a bug's output, not the model's opinion.

WHAT IT WILL NOT DO. It refuses to touch a pick whose game has kicked off, graded or
not. A pick that faced a real result stays in the record whatever it did, because
the alternative — dropping settled picks by any rule at all — is how a track record
becomes a marketing document.

    python3 src/void_picks.py --sport cfb --before 2026-08-20 --reason "..."   # dry run
    python3 src/void_picks.py ... --apply
    python3 src/void_picks.py --restore --reason-like "locked without a window"
"""

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402


def candidates(conn, sport, published_before=None, now=None):
    """Ungraded picks whose game has NOT kicked off. Nothing else is eligible."""
    now = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    q = ["SELECT p.game_id, p.season, p.week, p.kickoff, p.published_at,"
         "       p.home_team, p.away_team, p.ats_pick"
         "  FROM picks_log p"
         " WHERE p.sport = ? AND p.graded_at IS NULL AND p.voided_at IS NULL"
         "   AND p.kickoff IS NOT NULL AND p.kickoff > ?"]
    args = [sport, now]
    if published_before:
        q.append(" AND p.published_at < ?")
        args.append(published_before)
    q.append(" ORDER BY p.kickoff")
    return [dict(r) for r in conn.execute("".join(q), args)]


def apply_void(conn, rows, reason, now=None):
    """
    Move picks out of the ledger and into picks_voided, whole.

    They are MOVED, not flagged. game_id is picks_log's primary key and `lock` is
    `INSERT OR IGNORE`, so a flagged row still holds the slot and the replacement
    pick is dropped without a word — 880 withdrawn and 0 re-locked, the first time
    this ran. Withdrawing a pick has to free the game to be picked again.
    """
    import json
    stamp = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    n = 0
    for r in rows:
        full = conn.execute("SELECT * FROM picks_log WHERE game_id=?",
                            (r["game_id"],)).fetchone()
        if not full:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO picks_voided (game_id, payload, voided_at,"
            " void_reason) VALUES (?,?,?,?)",
            (r["game_id"], json.dumps(dict(full)), stamp, reason))
        conn.execute("DELETE FROM picks_log WHERE game_id=?", (r["game_id"],))
        n += 1
    conn.commit()
    return n


def restore(conn, sport, reason_like):
    """Put voided picks back in the ledger, exactly as they were."""
    import json
    rows = conn.execute(
        "SELECT game_id, payload FROM picks_voided WHERE void_reason LIKE ?",
        ("%" + reason_like + "%",)).fetchall()
    n = 0
    for r in rows:
        d = json.loads(r["payload"])
        if d.get("sport") != sport:
            continue
        d.pop("voided_at", None)
        d.pop("void_reason", None)
        cols = [k for k in d if k is not None]
        conn.execute(
            "INSERT OR REPLACE INTO picks_log (%s) VALUES (%s)"
            % (",".join(cols), ",".join(":" + c for c in cols)), d)
        conn.execute("DELETE FROM picks_voided WHERE game_id=?", (r["game_id"],))
        n += 1
    conn.commit()
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sport", default="cfb")
    ap.add_argument("--before", help="only picks published before this ISO date")
    ap.add_argument("--reason", default="")
    ap.add_argument("--apply", action="store_true", help="without this it is a dry run")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--reason-like", default="")
    args = ap.parse_args()

    conn = db.connect()
    if args.restore:
        if not args.reason_like:
            raise SystemExit("--restore needs --reason-like to say which void to undo")
        n = restore(conn, args.sport, args.reason_like)
        print("restored %d pick(s) to the record." % n)
        return

    if not args.reason:
        raise SystemExit("--reason is required: a void with no stated reason is a "
                         "deletion with extra steps")

    rows = candidates(conn, args.sport, args.before)
    print("%d pick(s) eligible — ungraded, and not yet kicked off." % len(rows))
    by_week = {}
    for r in rows:
        by_week[r["week"]] = by_week.get(r["week"], 0) + 1
    for w in sorted(by_week):
        print("   week %-3s %4d" % (w, by_week[w]))
    if rows:
        print("  earliest kickoff still ahead: %s" % rows[0]["kickoff"][:10])

    settled = conn.execute(
        "SELECT COUNT(*) c FROM picks_log WHERE sport=? AND graded_at IS NOT NULL"
        " AND voided_at IS NULL", (args.sport,)).fetchone()["c"]
    print("  %d already-graded pick(s) are untouched and stay in the record." % settled)

    if not args.apply:
        print("\nDRY RUN. Nothing was changed. Re-run with --apply to void these.")
        return
    n = apply_void(conn, rows, args.reason)
    print("\nvoided %d pick(s). Reason recorded on every row:\n  %s" % (n, args.reason))
    print("Undo with:  python3 src/void_picks.py --restore --reason-like %r"
          % args.reason[:30])


if __name__ == "__main__":
    main()
