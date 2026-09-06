"""
state_events.py — the facts that cannot be re-fetched, written where they survive.

The GitHub Actions cache is an accelerator. GitHub's own documentation says
caches are for reusable files such as dependencies and intermediate outputs, and
that a job must be able to recover when one is unavailable; caches are evicted
after inactivity and under storage pressure. That is fine for a CFBD response
cache, which can be re-fetched.

It is not fine for the only copy of:

    what a book was offering at 4:58pm on a Saturday
    what a team's grades were before a Friday edit
    which model version produced which forecast
    what a strategy decided, and why it declined
    which signals were official
    what was withdrawn, and for what reason

None of those can be reconstructed from a public API. Re-fetching /lines today
returns a retrospective number, not the one that was on the screen. So SQLite
becomes a MATERIALIZED WORKING DATABASE and this journal becomes the record.

THE SHAPE
---------
One JSON object per line, partitioned by date, on a dedicated branch. No service,
no cost, and readable by anything. Append-only: a superseding fact is a new
event, a withdrawal is a void event, and a conflicting write under the same
logical key becomes a correction rather than an overwrite.

IDEMPOTENCE COMES FROM THE ID
-----------------------------
`event_id` is derived from the event's own identity, so a workflow that retries
produces the same id, the append is recognised as a duplicate and skipped, and
the journal does not grow a second copy of a fact that happened once.
"""

import datetime as dt
import json
import os

import provenance

EVENT_VERSION = 1

# What each stream holds, where it lives, and WHETHER IT MAY BE PUBLISHED.
#
# Partitioned by day for the high-volume streams and by month for the slow ones,
# so no single file grows without bound and a day can be inspected by opening one
# file.
#
# THE THIRD COLUMN IS A SECURITY BOUNDARY, not a preference. This repository is
# PUBLIC. A grade_snapshot payload carries the eight position grades for a named
# team -- the film evaluation that is the entire moat, and the one input the
# .gitignore at the root of this repo exists to keep off the internet. Writing
# the journal to a `data-state` branch here without this column would publish
# every grade Grant has ever entered, in a machine-readable form, for good.
STREAMS = {
    # name                  folder                grain   publishable
    "market_quote":        ("state/market",       "day",   True),
    "grade_snapshot":      ("state/grades",       "month", False),
    "forecast":            ("state/forecasts",    "day",   True),
    "strategy_evaluation": ("state/strategy",     "day",   True),
    "signal":              ("state/signals",      "day",   True),
    "signal_result":       ("state/results",      "day",   True),
    "game_result":         ("state/results",      "day",   True),
    "snapshot_miss":       ("state/misses",       "day",   True),
    "void":                ("state/voids",        "month", True),
    "model_registry":      ("state/model-registry", "flat", True),
    # NON-REGENERABLE, both of them, which is the test §14.1 sets. ESPN's answer
    # to "who is out" is a statement about NOW; nobody can ask it again about
    # last Thursday, and a lost cache would destroy the transitions permanently.
    # Same for a forecast: the temperature at T24 is unknowable once T24 is past.
    #
    # Availability is FALSE here and that is not "withheld". The flag means "may
    # this leave the machine UNREDACTED", and availability may not: who is out is
    # a public fact anyone can read on ESPN, but `impact_points` is computed by
    # removing the player and re-rating all 138 teams from the film grades. It is
    # a grade number wearing different units. So the stream publishes with that
    # one field stripped, exactly the path grade_snapshot takes.
    "availability":        ("state/availability", "month", False),
    "weather":             ("state/weather",      "month", True),
}

