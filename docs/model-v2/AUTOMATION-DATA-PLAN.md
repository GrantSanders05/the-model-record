# The Model — Automation & Data Plan

**Goal:** Make the system hands-off without sacrificing decision-time integrity, API budget, durability, or auditability.

---

# 1. Automation principle

A sports model has several clocks. Treating all of them as one daily “update everything” job is wasteful and creates stale official picks.

Split the pipeline into:

1. **slow-changing data** — preseason priors, rosters, recruiting, venues;
2. **weekly data** — grades, advanced team metrics, rankings, returning/usage context;
3. **fast-changing data** — betting lines, availability reports, weather forecasts;
4. **event-driven data** — a grade-sheet edit, an official availability change, a kickoff approaching;
5. **postgame data** — final scores, close, grading, record publication.

Each gets its own cadence and failure policy.

---

# 2. GitHub Actions reliability constraint

GitHub's documentation explicitly warns that scheduled workflows can be delayed during high load and some queued scheduled jobs may be dropped. The repo has already measured substantial real-world schedule slippage.

Therefore:

- cron is a **backstop**, not proof that a time-sensitive snapshot happened;
- never fabricate a T-2h forecast after kickoff because a scheduled job was late;
- store the actual observation timestamp, not the nominal cron time;
- if a target horizon was missed, record `snapshot_status = missed`;
- use `repository_dispatch`/manual dispatch for event-driven refreshes where possible;
- schedule away from minute `00` because GitHub identifies the start of the hour as a high-load period;
- for high-frequency Saturday checks, use offset minutes such as `07,22,37,52` rather than `00,15,30,45`.

A missed snapshot is data. Hiding it creates survivorship bias.

---

# 3. Recommended workflows

## 3.1 `preseason.yml`

**Cadence:** weekly in offseason; daily during camp/portal-heavy windows if desired.

Fetch/update:

- FBS teams/conferences;
- venues;
- rosters;
- 247 talent composite;
- recruiting team and position-group data;
- transfer portal;
- returning production;
- coaching changes/tenures where available;
- prior-season advanced metrics;
- schedule.

Output:

- preseason feature snapshot per team;
- data-quality report;
- provenance hashes.

Do **not** hit these endpoints every 15 minutes on game day.

---

## 3.2 `grades-refresh.yml`

**Triggers:**

- repository dispatch from the Google Sheet Apps Script after edits;
- scheduled backstop every few hours during football week;
- manual dispatch.

Actions:

1. read the live sheet;
2. canonicalize/validate team names;
3. compare with previous grade snapshot;
4. write immutable `grade_snapshot` events;
5. calculate change deltas;
6. run formula/data-quality gates;
7. refresh shadow forecasts affected by changed teams;
8. do **not** rewrite already-official forecasts.

Important improvement over week-only semantics:

Store `observed_at` and `effective_at` timestamps. A Friday grade edit made before a Saturday game should be representable as legitimate Friday information without pretending it existed for earlier games in the same CFBD week.

Historical frozen “Week N Data” tabs remain useful as coarse snapshots, but V2 should make exact timestamps authoritative for new data.

---

## 3.3 `market-refresh.yml`

**Game-day cadence:** every 15–30 minutes during active windows; much slower otherwise.

API-budget strategy:

- query betting lines only in this job;
- do not refetch rankings/PPA/recruiting every time;
- store every provider quote returned;
- de-duplicate identical quotes by provider/game/line/price where appropriate while retaining first/last observation timestamps;
- write `line_history`/`market_quote` events.

Suggested windows:

```text
Mon-Tue: 2-4 snapshots/day
Wed-Thu: every 2-4 hours
Fri: every 1-2 hours
Sat active slate: every 15-30 minutes
Sun/postgame: final close verification
```

The exact schedule must stay under the current CFBD account's monthly quota. CFBD's current free tier lists 1,000 API calls/month, so endpoint splitting matters.

### Budget example

If one `/lines?year=...` request returns the season/week slate, then roughly 200-300 dedicated line calls/month can provide dense Saturday history while leaving budget for games, metrics, rankings, and recovery calls.

