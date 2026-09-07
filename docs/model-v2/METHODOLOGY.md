# Methodology

What every number on this project means, and which question it answers.
Written down because the same word was being used for several different things.

## The house convention

A margin is always from the **home team's** perspective, in lines and in results.

```
+7   home favoured by 7, or home won by 7
-3   away favoured by 3, or away won by 3
```

`db.py` normalizes this at ingest and re-proves it every run by regressing actual
margin on market margin: a slope near +1 means the sign is right, and a slope near
−1 would mean a source flipped. Nothing else in the project renegotiates it.

## Champion

The production model is `engine.Model` with `config/cfb_grades.json`, frozen as

```
C0-<date>.<first 8 of the merged config hash>
```

The hash is **of the fully merged config**, not the file. A sparse file inherits
engine defaults, so hashing the file would let a default move and leave the
version string unchanged — same name, different model. Two configs that differ
cannot share a version string even by accident.

`register_model` refuses to redefine an existing version whose config hash has
changed. A behaviour change is a new version.

## Horizons

Forecasts are taken at standardized distances from kickoff.

| | before kickoff | tolerance | official |
|---|---|---|---|
| T72 | 72 h | ±60 min | no |
| T24 | 24 h | ±45 min | no |
| T6 | 6 h | ±30 min | no |
| **T2** | **2 h** | **±20 min** | **yes** |
| T30 | 30 min | ±10 min | never |

**T2 is the official lock**: late enough that the grades, the availability and
the market have developed, early enough to be unambiguously pregame.

A run records its target, its real generated time, the delta and a status
(`accepted` / `early` / `late` / `post_kick`). A late run is recorded as late and
never relabelled — GitHub drops throttled cron runs, and a forecast that says
"T2" when it fired 90 minutes late makes every cross-game comparison quietly
false. `post_kick` is absolute: no tolerance widens far enough to make a forecast
generated after kickoff pregame.

A horizon whose window closes with nothing in it is written to `snapshot_misses`,
so a gap can never later be filled with a mislabelled forecast.

## Market

**Ingest preserves facts. Policy decides which facts to use.** Every provider
quote is stored in `market_quotes`, append-only, with the observation time. The
legacy `lines` table still keeps one preferred provider per game for the display
path; it is a materialized view, not the truth.

**`consensus_v1`** — each provider's latest quote at or before the instant, books
older than 90 minutes excluded, median spread and median total, moneyline
probabilities de-vigged **per book** before they are combined. A snapshot always
exists and carries a status: `valid_consensus`, `single_provider`,
`stale_fallback` or `missing`.

**`close_policy_v1`** — the same arithmetic over each provider's last valid quote
at or before **kickoff**, within 90 minutes of it. Nothing observed after kickoff
takes part, for any reason: a "closing line" fetched afterwards knows the score.
CFBD returns a retrospective line for a finished game, correctly stamped with the
fetch time, so historical closes come from the legacy stored line and are labelled
`legacy_stored_line`.

Prices are stored as `NULL` when the feed does not supply them, which for spread
and total juice is always. **`NULL` means unknown and stays unknown.**

## Grading

One module, `grading.py`, decides every result, and every function takes the
**side that was published**. None of them can infer one.

```
locked   the side published, at the line it was published at   ← THE RECORD
close    the same side, at the closing line                    ← a diagnostic
```

Both are recorded, forever, and neither substitutes for the other. A side taken
at +2.5 that closes +3.5 and loses by 3 won the wager and lost the closing-line
version; both are true.

**A pick's side is immutable.** The previous grader derived it at grading time as
`model_margin − closing_margin`, so when the market moved across the model's own
number the graded side flipped. One Virginia pick, laying 3 and winning by 26,
was recorded as a **loss**. The legacy `ats_result` column keeps that historical
value for audit; it is preserved, not trusted.

A side naming neither team raises rather than returning `L` — booking a defeat
for a wager that never existed is the failure that started this.

## ROI

Profit is reported **only where the exact price was recorded**.

A losing spread bet costs one unit whatever the juice was, so a loss has a known
return and a win without a recorded price does not. Averaging "rows with a known
profit" therefore averages over losses only and produces a confident **−100%**.
That is the −110 assumption wearing different clothes: an invented number that
looks like a measurement.

Where a synthetic analysis genuinely wants −110, the metric is named
`synthetic_roi_assuming_minus110` and never `roi`.

## Closing line value

```
spread, home side   closing − locked
spread, away side   locked − closing
total, OVER         closing − locked
total, UNDER        locked − closing
```

Positive means the number taken was better than the number it closed at. This is
**points only**. Half a point across a key number is worth more than half a point
at 16, and juice moves while the number does not, so price CLV is a separate
measure and the two are never summed.

`metrics.clv_mean` was the mean **absolute** gap between model and market at
prediction time — one observation, no direction, and not CLV at all. It is now
`mean_abs_market_disagreement`.

## What counts as what

