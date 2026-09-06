# The Model — V2 Roadmap

**Status:** proposed research architecture  
**Production rule:** the current model remains Champion until a challenger earns promotion prospectively.

---

# 1. V2 design objective

The current system mostly asks:

> “What score margin do our ratings imply, and how far is that from the market?”

V2 should ask the harder and more useful question:

> **“Given the market price available right now, what pregame information do we possess that predicts either future market movement or the eventual outcome residual?”**

That shift changes the model from “our power number versus Vegas” into a falsifiable information-edge system.

---

# 2. Canonical objects

V2 should have these first-class entities.

## 2.1 `market_quote`

One observation from one provider at one timestamp.

Suggested fields:

```text
quote_id
sport
game_id
provider
observed_at
home_spread
home_spread_price
away_spread_price
total
over_price
under_price
home_ml
away_ml
is_consensus
source
raw_payload_hash
```

Never overwrite quotes. Derived “current” and “close” views are queries over quote history.

## 2.2 `grade_snapshot`

A time-stamped view of the human grades.

```text
grade_snapshot_id
sport
season
team
observed_at
effective_at
source_tab
source_hash
qb
rb
wr
ol
dl
lb
db
coach_st
notes_version
```

A forecast references the exact snapshot it used.

## 2.3 `availability_snapshot`

```text
availability_snapshot_id
game_id
team
player_id/player_name
status
source
source_confidence
observed_at
estimated_play_probability
estimated_line_impact
```

Store raw status separately from model-estimated impact.

## 2.4 `feature_snapshot`

The exact feature vector used by a model at prediction time.

```text
feature_snapshot_id
game_id
horizon
created_at
feature_schema_version
payload_json
payload_hash
```

This makes leakage audits possible.

## 2.5 `forecast`

Every model opinion, even if the strategy says no bet.

```text
forecast_id
game_id
model_id
model_version
git_sha
config_hash
feature_snapshot_id
market_quote_id
grade_snapshot_home_id
grade_snapshot_away_id
horizon
created_at
pred_margin
pred_total
home_win_prob
home_cover_prob
over_prob
uncertainty_margin
uncertainty_total
```

## 2.6 `signal`

The strategy decision derived from one forecast and one market quote.

```text
signal_id
forecast_id
strategy_version
market
side
line
price
estimated_prob
estimated_ev
edge
uncertainty
reason_codes
created_at
is_official
```

No signal is a valid record without exact line **and price**.

## 2.7 `result`

```text
game_id
final_home_score
final_away_score
finalized_at
closing_quote_id
```

Derived grading should produce locked-line result and closing-line diagnostics separately.

---

# 3. Champion / challenger architecture

## Champion C0 — current hand-grade model

Freeze the exact current production logic and current config under a named version.

Example:

```text
C0-2026.09.05
```

Store the Git SHA and config hash.

Do not silently mutate C0. Any behavior-changing change becomes a new version.

## Challenger C1 — market-only baseline

A strong baseline is mandatory.

At each standardized horizon, save:

- market spread;
- total;
- de-vigged moneyline probabilities;
- provider consensus/dispersion;
- movement from open;
- movement since previous horizon.

The market is not just an opponent. It is the baseline forecaster every challenger must improve upon.

## Challenger C2 — grade residual model

Instead of predicting raw final margin, model:

```text
Target = actual_home_margin - market_home_margin_at_prediction_time
```

Start simple: **ridge regression** or a Bayesian regularized linear model.

Candidate features:

- total grade differential;
- QB differential;
- OL differential;
- DL differential;
- WR differential;
- DB differential;
- RB differential;
- LB differential;
- Coach/ST differential;
- market spread;
- neutral site;
- home field.

Why simple first: with limited hand-grade seasons, a regularized linear challenger tells us whether the private grades add information without giving a complex algorithm enough freedom to memorize 2025.

## Challenger C3 — matchup interaction model

Use the structure that a simple total throws away.

Suggested engineered features:

```text
home_pass_matchup = f(home_qb, home_wr, away_db, away_dl)
away_pass_matchup = f(away_qb, away_wr, home_db, home_dl)
home_run_matchup  = f(home_rb, home_ol, away_dl, away_lb)
away_run_matchup  = f(away_rb, away_ol, home_dl, home_lb)
home_trench       = home_ol - away_dl
away_trench       = away_ol - home_dl
home_skill_vs_db  = weighted(home_qb, home_wr) - away_db
away_skill_vs_db  = weighted(away_qb, away_wr) - home_db
```

