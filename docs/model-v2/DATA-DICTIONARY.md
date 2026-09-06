# Data dictionary

Every V2 table, what one row means, and what it is not.

Legacy tables (`games`, `lines`, `line_history`, `rankings`, `grades`,
`team_form`, `team_game_stats`, `picks_log`, `picks_voided`, `backtest_runs`) are
unchanged and still read. Nothing below replaces them.

---

## `market_quotes` — one book, one game, one moment

Append-only. A re-observation is a **new row**, which is what makes a movement
history exist. `quote_id` is content-addressed, so re-fetching an identical
observation collides and is ignored.

| column | meaning |
|---|---|
| `provider` | canonicalized. CFBD sends "DraftKings" and "Draft Kings" in the same response; a rename must not look like market movement, and the raw name is kept in `source` |
| `observed_at` | when **this system saw it**. The feed does not say when a book posted a number, and this is never presented as if it did |
| `home_spread` | house convention: positive means home favoured |
| `*_price` | `NULL` when the feed does not supply it, which for spread and total juice is always |
| `quality_status` | `valid` or `suspect`. A book posting nonsense is itself an observation; it is flagged and stored, not dropped |

## `market_snapshots` — the market a forecast actually saw

Derived, immutable, and named by policy. Carries the constituent `quote_ids_json`
so the consensus can be recomputed from the same inputs.

`status` is `valid_consensus` (2+ fresh books), `single_provider` (a quote, not a
consensus), `stale_fallback` (only books older than the window) or `missing`. A
snapshot always exists: "there was no market" is a fact a forecast needs to
record, and returning nothing makes it indistinguishable from nobody having asked.

## `grade_snapshots` — a team's whole vector, as of an instant

One row per **team**, not per position: a forecast that picked up this week's QB
beside last week's line would be a team that never existed, and its hash would
describe nothing.

| column | meaning |
|---|---|
| `observed_at` | when the system saw it |
| `effective_at` | when it may influence a forecast. Equal to `observed_at` for a live edit; **never earlier unless a human recorded why** |
| `source_hash` | hash of the eight graded positions ONLY — not the timestamp, not the tab name. "Has anything changed?" is a question about the football |
| `source_type` | `live_sheet`, `weekly_tab`, or `backfill`. A backfill is a reconstruction and says so; a row claiming `live_sheet` claims somebody watched the value appear |

## `feature_snapshots` — exactly what a model was given

Hashed. Contains the two grade vectors and their ids, the market snapshot id and
its consensus, the ranks in force, the venue, and the Champion's accrued state.
Contains **no result**: no score, no actual margin, no rank published after
kickoff, no statistic computed from the game being predicted.

## `model_registry` — code + config + feature schema, frozen

`config_json` is the **fully merged** config. `config_hash` is over that, so a
change to an engine default counts as a change to the model even when the file
did not move. A version whose hash changes is a **new version**; redefining one
would turn every forecast already filed under it into a claim about a model
nobody can reconstruct.

`role` is `champion`, `challenger`, `baseline` or `retired`.

## `forecast_log` — what a model believed

Never deleted because a strategy later declined it. Records the horizon it aimed
at, the real `generated_at`, the signed `horizon_delta_seconds` and a
`snapshot_status`, so a late run is visible as late.

`borrowed_fallback` is 1 when Elo answered because a team had no film grade.
`provenance_quality` is `complete`, `partial` or `legacy`.

## `strategy_evaluations` — what a strategy decided, including no

**One row per market per official-horizon forecast, whether or not it produces a
signal.** Without this a no-bet simply disappears and a strategy's record becomes
the record of the games it liked.

`reason_codes_json` holds why: `UNRATED_TEAM`, `BORROWED_RATER`,
`MISSING_MARKET`, `MISSING_PRICE`, `BLOWOUT_OUT_OF_DOMAIN`, `OUTSIDE_HORIZON`,
`POST_KICKOFF`, `STALE_MARKET`, `DEGRADED_AVAILABILITY`,
`UNVALIDATED_PROBABILITY`, `EDGE_BELOW_THRESHOLD`, `MARKET_DISABLED`,
`INCOMPLETE_GRADE`, `NO_MODEL_NUMBER`.

## `signal_log` — what the strategy chose

Side, line, price, provider and market snapshot are **immutable from creation**.
A withdrawal before kickoff is a void event, not an edit.

| column | meaning |
|---|---|
| `locked_result` | at the line this signal locked, on the side it recorded. **The record** |
| `close_result` | the same side at the close. A diagnostic |
| `line_clv` | points gained against the close, signed for the side taken |
| `profit_units` | `NULL` unless the price was recorded |

A partial unique index enforces one official signal per game per market per
strategy version, so a retried workflow collides instead of publishing twice.

## `game_results_v2` — what happened

No model opinion. Grading joins results to signals. `close_policy_version` says
how the close was determined.

## `snapshot_misses` — a horizon that closed empty

Recorded so a gap is visible and can never later be filled with a mislabelled
forecast. An unrecorded gap is indistinguishable from a healthy game.

## `v2_void_events` — withdrawals and corrections

A rollback is a new event. Nothing is erased.

## `v2_migrations` — what the migration did

The reconciliation report, stored, so its numbers can be re-read later.

---

## The state journal

`state/<stream>/<partition>.jsonl`, one event per line.

```json
{"event_id":"evt_...","event_type":"signal","event_version":1,
 "occurred_at":"...","recorded_at":"...","payload":{},"payload_hash":"...",
 "source_run":"..."}
```

`event_id` includes the payload hash, so an unchanged row dedups and a **changed
row is a correction** appended beside the original. Replay orders by
`(occurred_at, recorded_at, event_id)` so a correction lands after what it
corrects.

`payload_hash` is checked on read: an event whose payload no longer matches has
been edited, which is what makes "append-only" a checkable promise rather than a
stated one.

Published events are redacted by stream. Grade events keep their identity and
hash and lose the eight numbers.
