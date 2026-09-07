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

## E017 — units fitted per grade vintage — **SHIPPED 2026-09-07**

**The finding.** `scale` is a units conversion between rating points and points
of margin, and it is a property of the grade sheet. Fitted jointly with
`quality_scale` and home field: 2025's hand grades want **1.30**, 2026's
EA-derived grades want **2.06**. The config shipped 1.311 for both.

**How it showed.** A one-directional bias by line size — +2.78 points on
pick'ems, **−5.67 on the biggest favourites** — so every large favourite was
priced short and the model took the dog. Week 1 2026: away side 20–33, home side
18–14. Oregon at Oklahoma State priced Oregon by 6.5 against 22.5.

**What was already known and not acted on.** `best_bets` carries a comment
stating that Grant's hand grades needed 1.57× and the EA-derived ones 1.98×.
`calibrate.fit_two` exists and fits exactly this. **Nothing ever called it.** A
calibration tool with no caller is a calibration that happens once and rots.

**The fix.** `calibrate.fit_units` runs every update from the season's own priced
games, behind guards (n ≥ 60, R² ≥ 0.45, band 0.40–4.00, 0.05 hysteresis). Every
config load goes through `calibrated_config`.

**Measured.** On 2025 with grades frozen at week 1, auto-calibrated units,
form 0.6, the board rule: **56.77% over 384** against 56.49% for the hand value —
a wash, which is the point. Rescoring 2026's played games at the corrected scale
takes the season grade from 46.81% to **61.70%**. Model-market dispersion
0.65 → 0.91.

**Rejected in the same pass.** Team-level market anchoring — blending the film
rating with market-implied power ratings — looked like 60.9% ATS and was **pure
look-ahead**: the pool included later weeks' *closing* lines, which are
statements made after the game being predicted. Restricted to strictly earlier
weeks it adds nothing (56.21% vs 56.16%). Also rejected: adopting the fitted home
field, which hands back a measured half-point per home game.

---

## E015 — performance form in the champion — **SHIPPED 2026-09-07**

Distinct from E005, which fitted continuous form as a *challenger ridge feature*
against the market and lost (valid RMSE 14.929 vs 14.891). This asks a different
question: does the same quantity improve the **champion's own rating**?

**Why it might, when E005 did not.** The champion's quality-point rule is binary
— beating an unranked team 63–3 and 17–14 both score zero — so every point of
margin is discarded. That is harmless while the film is regraded weekly, because
the film carries the update. 2025 had nine grade snapshots. **2026 has one.**