Do not hand-pick final coefficients from 2025 ATS. Fit/shrink them under nested time-series validation or keep them fixed as pre-registered football hypotheses.

## Challenger C4 — grades + prior public information

Blend the unique grades with features that are useful as priors/context, not as replacements for the grades:

### Preseason

- returning production;
- QB returning/start experience;
- player usage returning;
- transfer additions/losses;
- talent composite;
- recruiting by position group;
- coach/coordinator continuity;
- previous-season opponent-adjusted efficiency;
- roster depth.

### In-season

- opponent-adjusted efficiency;
- EPA/PPA per play;
- success rate;
- explosiveness;
- havoc;
- line yards / run efficiency;
- passing-down performance;
- finishing drives / points per opportunity;
- pace/plays;
- garbage-time filtered splits;
- player usage/PPA changes.

Every in-season feature must be computed from games completed **before** the forecast timestamp.

## Challenger C5 — line-movement model

This is a separate and extremely useful research target:

```text
Target = closing_line - current_line
```

Predict whether your information anticipates the close.

This model can tell us whether the grades/availability news are useful to the market even before enough final outcomes exist to establish ATS profitability.

Features can include:

- current model residual;
- grade changes since prior snapshot;
- availability changes;
- market-provider disagreement;
- open-to-current movement;
- time to kickoff;
- news source confidence.

A forecast that consistently predicts the direction of the close is evidence of information value, even if final-game variance makes ATS noisy.

## Challenger C6 — totals model

Treat totals independently from spreads.

### Core decomposition

```text
Expected Total = Expected Drives/Possessions × Expected Points per Possession
```

Or equivalently predict each team's score distribution separately and sum them.

Features:

- offensive pace;
- defensive pace effects;
- offense/defense EPA/PPA;
- success rate;
- explosiveness;
- finishing drives;
- rush/pass mix;
- QB status;
- OL/DL matchup;
- red-zone/field-position indicators;
- special teams;
- wind;
- precipitation;
- temperature extremes;
- venue/dome;
- game-script interaction with spread.

Never use a spread-model edge threshold as a totals threshold.

---

# 4. Replace the current “quality points” carefully

Do not delete the Champion's quality points because a new idea sounds cleaner. Create a challenger.

## Current weakness

Top-5/top-10/top-25 and ranked/unranked cutoffs are discrete and poll-dependent.

## Continuous challenger

For each completed game, calculate a pregame expectation using a line or a strictly prior model snapshot:

```text
performance_residual = actual_margin - expected_margin_before_game
```

Then update team form with exponential decay and shrinkage:

```text
new_form = shrink * old_form + learning_rate * performance_residual
```

Possible refinements:

- cap/winsorize one-game residuals;
- separate offense and defense residuals;
- downweight garbage-time distortion;
- weight by opponent uncertainty;
- shrink early-season form more heavily toward preseason prior;
- use market expectation only if the research question explicitly allows market-informed team strength.

Evaluate this against the existing discrete quality term prospectively.

---

# 5. Probability layer

A betting strategy needs probabilities, not just point estimates.

## 5.1 Predict distributions, not only margins

For each forecast, retain an uncertainty estimate:

```text
pred_margin_mean
pred_margin_sd
pred_total_mean
pred_total_sd
```

A 4-point edge from a model with 6-point residual SD is different from a 4-point edge with 17-point residual SD.

## 5.2 Initial distribution approach

Do not begin with a neural net. Begin by estimating an empirical residual distribution from prior out-of-sample forecasts.

For example:

```text
residual = actual_margin - predicted_margin
```

Estimate residual variance by relevant strata only where sample permits:

- season phase;
- absolute spread range;
- FBS/FBS only;
- model version;
- maybe favorite/underdog.

Use shrinkage so small bins do not produce nonsense.

## 5.3 Calibration

Candidate calibration methods, in increasing flexibility:

1. logistic/Platt-style calibration;
2. beta calibration;
3. isotonic regression once sample size is adequate.

Fit calibration only on earlier data and score later data.

## 5.4 Proper scoring rules

Primary probability metrics:

- Brier score;
- log loss;
- calibration intercept/slope.

Secondary:

