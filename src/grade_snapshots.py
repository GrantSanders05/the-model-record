"""
grade_snapshots.py — a team's grades as of an instant, not as of a week.

The `grades` table is keyed by (season, week, team, position). That is the right
shape for a backtest over frozen weekly tabs and the wrong shape for live
information, because it cannot express "Grant edited the sheet on Friday
afternoon". Under a week key that edit either

  * disappears until next week, so the Saturday forecast cannot see it, or
  * overwrites week N, so Monday's forecast retroactively knew a Friday fact.

The first wastes the edge; the second is leakage. Both are silent.

TWO TIMES, AND THEY ARE NOT THE SAME
------------------------------------
    observed_at   when this system saw the value
    effective_at  when the value is allowed to influence a forecast

For a live sheet edit they are equal, and that is the normal case. They differ
only when a human explicitly records that a value represents an earlier known
state. Nothing here ever computes an effective time earlier than the observation
on its own initiative: a grade may not become retroactively true.

ONE ROW PER TEAM, NOT PER POSITION
----------------------------------
A snapshot carries the whole vector. Per-position rows would let a forecast pick
up this week's QB beside last week's OL and call the result a team, and the hash
of such a mixture describes nothing that ever existed.

WHAT IT DOES NOT REPLACE
------------------------
`grades` stays exactly as it is. Every backtest, the whole 2025 development
history and `engine.GradeRater` keep reading it and keep getting the same
answers. This is added beside it for forecasts that need an exact as-of.
"""

import datetime as dt

import provenance

# The vector. Order is fixed so the hash of a team's grades is stable.
POSITIONS = ["qb", "rb", "wr", "ol", "dl", "lb", "db", "coach_st"]

# Where a snapshot came from, which decides how much it can be trusted to be a
# contemporaneous fact rather than a reconstruction.
SOURCE_LIVE = "live_sheet"          # the tab Grant edits, seen now
SOURCE_WEEKLY = "weekly_tab"        # a frozen "Week N Data" tab
SOURCE_BACKFILL = "backfill"        # reconstructed from the grades table

COLUMNS = ["grade_snapshot_id", "sport", "season", "team", "observed_at",
           "effective_at", "source_type", "source_name", "source_hash",
           "qb", "rb", "wr", "ol", "dl", "lb", "db", "coach_st",
           "extra_json", "created_at"]


def vector_hash(values):
    """
    Hash of the grade vector alone. Two syncs of an unchanged sheet agree.

    Only the eight graded positions take part: not the timestamp, not the tab
    name, not the row count. That is what makes "has anything changed?" a
    question about the football and not about the plumbing.
    """
    return provenance.payload_hash({p: values.get(p) for p in POSITIONS})


