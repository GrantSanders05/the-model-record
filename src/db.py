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
-- Withdrawn picks. A voided pick must LEAVE picks_log, not merely be flagged in it:
-- game_id is the primary key, so a flagged row still occupies the slot and the next
-- legitimate lock for that game is silently ignored. Marking alone made the record
-- honest and the re-lock impossible, which is the worst of both. Nothing is lost —
-- the row moves here with its reason, and --restore moves it back.
CREATE TABLE IF NOT EXISTS picks_voided (
    game_id       TEXT PRIMARY KEY NOT NULL,
    payload       TEXT NOT NULL,        -- the whole original row, as JSON
    voided_at     TEXT NOT NULL,
    void_reason   TEXT NOT NULL
);

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
    graded_at      TEXT,
    -- Moneyline. ml_pick was recorded from the start and never graded, so the
    -- market the model has an actual expected-value calculation for contributed
    -- nothing to the record. Odds are stored AT PICK TIME because a moneyline
    -- result is worthless without the price it was taken at: 30 wins at +150 and
    -- 30 wins at -150 are opposite outcomes.
    ml_odds_at_pick INTEGER,
    closing_ml      INTEGER,
    ml_result       TEXT                -- 'W' | 'L' | 'P'
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


# Columns added after a table already existed somewhere. CREATE TABLE IF NOT EXISTS
# does nothing to a table that is already there, so a new column in SCHEMA above
# reaches a fresh database and silently misses every existing one -- including the
# only copy that matters, the model.db restored from the Actions cache on every run.
# Each entry is applied with ALTER TABLE ADD COLUMN and skipped if already present.
ADDED_COLUMNS = [
    # (table, column, type) -- append only, never edit or remove an entry
    ("picks_log", "ml_odds_at_pick", "INTEGER"),
    ("picks_log", "closing_ml", "INTEGER"),
    ("picks_log", "ml_result", "TEXT"),
    # A pick is never deleted. It can be VOIDED — marked, with a reason, and left
    # in place — which is the only honest way to withdraw something from a record
    # that claims to be append-only. Every reader excludes voided rows; the rows
    # themselves stay so the withdrawal is auditable and reversible.
    ("picks_log", "voided_at", "TEXT"),
    ("picks_log", "void_reason", "TEXT"),
]

# Appended to any query that computes the published record.
NOT_VOIDED = "voided_at IS NULL"


# ── what "final" means ─────────────────────────────────────────────────────────
#
# A game is FINAL when BOTH scores are present, and at no other time. This looks
# too obvious to write down, and it cost the 2026 season: fourteen places asked
# `home_score IS NOT NULL` and then subtracted `away_score`, so one row carrying a
# home score and a NULL away score took the whole export down with
#
#     TypeError: unsupported operand type(s) for -: 'int' and 'NoneType'
#
# CFBD hands back exactly that shape for a game that is in progress, forfeited, or
# mid-correction. Every scheduled update from 22 Aug onward died on it, and because
# the half-hourly refresh job never re-fetches, the site stayed green and two weeks
# of results were silently discarded.
#
# So the definition lives here, once, and everything reads it from here.
FINAL_SQL = "home_score IS NOT NULL AND away_score IS NOT NULL"


def is_final(row):
    """True only when a game has both scores. Accepts a Row or a dict."""
    try:
        return row["home_score"] is not None and row["away_score"] is not None
    except (KeyError, IndexError):
        return False


def repair_half_scored(conn):
    """
    Blank any game carrying one score, and report how many. Returns the count.

    A half-scored row is not data, it is a snapshot of a game in flight. Storing it
    means every consumer has to defend against it forever; blanking it at the door
    means "final" stays a property of the row rather than a thing each caller has
    to re-derive. The next fetch rewrites it with both scores once the game ends.
    """
    n = conn.execute(
        "SELECT COUNT(*) c FROM games "
        "WHERE (home_score IS NULL) != (away_score IS NULL)").fetchone()["c"]
    if n:
        conn.execute("UPDATE games SET home_score = NULL, away_score = NULL "
                     "WHERE (home_score IS NULL) != (away_score IS NULL)")
        conn.commit()
    return n