Add a usage guard:

```text
monthly_calls_used
monthly_calls_remaining
projected_month_end_usage
```

If projected usage exceeds a safety threshold, reduce nonessential refresh frequency rather than suddenly hitting quota on a Saturday.

---

## 3.4 `availability-refresh.yml`

This should not scrape all 138 ESPN rosters on every run.

### Scope

Only teams playing within the next relevant window (e.g. 96 hours), plus explicitly watched teams.

### Source adapters

#### Big Ten

For 2026 conference games, target official report times:

- T-3 days;
- T-2 days;
- T-1 day;
- final report T-2 hours.

Parse standardized statuses into immutable events.

#### SEC

Use the official SEC football availability reporting pages for conference games. Preserve report timestamp and status exactly as published.

#### ACC

Add the conference availability page as another structured source where available.

#### Fallback

ESPN roster/injury flags and verified team announcements can remain fallback sources, but source confidence must travel with the event.

### Status handling

Never mutate a prior status row. Store transitions:

```text
Questionable -> Probable -> Available
Questionable -> Out
...
```

This creates a dataset for calibrating the probability that each status ultimately means “does not play.”

---

## 3.5 `weather-refresh.yml`

Only run for outdoor games inside a useful forecast horizon.

Suggested snapshots:

- T-48h;
- T-24h;
- T-6h;
- T-2h.

Store:

- temperature;
- sustained wind;
- gusts;
- precipitation probability/rate;
- humidity if desired;
- pressure only if later shown useful;
- dome/roof status;
- source and observation timestamp.

Do not aggressively feature-engineer every weather variable at first. Wind is a natural totals hypothesis; test it rather than assuming every weather field matters.

CFBD's current API reference includes a game-weather endpoint; an external weather provider can be added only if it supplies better forecast-time snapshots or coverage.

---

## 3.6 `forecast-snapshots.yml`

This job generates all model versions — Champion plus Shadow Challengers — at standardized horizons.

### Target horizons

```text
T-72h
T-24h
T-6h
T-2h
optional T-30m
```

A snapshot job selects games whose kickoff falls within a tolerance around the target.

Example T-2h acceptance window:

```text
target = kickoff - 120 minutes
accept if run occurs between target - 20m and target + 20m
```

If GitHub is delayed outside the window, write a missed-horizon event instead of silently labeling a T-70m forecast “T-2h.”

### Official signal

Initially recommend **T-2h** as the official lock because it captures much of the week's information and lines while preceding kickoff enough to be operationally useful. This can be revisited after comparing horizon performance and CLV.

The official strategy must designate exactly one horizon before the week starts. Do not select whichever horizon performed best afterward.

---

## 3.7 `results-close.yml`

Run after games begin/end and the provider has final numbers.

Responsibilities:

1. fetch final scores/status;
2. identify the formal closing quote under the documented close rule;
3. grade every official signal at its exact locked line/price;
4. calculate closing-line diagnostics separately;
5. update model-vs-market metrics;
6. update experiment results;
7. publish dashboard;
8. verify every final game with an official signal is graded;
9. verify no post-kick forecast was admitted into the record.

---

## 3.8 `health.yml`

Keep the existing health philosophy but add model-specific integrity checks.

### Required checks

- latest market snapshot age;
- latest grade snapshot age;
- CFBD calls remaining;
- teams with alias failures;
- games within 3h with no current quote;
- games within 3h with unavailable/old availability snapshot;
- official horizon misses;
- stale locks;
- ungraded finals;
- model version missing hash;
- public bundle private-field scan;
- event store/database reconciliation;
- database rebuild-from-events test;
- current page freshness;
- workflow failures.

A health check should open one actionable issue per failure category and close it after recovery, as the current code already attempts for other failures.

---

# 4. Durable state architecture

## Current problem

The working SQLite DB is valuable, but the workflow's dependency on Actions cache is dangerous for unique, non-regenerable history. GitHub documentation notes caches can be evicted and are designed for data that can be regenerated/re-downloaded.