def build_snapshot(*, sport, season, team, values, observed_at,
                   effective_at=None, source_type=SOURCE_LIVE, source_name=None,
                   extra=None):
    """
    One team's full grade vector, content-addressed. -> dict

    `effective_at` defaults to `observed_at`. Passing an EARLIER one is how a
    human says "this value was already true then", and it is refused here unless
    the caller has said so explicitly by passing it -- there is no code path that
    derives a backdated effective time on its own.
    """
    eff = effective_at or observed_at
    src_hash = vector_hash(values)
    snap = {
        "sport": sport, "season": int(season), "team": team,
        "observed_at": observed_at, "effective_at": eff,
        "source_type": source_type, "source_name": source_name,
        "source_hash": src_hash,
        "extra_json": provenance.canonical_json(extra) if extra else None,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    for p in POSITIONS:
        snap[p] = values.get(p)
    snap["grade_snapshot_id"] = provenance.stable_id("grade_snapshot", {
        "s": sport, "y": season, "t": team, "e": eff, "h": src_hash})
    return snap


def latest_snapshot(conn, sport, season, team):
    """The most recent snapshot on record for a team, by effective time."""
    r = conn.execute(
        "SELECT * FROM grade_snapshots WHERE sport=? AND season=? AND team=?"
        " ORDER BY effective_at DESC, observed_at DESC, grade_snapshot_id DESC LIMIT 1",
        (sport, season, team)).fetchone()
    return dict(r) if r else None


def grade_asof(conn, sport, season, team, when):
    """
    The grade vector in force at `when`. -> dict | None

    THE LEAKAGE GUARD, and it is one `<=`. A snapshot whose effective time is
    later than the forecast cannot be returned to it, so a Friday edit cannot
    reach a Wednesday forecast however the caller asks.

    Ordered by (effective_at, observed_at, id) so that two snapshots effective at
    the same instant resolve the same way on every machine and every run.
    """
    r = conn.execute(
        "SELECT * FROM grade_snapshots"
        " WHERE sport=? AND season=? AND team=? AND effective_at <= ?"
        " ORDER BY effective_at DESC, observed_at DESC, grade_snapshot_id DESC LIMIT 1",
        (sport, season, team, when)).fetchone()
    return dict(r) if r else None


def vector_of(row):
    """{position: grade} from a snapshot row, or None."""
    if row is None:
        return None
    return {p: row[p] for p in POSITIONS}


def append_if_changed(conn, snap):
    """
    Store a snapshot only when the vector actually moved. -> bool (stored)

    A sync that finds nothing changed must not write a row, or the journal fills
    with identical vectors and "when did this team's grades change" becomes
    unanswerable. Compared on the vector hash, so a re-read of the same sheet at
    a new time is correctly a non-event.
    """
    prev = latest_snapshot(conn, snap["sport"], snap["season"], snap["team"])
    if prev is not None and prev["source_hash"] == snap["source_hash"]:
        return False
    row = {k: snap.get(k) for k in COLUMNS}
    conn.execute(
        "INSERT OR IGNORE INTO grade_snapshots (%s) VALUES (%s)"
        % (",".join(COLUMNS), ",".join(":" + c for c in COLUMNS)), row)
    return True


def sync_from_records(conn, records, *, sport, season, observed_at,
                      source_type=SOURCE_LIVE, source_name=None,
                      effective_at=None, commit=True):
    """
    Turn `import_workbook.parse_rows` output into snapshots. -> (stored, examined)

    `records` are per-position rows; they are gathered into one vector per team
    before anything is written, so a partial sheet cannot produce a snapshot that
    mixes eras.
    """
    by_team = {}
    for r in records:
        pos = r.get("position")
        if pos in POSITIONS:
            by_team.setdefault(r["team"], {})[pos] = r.get("grade")
    stored = 0
    for team, values in sorted(by_team.items()):
        snap = build_snapshot(
            sport=sport, season=season, team=team, values=values,
            observed_at=observed_at, effective_at=effective_at,
            source_type=source_type, source_name=source_name)
        if append_if_changed(conn, snap):
            stored += 1
    if commit and stored:
        conn.commit()
    return stored, len(by_team)


def missing_positions(conn, sport, season, team, when):
    """
    Which graded positions a team's as-of vector is missing. -> [str]

    A blank is not a zero. The strategy layer decides whether an incomplete
    vector may back an official signal; this only reports what is absent.
    """
    row = grade_asof(conn, sport, season, team, when)
    if row is None:
        return list(POSITIONS)
    return [p for p in POSITIONS if row[p] is None]


def backfill_from_grades(conn, sport, season=None, commit=True):
    """
    Reconstruct snapshots from the `grades` table. -> (stored, teams_seen)

    For history only. The weekly grades carry no timestamp, so an effective time
    has to be DERIVED, and the honest derivation is the earliest kickoff of the
    week the grade was stamped effective for: that is the first moment a forecast
    could legitimately have used it.

    Every row is labelled `backfill`, because it is a reconstruction. A snapshot
    that says `live_sheet` claims somebody watched the value appear; these did
    not, and the distinction is the difference between evidence and an inference
    that looks like evidence.

    Weeks with no scheduled game get no snapshot rather than a guessed time.
    """
    args = [sport]
    where = "sport=?"
    if season is not None:
        where += " AND season=?"
        args.append(season)

    starts = {}
    for r in conn.execute(
            "SELECT season, week, MIN(kickoff) k FROM games WHERE " + where
            + " AND kickoff IS NOT NULL GROUP BY season, week", args):
        if r["k"]:
            starts[(r["season"], r["week"])] = r["k"]

    rows = list(conn.execute(
        "SELECT season, week, team, position, grade FROM grades WHERE " + where
        + " ORDER BY season, week, team", args))
    vectors = {}
    for r in rows:
        if r["position"] in POSITIONS:
            vectors.setdefault((r["season"], r["week"], r["team"]), {})[
                r["position"]] = r["grade"]

    stored, skipped = 0, 0
    for (yr, wk, team), values in sorted(vectors.items()):
        eff = starts.get((yr, wk))
        if eff is None:
            # Week 99 is the importer's sentinel for the Team Data tab, and a
            # week with no scheduled game has no first moment. Neither gets an
            # invented timestamp.
            skipped += 1
            continue
        snap = build_snapshot(
            sport=sport, season=yr, team=team, values=values,
            observed_at=eff, effective_at=eff,
            source_type=SOURCE_BACKFILL,
            source_name="grades week %s" % wk,
            extra={"reconstructed_from": "grades", "effective_week": wk,
                   "effective_at_rule": "earliest kickoff of the effective week"})
        if append_if_changed(conn, snap):
            stored += 1
    if commit and stored:
        conn.commit()
    return stored, len(vectors), skipped
