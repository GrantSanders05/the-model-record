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
`migrate_v2.py` · `api_budget.py` · `public_export.py` · `availability.py` ·
`weather.py` ·
`models_v2/{base,market_baseline,ridge,residual_grade,matchup_residual,form_quality,totals}.py` ·
`tools/{test_v2_integrity.py,compare_champion.py,fit_challengers.py,state_commit.sh}` ·
`.github/workflows/{ci,v2-forecast-snapshots,v2-state-commit}.yml` ·
`docs/model-v2/{METHODOLOGY,DATA-DICTIONARY,OPERATIONS}.md`

**Changed:** `db.py` (14 V2 tables, 9 columns, no removals) · `ledger.py` ·
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

## Challengers — every fitted one lost, and every loss is recorded

```
E003 residual ridge     valid RMSE 15.146 vs market 14.891   (−0.255)  lambda 100
E004 matchup residual   valid RMSE 15.035 vs market 14.891   (−0.144)  lambda 100
E005 form quality       valid RMSE 14.929 vs market 14.891   (−0.038)  lambda 100
E006 totals scoring     valid  MAE 12.993 vs market 12.257   (−0.736)  lambda 0.01
```

The three spread models all chose lambda 100, the top of the grid — the fitter
shrinking as hard as it is allowed to. E005 is the least bad of them precisely
because it is shrunk hardest toward doing nothing, which is the fitter declining
to commit rather than a finding about form.

**E006 is the one that is different.** It chose the *bottom* of the grid: season
scoring rates carry real signal about totals, there is simply less of it than the
market already has. C1 market-baseline runs beside all of them so every
comparison is against the line as well as against the Champion.

Nothing is promoted. `totals_enabled` and `moneyline_enabled` stay false.

---

## The model lab

§27.4 and §27.5 are on the research page now: four scoreboards as four cards that
are never summed, a challenger table **ordered by role and never by win rate**,
the hashed strategy config, the decline reasons in English, the probability
scoreboard, the shadow layers, and the legacy→locked methodology note stated on
the page rather than in a commit message.

`vs market` inverts the page's sign convention — negative is the model doing
better — and is coloured by meaning, with a control asserting a better-than-market
model is not painted red. That mistake would have been invisible for a season,
because the number itself would still have been correct.

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

## Three more, found finishing it

6. **No challenger had ever forecast in production.** Fitted artifacts were
   written to `output/`, `output/` is in `.gitignore`, and the Actions cache
   carries `data/` and not `output/`. Every scheduled run found no artifact file
   and skipped every challenger — precisely the "accumulates a record of the
   games it happened to be alive for" failure `load_challengers`' own docstring
   warns about, announced only in a log line nobody reads. Challengers rehydrate
   from `model_registry.config_json` now, which is the row `register_model`
   hashed to establish the version's identity.

7. **The loader's fitted-check would have dropped C6 the same way.** It looked
   for a top-level `coefficients` key; C6 keeps two sub-artifacts and has none.
   `is_fitted()` is asked of the class now.

8. **The weather matcher used ESPN's `displayName`**, which carries the mascot —
   "Washington Huskies" against this database's "Washington". Four events
   fetched, four unmatched, zero stored, and the run reported a successful fetch.

---

## Gates

```
158  tracking          58  model            346  V2 integrity
 19  migration          9  replay            58  public DOM
285  research DOM     292  fixture research  58  fixture public
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

## Phase 3, built as shadow

**§22 availability.** `roster_watch` already fetched ESPN and priced each absence
in points of spread, then overwrote its report every run. `availability_events` is
append-only, deduplicated on meaning rather than time, tiered by source, with an
as-of reader and §22.4's line-movement evaluation against the quote history. It
adjusts nothing: no calibrated `P(absent | status)` exists, and §22.3 forbids
turning Questionable into Out.

**§23 weather.** Built, and the variable §23 names is not in it. ESPN's scoreboard
publishes temperature, a condition and a dome flag and **no wind**. The columns
exist and stay NULL rather than holding a proxy, and the gap is stated in the
module, the bundle and on the page. `indoor` is a verified fact and the largest
weather effect there is.

**§20.8 totals.** E006, above. Predicts both team scores separately from scoring
rates and sums them — §20.8 opens with *do not reuse spread logic*.

**§19 probability.** Brier, log loss and five-band calibration against the
de-vigged market probability from the same snapshot, with the base-rate model's
score beside it. `enables_moneyline` is false with a reason.

**§19.4 week-block bootstrap.** Paired comparisons now carry an interval
resampled over whole weeks, not games.

## Not built

**C4 preseason priors (§20.6)** — the document says "later phase".

**C5 line movement (§20.7)** — blocked on data, not design.
`lines.home_margin_open` has no observation timestamp, so nothing can be honestly
dated to the open and any fit would leak. `market_quotes` is the leak-free source
and started this month.

**Three of the four Phase 2A workflows.** `v2-forecast-snapshots.yml` was built
because a horizon window is 20–60 minutes wide and nothing else runs on that
clock; it refreshes the market itself before forecasting, which is what
`v2-market-refresh.yml` was for. Grades refresh and results/close already run on
`update.yml`'s cadence, and a second job writing the same database cache is how a
half-populated database becomes the base every later run restores from.

**Wind.** No free source wired here carries it.
