"""
db.py — storage layer for The Model.

Pure stdlib. One SQLite file, versioned alongside the code, so every backtest
result is reproducible from a known data snapshot.

────────────────────────────────────────────────────────────────────────────
THE ONE CONVENTION THAT MATTERS

Every margin in this database — model or market — is expressed as:

    HOME MARGIN = points the HOME team is expected to (or did) win by.

    +7  ->  home favored by 7 / home won by 7
    -3  ->  away favored by 3 / away won by 3

This matches how Grant's sheet already thinks: Spread = Home TOTAL - Away TOTAL.
It matches nflverse's `spread_line` directly.
It is the NEGATION of CFBD's `spread` field.

Source conventions are normalized at ingest, never at read time, and
`verify_conventions()` re-proves it against real results. A silent sign flip
is the single most likely way to produce a beautiful, meaningless backtest.
────────────────────────────────────────────────────────────────────────────
"""

import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "model.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id      TEXT PRIMARY KEY,
    sport        TEXT NOT NULL,          -- 'cfb' | 'nfl' | 'nba'
    season       INTEGER NOT NULL,
    week         INTEGER,
    season_type  TEXT,
    kickoff      TEXT,                   -- ISO8601 UTC; ordering key for walk-forward
    home_team    TEXT NOT NULL,
    away_team    TEXT NOT NULL,
    home_score   INTEGER,                -- NULL until played
    away_score   INTEGER,
    neutral_site INTEGER DEFAULT 0,
    home_conf    TEXT,
    away_conf    TEXT,
    home_div     TEXT,                   -- 'fbs' | 'fcs' etc. Needed for loss-quality penalties.
    away_div     TEXT
);
CREATE INDEX IF NOT EXISTS idx_games_order  ON games(sport, season, week, kickoff);
CREATE INDEX IF NOT EXISTS idx_games_home   ON games(sport, season, home_team);
CREATE INDEX IF NOT EXISTS idx_games_away   ON games(sport, season, away_team);

CREATE TABLE IF NOT EXISTS lines (
    game_id       TEXT NOT NULL,
    provider      TEXT NOT NULL,
    home_margin   REAL,                  -- NORMALIZED: + = home favored. See module docstring.
    home_margin_open REAL,
    total         REAL,
    total_open    REAL,
    home_ml       INTEGER,
    away_ml       INTEGER,
    PRIMARY KEY (game_id, provider),
    FOREIGN KEY (game_id) REFERENCES games(game_id)
);

CREATE TABLE IF NOT EXISTS rankings (
    sport   TEXT NOT NULL,
    season  INTEGER NOT NULL,
    week    INTEGER NOT NULL,
    poll    TEXT NOT NULL,
    team    TEXT NOT NULL,
    rank    INTEGER NOT NULL,
    PRIMARY KEY (sport, season, week, poll, team)
);
CREATE INDEX IF NOT EXISTS idx_rank_lookup ON rankings(sport, season, week, poll);

-- Pace / efficiency inputs for the TOTALS model. Deliberately separate from
-- the position grades: totals are a function of tempo x efficiency, which is
-- orthogonal to the relative-strength grades that drive the spread.
CREATE TABLE IF NOT EXISTS team_form (
    sport        TEXT NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,       -- form as of BEFORE this week (never includes it)
    team         TEXT NOT NULL,
    plays_pg     REAL,
    pace         REAL,                   -- possessions or plays per game
    off_eff      REAL,                   -- points per possession/play, offense
    def_eff      REAL,                   -- points per possession/play allowed
    pts_for_pg   REAL,
    pts_against_pg REAL,
    games_played INTEGER,
    PRIMARY KEY (sport, season, week, team)
);

-- Grant's film-based position group grades, ported from the Google Sheets.
-- Stored with the week they became effective so the backtester can only ever
-- read grades that existed BEFORE the game being predicted.
CREATE TABLE IF NOT EXISTS grades (
    sport       TEXT NOT NULL,
    season      INTEGER NOT NULL,
    week        INTEGER NOT NULL,        -- effective from this week onward
    team        TEXT NOT NULL,
    position    TEXT NOT NULL,           -- 'qb','ol','dl','coach','st','rb','wr','lb','db' ...
    grade       REAL NOT NULL,
    PRIMARY KEY (sport, season, week, team, position)
);

