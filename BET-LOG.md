# Tracking your own bets

The site tracks two records, and keeps them apart on purpose.

**The model's record** is every pick it made, locked before kickoff and graded
after. It is on the **Results** tab and on the public page. Nothing you do affects
it and nothing can edit it after the fact — that is the whole reason it is worth
anything.

**Your record** is what you actually put money on, at the price you got, in the
size you chose. It is on the **My bets** tab. This is the one you set up below.

They will not match, and the gap between them is the interesting part. Believing a
model is profitable because the four games you happened to bet came in is the
classic way to lose money with a good model.

---

## Setting it up (once, about two minutes)

Add a tab named **`My Bets`** to the same Google Sheet your film grades live in.
Put these headers in the first row — any order, capitalisation does not matter:

| Date | Week | Team | Market | Side | Line | Odds | Units | Book | Notes |
|---|---|---|---|---|---|---|---|---|---|

That is it. It is read automatically within half an hour, every bet is matched to
its game, graded, and totalled. Nothing else to install or maintain.

### What goes in each column

| Column | Required | What it means |
|---|---|---|
| **Week** | yes | The week as the site shows it. Week 0 is a real week and works. |
| **Team** | yes | The side you bet. For a total, either team in the game. |
| **Market** | yes | `spread`, `moneyline` or `total`. `ml`, `ats` and `o/u` also work. |
| **Side** | totals only | `over` or `under`. Ignored for anything else. |
| **Line** | spread & totals | **The number you took, from your side.** `-7.5` if you laid 7.5, `+3` if you took 3, `52.5` for a total. |
| **Odds** | moneyline only | American odds: `-110`, `+150`. Blank means `-110`. |
| **Units** | yes | Your stake, in units. `1`, `0.5`, `2`. |
| **Date** | usually no | Only needed to tell two games apart — see below. |
| Book, Notes | no | For you. Never used in any calculation. |

**Week + Team is how a bet finds its game.** A team plays once a week, so that
pair is enough — no game IDs, no dates to get right. Team names are matched
loosely, so `Ole Miss`, `Mississippi` and `Ole Miss Rebels` all land on the same
team.

**Bowls are week `Bowls` on the site.** College football's data source numbers the
postseason from 1, which makes a 27 December bowl and the opening Saturday both
"week 1" — so bowls and playoff games get their own bucket instead.

**The one case where you need the Date:** a playoff team plays two or three games
inside that bucket, so `Bowls + Georgia` does not identify one game. Put the
kickoff date in the Date column and it resolves. `12/27`, `12/27/25` and
`2025-12-27` all work. If it is ambiguous and there is no date, the row is
reported rather than guessed — a bet graded against the wrong game still shows a
W or an L, and a wrong result that looks right is worse than a row you have to
fix.

### Example

| Date | Week | Team | Market | Side | Line | Odds | Units | Book |
|---|---|---|---|---|---|---|---|---|
| 9/5 | 1 | Clemson | spread | | +7.5 | -110 | 1 | DK |
| 9/5 | 1 | Wisconsin | moneyline | | | +1000 | 0.5 | FD |
| 9/6 | 1 | Texas | total | over | 51.5 | -105 | 1 | DK |
| 9/13 | 2 | Michigan | spread | | -3 | -115 | 2 | MGM |
| 12/27 | Bowls | Georgia | spread | | -3 | -110 | 1 | DK |

Row 1: took Clemson +7.5 for one unit. Row 2: half a unit on Wisconsin at +1000.
Row 3: over 51.5 in the Texas game. Row 4: laid 3 with Michigan, two units.
Row 5: a bowl — `Bowls` as the week, and the date so it knows which of Georgia's
postseason games you mean.

---

## Why units

A unit is a percentage of your bankroll, not a dollar amount. A record kept in
units stays comparable as the bankroll grows, and cannot be flattered by bet
sizing — betting 5 units on winners and 1 unit on losers looks great in dollars
and tells you nothing about whether you can pick.

The site shows dollars too. Set **1 unit =** on the My bets tab and every figure
gets a dollar equivalent beside it. That setting lives in your browser only; it
changes the display and never the record.

---

## What you get

- **Units won, and ROI per unit risked.** Per unit *risked*, not per bet — a
  2-unit loss and a 0.5-unit loss are not the same event.
- **Your break-even rate, from the prices you actually took.** Not the textbook
  52.38%. A book full of plus-money dogs needs a far lower hit rate to profit, and
  judging it against 52.38% would call a winning book a losing one.
- **Your closing line value** — whether the numbers you took beat where the market
  closed. This is the single most useful thing in the whole system, because it
  tells you whether you are good long before the win rate can.
- **Splits by market and by week**, and a bankroll curve in units.
- **Every row that could not be used, listed with the reason.** A bet log that
  quietly drops what it cannot parse is a log that is wrong and looks complete.

---

## Reading the model's record (the Results tab)

Every rate on that page carries the number of decisions behind it and a 95%
interval, and one column says **clears** — whether the whole interval sits above
break-even. That column is the only thing that answers "is this real".

It is worth knowing how slow this is. For a 95% interval to lift clear of 52.38%:

| If the true rate is | it takes about |
|---|---|
| 54% | 3,700 bets |
| 55% | 1,400 bets |
| 56% | 800 bets |
| 58% | 400 bets |

A 60% November means nothing on its own. This is why **closing line value** is on
the same page and why it is worth more attention early: it does not depend on
whether the ball bounced your way, so it separates a real edge from luck in dozens
of bets rather than thousands.
