# The Model — Experiment Register

**Purpose:** Prevent good ideas, bad ideas, failed tests, and design decisions from disappearing — and prevent repeated testing from turning noise into “proof.”

This file should be updated **before** a serious model experiment is run.

---

# Rules

1. Every experiment gets a permanent ID.
2. Write the hypothesis before seeing the test result.
3. Name the development data and the untouched/prospective data.
4. Name one or two primary metrics before running the experiment.
5. Keep failed experiments in the register.
6. Never relabel a secondary slice as the primary result after the fact.
7. If the test result influences the next model design, that test period is thereafter development information for the new design.
8. A code bug discovered after forecasts are made does not erase those forecasts. Record a bug/version event and preserve the original record.
9. Promotion is a separate decision from “this experiment looks interesting.”
10. Do not promote on ATS percentage alone.

---

# Experiment template

Copy this block for every experiment.

```markdown
## E### — Short name

**Status:** proposed | running-shadow | completed | rejected | promoted  
**Registered:** YYYY-MM-DD  
**Owner:**  
**Champion version:**  
**Challenger version:**

### Hypothesis
What football/information mechanism should create incremental predictive value?

### What changes
Exactly one coherent change if possible.

### What stays fixed
Market source, decision horizon, sample eligibility, staking, etc.

### Development data
Data the design/fitting process is allowed to inspect.

### Untouched/prospective data
Data that will decide the claim.

### Primary metrics
1.
2.

### Secondary diagnostics
- 

### Pre-registered slices
Only slices justified before results.

### Failure criteria
What result would make us reject the hypothesis?

### Promotion criteria
What evidence would justify replacing the Champion?

### Result
Fill only after completion.

### Decision
Promote / keep shadow / reject / revise as a new experiment ID.
```

---

# Registered V2 experiments

## E001 — Record-definition repair

**Status:** proposed — engineering prerequisite, not a predictive experiment  
**Registered:** 2026-09-05

### Hypothesis
The current headline record can become more trustworthy by separating the realized result at the locked line from performance against the closing line.

### Change
Add:

- locked spread/total line result;
- closing-line result;
- future spread/total prices;
- line CLV;
- price CLV where data allow.

### Primary acceptance checks

- synthetic example where a bet wins at lock but loses at close is reported correctly in both columns;
- no historical pick is deleted/re-written;
- official strategy W-L uses locked line;
- close diagnostics remain available.

### Promotion
Mandatory measurement fix after tests pass.

---

## E002 — Forecast vs signal separation

**Status:** proposed — engineering prerequisite  
**Registered:** 2026-09-05

### Hypothesis
A canonical signal log will eliminate ambiguity between every model forecast and the subset the strategy would actually recommend.

### Change
Split `forecast_log` and `signal_log`. Best Bets becomes the strategy rule that creates signals; it does not erase forecasts.

### Acceptance checks

- every signal references exactly one forecast;
- no-bet forecast remains queryable;
- strategy W-L is computed only from official signals;
- all-forecast diagnostics are separately labeled;
- a game outside the supported range cannot enter official strategy ROI.

---

## E003 — Market residual ridge model

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
The hand grades contain incremental information beyond the market at a fixed pregame horizon.

### Target

```text
actual_home_margin - market_home_margin_at_horizon
```

### Challenger
Regularized ridge regression using only pregame grade features and minimal context.

### Candidate features

- total grade differential;
- QB, RB, WR, OL, DL, LB, DB, Coach/ST differentials;
- market spread;
- neutral-site flag;
- fixed Champion HFA treatment.

### Development data
2025 hand-grade data may be used for model specification/fitting, with internal rolling splits clearly labeled development-only.

### Prospective data
2026 shadow forecasts generated before games.

### Primary metrics

1. paired MAE difference vs Champion;
2. paired MAE difference vs market spread.

### Secondary