## Recommended zero-new-service design

### 4.1 SQLite = fast materialized view

Keep `data/model.db` for queries and publishing.

### 4.2 Append-only event journal = source of truth

Create a dedicated branch, e.g.:

```text
data-state
```

Files:

```text
state/market/2026/09/05.jsonl
state/grades/2026/09.jsonl
state/availability/2026/09/05.jsonl
state/forecasts/2026/09/05.jsonl
state/signals/2026/09/05.jsonl
state/results/2026/09/05.jsonl
state/model-registry.jsonl
state/voids.jsonl
```

Each event has a stable event ID/hash.

On update:

1. restore/rebuild local SQLite;
2. ingest new external data;
3. append events;
4. transactionally apply events to SQLite;
5. run checks;
6. commit changed daily event files to `data-state`;
7. publish.

### Why a separate branch

It keeps automated state commits out of the product/model-development history on `main` while preserving Git's durable audit trail.

### Rebuild test

Once per week:

- create an empty SQLite DB;
- replay all event files;
- compare canonical row counts/hashes with production materialized state.

If they differ, fail health and do not claim the data are reproducible.

---

# 5. Market data model

## Do not keep one row per game as the underlying truth

`lines` can remain a convenience table, but add a normalized quote history.

Example SQL shape:

```sql
CREATE TABLE market_quotes (
  quote_id TEXT PRIMARY KEY,
  game_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  home_spread REAL,
  home_spread_price INTEGER,
  away_spread_price INTEGER,
  total REAL,
  over_price INTEGER,
  under_price INTEGER,
  home_ml INTEGER,
  away_ml INTEGER,
  source TEXT,
  raw_hash TEXT,
  UNIQUE(game_id, provider, observed_at)
);
```

Derived views:

- `market_current`
- `market_open`
- `market_close`
- `market_consensus_current`
- `market_best_price`

---

# 6. Provider-selection policy

The current priority order is useful for fallback display but should not define research truth.

### Display line

Use a documented consensus/reference line.

### Actual bet line

Use the exact book/price the user selected.

### Model research line

At each horizon, calculate:

- median spread;
- median total;
- de-vigged consensus ML probability;
- provider dispersion;
- min/max spread;
- best available price.

Keep the provider-level source rows forever.

---

# 7. Feature data cadence

| Data | Cadence | Historical importance | Production importance |
|---|---|---:|---:|
| Games/schedule | daily + postgame | high | high |
| Betting quotes | dense near kickoff | critical | critical |
| Hand grades | event-driven | critical | critical |
| Final scores | postgame | critical | critical |
| Player availability | official report cadence | high | high |
| Weather | T-48/24/6/2 | medium | medium/high totals |
| Team advanced stats | after games | high | medium/high |
| Player usage/PPA | after games | high | high for injuries |
| Rankings | weekly | low/benchmark | low |
| Talent composite | preseason | medium | medium |
| Recruiting groups | preseason | medium | medium |
| Returning production | preseason | medium/high | medium/high |
| Transfer portal | offseason + changes | high prior | high prior |
| Venue/travel | schedule-time | stable | medium |
| Social/capper picks | timestamped, later phase | optional | optional |

---

# 8. CFBD feature additions worth implementing

Current CFBD documentation exposes useful inputs that can be fetched efficiently.

## Preseason / roster

- `/player/returning`
- `/player/portal`
- `/talent`
- `/recruiting/teams`
- `/recruiting/groups`
- `/roster`

## In-season

- `/ppa/teams`
- `/ppa/games`
- `/player/usage`
- `/ppa/players/season`
- `/stats/season/advanced`
- `/stats/game/advanced`
- `/stats/game/havoc`
- play-by-play where a specifically needed derived metric cannot be obtained more cheaply.

## Baselines/reference ratings

- SP+
- SRS
- Elo
- FPI
- CORE

Use these as baselines/context. Respect each endpoint's historical availability and methodology. In particular, CFBD warns that historical CORE is retrospective rather than a contemporaneous archived live forecast, so it must not be naively inserted into decision-time historical backtests.