# Fields removed from a grade_snapshot before it may leave this machine. What
# SURVIVES is the identity: the snapshot id, the team, both timestamps, and the
# source_hash of the vector. So a public journal can still prove WHICH grade
# state was in force for a forecast and when it changed -- the provenance
# property -- without publishing what the numbers were.
#
# The numbers themselves stay in the local database and in Grant's sheet. That is
# a real reduction in durability and it is the correct trade: a market quote
# cannot be re-fetched, and a grade can be re-entered.
REDACTED_FIELDS = {
    "grade_snapshot": ["qb", "rb", "wr", "ol", "dl", "lb", "db", "coach_st",
                       "extra_json"],
    # How far the line moves without this player is computed by removing him and
    # re-rating all 138 teams from the film grades. It is a grade number in
    # different units, and it does not leave the machine.
    "availability": ["impact_points"],
}


def publishable(event_type):
    """May this stream leave the machine unredacted?"""
    spec = STREAMS.get(event_type)
    return bool(spec and spec[2])


def redact(event):
    """
    A version of an event that is safe to publish. -> dict | None

    A stream marked unpublishable is stripped to its identity rather than
    dropped, because a public journal that silently omits a stream cannot be told
    apart from one where nothing happened.

    The redacted event carries `redacted: [fields]` so a reader knows the payload
    is partial, and its own hash is recomputed over what remains -- verification
    must pass on what is actually there, not on what used to be.
    """
    et = event.get("event_type")
    if publishable(et):
        return event
    fields = REDACTED_FIELDS.get(et)
    if fields is None:
        return None                        # unpublishable and no redaction defined
    payload = {k: v for k, v in (event.get("payload") or {}).items()
               if k not in fields}
    out = dict(event)
    out["payload"] = payload
    out["payload_hash"] = provenance.payload_hash(payload)
    out["redacted"] = sorted(fields)
    return out

DEFAULT_STATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def make_event(event_type, payload, *, occurred_at=None, source_run=None,
               event_id=None):
    """
    One journal line. -> dict

    `occurred_at` is when the fact happened; `recorded_at` is when this system
    wrote it down. They differ whenever a run is late, and keeping both is what
    lets a replay order events by the world's clock rather than the runner's.
    """
    if event_type not in STREAMS:
        raise ValueError("unknown event type %r; add it to STREAMS" % (event_type,))
    occurred = occurred_at or _now().isoformat()
    ev = {
        "event_id": event_id or provenance.stable_id(
            "event", {"t": event_type, "p": payload}),
        "event_type": event_type,
        "event_version": EVENT_VERSION,
        "occurred_at": occurred,
        "recorded_at": _now().isoformat(),
        "payload": payload,
        "source_run": source_run,
    }
    ev["payload_hash"] = provenance.payload_hash(payload)
    return ev


def _partition(event, state_dir):
    folder, grain = STREAMS[event["event_type"]][:2]
    base = os.path.join(state_dir, os.path.relpath(folder, "state"))
    ts = event["occurred_at"][:10]
    if grain == "flat":
        return base + ".jsonl"
    y, m, d = ts[:4], ts[5:7], ts[8:10]
    return (os.path.join(base, y, m, "%s.jsonl" % d) if grain == "day"
            else os.path.join(base, y, "%s.jsonl" % m))


def existing_ids(state_dir=None):
    """Every event id already in the journal. Used to make appends idempotent."""
    state_dir = state_dir or DEFAULT_STATE_DIR
    ids = set()
    for root, _dirs, files in os.walk(state_dir):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            with open(os.path.join(root, fn)) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ids.add(json.loads(line)["event_id"])
                    except (ValueError, KeyError):
                        continue
    return ids