- RMSE;
- locked-line ATS of a pre-registered signal rule;
- CLV;
- residual bias by spread range;
- coefficient stability.

### Failure criterion
No stable paired error improvement and/or coefficients reverse wildly over time.

### Promotion
Prospective improvement that is not explained by a small post-hoc subset and does not worsen calibration/tails materially.

---

## E004 — Matchup interaction residual model

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
Position-group grades are more informative when expressed as football matchups than as a single team total.

### Added features

- OL vs opposing DL;
- QB/WR vs opposing DB/pass rush;
- RB/OL vs opposing DL/LB;
- mirrored away-team interactions;
- special teams/coaching differential.

### Development data
2025 only for specification/regularization.

### Prospective data
2026 shadow forecasts.

### Primary metrics

1. paired MAE vs E003;
2. paired MAE vs Champion.

### Secondary

- CLV by matchup edge;
- coefficient signs/stability;
- ablation by position interaction.

### Failure criterion
Interactions add complexity without stable prospective error reduction.

---

## E005 — Continuous performance quality vs AP-threshold quality

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
A continuous opponent/expectation-adjusted performance residual will generalize better than top-5/top-10/top-25 and ranked/unranked discrete quality points.

### Challenger update

```text
performance_residual = actual_margin - pregame_expected_margin
form_t = decay * form_(t-1) + rate * winsorized(performance_residual)
```

Possible offense/defense decomposition is a separate later experiment unless pre-registered here before running.

### What stays fixed
Position grades, official horizon, market source, HFA, signal policy.

### Primary metrics

1. prospective margin MAE vs current quality system;
2. calibration slope/intercept over season phase.

### Secondary

- ATS at locked line;
- CLV;
- early/late-season stability.

### Failure criterion
No improvement or a form term that mainly chases recent noise.

---

## E006 — Standardized horizon study

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
Different forecast horizons answer different questions, and the best official lock should be selected from prospectively collected snapshots rather than convenience.

### Horizons

- T-72h
- T-24h
- T-6h
- T-2h
- optional T-30m

### What changes
Only information/market time. Same model version.

### Primary metrics

1. line CLV to close;
2. locked-line strategy ROI/ATS only under a pre-registered signal rule.

### Secondary

- forecast MAE;
- availability completeness;
- missed-snapshot rate;
- quote staleness.

### Important rule
Do not switch the official horizon during the same evaluation window because another horizon happened to start hot.

---

## E007 — Multi-provider market consensus

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
Provider consensus and dispersion are a more stable market benchmark than selecting one preferred provider during ingestion.

### Challenger market definition

- median spread/total among valid current books;
- de-vigged ML consensus;
- dispersion features;
- stale/outlier detection.

### Primary metrics

1. market benchmark MAE consistency;
2. model residual stability vs single-provider version.

### Secondary

- frequency of provider-driven fake line movement;
- CLV sensitivity to close definition.

---

## E008 — Availability impact model

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
Official player-availability reports combined with player usage and replacement quality improve pregame price estimates beyond generic roster flags.

### Inputs

- official conference status;
- player usage;
- player PPA/production where available;
- hand grade of position group;
- replacement player quality/depth;
- opponent matchup.

### Shadow-only first

Do not automatically move the Champion line until the impact method is validated.

### Primary metrics

1. direction/magnitude of closing-line movement following availability changes;
2. margin residual improvement on games with material availability events.

### Secondary

- calibration of status -> actual participation;
- false positive rate of generic ESPN flags vs official reports.

---

## E009 — Preseason prior enhancement

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
Returning production, transfers, talent, recruiting by position, and player usage provide a better preseason prior than relying primarily on hand grades/EA-derived roster rank mapping alone.

### Features

- returning total/pass/receive/rush PPA;
- usage returned;
- transfer ratings in/out;
- team talent composite;
- position-group recruiting ratings;
- QB starts/usage;
- coaching continuity;
- prior-season opponent-adjusted efficiency.

