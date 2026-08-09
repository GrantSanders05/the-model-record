"""
db_count.py — how many games of a sport are actually in the database.

Exists because the seeding step needs to ask what the database CONTAINS, not
whether the file exists. Those came apart in exactly the way that is hard to
notice: runs that failed before the CFBD key was configured still cached a
model.db holding NFL games and no college ones. Every later run saw the file,
announced "database restored from cache", and skipped the college seed — a
cache that poisoned itself and would never have healed.

Prints a single integer, and prints 0 rather than failing when the database is
missing or unreadable, so it is safe to use in a shell condition.

    python3 tools/db_count.py cfb
    python3 tools/db_count.py cfb team_game_stats

The second argument exists for the same reason as the first. "The database is
seeded" was being taken to mean every table is populated, and team_game_stats was
empty on every machine but one -- so the efficiency view shipped blank and no step
in the pipeline had any way to notice.
"""

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ALLOWED = {"games", "team_game_stats", "grades", "lines", "picks_log"}


def count(sport, table="games", path=None):
    # The table name cannot be bound as a parameter, so it is checked against a
    # fixed set rather than interpolated from whatever the caller passed.
    if table not in ALLOWED:
        raise SystemExit("unknown table %r (allowed: %s)"
                         % (table, ", ".join(sorted(ALLOWED))))
    path = path or os.path.join(ROOT, "data", "model.db")
    if not os.path.exists(path):
        return 0
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM %s WHERE sport = ?" % table, (sport,)).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


if __name__ == "__main__":
    print(count(sys.argv[1] if len(sys.argv) > 1 else "cfb",
                sys.argv[2] if len(sys.argv) > 2 else "games"))