def append(events, state_dir=None, *, known=None):
    """
    Append events, skipping any whose id is already present. -> (written, skipped)

    Writes are grouped per file and each file is written with a temp-and-replace,
    so a run killed mid-append leaves a complete file rather than a truncated
    line that no parser can recover.
    """
    state_dir = state_dir or DEFAULT_STATE_DIR
    seen = set(known) if known is not None else existing_ids(state_dir)
    by_file = {}
    written = skipped = 0
    for ev in events:
        if ev["event_id"] in seen:
            skipped += 1
            continue
        seen.add(ev["event_id"])
        by_file.setdefault(_partition(ev, state_dir), []).append(ev)
        written += 1

    for path, evs in by_file.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        prior = ""
        if os.path.exists(path):
            with open(path) as fh:
                prior = fh.read()
            if prior and not prior.endswith("\n"):
                prior += "\n"
        with open(tmp, "w") as fh:
            fh.write(prior)
            for ev in evs:
                fh.write(provenance.canonical_json(ev) + "\n")
        os.replace(tmp, path)
    return written, skipped


def read_all(state_dir=None):
    """
    Every event, ordered by (occurred_at, event_id). -> [dict]

    Ordered by the world's clock and not by file order, so a replay applies
    facts in the order they happened however the files were written. The id
    breaks ties so the order is total and identical on every machine.
    """
    state_dir = state_dir or DEFAULT_STATE_DIR
    out = []
    for root, _dirs, files in os.walk(state_dir):
        for fn in sorted(files):
            if not fn.endswith(".jsonl"):
                continue
            with open(os.path.join(root, fn)) as fh:
                for n, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError as e:
                        raise ValueError("%s line %d is not JSON: %s"
                                         % (os.path.join(root, fn), n, e))
    # (occurred_at, recorded_at, event_id). The middle key is what makes a
    # correction land AFTER the event it corrects: a signal's occurred_at is when
    # it was created and does not move when the result is filled in, so ordering
    # by occurred_at alone would leave the winner decided by a hash.
    out.sort(key=lambda e: (e.get("occurred_at") or "",
                            e.get("recorded_at") or "",
                            e.get("event_id") or ""))
    return out


def verify(events):
    """
    Check every event against its own hash. -> [problems]

    An event whose payload no longer hashes to its recorded payload_hash has been
    EDITED. The journal's only promise is that it is append-only, and this is
    what makes that promise checkable rather than merely stated.
    """
    problems = []
    seen = {}
    for ev in events:
        eid = ev.get("event_id")
        want = ev.get("payload_hash")
        got = provenance.payload_hash(ev.get("payload"))
        if want != got:
            problems.append("%s: payload does not match its hash (%s vs %s)"
                            % (eid, str(want)[:12], got[:12]))
        if eid in seen and seen[eid] != got:
            problems.append("%s: two events share an id with different payloads" % eid)
        seen[eid] = got
    return problems


# ── turning database rows into events ────────────────────────────────────────

EXPORTS = [
    ("market_quote", "market_quotes", "quote_id", "observed_at"),
    ("grade_snapshot", "grade_snapshots", "grade_snapshot_id", "effective_at"),
    ("model_registry", "model_registry", "model_version", "created_at"),
    ("forecast", "forecast_log", "forecast_id", "generated_at"),
    ("strategy_evaluation", "strategy_evaluations", "evaluation_id", "evaluated_at"),
    ("signal", "signal_log", "signal_id", "created_at"),
    ("game_result", "game_results_v2", "game_id", "finalized_at"),
    ("snapshot_miss", "snapshot_misses", "miss_id", "detected_at"),
    ("void", "v2_void_events", "void_id", "voided_at"),
    ("availability", "availability_events", "event_id", "observed_at"),
    ("weather", "weather_snapshots", "snapshot_id", "observed_at"),
]


