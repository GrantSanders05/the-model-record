"""
count_challengers.py — how many shadow models this database has registered.

One number on stdout, so a workflow can branch on it without embedding Python in
YAML. It exists because the challengers were fitted on a laptop and the registry
lives in the cached database: production published a challenger table holding the
Champion and nothing else, and every test passed.
"""

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import db                                                    # noqa: E402

if __name__ == "__main__":
    conn = db.connect()
    print(conn.execute("SELECT COUNT(*) FROM model_registry"
                       " WHERE role IN ('challenger','baseline')").fetchone()[0])