- reliability diagram;
- ECE;
- resolution/sharpness.

Every probability model should be compared with the market's de-vigged probability on the same events.

---

# 6. Bet-selection policy

## Stage 1 — research-only signals

For the beginning of 2026 validation:

- flat unit size;
- no Kelly;
- no parlays in model evaluation;
- no arbitrary “lock of the day” multipliers;
- one pre-registered official horizon;
- one canonical quote-selection rule.

## Stage 2 — uncertainty-aware threshold

A signal should pass only when its estimated edge exceeds a safety margin tied to model uncertainty.

Conceptually:

```text
signal if expected_value_lower_bound > 0
```

or

```text
signal if |market_residual_prediction| > threshold
```

with the threshold selected on training/development data and frozen before prospective evaluation.

## Stage 3 — calibrated staking

Only after probability calibration survives prospective validation should fractional Kelly be reconsidered.

When it is used:

- cap maximum bankroll fraction;
- use conservative fractional Kelly;
- reduce stake further for model/version/data-source uncertainty;
- record the exact price used to calculate the stake;
- do not size an outcome with a display probability that has not passed calibration checks.

---

# 7. Market architecture

## 7.1 Ingest all available providers

Do not select one provider during ingest.

Derive later:

- median consensus;
- best available line;
- best available price;
- preferred sharp/reference book;
- provider dispersion;
- stale-quote flags.

## 7.2 Quote quality checks

Flag or reject:

- missing price;
- stale timestamp;
- provider outlier beyond threshold;
- line with one side but not the other;
- impossible overround;
- movement caused by provider switch rather than real market move.

## 7.3 Closing-line definition

Recommended research close:

> the last valid consensus/reference quote observed before kickoff, with a maximum staleness threshold.

Store the exact quote IDs that compose the close.

---

# 8. Availability model

## 8.1 Source hierarchy

Highest confidence first:

1. official conference availability report;
2. official school/team announcement;
3. verified roster status;
4. credible beat reporter;
5. generic media aggregation;
6. rumor/social post.

Only the first three should initially move the production model automatically. Lower tiers can show as alerts/shadow features until validated.

## 8.2 Player impact

Replace “EA rating only” with a hybrid impact estimate:

```text
impact = position_value
       × usage_share
       × quality_above_replacement
       × replacement_gap
       × matchup_multiplier
```

Useful public inputs include player usage, player PPA, returning production, roster/recruit ratings, and the hand grade of the affected position group.

## 8.3 Status uncertainty

Do not turn Questionable into Out.

Model expected impact:

```text
expected_line_change = P(absent | status) × full_absence_impact
```

Calibrate `P(absent | status)` from actual outcomes by conference and status designation.

---

# 9. Home field / situational layer

Champion: keep fixed HFA.

Challenger features:

- true home vs neutral;
- venue elevation;
- travel miles;
- time-zone crossings;
- short week / rest days;
- prior bye;
- same-conference familiarity;
- crowd/capacity proxy if obtainable reliably;
- weather/dome;
- kickoff body-clock effects.

Avoid classic “trend” overfitting such as arbitrary records after a bye or against ranked teams unless the mechanism is explicit and the variable survives prospective evaluation.

---

# 10. Validation architecture

## 10.1 Three levels of validation

### Level A — code correctness

Synthetic fixtures, oracle/anti-oracle, leakage tests, mutation/control tests.

### Level B — historical modeling validation

Rolling/nested walk-forward only.

Example for public-data challengers with many seasons:

```text
Train: 2014-2019 | Validate: 2020 | Test: 2021
Train: 2014-2020 | Validate: 2021 | Test: 2022
...
Train: 2014-2024 | Validate: 2025 | Test: 2026 prospective
```

Aggregate only predictions generated by models that had no access to their test season.

### Level C — prospective validation

The gold standard.

Every challenger writes predictions in real time. Results are filled later. No backfilling.

## 10.2 Human-development leakage rule

Once a test result has influenced a design decision, that test period is no longer untouched for future versions.

This is why 2025 remains useful development evidence but should not be the final proof of V2.

## 10.3 Paired comparisons

Compare models on the **same games**. Use paired error differences rather than comparing two headline win rates from different samples.

Useful tests/intervals:

- paired bootstrap CI for MAE/RMSE difference;
- bootstrap/clustered interval for ROI;
- exact or Wilson interval for ATS proportion;
- calibration intervals;
- block bootstrap by week where practical to account for slate-level dependence.

