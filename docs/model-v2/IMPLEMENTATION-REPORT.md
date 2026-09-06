# V2 implementation report

**Branch** `feature/model-v2-integrity` · **PR** #5 · **Baseline** `d032577`
**Date** 2026-09-06

---

## What was wrong

`ledger.grade` derived the graded side at grading time:

```python
edge = model_margin - closing_margin
result = "W" if (edge > 0) == (actual - closing > 0) else "L"
```

`ats_pick` — the side actually published — was never read. When the market moved
across the model's own number, the graded side **flipped**.

**NC State @ Virginia.** Published side Virginia, laying 3, closed at 4. Virginia
won by 26 and covered both numbers. The grader computed `3.7 − 4.0 = −0.3`, took
the sign as the side, graded NC State +4, and recorded a **loss**. Three of
seventeen graded results disagreed with a side-preserving grading.

Totals had the identical shape via `model_total − closing_total`.

---

## Files

**New:** `src/grading.py` · `provenance.py` · `market.py` · `market_policy.py` ·
`grade_snapshots.py` · `features_v2.py` · `forecast_v2.py` · `horizons.py` ·
`signals.py` · `metrics_v2.py` · `state_events.py` · `replay_state.py` ·
`migrate_v2.py` · `api_budget.py` · `public_export.py` ·
`models_v2/{base,market_baseline,ridge,residual_grade,matchup_residual,form_quality}.py` ·
`tools/{test_v2_integrity.py,compare_champion.py,fit_challengers.py,state_commit.sh}` ·
`.github/workflows/{ci,v2-forecast-snapshots,v2-state-commit}.yml` ·
`docs/model-v2/{METHODOLOGY,DATA-DICTIONARY,OPERATIONS}.md`

**Changed:** `db.py` (12 V2 tables, 9 columns, no removals) · `ledger.py` ·
`fetch_cfb.py` · `sync_grades.py` · `predict.py` · `best_bets.py` ·
`research_export.py` · `publish.py` · `run_update.py` · `metrics.py` (one rename
with a deprecated alias) · `tools/{test_model,make_fixture,health_check}.py` ·
`tools/qa/public.mjs` · `.github/workflows/update.yml` · `.gitignore`

**Removed:** nothing. No table dropped, no column repurposed, no historical value
rewritten.

---

## Migration

99 picks examined, 25 settled at migration time. Created 2 legacy model versions,
99 forecasts, 198 evaluations, 163 signals, 861 void events, and reconstructed
557 grade snapshots from 1,500 historical team-weeks (138 skipped rather than
given an invented timestamp — week 99 is the importer's sentinel and has no first
moment).

**Locked-line vs legacy: 3 differences.** Side-preserving close vs legacy: 2.

Refused, deliberately: no spread or total price invented; no market snapshot
built where only one preferred quote survives; no feature hash manufactured. Those
rows say `provenance_quality = 'legacy'`. `ml_pick` was **not** promoted to a
wager — `predict.py` documents it as a straight-up prediction.

---

## The record, three ways

```
legacy   (close, side recomputed)   picks_log.ats_result, untouched
locked   (published side, locked)   62-77-0   44.6%   n=139   ← now the headline
close    (published side, close)     7- 9-1   43.8%   n=16    ← a diagnostic
```

The close `n` is small because `close_policy_v1` needs quotes observed before
kickoff and historical games have none. ROI is `None` throughout: no spread price
was ever recorded, and −110 is not a default.

---

## Champion, unchanged and proved so

`tools/compare_champion.py` prices the same slate through the legacy path and the
V2 adapter. **801 games, zero margin differences, zero fallback-flag
disagreements.** V2 changed how what the model thinks is recorded, not what it
thinks.

---

## Challengers — both fitted ones failed

```
E003 residual ridge     valid RMSE 15.146 vs market 14.891   (−0.255)
E004 matchup residual   valid RMSE 15.035 vs market 14.891   (−0.144)
```

Both chose lambda 100, the top of the grid — the fitter shrinking as hard as it
is allowed to. Registered as shadow anyway; a negative recorded is worth more
than one quietly dropped. C1 market-baseline and E005 form are built and running
shadow; E005 is not yet fitted.

---

## Five things found while building

1. **The state journal would have published the moat.** A `grade_snapshot`
   payload carries eight position grades for a named team, and this repo is
   public. Publishability is now a property of the stream.

2. **ROI read −100%.** A losing spread bet costs one unit at any price, so only
   losses had a known return and the average was over losses alone.

3. **The journal could never record a graded result.** `event_id` came from the
   row key, so a signal graded after export re-derived the same id and the append
   was skipped as a duplicate. The id now includes the payload hash.

4. **`grade_signals` destroyed 122 closing lines**, writing a "missing" close over
   real ones. Nothing failed; the only symptom was the journal ceasing to
   reconcile. COALESCE now, and a repair restored them.

5. **`data/model.db` was ignored by exact name**, which does not match
   `data/model.pre-v2-<stamp>.db` — two 20 MB copies of the grade database were
   untracked and unignored.

---

## Gates

```
158  tracking          58  model            244  V2 integrity
 19  migration          9  replay            58  public DOM
226  research DOM     233  fixture research  58  fixture public
```

`ci.yml` runs the secretless subset on every branch. `update.yml` declared
`environment: github-pages` at the job level, so the suite could only run on the
branch permitted to deploy — a dispatch against this branch died in 2 seconds on
the protection rule before a single test ran.

---

## Rollback

```bash
MODEL_V2_OFFICIAL=0 python3 src/run_update.py --sport cfb
```

Legacy locking resumes; V2 events already written stay written. A rollback is an
operational event, not an erasure. The public page falls back to the legacy
headline automatically when `signal_log` holds no official signals.

---

## Not built

**Phase 3 — availability, weather, totals, moneyline probability.** Gated in the
build document on a clean prospective pipeline existing first. It now does. Not
started, rather than half-started.

**Three of the four Phase 2A workflows.** `v2-forecast-snapshots.yml` was built
because a horizon window is 20–60 minutes wide and nothing else runs on that
clock; it refreshes the market itself before forecasting, which is what
`v2-market-refresh.yml` was for. Grades refresh and results/close already run on
`update.yml`'s cadence, and a second job writing the same database cache is how a
half-populated database becomes the base every later run restores from.

**The research page's Champion-vs-Challenger UI.** The data is exported in the
bundle's `v2` block — four scoreboards, every model with its paired comparison,
the strategy config and hash, and the decline reasons — and no section renders it
yet.

**E005 is unfitted.** The module, its guardrails and its tests exist; no
coefficients have been estimated.