def export_from_db(conn, *, since=None, source_run=None):
    """
    Every V2 row, as events. -> [dict]

    The id of the row becomes part of the id of the event, so exporting the same
    database twice produces the same events and appending them is a no-op.
    """
    out = []
    for event_type, table, key, time_col in EXPORTS:
        q = "SELECT * FROM %s" % table
        args = []
        if since:
            q += " WHERE %s >= ?" % time_col
            args.append(since)
        for r in conn.execute(q + " ORDER BY %s, %s" % (time_col, key), args):
            payload = dict(r)
            # THE ID INCLUDES THE PAYLOAD, so a row that has CHANGED produces a
            # new event rather than being skipped as a duplicate.
            #
            # Keying on the row id alone was wrong and the failure was silent: a
            # signal is created ungraded and its result is filled in later, so
            # every re-export re-derived the same id, the append was treated as a
            # duplicate, and the graded result never reached the journal. The
            # database and its record drifted apart while both looked healthy.
            #
            # An unchanged row still dedups, because an unchanged payload hashes
            # the same. A changed one is a CORRECTION, appended beside the
            # original — which is what append-only means. Nothing is overwritten;
            # the replay applies them in order and the later one wins.
            out.append(make_event(
                event_type, payload,
                occurred_at=payload.get(time_col),
                source_run=source_run,
                event_id=provenance.stable_id("event", {
                    "t": event_type, "k": payload.get(key),
                    "h": provenance.payload_hash(payload)})))
    return out


def write_publishable(state_dir, out_dir):
    """
    Write a redacted copy of the journal, safe for a public branch. -> counts

    THE ONLY FUNCTION ALLOWED TO PRODUCE A JOURNAL THAT LEAVES THIS MACHINE.
    `state_commit.sh` publishes `out_dir` and never `state_dir`, so forgetting
    to redact is not a thing a caller can do by omission -- it would have to
    publish the wrong directory by name.
    """
    events = read_all(state_dir)
    kept, redacted, dropped = [], 0, 0
    for ev in events:
        r = redact(ev)
        if r is None:
            dropped += 1
            continue
        if r is not ev:
            redacted += 1
        kept.append(r)
    if os.path.isdir(out_dir):
        import shutil
        shutil.rmtree(out_dir)
    written, _skipped = append(kept, out_dir, known=set())
    return {"events": len(events), "written": written,
            "redacted": redacted, "dropped": dropped}


def audit_publishable(out_dir, positions=("qb", "rb", "wr", "ol", "dl", "lb",
                                          "db", "coach_st")):
    """
    Scan a journal about to be published for film grades. -> [problems]

    THE FORBIDDEN THING IS A GRADE VALUE ATTACHED TO A NAMED TEAM, and the check
    has to say that precisely. A first version flagged any key called `qb` or
    `ol` anywhere and immediately fired on `config_json.grade_weights` -- the
    model's per-position weights, every one of them 1.0, which are public
    arithmetic and part of the transparency this project sells. A gate that
    cries wolf on the config would be switched off within a week.

    So two rules, both about meaning rather than spelling:

      1. a grade_snapshot event may not carry position fields at all;
      2. no object anywhere may carry a team name AND numeric position values,
         which is the shape a leak takes however it got there.

    Recursive, including into JSON stored as a string: a blob inside `extra_json`
    is exactly as published as a top-level key.
    """
    problems = []
    posset = set(positions)

    def walk(node, path, event_id, event_type):
        if isinstance(node, dict):
            if event_type == "grade_snapshot":
                for k in sorted(posset & set(node)):
                    problems.append(
                        "%s: %s.%s — a grade_snapshot may not carry position values"
                        % (event_id, path, k))
            has_team = any(k in node for k in ("team", "school", "team_name"))
            graded = sorted(k for k in posset & set(node)
                            if isinstance(node[k], (int, float)))
            if has_team and len(graded) >= 2:
                problems.append(
                    "%s: %s carries a team name and its grades (%s)"
                    % (event_id, path, ", ".join(graded)))
            for k, v in node.items():
                walk(v, "%s.%s" % (path, k), event_id, event_type)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (path, i), event_id, event_type)
        elif isinstance(node, str) and node.startswith(("{", "[")):
            try:
                walk(json.loads(node), path, event_id, event_type)
            except ValueError:
                pass

    for ev in read_all(out_dir):
        walk(ev.get("payload"), "payload", ev.get("event_id"), ev.get("event_type"))
    return problems