```
a FORECAST     what a model believed at a time
an EVALUATION  what a strategy decided about it — INCLUDING no
a SIGNAL       what the strategy chose, at a side and a price
a BET          what a person actually wagered
a RESULT       what happened
a CLOSE        a later market fact
```

These are separate tables and they never overwrite each other. A forecast is not
deleted because a strategy declined it; a decline is a recorded row with reason
codes, because a strategy whose no-bets disappear has a record of the games it
liked.

## Strategy

`S0-2026.09.06`, a hashed data object rather than constants in code:

```
official_horizon          T2
allow_borrowed            false    a game the film could not answer
require_complete_grades   true
max_abs_spread            28.0     the domain the grade sheet can express
totals_enabled            false
moneyline_enabled         false
```

**Why unrated games are declined.** Elo answers when a team has no film grade, and
Elo holds every non-FBS team at one constant. On the 5 September slate the market
priced FCS opponents across 24 points while the model held them equal; six of the
top ten claimed edges were that constant talking. Those games went 4-6 in week 1
against 9-5 where both teams were graded.

**Why ±28.** A grade sheet spans about 25 rating points end to end and cannot
express a 45-point spread. Outside it the model is not wrong, it is out of domain.

**Why totals and moneylines are off.** A total is pace times efficiency, not
relative strength, and this model does not solve it. The repository has already
found that the largest claimed moneyline EVs performed worst.

## The four scoreboards

Never one number, and never summed.

| | over what | answers |
|---|---|---|
| **Forecast quality** | every forecast, bet or not | how accurate is the model |
| **Signal performance** | only official signals, at the locked line | what did the strategy do |
| **Closing diagnostic** | the same side at the close | did it beat the market |
| **User bets** | actual wagers | not modelled here, ever |

Forecast quality is paired against the market number **the model itself saw**,
from the same feature snapshot. Comparing a T2 forecast's error with the closing
line's error flatters or damns it depending on which way the market moved and says
nothing either way.

## Units — `scale` is fitted, not remembered

`scale` converts a rating point into a point of margin. It is a property of the
**grade sheet**, not of the model's edge, and the sheet changed between seasons
while the constant did not.

| grade vintage | rating spread | correlation with the market | fitted scale |
|---|---:|---:|---:|
| 2025, Grant's hand grades | 35.2 pts | 0.82 | **1.31** |
| 2026, EA-derived | 25.3 pts | 0.94 | **1.94** |

The config shipped 1.311 — correct for 2025, **48% too small for 2026**. The
consequence was systematic and one-directional:

| market line | model's mean error, scale 1.311 | at 1.94 |
|---|---:|---:|
| 2–3 (pick'em) | **+2.78** | +2.47 |
| 7–12 | +0.27 | +1.01 |
| 14–22 | −2.91 | −0.14 |
| 22–50 | **−5.67** | +0.86 |

Every big favourite was priced short, so the model took the underdog on games it
had no business being on: in week 1 of 2026 it took the away side 53 times and
went **20–33**. Oregon at Oklahoma State priced as Oregon by 6.5 against a market
number of 22.5. Model-to-market dispersion was 0.65; it is 0.91 now.

`calibrate.fit_units` re-fits it every run from the current season's own priced
games, so it cannot go stale again whatever sheet arrives next year. Guards: at
least 60 games, R² ≥ 0.45, a sanity band of 0.40–4.00, and 0.05 of hysteresis so
a fit that has not really moved does not mint a new Champion version. On 2025 the
auto-fit reproduces the hand value (1.16–1.31 through the season) and the record
is unchanged, which is the property that makes it safe.

**Home field is fitted and deliberately not adopted.** The joint fit puts it at
2.5–3.0 against a shipped 4.0, but fitting to the market means inheriting the
market's opinions — right for units, wrong for a quantity the market may be
mispricing. Across 2023–2026 the home team actually wins by 5.21 while the market
charges 4.71, and on 2025 the shipped 4.0 leaves the model's residual bias at
+0.13 points where the market-fitted 2.96 leaves it at +1.19. So `scale` is
fitted with `hfa` held, and the fitted home number is reported beside it.

**A replay uses its own season's units.** 2025 is replayed at 1.31 and 2026 runs
at 1.94; using the current season's number to replay an older one describes a
model that never existed.

## Selection — what counts as a best bet

Two records, never one, and never merged. The **best bets** record counts only
picks that qualified under the rule below; the **every game** record counts every
published pick. The public page opens on the first and offers the second in a
switch; the research page does the same on its Results tab.

The rule, version `B1`:

| rule | a pick is out when | what the excluded games did |
|---|---|---|
| `unrated` | either team has no film grade, so Elo answered | 2025: 49.2% over 126 games |
| `blowout` | the line is outside ±28, wider than a grade sheet can express | 2026 wk 1: 4–7 |
| `thin` | the model disagrees by under 3 points | 2025: 49.6% over 238 games |
| `early` | week 1 — no game has been played, so the model holds no in-season information at all | 2025: 49.1% (n=53) · 2026: 44.7% (n=85) |
| `market` | it is a total, not a spread | 2025 totals get *worse* with more disagreement: 52.2% at any gap, 48.8% past 4, 46.9% past 5 |

Replaying 2025 with the grades frozen at week 1 — which is the state 2026 is
actually in — the surviving board is **56.49% over 393 bets, ROI +7.8%**, against
**52.44%** for every game with a line. Its 95% interval is 51.6–61.4%, so the
lower bound is still under the 52.38% break-even: this is the best estimate
available, not a proven edge.

**The answer is stored, not recomputed.** `selection.classify` runs once, at lock
time, and writes `best_bet`, `decline_reason` and `selection_version` onto the
pick. Asking the question again at render time would mean a later edit to
`MIN_EDGE` could retroactively add winners to a finished season — the same reason
`ats_result_at_pick` is stored beside `grading_version` rather than regraded.

One consequence worth stating plainly: **week 1 of 2026 contains no best bets at
all.** 99 picks were published, 85 have been graded, and every one is a game the
board declines. The best-bets record is therefore 0–0, and the page says so
rather than borrowing the wider number.

## Performance form

The quality-point rule is binary: beating an unranked team 63–3 and 17–14 both
score `wq_other`, which is zero. That is survivable while the film is regraded
weekly, because the film carries the update. It is not survivable when the grades
are one preseason snapshot — 2026 has a single sync in week 1 and nothing since.

`form_weight` adds back, per team, a decayed and winsorized mean of how far it
beat or missed the model's own prediction, shrunk by `n/(n+k)`:

    form(team) = mean_decayed(actual - predicted, signed) × n/(n+k)
    margin    += form_weight × (form(home) - form(away))

Measured by replaying 2025 with the grades frozen at week 1:

| | ATS on the board | RMSE |
|---|---|---|
| no form term | 54.17% (n=432) | 16.228 |
| with it | **56.49%** (n=393) | **15.786** |

With 2025's real weekly regrades it is 56.16% — so it does not need switching off
if Grant resumes grading film; it simply has less left to do. **Fresh film is
still worth more than this term recovers**, which is the single largest available
improvement to the model and the one a person has to make.

Leak-free by construction: the accumulator is written only in `observe()`, and
both callers are strictly predict-then-observe. The residual is measured against
the **rating's** margin, never the form-adjusted one — feeding a correction its
own output back is how a correction runs away. The cap is 30 points, two standard
deviations of the actual-minus-market residual measured at 15.0 on 2025; the
first value was 21 and it bound on 32% of teams after week 1 alone.

## Development and prospective evidence

**2025 is development data.** It has been used to choose terms, fit scale, split
the grade and quality coefficients, fit the edge shrink and inspect buckets — by a
human who saw each result and then changed the system. That makes it a validation
set however carefully any single script splits it.

**2026 is prospective evidence**, for versions that did not train on those
outcomes. When a version changes, the old one keeps its record and the new one
starts a new bucket. A new model's backfilled history is a diagnostic, not
evidence.

## Win probability

Scored with Brier and log loss against the **de-vigged market probability from
the same feature snapshot** — the same-time baseline §19.2 requires. Scoring a
T2 model probability against a closing price would flatter the model for being
late rather than for being right.

A Brier score means nothing on its own, so the score a model that always
predicted the base rate would have achieved is reported beside it, and
calibration is reported in five bands rather than as one number.

A probability at exactly 0 or 1 is a claim of certainty about a football game.
Those are clipped, and the count of clipped rows is published — a model doing it
often is telling you something about itself.

**Measuring a probability does not authorise betting one.** `moneyline_enabled`
stays `false` in the strategy until a prospective calibration sample exists that
a human has read, per §12.5.

## Intervals

ATS records carry Wilson intervals with the −110 break-even (52.38%) printed
beside them, and the page says "not yet separated from break-even" rather than
using significance language.

Paired Champion-vs-challenger comparisons carry a **week-block bootstrap**
interval, fixed seed. Games inside one weekend share the market, the news cycle
and the weather; resampling games treats forty correlated observations as forty
independent ones and reports an interval too narrow to be true — flattering, and
wrong in the direction that promotes a challenger. Below four weeks it returns a
reason instead of an interval, because three weekends labelled 95% is fake
precision.

## Shadow layers

Availability (§22) and weather (§23) are recorded and evaluated, and **adjust
nothing**. Their reasons differ and both are stated on the page: availability has
no calibrated probability of absence, and weather has no wind from the one source
wired. A layer that says "shadow" in a docstring and shows numbers on a dashboard
is read as an input the model uses.

## Durability

The Actions cache is an accelerator. The record is an append-only JSONL journal
on the `data-state` branch, from which an empty database can be rebuilt and
reconciled table by table.

**Film grades are redacted from the published journal.** A grade event there
carries its snapshot id, team, timestamps and the hash of the vector — enough to
prove which grade state was in force and when it changed — and none of the
numbers.