---

# 9. API quota safeguards

CFBD currently lists 1,000 calls/month on the free tier.

Add a central API budget manager.

Example policies:

```text
SOFT_LIMIT = 800
HARD_RESERVE = 100
```

At soft limit:

- stop nonessential research refreshes;
- retain line/game/result critical calls;
- lower roster/weather redundancy.

At hard reserve:

- only final/line/critical recovery calls.

Never let a research chart consume the calls needed to record Saturday's official prices.

Cache immutable historical endpoint responses by request parameters indefinitely where allowed.

---

# 10. Failure policy

Every fetch operation returns one of:

```text
success
no_data_expected
source_unavailable
quota_blocked
parse_failed
stale_data
```

Do not convert all failures into empty lists. “Nobody is injured” and “the injury source failed” must remain distinguishable — a principle `roster_watch.py` already correctly recognizes.

### Fail closed for official signals when critical data are missing

Examples:

- no valid market price: no official signal;
- grade model selected but a required team has no grade: no official signal;
- requested T-2 horizon missed after kickoff: no fabricated lock;
- closing quote missing: realized result still gradeable at locked line, CLV marked unavailable;
- weather unavailable: spread signal may still run if strategy does not require it; weather-dependent totals challenger should flag degraded mode.

---

# 11. Public/private boundary

The public research export must use an allow-list.

Public-safe examples:

- model forecasts;
- model record;
- aggregate CLV;
- team grades only if intentionally public;
- market data;
- experiment summaries.

Private by default:

- personal stakes;
- sportsbook/account choices tied to the user;
- personally logged bets;
- bankroll;
- any future account identifiers/tokens.

Add a test that recursively scans the public JSON for forbidden keys before deployment.

---

# 12. Recommended kickoff-day sequence

For a Saturday 7:30 PM ET game:

```text
Wed 7:30 PM   T-72 forecast + market quote
Fri 7:30 PM   T-24 forecast + market quote
Sat 1:30 PM   T-6 forecast + weather + availability
Sat 5:30 PM   T-2 final official forecast/signal
Sat ~7:15 PM  closing market capture begins/final pre-kick quote
7:30 PM       kickoff; no new official forecast accepted
Postgame      final score + locked-line grade
Later         closing-line diagnostic + experiment update
```

If an official conference availability report is due two hours before kickoff, ingest it before the T-2 model run or use a short deterministic sequence:

```text
T-2:05 availability refresh
T-2:02 market refresh
T-2:00 forecast/signal lock
```

Because GitHub may delay scheduled jobs, use a tolerance window and record actual times. If exact orchestration becomes important enough that GitHub scheduling cannot meet it, move only the trigger layer to a more reliable external scheduler while keeping the model execution/audit trail in GitHub.

---

# 13. Definition of “fully automated”

The system is fully automated only when all of these hold:

- grade edits propagate without manual export;
- market quotes are captured at intended horizons;
- availability reports are ingested automatically;
- weather is captured automatically where used;
- models/challengers generate forecasts automatically;
- official signals lock only under valid pregame conditions;
- results grade automatically at the locked line/price;
- close is captured and CLV calculated automatically;
- unique state survives cache loss;
- health alerts failures and stale data;
- experiment dashboards update without manually choosing favorable slices;
- no personally sensitive wager data are published accidentally.

Automation is not “a cron exists.” Automation means the system knows when it **missed** something and refuses to pretend otherwise.

---

# Sources

- GitHub scheduled events: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- GitHub caching/eviction: https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching
- GitHub cache vs artifacts: https://docs.github.com/en/actions/concepts/workflows-and-actions/dependency-caching
- CFBD tiers: https://collegefootballdata.com/api-tiers
- CFBD API reference: https://api.collegefootballdata.com/
- Big Ten 2026 availability: https://bigten.org/fb/article/60284/
- SEC availability: https://www.secsports.com/fbreports
- ACC availability: https://theacc.com/sports/2025/8/28/availability-reporting-football.aspx
