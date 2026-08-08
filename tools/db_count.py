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
"""

import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def count(sport, path=None):
    path = path or os.path.join(ROOT, "data", "model.db")
    if not os.path.exists(path):
        return 0
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM games WHERE sport = ?", (sport,)).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


if __name__ == "__main__":
    print(count(sys.argv[1] if len(sys.argv) > 1 else "cfb"))
