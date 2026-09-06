# Operations

## The commands

```bash
# the whole pipeline, as the scheduler runs it
python3 src/run_update.py --sport cfb

# forecasts only, at whatever horizons are due right now
python3 src/forecast_v2.py --sport cfb
python3 src/forecast_v2.py --sport cfb --now 2026-09-12T21:30:00+00:00   # pretend

# the journal
python3 src/replay_state.py --export
python3 src/replay_state.py --verify-against data/model.db
tools/state_commit.sh            # DRY_RUN=1 to build and audit without pushing

# the migration
python3 src/migrate_v2.py --dry-run
python3 src/migrate_v2.py --apply --report output/v2_migration_report.json

# is the V2 Champion still the Champion
python3 tools/compare_champion.py --week 2

# refit the challengers (shadow only; nothing is promoted)
python3 tools/fit_challengers.py --season 2025 --apply
```

## Every gate

```bash
python3 tools/test_tracking.py
python3 tools/test_model.py
python3 tools/test_v2_integrity.py
python3 src/migrate_v2.py --fixture-test
python3 src/replay_state.py --self-test
node tools/qa/public.mjs
node tools/qa/research.mjs
python3 tools/make_fixture.py
QA_BUNDLE=output/research/fixture.json node tools/qa/research.mjs
QA_PAGE=output/site/fixture.html node tools/qa/public.mjs
```

## The cutover switch

```bash
MODEL_V2_OFFICIAL=0 python3 src/run_update.py --sport cfb   # back to the legacy writer
```

Default is on. **One writer, never both** — two pipelines publishing official
picks for the same game would leave the record holding two opinions.

Rolling back is an operational event, not an erasure: V2 events already written
stay written.

## When something is wrong

**The record changed and nobody expected it.** `picks_log.ats_result` still holds
the legacy close-based value for every historical pick. The locked-line result is
in `ats_result_at_pick`, and `signal_log` has both. Nothing was overwritten.

**The journal does not reconcile.** `--verify-against` prints which tables and the
first rows that differ. *Fewer* rows in the replay means the journal is behind —
run `--export`. Rows that *differ* means drift, and the health check separates the
two because conflating them makes the check useless.

**A signal has no provenance.** The health check fails on it. An official signal
needs a model version with a git SHA and a config hash; a run on a dirty tree
refuses to sign at all and says so.

**The budget is tight.** `api_budget.describe()`. Research calls pause at 800,
and the last 100 are held for market and results — a line observed at 4:58pm
cannot be recovered and a season of PPA can.

**Grades changed but the forecast did not see them.** `grade_asof` is a single
`<=`. Check `effective_at`: a snapshot effective one minute after the forecast is
invisible to it, by design.

## What is deliberately not built

- **Availability (§22)** and **weather (§23)**. Both are Phase 3 in the build
  document, gated on a clean prospective pipeline existing first. It now does;
  they have not been started.
- **Totals (§20.8)** and **moneyline probability (§19)**. Disabled in the
  strategy rather than half-built.
- **Kelly staking** on official signals. The build document is explicit: keep
  Kelly out until probability calibration is demonstrated prospectively.
