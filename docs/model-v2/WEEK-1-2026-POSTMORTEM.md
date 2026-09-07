# Week 1, 2026 — what happened, and what changed because of it

**Record as published:** 38–47 against the spread (44.71%), 31–39 on totals
(44.29%), across 85 graded picks. 99 picks were locked; 14 had not been graded
when this was written.

**The finding in one line:** the model did not break. The *record* was measuring
85 bets on a board that never offered any of them.

---

## 1. The record and the product were different things

`best_bets.py` has always declined two kinds of game, for reasons written down
long before this week: a game where a team has no film grade (Elo answers, and
Elo holds every non-FBS side at one constant rating), and a game whose line is
wider than a grade sheet can express. The public record counted both anyway.

Splitting week 1 by the board's own rules:

| | record | rate |
|---|---:|---:|
| **as published** | 38–47 | 44.7% |
| — a team had no film grade | 18–20 | 47.4% |
| — the line was wider than ±28 | 4–7 | 36.4% |
| **what was left** | 16–20 | 44.4% |

And then the rule the board did *not* have: **week 1 itself.** Before any game
has been played, the quality-points half of the rating is literally zero, and so
is form. The rating is the preseason film and nothing else. The site's own
preseason banner has said *"track these, do not bet them"* since it was written.
Replaying 2025, week 1 went 49.1% on the same rules; 2026 went 44.7%.

So the honest count of best bets in week 1 2026 is **zero**, and the best-bets
record is 0–0 rather than a selection of the ones that happened to win.

## 2. It was not mis-graded teams

The obvious reading of a bad week is that the ratings are wrong, so that was
tested first. For every team, `tools/regrade_report.py` computes how far it beat
the **model's** expectation, how far it beat the **market's**, and the difference
— the part of the miss the rating owns rather than the game.

Almost every large miss was one the market shared. Rutgers missed the model by
37.8 points and the closing line by 45.5; the market was *more* wrong. The
biggest gap the rating owns is Indiana at 18.6 points, which against a
game-to-game spread of 15.0 points is **1.08 standard errors** — an ordinary
Saturday.

**Zero teams clear 1.75σ.** After one game, none can: one game buys one game of
certainty. A first draft of that report used a flat 6-point bar and flagged 41 of
94 teams, which is not a regrade list, it is noise printed as a to-do.

What week 1 *did* show, twice, is the compression problem: the two clean cases
where the model missed by 15+ and the market missed by under 4 were Ohio State
56–3 over Ball State and Indiana 52–16 over North Texas. Both are games where the
model could not express a mismatch the market priced correctly. That is the
mechanism the ±28 rule already exists for, not a grading error.

## 3. What changed

**A stored selection rule.** `best_bets.qualifies` is now one function, and the
board, the record, the public page and the tests all ask it. The answer is
written onto the pick at lock time with the rule version that produced it, so a
later edit to a constant cannot retroactively improve a finished season. See
METHODOLOGY § *Selection*.

**Two records, both published.** Best bets leads because it is what the board
offers; every game is one click away and never hidden. Reporting only the wide
number describes a product nobody sells; reporting only the narrow one, with no
sign of the wide one, would look exactly like dropping the losers.

**A performance-form term.** The quality-point rule scores a 63–3 win and a 17–14
escape identically — every point of margin is discarded. That is fine while the
film is regraded weekly and it is not fine now: 2026 has **one** grade snapshot,
synced in week 1, and 2025 had nine. The model now carries each team's margin
against its own expectation, decayed, winsorized and shrunk for how little
evidence sits behind it. See METHODOLOGY § *Performance form*.

**A regrade report** (`tools/regrade_report.py`) that ranks teams by the part of
the miss the rating owns, in standard errors, and names which half of the sheet
to re-read. It writes nothing to `grades`: a position grade is a reading of film,
and deriving one from a box score would make the sheet's provenance a lie.

## 4. What this is worth

Replaying 2025 with the grades frozen at week 1 — 2026's actual situation:

| | record | rate | ROI |
|---|---:|---:|---:|
| every game with a line *(what the record used to measure)* | 483–438 | 52.44% | +0.1% |
| the best-bets rule, no form term | 234–198 | 54.17% | +3.4% |
| **the best-bets rule + form term** | **222–171** | **56.49%** | **+7.8%** |

Stable in every split: weeks 2–6 52.8%, weeks 7–11 58.1%, weeks 12+ 59.1%, odd
weeks 57.4%, even weeks 55.8%.

**The 95% interval is 51.6–61.4%, and break-even at −110 is 52.38%.** The lower
bound is still under water. This is the best estimate available with its
precision stated, on one season of development data — not a proven edge, and the
page says so in those words.

## 5. What was tested and rejected

- **Tightening ±28.** The 24–28 bucket went 8–10 on 2025, but n=18 and the
  cumulative caps are flat from 14 to 99. Moving a threshold on eighteen games is
  how a model gets fitted to a season. Unchanged.
- **An upper bound on edge.** A first pass found `|edge| ≥ 10` at 51.0% and
  proposed capping there. That was weeks 1–2 contamination: with week 1 excluded
  the same bucket is 54.5%, and `3 ≤ edge < 10` and `3 ≤ edge < ∞` are within
  0.4 points of each other. Dropped.
- **Excluding week 2.** Grouping "weeks 1–2" hid that they are categorically
  different — week 1 has no in-season information at all, week 2 has a full
  week's. Week 2 is in.
- **The form term with fresh grades.** Neutral on ATS (56.16% vs 56.14%) and
  positive on RMSE, so it stays on either way.

## 6. What is still missing

- **Injuries.** The §22 availability layer is built and its ESPN feed resolves
  every team, and both the `injuredReserveOrOut` and `suspended` groups are empty
  on all 136, with no athlete carrying a status. Verified directly on 7 September
  2026. College football has no mandatory injury report and ESPN's free API does
  not substitute for one. The layer is correct; the upstream is silent.
- **Fresh film.** This is the big one. Replaying 2025 with weekly regrades beats
  replaying it with the grades frozen, and the gap is larger than anything in
  this document recovers. 2026 has one snapshot, from week 1. **The highest-value
  change available to this model is Grant grading week 1's film.**
- **Prices.** The feed supplies moneylines and not spread juice, so spread ROI is
  reported as unavailable rather than assumed at −110.