-- Per-game team efficiency (CFBD PPA = predicted points added, their EPA).
-- This is the real-data stand-in for position grades across seasons Grant never
-- graded: passing/rushing PPA on each side of the ball measures the same
-- underlying team quality his QB/OL/DL/DB columns are estimating by eye.
-- Stored per GAME, never per season, so ratings can be built walk-forward.
CREATE TABLE IF NOT EXISTS team_game_stats (
    game_id   TEXT NOT NULL,
    sport     TEXT NOT NULL,
    season    INTEGER NOT NULL,
    week      INTEGER,
    team      TEXT NOT NULL,
    opponent  TEXT,
    off_ppa   REAL, off_pass_ppa REAL, off_rush_ppa REAL,
    def_ppa   REAL, def_pass_ppa REAL, def_rush_ppa REAL,
    PRIMARY KEY (game_id, team)
);
CREATE INDEX IF NOT EXISTS idx_tgs ON team_game_stats(sport, season, week);

-- Every line we have ever seen, one row per observation.
--
-- The `lines` table holds only the CURRENT number for a game; each fetch
-- overwrites it. That is fine for backtesting against closing lines, but it
-- destroys the movement history -- and closing-line value is the leading
-- indicator of a real edge. CLV shows up in the data months before a win rate
-- separates from noise, so it is worth capturing from the first day.
--
-- Append-only by construction: the primary key includes the observation time,
-- so a re-run adds a row rather than replacing one.
CREATE TABLE IF NOT EXISTS line_history (
    game_id     TEXT NOT NULL,
    observed_at TEXT NOT NULL,          -- UTC ISO8601
    provider    TEXT,
    home_margin REAL,
    total       REAL,
    home_ml     INTEGER,
    away_ml     INTEGER,
    PRIMARY KEY (game_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_linehist ON line_history(game_id, observed_at);

-- THE PUBLIC LEDGER. One row per game, written BEFORE kickoff, never rewritten.
--
-- This table is the entire basis of the published track record, so its value
-- comes from what it refuses to do: `INSERT OR IGNORE` on game_id means the
-- first pick recorded for a game is the only pick that will ever exist for it.
-- A later run cannot revise a pick after seeing how it went, and neither can
-- anyone editing by hand. Grading fills in the result columns afterwards and
-- touches nothing else.
--
-- market_margin_at_pick is stored separately from closing_margin on purpose:
-- keeping both is what makes closing-line value measurable, and it stops a
-- pick from being quietly re-graded against a friendlier number.
CREATE TABLE IF NOT EXISTS picks_log (
    -- NOT NULL matters: SQLite allows multiple NULLs in a PRIMARY KEY column,
    -- so a missing game_id would silently defeat the one-pick-per-game rule.
    game_id       TEXT PRIMARY KEY NOT NULL,
    sport         TEXT NOT NULL,
    season        INTEGER,
    week          INTEGER,
    home_team     TEXT,
    away_team     TEXT,
    kickoff       TEXT,
    published_at  TEXT NOT NULL,       -- UTC timestamp the pick was locked
    config_label  TEXT,                -- which model version produced it
    model_margin  REAL,
    market_margin_at_pick REAL,
    model_total   REAL,
    market_total_at_pick  REAL,
    ats_pick      TEXT,
    ml_pick       TEXT,
    ou_pick       TEXT,
    -- filled in by grading, after the game is final
    closing_margin REAL,
    closing_total  REAL,
    actual_margin  REAL,
    actual_total   REAL,
    ats_result     TEXT,               -- 'W' | 'L' | 'P'
    ou_result      TEXT,
    graded_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_picks_season ON picks_log(sport, season, week);

-- Every backtest run is recorded so results are auditable, not just printed.
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    sport       TEXT NOT NULL,
    label       TEXT,
    config_json TEXT NOT NULL,
    train_seasons TEXT,
    test_seasons  TEXT,
    n_games     INTEGER,
    ats_pct     REAL,
    su_pct      REAL,
    mae         REAL,
    rmse        REAL,
    calib_slope REAL,
    roi         REAL
);
"""


def connect(path=DB_PATH):
    """Open the database, creating it and its schema if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def verify_conventions(conn, sport):
    """
    Re-prove the sign convention against reality instead of trusting the ingest code.

    If normalization is correct, regressing actual home margin on market home
    margin gives a slope near +1. A slope near -1 means a source flipped its
    sign. Returns (slope, r, n) and raises on an obvious flip.

    This exists because a sign error does not crash anything — it produces a
    backtest that looks fine and is entirely wrong.
    """
    rows = conn.execute(
        """
        SELECT l.home_margin AS m, (g.home_score - g.away_score) AS actual
        FROM games g JOIN lines l ON l.game_id = g.game_id
        WHERE g.sport = ? AND g.home_score IS NOT NULL AND l.home_margin IS NOT NULL
        """,
        (sport,),
    ).fetchall()
    n = len(rows)
    if n < 30:
        return None, None, n

    xs = [r["m"] for r in rows]
    ys = [float(r["actual"]) for r in rows]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return None, None, n
    slope = sxy / sxx
    r = sxy / (sxx * syy) ** 0.5

    if slope < 0:
        raise ValueError(
            "Sign convention is INVERTED for sport=%s (slope=%.3f). "
            "The market is not anti-correlated with results; an ingest path "
            "failed to normalize. Refusing to continue." % (sport, slope)
        )
    return slope, r, n


def upsert_games(conn, rows):
    conn.executemany(
        """
        INSERT INTO games (game_id, sport, season, week, season_type, kickoff,
                           home_team, away_team, home_score, away_score,
                           neutral_site, home_conf, away_conf, home_div, away_div)
        VALUES (:game_id, :sport, :season, :week, :season_type, :kickoff,
                :home_team, :away_team, :home_score, :away_score,
                :neutral_site, :home_conf, :away_conf, :home_div, :away_div)
        ON CONFLICT(game_id) DO UPDATE SET
            home_score = excluded.home_score,
            away_score = excluded.away_score,
            kickoff    = excluded.kickoff
        """,
        rows,
    )
    conn.commit()


def snapshot_lines(conn, rows, observed_at=None):
    """Append the current lines to the history. Never updates an existing row."""
    import datetime as _dt
    ts = observed_at or _dt.datetime.now(_dt.timezone.utc).isoformat()
    conn.executemany(
        """INSERT OR IGNORE INTO line_history
           (game_id, observed_at, provider, home_margin, total, home_ml, away_ml)
           VALUES (:game_id, :observed_at, :provider, :home_margin, :total,
                   :home_ml, :away_ml)""",
        [dict(r, observed_at=ts) for r in rows])
    conn.commit()
    return len(rows)


def upsert_lines(conn, rows):
    conn.executemany(
        """
        INSERT INTO lines (game_id, provider, home_margin, home_margin_open,
                           total, total_open, home_ml, away_ml)
        VALUES (:game_id, :provider, :home_margin, :home_margin_open,
                :total, :total_open, :home_ml, :away_ml)
        ON CONFLICT(game_id, provider) DO UPDATE SET
            home_margin = excluded.home_margin,
            total       = excluded.total,
            home_ml     = excluded.home_ml,
            away_ml     = excluded.away_ml
        """,
        rows,
    )
    conn.commit()


def upsert_rankings(conn, rows):
    conn.executemany(
        """
        INSERT INTO rankings (sport, season, week, poll, team, rank)
        VALUES (:sport, :season, :week, :poll, :team, :rank)
        ON CONFLICT(sport, season, week, poll, team) DO UPDATE SET
            rank = excluded.rank
        """,
        rows,
    )
    conn.commit()


def summary(conn):
    """Human-readable snapshot of what's loaded."""
    out = []
    for row in conn.execute(
        """
        SELECT sport, COUNT(*) n,
               SUM(home_score IS NOT NULL) played,
               MIN(season) s0, MAX(season) s1
        FROM games GROUP BY sport ORDER BY sport
        """
    ):
        nl = conn.execute(
            "SELECT COUNT(DISTINCT game_id) c FROM lines l "
            "JOIN games g USING(game_id) WHERE g.sport = ?",
            (row["sport"],),
        ).fetchone()["c"]
        out.append(
            "  %-4s %5d games (%d played, %d with lines)  seasons %d-%d"
            % (row["sport"], row["n"], row["played"], nl, row["s0"], row["s1"])
        )
    return "\n".join(out) if out else "  (empty)"
