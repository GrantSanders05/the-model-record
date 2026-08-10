# Tracking your own bets

The site tracks two records, and keeps them apart on purpose.

**The model's record** is every pick it made, locked before kickoff and graded
after. It is on the **Results** tab and on the public page. Nothing you do affects
it and nothing can edit it after the fact — that is the whole reason it is worth
anything.

**Your record** is what you actually put money on, at the price you got, in the
size you chose. It is on the **My bets** tab, and you keep it there.

They will not match, and the gap between them is the interesting part. Believing a
model is profitable because the four games you happened to bet came in is the
classic way to lose money with a good model.

---

## Logging a bet

Open **My bets**. The form is the first thing on the tab and there is no setup.

1. **Week**, then **Game** — both are real dropdowns off the actual schedule.
2. **Market** — Spread, Moneyline or Total.
3. **Side** — the two buttons carry the number and the price on offer, so
   picking a side fills in the line. `Georgia −7` and `Auburn +7` are one click
   each; you never retype a number that is already on the screen.
4. **Line, Odds, Units** — prefilled from the market. Change them to whatever
   you actually got; that is the number that matters.
5. **Log bet.**

That is the whole thing. It grades itself when the score lands, and everything
below the form — units, ROI, break-even, CLV, the bankroll curve, the splits —
recalculates immediately.

There are **Log** buttons on every row of the **Best bets** and **Schedule**
tabs too. They open this form already filled in from that row.

### A bet knows which game it is on

Because you pick the game from the schedule, a bet carries that game's own id.
Nothing is matched by name or by week afterwards, so a whole category of mistake
— two games in one week, a team spelled three ways, a bowl and a September game
sharing a week number — simply cannot happen to a bet logged here.

### Editing and deleting

Every bet you logged has **Edit** and **Delete** beside it. Editing loads it back
into the form. Logging the same bet twice at the same price asks you to confirm
first, because that is usually a double-click and occasionally real.

---

## Where the bets are kept, and how to move them

**In your browser, on the device you logged them on.** There is no account and no
server. That is deliberate: the entire system runs on free hosting, and a free
backend is a backend that disappears when somebody's trial ends.

The cost is that a bet logged on your laptop is not on your phone. Four buttons
handle that:

| Button | What it does |
|---|---|
| **Export** | Writes every bet to a `.json` file. This is also your backup. |
| **Copy** | Puts the same content on the clipboard — the easiest way to a phone. |
| **Import** | Reads an exported file back in. |
| **Paste in** | Takes the copied text on the other device. |

Importing the same bets twice does not duplicate them — anything already in the
book is skipped and it tells you how many.

> **Do this before you clear your browsing data.** Clearing site data deletes the
> book, the same as it would delete a password. Export takes two seconds.

---

## Why units

A unit is a percentage of your bankroll, not a dollar amount. A record kept in
units stays comparable as the bankroll grows, and cannot be flattered by bet
sizing — betting 5 units on winners and 1 unit on losers looks great in dollars
and tells you nothing about whether you can pick.

The site shows dollars too. Set **1 unit =** and every figure gets a dollar
equivalent beside it. That setting lives in your browser only; it changes the
display and never the record.

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
- **Every bet that could not be graded, listed with the reason.** A bet log that
  quietly drops what it cannot use is a log that is wrong and looks complete.

---

## The Google Sheet tab (optional, and no longer necessary)

A `My Bets` tab in the grades workbook still works, and rows already in one are
read every half hour and merged into the same tables, marked **sheet**. Use it if
you want to bulk-enter a backlog in a spreadsheet; otherwise ignore it. Bets
logged on the site are faster, appear instantly, and cannot be matched to the
wrong game.

If you do use it, the columns are:

| Date | Week | Team | Market | Side | Line | Odds | Units | Book | Notes |
|---|---|---|---|---|---|---|---|---|---|

**Week + Team is how a sheet row finds its game**, which is why this path needs
rules the site does not. Team names are matched loosely, so `Ole Miss`,
`Mississippi` and `Ole Miss Rebels` all land on the same team. **Line** is the
number you took, from your side: `-7.5` if you laid 7.5, `+3` if you took 3.
**Side** is only for totals. **Odds** defaults to `-110`. Bowls are week `Bowls`,
and if a playoff team plays more than one game in that bucket you need the
kickoff date in **Date** to say which — a row that is ambiguous is reported
rather than guessed, because a bet graded against the wrong game still shows a W
or an L, and a wrong result that looks right is worse than a row you have to fix.

A row edited in the sheet is edited in the sheet; the site will not write back to
it. If you log the same bet in both places, it is counted once.

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