def _migrate(conn):
    for table, column, decl in ADDED_COLUMNS:
        have = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if not have:
            continue                      # table itself is new; SCHEMA just made it
        if column not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
    conn.commit()
    repair_half_scored(conn)
    repair_duplicate_lines(conn)
    _archive_flagged_picks(conn)


def _archive_flagged_picks(conn):
    """
    Move any picks flagged voided IN PLACE into picks_voided. Returns the count.

    The first version of voiding set a flag and left the row where it was. Every
    reader excluded it correctly, so the record was right — and `lock` is
    `INSERT OR IGNORE` on game_id, so the slot stayed occupied and the replacement
    pick was silently dropped. 880 picks were withdrawn and 0 re-locked, which is
    the one outcome worse than either doing nothing or doing it properly.
    """
    import json as _json
    have = {r["name"] for r in conn.execute("PRAGMA table_info(picks_log)")}
    if "voided_at" not in have:
        return 0
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM picks_log WHERE voided_at IS NOT NULL")]
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO picks_voided (game_id, payload, voided_at, void_reason)"
            " VALUES (?,?,?,?)",
            (r["game_id"], _json.dumps(r), r["voided_at"],
             r.get("void_reason") or "no reason recorded"))
        conn.execute("DELETE FROM picks_log WHERE game_id=?", (r["game_id"],))
    if rows:
        conn.commit()
    return len(rows)


def connect(path=DB_PATH):
    """Open the database, creating it and its schema if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
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


# The order a book is trusted in. Canonical here rather than in fetch_cfb, because
# both the writer and the duplicate repair below have to agree about which of two
# rows for the same game is the one to keep.
LINE_PROVIDER_PRIORITY = ["consensus", "DraftKings", "Bovada", "ESPN Bet",
                          "Caesars Sportsbook (Colorado)",
                          "William Hill (New Jersey)"]


def repair_duplicate_lines(conn):
    """
    Collapse `lines` to ONE row per game. Returns how many were removed.

    The table is keyed (game_id, provider), which quietly permits a game to hold a
    row per book — and every join in this project is `LEFT JOIN lines ON game_id`,
    with no provider clause anywhere. One extra row therefore duplicates that game
    through the schedule, the bets board, the pick list and the bet matcher, and it
    does it silently: the row simply appears twice, its edge counted twice in every
    summary above it.

    It took a season to show up because it needs the preferred book to CHANGE. Early
    in a week only one book has posted, so `_pick_line` stores that one; when a
    higher-priority book appears, the next fetch stores that one too and the first is
    never removed. Two rows, two of everything downstream.

    Found by a QA assertion that the Staked card equals the staked column: the card
    sums a Map keyed by game_id and the column sums rendered rows, so a duplicate is
    the one thing that can make two views of one number disagree. $2,761.63 against
    $2,800.00, from two games out of nine hundred.
    """
    order = {p: i for i, p in enumerate(LINE_PROVIDER_PRIORITY)}
    dupes = [r["game_id"] for r in conn.execute(
        "SELECT game_id FROM lines GROUP BY game_id HAVING COUNT(*) > 1")]
    removed = 0
    for gid in dupes:
        rows = conn.execute(
            "SELECT provider, home_margin, total, home_ml FROM lines WHERE game_id=?",
            (gid,)).fetchall()
        # Priority first; then whichever row actually carries numbers, so a repair
        # never trades a populated line for an empty one from a better-named book.
        keep = sorted(rows, key=lambda r: (
            order.get(r["provider"], len(order)),
            -sum(x is not None for x in (r["home_margin"], r["total"], r["home_ml"])),
            r["provider"] or ""))[0]["provider"]
        removed += conn.execute(
            "DELETE FROM lines WHERE game_id=? AND provider<>?", (gid, keep)).rowcount
    if removed:
        conn.commit()
    return removed


def upsert_lines(conn, rows):
    # One row per game, enforced at the door. Without this the repair above would
    # have to run after every fetch to undo what the fetch just did.
    ids = sorted({r["game_id"] for r in rows})
    for gid in ids:
        keep = next(r["provider"] for r in rows if r["game_id"] == gid)
        conn.execute("DELETE FROM lines WHERE game_id=? AND provider<>?", (gid, keep))
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