**Method.** Walk-forward 2025, predict strictly before observe. Two grade
regimes: as they actually were (weekly), and frozen at week 1 (2026's situation).
Scored on the best-bets board.

**Result.** Frozen grades: 54.17% → **56.49%** ATS, RMSE 16.228 → **15.786**.
Weekly grades: 56.14% → 56.16%, RMSE 16.087 → 15.820. Every one of 54 tested
(weight, half-life, k) cells improved RMSE and 53 of 54 improved the hit rate;
the shipped values sit mid-plateau rather than at either maximum.

**Parameters.** `form_weight` 0.6, `form_half_life` 8, `form_shrink_k` 4,
`form_cap` 30. The cap is two standard deviations of the actual-minus-market
residual (15.0 on 2025); at the first value of 21 it bound on 32% of teams after
week 1 alone, which is a ceiling rather than a guard.

**Caveat.** 2025 is development data and this was chosen on it. The prospective
test is 2026 weeks 2 onward.

---

## E016 — best-bet selection — **SHIPPED 2026-09-07**

Week 1 2026 published a side on 85 graded games and went 38–47, on a board that
would have offered none of them. This makes the board's rule explicit, versioned
and stored, and splits the public record into best bets and every game.

Rules and the rate of what each removes: `unrated` 49.2% (n=126), `blowout`,
`thin` 49.6% (n=238), `early` 49.1% (n=53), totals excluded (they get *worse* as
they disagree more: 48.8% past 4 points, 46.9% past 5).

**Result on 2025, frozen grades, with E015:** 56.49% over 393, ROI +7.8%,
against 52.44% for every game with a line. 95% interval 51.6–61.4% — the lower
bound is under break-even, and it must be quoted that way.

**Rejected in the same pass:** an upper bound on edge (an artefact of week-1
contamination), tightening ±28 (n=18), and excluding week 2 (categorically
different from week 1).

See `WEEK-1-2026-POSTMORTEM.md`.

---

## E005 — continuous team form — **FITTED, shadow**

`models_v2/form_quality.py`. Exponentially decayed, winsorized performance
residual, shrunk hard early in a season, cleared between seasons.

`expected_from` is declared and not defaulted: form measured against the MARKET's
pregame number is explicitly market-informed and must be labelled as such;
against the MODEL's own number it is self-contained and noisier. They answer
different questions.

**The Champion's threshold rule is untouched**, and a test asserts `engine.py`
does not import this module.

Fitted 2026-09-06 on 2025 development data, one term, lambda chosen on the same
held-out late-season split as E003 and E004:

```
        RMSE     market RMSE   improvement
train   15.042   15.086        +0.043
valid   14.929   14.891        −0.038
```

Standardized coefficient `form_diff +0.075`, and lambda 100 — the top of the
grid again. It is the least bad of the three on held-out data, and the reason is
that it is the most heavily shrunk toward doing nothing. That is not a finding
about form; it is the fitter declining to commit.

**The feature is replayed, not stored.** A game enters a team's form only once it
had certainly finished — kickoff plus six hours, not kickoff — and a game with no
line contributes nothing rather than its raw margin, which would have made form
mean "how much did you win by" for exactly the games the market did not price.

## E006 — totals from scoring rates — **FITTED, shadow**

`models_v2/totals.py`. §20.8 opens with *do not reuse spread logic*, so this does
not predict the market's residual. It predicts **both team scores separately**
from season-to-date points scored and allowed, each shrunk toward the league mean
over the same games, and adds them. Two fits, because a home offence facing a
road defence is not the mirror of the reverse.

```
        MAE      market total MAE   improvement
train   13.082   12.673             −0.383
valid   12.993   12.257             −0.736
```

**It chose the BOTTOM of the lambda grid**, 0.01, where the three spread models
all chose the top. The scoring rates carry real signal about totals; there is
simply less of it than the market already has. That is a different result from
"shrink it to nothing" and is recorded as one.

It emits no spread. The difference of two shrunk scoring rates has a plausible
shape and no thought behind it, and a test asserts `pred_home_margin` is None.

The strategy keeps `totals_enabled: False`. That switch is a switch rather than a
threshold precisely so that a model losing by 0.74 points cannot promote itself.

## C5 line movement — **NOT FITTED, and the reason is a data fact**

§20.7 asks whether grade changes and model residuals predict the later market,
targeting `closing_consensus_spread − current_consensus_spread`. The obvious
source is the `lines` table, which holds `home_margin_open` and `home_margin` for
816 completed 2025 games with 645 non-zero moves.

**It cannot be used, because `home_margin_open` has no observation timestamp.**
The columns are `game_id, provider, home_margin, home_margin_open, total,
total_open, home_ml, away_ml` — there is no time on the opener, so no feature can
be honestly dated to it. Grades published between the open and the close would
sit in the feature set and predict the move trivially, and the model would look
excellent for the worst possible reason. That is §13's *do not ingest a
retrospective rating and pretend it was available in that exact form
historically*, arrived at from the other direction.

The leak-free source is `market_quotes`, which is append-only and timestamped and
which V2 started collecting this month. C5 is blocked on sample, not on design,
and the thing that unblocks it is already running.

## C4 preseason/public priors — not started

§20.6 says "later phase" and assigns it to none. Returning production, transfer
movement and talent composites would improve early-season priors; nothing here
touches them.

## What the failures mean

Three challengers built on the film grades do not beat the closing line on
development data, and a fourth does not beat the market total. That is not the same as "the grades are worthless" — the
Champion uses them differently, and the development market number is one
preferred provider at an unrecorded time rather than a T2 consensus, which is a
different data regime from the one the models will forecast in.

It does mean **nothing here is ready to promote**, and that the honest next step
is prospective 2026 data from forecasts filed before kickoff, which the
infrastructure now collects.
