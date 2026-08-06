# The Model

A college football prediction model, its backtester, and a public track record
that grades itself.

**The picks are published before kickoff and never edited.** That is the entire
point. Anyone can claim a record; this repository is the receipt.

---

## What this is

A position-group rating system, kept by hand from film, turned into a point
spread for every FBS game. The arithmetic is in this repository. The grades that
feed it are not — they are the one input that isn't freely available, and they
stay private. Everything else is open, including the parts that don't flatter
the model.

## How the record is kept honest

- **Picks lock before kickoff.** `picks_log` is append-only: the first number
  recorded for a game is the only one that will ever exist for it. Grading fills
  in results afterwards and cannot touch the pick.
- **Graded against the closing line**, not the line the pick was made at. Both
  are stored, so closing-line value stays measurable.
- **Walk-forward everywhere.** Games replay in chronological order and the model
  predicts a game before it observes the result. A rating used for week 8 can
  only contain weeks 1–7.
- **The backtester has to prove it works.** `validate_harness.py` feeds the
  evaluator an oracle, an anti-oracle, a small real edge, and pure noise, and
  asserts it labels each correctly. A backtester that reports ~50% for
  everything is indistinguishable from a broken one until you check.

## What the numbers actually say

Honest results matter more than good ones, so here are both.

**Break-even at −110 juice is 52.38%.** Anything below that loses money no
matter how it looks. Picking winners is not the test: in college football the
market favourite wins ~76% straight up before any model exists.

**No model built from public data beat the closing line.** Fitted on 2014–2020
and tested on 2021–2025 — 4,440 bets:

| Model | ATS% | 95% CI | ROI% |
|---|---|---|---|
| Elo (results only) | 49.89 | 48.4 – 51.4 | −4.76 |
| Opponent-adjusted efficiency (SP+ family) | 49.50 | 48.0 – 51.0 | −5.49 |
| Either, anchored to the market | ~49.3 | 47.9 – 50.7 | −5.9 |

That is the state of the art, not a failed implementation. The closing line is
very hard to beat, and most published "systems" are measuring something else.

**The hand-kept grades did better than the machines** — 53.2% ATS untouched, and
54.8% with one redundant term removed, over 502 games in 2025. That is above
break-even and it is **not proven**: the confidence interval still includes it,
and it is a single partial season.

**How much evidence a real edge needs:**

| If the true rate is | Bets required to prove it |
|---|---|
| 54% | 5,875 |
| 55% | 2,243 |
| 56% | 1,173 |

At roughly 500 graded bets a season, proving a 54% edge takes about a decade.
Which is why this repository publishes a running ledger instead of a claim.

## Running it

Nothing outside the Python standard library is needed to fetch data, backtest,
optimize, or generate picks. `requirements.txt` covers only the optional Google
Sheets export.

```bash
python3 src/fetch_nfl.py                        # free, no key
python3 src/fetch_cfb.py --seasons 2014-2026    # needs a CollegeFootballData key
python3 src/validate_harness.py                 # prove the backtester is honest
python3 src/bakeoff.py --train 2014-2020 --test 2021-2025
python3 src/run_update.py --sport cfb --config config/cfb_grades.json
```

| Module | Role |
|---|---|
| `db.py` | schema, and the one sign convention everything depends on |
| `fetch_cfb.py` / `fetch_nfl.py` | data in, with an API budget guard |
| `engine.py` | the model: grade, Elo, efficiency and blended raters |
| `pro_models.py` | opponent-adjusted efficiency + market anchoring |
| `backtest.py` | walk-forward replay |
| `metrics.py` | ATS vs close, ROI, calibration, significance |
| `optimize.py` | parameter search with a train/test split |
| `validate_harness.py` | proves the evaluator can tell good from bad |
| `ledger.py` | the append-only pick record |
| `publish.py` | renders the public track record page |
| `staking.py` | Kelly sizing and how much evidence an edge needs |

## Data sources

| Source | Covers | Key |
|---|---|---|
| [CollegeFootballData](https://collegefootballdata.com) | games, lines, rankings, per-play efficiency | free key |
| [nflverse](https://github.com/nflverse/nfldata) | NFL 1999–present with closing spreads and totals | none |

## A word on betting

Nothing here is advice. The model has not demonstrated an edge that survives
statistical scrutiny, and this repository says so on purpose. If it ever does,
size with fractional Kelly — betting a *believed* 54% edge at full Kelly carries
a 34% chance of halving a bankroll over 1,000 bets, and a 96% chance if the edge
turns out to be noise. `staking.py` computes both.