### Primary metric
Early-season (pre-registered weeks) margin MAE vs Champion.

### Secondary

- spread-range calibration;
- large-favorite underpricing;
- line-movement prediction.

### Failure criterion
Prior adds complexity but does not improve early-season forecasts prospectively.

---

## E010 — Probability calibration layer

**Status:** proposed; must wait for sufficient clean forecast history  
**Registered:** 2026-09-05

### Hypothesis
A calibrated probability layer produces more reliable economic decisions than the current fixed logistic margin-to-probability transform.

### Candidates

- empirical residual CDF;
- logistic/Platt calibration;
- beta calibration;
- isotonic only when enough training observations exist.

### Primary metrics

1. Brier score;
2. log loss.

### Secondary

- calibration intercept/slope;
- reliability diagram;
- ECE;
- comparison with de-vigged market probability.

### Promotion
Must be genuinely held out/prospective. No staking change from this experiment until calibration evidence is adequate.

---

## E011 — Moneyline EV validation

**Status:** proposed / shadow-only  
**Registered:** 2026-09-05

### Hypothesis
A newly calibrated probability layer may identify moneyline value more reliably than the current raw/shrunk-margin method, whose historical claimed high-EV tail performed poorly.

### Primary metrics

1. Brier/log loss vs de-vigged market;
2. prospective ROI at exact locked ML price under a frozen threshold.

### Hard rule
No Kelly promotion from this experiment unless E010 is already validated.

---

## E012 — Possession/PPP totals model

**Status:** proposed  
**Registered:** 2026-09-05

### Hypothesis
Totals are better predicted by a purpose-built possession/efficiency model than by a generic scoring average carried inside the spread engine.

### Core

```text
expected_total = expected_possessions × (home_PPP + away_PPP)
```

### Features

- pace;
- EPA/PPA;
- success rate;
- explosiveness;
- finishing drives;
- run/pass mix;
- weather/wind;
- QB/OL/DL availability;
- spread/game-script context;
- dome/venue.

### Primary metrics

1. total MAE/RMSE vs market total;
2. paired residual improvement vs Champion totals.

### Secondary

- locked-line O/U ATS;
- total CLV;
- calibration of over probabilities.

---

## E013 — Contextual home-field model

**Status:** proposed, low priority  
**Registered:** 2026-09-05

### Hypothesis
A contextual HFA model can improve upon the fixed 4.0-point Champion while retaining a strong prior toward the fixed value.

### Candidate context

- neutral;
- travel distance;
- time zones;
- altitude;
- venue/crowd proxy;
- conference familiarity;
- rest/short week;
- weather/dome.

### Primary metric
Paired margin MAE vs fixed HFA.

### Guardrail
Strong regularization. Do not fit team-specific HFA freely on tiny samples.

---

## E014 — Public capper consensus research

**Status:** deferred until core measurement is clean  
**Registered:** 2026-09-05

### Hypothesis
Timestamped selections from demonstrably transparent public handicappers may contain incremental market information, particularly line-movement information.

### Data rule
Store only public/free picks with:

- author/source;
- post timestamp;
- exact side/line/price if given;
- market quote at observed time;
- deletion/edit detection where feasible;
- closing quote;
- result.

### Primary metric
CLV by source over a meaningful sample.

### Secondary
Realized ROI at the posted price.

### Guardrail
No TikTok/Discord follower count, claimed VIP record, or screenshots are accepted as performance evidence.

---

# Decision log

Use this section for project-wide decisions that should not be forgotten.