## 10.4 Multiple-experiment control

The Experiment Register must log every serious challenger, including failures.

Do not run 30 variants and report only the best one without accounting for the search.

The promotion decision should emphasize:

- stable improvement across folds/time periods;
- mechanism plausibility;
- calibration;
- consistency with market movement;
- prospective performance;
- not merely one best p-value.

---

# 11. Promotion criteria

Do not hard-code a magic ATS percentage. Promote a challenger when the evidence collectively supports it.

Minimum requirements:

1. pre-registered hypothesis and primary metrics;
2. no leakage detected;
3. complete provenance for essentially all scored forecasts;
4. better or non-inferior margin error versus Champion on paired games;
5. no material calibration deterioration;
6. strategy results at the exact locked line/price are not worse in a way that contradicts the modeling improvement;
7. improvement is not isolated to one tiny subset discovered after results;
8. prospective sample is meaningful enough that the result is not being driven by a few games;
9. no catastrophic tail behavior in large spreads, FCS games, early season, or missing-data states;
10. implementation survives synthetic failure tests.

Promotion creates a **new** Champion version. Old results remain assigned to the old Champion.

---

# 12. Recommended implementation phases

## Phase 0 — measurement integrity

- fix locked-line vs close grading;
- add spread/total prices;
- separate forecast/signal/bet;
- add model/config/data hashes;
- durable event store;
- formal close definition;
- freeze C0.

**Do this before adding new predictive features.**

## Phase 1 — market snapshots + prospective harness

- all-provider quotes;
- standardized horizons;
- shadow forecast table;
- challenger registry;
- line-movement scoring;
- market baseline metrics.

## Phase 2 — private-grade information model

- ridge residual model;
- matchup interactions;
- uncertainty;
- ablations by position group;
- no automatic promotion.

## Phase 3 — availability + roster priors

- Big Ten adapter;
- SEC adapter;
- ACC adapter;
- player usage/PPA/replacement impact;
- transfer/returning-production preseason priors.

## Phase 4 — continuous team-form challenger

- residual-performance update;
- replace AP-threshold quality only if it wins.

## Phase 5 — totals engine

- pace + PPP architecture;
- weather;
- QB/OL/DL effects;
- total-specific calibration.

## Phase 6 — probability/EV layer

- Brier/log-loss dashboard;
- calibration model;
- de-vigged price comparison;
- EV strategy;
- fractional Kelly only after proof.

## Phase 7 — capper/public-pick research layer

Only after the core model is clean:

- timestamp public free picks;
- store exact line/price at post time;
- verify/de-duplicate accounts;
- track their CLV and realized ROI;
- use as a meta-feature only after enough history;
- never let social popularity count as evidence.

---

# 13. Things V2 should deliberately *not* do

- Do not optimize directly for one-season ATS percentage.
- Do not delete losing experiments.
- Do not overwrite old forecasts after a bug/model change.
- Do not call model disagreement CLV.
- Do not assume every spread is -110.
- Do not use future closing lines as features in a historical decision-time backtest.
- Do not ingest a retrospective rating and pretend it was available in that exact form historically.
- Do not automatically bet every nonzero edge.
- Do not use Kelly on uncalibrated probabilities.
- Do not let one giant claimed EV outrank basic model-quality checks.
- Do not publish personal wager data in the public research bundle.
- Do not make the official lock so early that material information arrives afterward.
- Do not let GitHub cache be the only copy of unique history.

---

# 14. The best first modeling experiment

If only one predictive V2 experiment is implemented first, make it this:

## “Do our hand-grade matchups predict residual against the market?”

At a fixed horizon (e.g. T-24h initially):

```text
Y = final_home_margin - market_home_margin_T24
```

Use a regularized model with only pregame features:

```text
X = [
  total_grade_diff,
  home_ol_minus_away_dl,
  away_ol_minus_home_dl,
  home_qb_wr_minus_away_db,
  away_qb_wr_minus_home_db,
  home_run_matchup,
  away_run_matchup,
  coach_st_diff,
  market_spread,
  neutral,
  HFA_context
]
```

Then generate all predictions prospectively in 2026.

That experiment directly tests the thing we actually hope is proprietary: **whether the film grades contain matchup information the market has not fully priced.**