| Date | Decision | Reason | Revisit condition |
|---|---|---|---|
| 2026-09-05 | Treat current production model as Champion | Preserve prospective record while V2 develops | Challenger promotion |
| 2026-09-05 | Treat 2025 hand-grade results as development evidence, not pristine final proof | Repeated design/ablation/calibration exposure | New untouched season/history |
| 2026-09-05 | Separate locked-line W-L from closing-line diagnostics | They answer different questions | Never |
| 2026-09-05 | Separate forecasts from strategy signals | Avoid record-universe ambiguity | Never |
| 2026-09-05 | No Kelly until probability calibration is validated | Current probability layer is not sufficiently proven | E010 success |
| 2026-09-05 | Preserve failed experiments | Control researcher degrees of freedom | Never |
| 2026-09-05 | Prefer market-residual modeling for private-grade challengers | Directly tests incremental information beyond available price | If evidence favors raw-margin architecture |


---

# Results recorded 2026-09-06

Implementation of the V2 build document. Every number below is from
`tools/fit_challengers.py --season 2025`, with lambda chosen on a held-out
late-season split.

## E001 — record-definition repair — **DONE**

`grading.py` grades the side that was published, at the line it was locked at,
and separately at the close. Both are stored; the legacy close-based column is
preserved untouched.

Found in the live record: **3 of 17 graded results disagreed**, including one
win recorded as a loss (Virginia, laying 3, won by 26 — the old grader derived
the side as `3.7 − 4.0 < 0` and graded NC State).

Record under each definition, after week 1:

```
legacy   (close, side recomputed)   see picks_log.ats_result
locked   (published side, locked)   62-77-0   44.6%   n=139
close    (published side, close)     7- 9-1   43.8%   n=16
```

The close `n` is small because `close_policy_v1` requires quotes observed before
kickoff, and historical games have none.

## E002 — forecast vs signal separation — **DONE**

`forecast_log`, `strategy_evaluations` and `signal_log` are separate tables. A
decline is a recorded row with reason codes. Strategy `S0-2026.09.06` is a hashed
data object; `is_official` and a partial unique index prevent double publication.

## E003 — market residual ridge — **FAILED on development data, running shadow**

Target `actual − market at the forecast's own time`. Twelve features: eight
position differences, their total, the market spread and neutral site. Ridge,
intercept unpenalized, standardized on training rows only.

```
        RMSE     market RMSE   improvement
train   14.898   15.086        +0.188
valid   15.146   14.891        −0.255
```

**Lambda 100 — the top of the grid.** The fit shrinks as hard as it is allowed
to, which is the fitter saying these features add nothing to the market number
that survives a split.

Registered as a shadow challenger anyway. A negative recorded is worth more than
one quietly dropped, and 2025 is development data: it cannot promote or demote
anything on its own.

## E004 — matchup interaction residual — **FAILED on development data, running shadow**

Six pre-registered matchup formulas (OL vs DL both ways, pass game vs coverage
both ways, run game vs box both ways) plus coach/ST, on top of E003's frame.

```
        RMSE     market RMSE   improvement
train   14.998   15.086        +0.088
valid   15.035   14.891        −0.144
```

Also lambda 100. The interactions do not rescue it.

**The formulas were written before fitting.** With eight positions there are
dozens of plausible interactions and 800 games will happily rank one best;
choosing after looking is how a model acquires a beautiful backtest and no
future.

## E005 — continuous team form — **BUILT, not yet fitted**

`models_v2/form_quality.py`. Exponentially decayed, winsorized performance
residual, shrunk hard early in a season, cleared between seasons.

`expected_from` is declared and not defaulted: form measured against the MARKET's
pregame number is explicitly market-informed and must be labelled as such;
against the MODEL's own number it is self-contained and noisier. They answer
different questions.

**The Champion's threshold rule is untouched**, and a test asserts `engine.py`
does not import this module.

## What the failures mean

Two challengers built on the film grades do not beat the closing line on
development data. That is not the same as "the grades are worthless" — the
Champion uses them differently, and the development market number is one
preferred provider at an unrecorded time rather than a T2 consensus, which is a
different data regime from the one the models will forecast in.

It does mean **nothing here is ready to promote**, and that the honest next step
is prospective 2026 data from forecasts filed before kickoff, which the
infrastructure now collects.
