"""
test_tracking.py — prove the record arithmetic, including that it can be wrong.

Grading is the one part of this system nobody will ever eyeball. A spread bet
graded from the wrong side is invisible: the record still fills up, the
percentages still look plausible, and the error only shows as "the model is worse
than the backtest said" a season later.

So every branch is checked against a hand-worked case, and the suite ends by
CORRUPTING the grader and asserting the tests go red. A test file that has never
failed is a claim, not evidence.

    python3 tools/test_tracking.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import bet_log            # noqa: E402
import tracking           # noqa: E402

P = F = 0


def ok(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
        print("  [PASS] %s" % name)
    else:
        F += 1
        print("  [FAIL] %s%s" % (name, (" — " + str(detail)) if detail else ""))


def near(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) < tol


# ── a game: home 31, away 24. Home by 7, total 55. ────────────────────────────
GAME = {"game_id": "g1", "home_team": "Georgia", "away_team": "Auburn",
        "home_score": 31, "away_score": 24, "kickoff": "2026-09-05T18:00",
        "home_margin": 10.0, "total": 52.5, "home_ml": -400, "away_ml": 320,
        "neutral_site": 0, "week": 2}


def bet(**kw):
    b = {"row": 2, "week": 2, "team_raw": kw.get("team", "Georgia"),
         "team": kw.get("team", "Georgia"), "market": "spread", "side": None,
         "line": None, "odds": -110.0, "units": 1.0}
    b.update(kw)
    return b


print("\n── spread grading, from the BET's side ──")
# Home laid 10, won by 7 -> loses. This is the one that is invisible when wrong:
# flip the sign and it grades as a win and nothing complains.
r, u, _ = bet_log.grade_bet(bet(team="Georgia", line=-10.0), GAME)
ok("home -10 wins by 7 -> LOSS", r == "L" and near(u, -1.0), "%s %s" % (r, u))
r, u, _ = bet_log.grade_bet(bet(team="Auburn", line=10.0), GAME)
ok("away +10 loses by 7 -> WIN", r == "W" and near(u, 100 / 110), "%s %s" % (r, u))
r, u, _ = bet_log.grade_bet(bet(team="Georgia", line=-3.0), GAME)
ok("home -3 wins by 7 -> WIN", r == "W")
r, u, _ = bet_log.grade_bet(bet(team="Georgia", line=-7.0), GAME)
ok("home -7 wins by exactly 7 -> PUSH", r == "P" and near(u, 0.0), r)
r, u, _ = bet_log.grade_bet(bet(team="Auburn", line=7.0), GAME)
ok("away +7 -> PUSH too", r == "P", r)
# Half-point lines cannot push, which is the entire reason books use them.
r, _, _ = bet_log.grade_bet(bet(team="Georgia", line=-6.5), GAME)
ok("home -6.5 -> WIN", r == "W")
r, _, _ = bet_log.grade_bet(bet(team="Georgia", line=-7.5), GAME)
ok("home -7.5 -> LOSS", r == "L")

print("\n── stake size scales the payout, not the result ──")
r, u, _ = bet_log.grade_bet(bet(team="Georgia", line=-3.0, units=2.5), GAME)
ok("2.5u win at -110 pays 2.273u", near(u, 2.5 * 100 / 110), u)
r, u, _ = bet_log.grade_bet(bet(team="Georgia", line=-10.0, units=2.5), GAME)
ok("2.5u loss costs exactly 2.5u", near(u, -2.5), u)

print("\n── odds change the payout ──")
r, u, _ = bet_log.grade_bet(bet(team="Auburn", line=10.0, odds=+320, units=1), GAME)
ok("+320 win pays 3.2u", near(u, 3.2), u)
r, u, _ = bet_log.grade_bet(bet(team="Georgia", market="moneyline", odds=-400), GAME)
ok("-400 favourite wins -> 0.25u", r == "W" and near(u, 0.25), "%s %s" % (r, u))
r, u, _ = bet_log.grade_bet(bet(team="Auburn", market="moneyline", odds=+320), GAME)
ok("+320 dog loses -> -1u", r == "L" and near(u, -1.0), "%s %s" % (r, u))

print("\n── totals ──")
r, _, _ = bet_log.grade_bet(bet(market="total", side="over", line=52.5), GAME)
ok("over 52.5 with 55 -> WIN", r == "W")
r, _, _ = bet_log.grade_bet(bet(market="total", side="under", line=52.5), GAME)
ok("under 52.5 with 55 -> LOSS", r == "L")
r, _, _ = bet_log.grade_bet(bet(market="total", side="over", line=55.0), GAME)
ok("over 55 with 55 -> PUSH", r == "P")

print("\n── a game that has not finished is not a loss ──")
unplayed = dict(GAME, home_score=None, away_score=None)
r, u, d = bet_log.grade_bet(bet(team="Georgia", line=-3.0), unplayed)
ok("ungraded returns None, not L", r is None and u is None, "%s %s" % (r, u))

print("\n── closing line value, in the bet's own direction ──")
# Took Georgia -7, it closed -10: the number got worse for us by 3.
ok("laying fewer points than the close is +CLV",
   near(bet_log.closing_clv(bet(team="Georgia", line=-7.0), GAME), 3.0),
   bet_log.closing_clv(bet(team="Georgia", line=-7.0), GAME))
ok("laying more is -CLV",
   near(bet_log.closing_clv(bet(team="Georgia", line=-12.0), GAME), -2.0),
   bet_log.closing_clv(bet(team="Georgia", line=-12.0), GAME))
ok("taking more points than the close is +CLV",
   near(bet_log.closing_clv(bet(team="Auburn", line=12.0), GAME), 2.0),
   bet_log.closing_clv(bet(team="Auburn", line=12.0), GAME))
ok("over below the closing total is +CLV",
   near(bet_log.closing_clv(bet(market="total", side="over", line=50.0), GAME), 2.5))
ok("under below the closing total is -CLV",
   near(bet_log.closing_clv(bet(market="total", side="under", line=50.0), GAME), -2.5))

print("\n── unit accounting ──")
graded = [
    dict(bet(team="Georgia", line=-3.0), result="W", units_won=100 / 110,
         kickoff="1", clv=1.0, market="spread", week=1),
    dict(bet(team="Georgia", line=-10.0), result="L", units_won=-1.0,
         kickoff="2", clv=-1.0, market="spread", week=1),
    dict(bet(team="Georgia", line=-7.0), result="P", units_won=0.0,
         kickoff="3", clv=0.0, market="spread", week=2),
    dict(bet(team="Auburn", market="moneyline", odds=+320, units=2.0),
         result="W", units_won=6.4, kickoff="4", clv=None, week=2),
]
t = bet_log.totals(graded, unit_size=50.0)
ok("pushes are excluded from the rate", t["w"] == 2 and t["l"] == 1 and t["push"] == 1)
ok("units risked excludes the push", near(t["units_risked"], 4.0), t["units_risked"])
ok("units won sums the payouts", near(t["units_won"], round(100/110 - 1 + 6.4, 2), 0.01),
   t["units_won"])
# Compared against the UNROUNDED total. Checking against the rounded one is off by
# a cent and would have made a correct function look broken.
raw = 100 / 110 - 1 + 6.4
ok("ROI is per unit RISKED", near(t["roi"], round(100 * raw / 4.0, 2), 0.02), t["roi"])
ok("dollars are units x unit size", near(t["dollars_won"], round(raw * 50.0, 2), 0.02),
   t["dollars_won"])
# Break-even must follow the prices actually taken. A book with a +320 winner in it
# does not need 52.38%.
ok("break-even reflects the odds taken, not a textbook -110",
   t["break_even"] < 52.38, t["break_even"])
ok("bankroll curve has one point per settled bet", len(t["curve"]) == 4)
ok("curve ends at the net", near(t["curve"][-1]["units"], t["units_won"], 0.01))
ok("CLV mean ignores bets with no closing line",
   t["clv_n"] == 3 and near(t["clv_mean"], 0.0), t.get("clv_mean"))

print("\n── rates carry an interval, and it decides 'clears' ──")
hot = tracking.rate(6, 2)                      # 75% on 8
grind = tracking.rate(540, 460)                # 54% on 1,000
proven = tracking.rate(784, 616)               # 56% on 1,400
ok("a 75% start does NOT clear break-even", hot["pct"] == 75.0 and hot["clears"] is False,
   str(hot["ci95"]))
# I asserted this one cleared. It does not, and the code was right: at 54% the
# interval only lifts clear of 52.38% somewhere around 3,700 bets. That number is
# the reason this column exists.
ok("54% on 1,000 does NOT clear either", grind["clears"] is False, str(grind["ci95"]))
ok("56% on 1,400 does", proven["clears"] is True, str(proven["ci95"]))
ok("the interval is the reason, not the rate",
   hot["pct"] > proven["pct"] and proven["clears"] and not hot["clears"])
ok("no decisions -> no rate, not 0%", tracking.rate(0, 0)["pct"] is None)
ok("pushes reported, excluded from n", tracking.rate(5, 5, 3)["n"] == 10)

print("\n── a straight-up hit rate is not a profit ──")
# Ten -400 favourites, seven win. 70% "accuracy", and it loses money: break-even at
# -400 is 80%. My first draft used nine winners, which profits — the trap needs a
# rate that beats a coin flip and loses to the price, not one that beats both.
rows = [{"ml_result": "W" if i < 7 else "L", "ml_odds_at_pick": -400}
        for i in range(10)]
m = tracking.moneyline_rate(rows)
ok("70% hit rate", m["pct"] == 70.0)
ok("...and negative units", m["units"] < 0, m["units"])
ok("units are what the record must lead with", m["roi"] < 0, m["roi"])

print("\n── model CLV reads from the pick's side ──")
# Home favoured by 3 when picked, closed at 5.
row = {"market_margin_at_pick": 3.0, "closing_margin": 5.0,
       "ats_pick": "Georgia", "home_team": "Georgia", "away_team": "Auburn"}
ok("home laid 3 and it closed 5 -> beat the close by 2",
   near(tracking.clv_of(row), 2.0), tracking.clv_of(row))
# The away side of that same move took +3 and watched it become +5. Worse number.
ok("away took +3 and it closed +5 -> WORSE by 2",
   near(tracking.clv_of(dict(row, ats_pick="Auburn")), -2.0),
   tracking.clv_of(dict(row, ats_pick="Auburn")))
# ...and the reverse move, so a passing suite cannot just be sign-agnostic.
back = {"market_margin_at_pick": 7.0, "closing_margin": 3.0,
        "ats_pick": "Auburn", "home_team": "Georgia", "away_team": "Auburn"}
ok("away took +7 and it closed +3 -> beat the close by 4",
   near(tracking.clv_of(back), 4.0), tracking.clv_of(back))
ok("home laying 7 that closed 3 -> WORSE by 4",
   near(tracking.clv_of(dict(back, ats_pick="Georgia")), -4.0),
   tracking.clv_of(dict(back, ats_pick="Georgia")))
ok("no closing line -> no CLV, not 0",
   tracking.clv_of(dict(row, closing_margin=None)) is None)

print("\n── the parser reports bad rows instead of dropping them ──")
rows_in = [
    ["Date", "Week", "Team", "Market", "Side", "Line", "Odds", "Units"],
    ["9/5", "2", "Georgia", "spread", "", "-7.5", "-110", "1"],
    ["9/5", "2", "Auburn", "ml", "", "", "+320", "0.5"],
    ["9/5", "2", "Georgia", "total", "over", "52.5", "", "1"],
    ["", "", "", "", "", "", "", ""],
    ["9/6", "2", "Georgia", "total", "", "52.5", "", "1"],
    ["9/6", "", "Georgia", "spread", "", "-3", "", "1"],
    ["9/6", "2", "Georgia", "spread", "", "-3", "", "0"],
]
bets, problems = bet_log.parse_rows(rows_in)
ok("three good rows parsed", len(bets) == 3, len(bets))
ok("blank rows are skipped silently", all("row 5" not in p for p in problems))
ok("a total with no side is reported", any("over or under" in p for p in problems))
ok("a missing week is reported", any("needs a Week" in p for p in problems))
ok("zero units is reported", any("positive number" in p for p in problems))
ok("problems are returned, not raised", len(problems) == 3, problems)
ok("odds default to -110 on a spread", bets[0]["odds"] == -110.0)
ok("plus-money odds survive parsing", bets[1]["odds"] == 320.0)
ok("'ml' is understood as moneyline", bets[1]["market"] == "moneyline")
# The site labels that bucket "Bowls", so the sheet must accept the word it shows.
for word in ("Bowls", "bowl", "Playoff", "postseason"):
    b, _p = bet_log.parse_rows([["Week", "Team", "Market", "Line", "Units"],
                                [word, "Georgia", "spread", -3, 1]])
    ok("week %r is understood as the bowls bucket" % word,
       len(b) == 1 and b[0]["week"] == 99, b and b[0].get("week"))

# ── the shared case table ─────────────────────────────────────────────────────
#
# The cases above are written out longhand because they are the readable record of
# what "graded correctly" means. This runs the SAME arithmetic from a JSON file
# that the browser's grader also reads, and that is the point of the file: bets are
# now entered on the site and graded there, so the same book is served by two
# implementations. Two implementations of anything drift. When they do, this goes
# red on one side and tools/qa/research.mjs goes red on the other, instead of the
# two producing different totals that nobody can reconcile.
print("\n── the shared case table, as Python grades it ──")
import json as _json

_CASES = _json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "grading_cases.json")))


def _case_game(name):
    """The table's neutral game shape, in the column names bet_log expects."""
    g = _CASES["games"][name]
    return {"game_id": name, "home_team": g["home"], "away_team": g["away"],
            "home_score": g["home_score"], "away_score": g["away_score"],
            "home_margin": g["line"], "total": g["total"],
            "home_ml": g.get("home_ml"), "away_ml": g.get("away_ml"),
            "kickoff": "2025-09-06T18:00", "neutral_site": 0, "week": 1}


_bad = 0
for c in _CASES["cases"]:
    g = _case_game(c["game"])
    b = {"team": c["team"], "market": c["market"], "side": c.get("side"),
         "line": c["line"], "odds": c["odds"], "units": c["units"]}
    r, u, _d = bet_log.grade_bet(b, g)
    clv = bet_log.closing_clv(b, g)
    good = (r == c["result"]
            and (u is None if c["units_won"] is None else near(u, c["units_won"], 5e-4))
            and (clv is None if c["clv"] is None else near(clv, c["clv"], 5e-4)))
    if not good:
        _bad += 1
        print("     %s -> %s %s clv %s, expected %s %s clv %s"
              % (c["why"], r, u, clv, c["result"], c["units_won"], c["clv"]))
ok("all %d shared cases grade as written" % len(_CASES["cases"]), _bad == 0,
   "%d wrong" % _bad)
ok("the table covers every market",
   {c["market"] for c in _CASES["cases"]} == {"spread", "moneyline", "total"})
ok("the table covers win, loss, push and not-yet-graded",
   {c["result"] for c in _CASES["cases"]} == {"W", "L", "P", None})

print("\n" + "=" * 62)
print("%d passed, %d failed" % (P, F))

# ── anti-vacuity: break it on purpose and require red ──────────────────────────
print("\n── proving these tests can fail ──")
_real = bet_log.grade_bet


def flipped(b, g):
    r, u, d = _real(b, g)
    return ({"W": "L", "L": "W"}.get(r, r),
            None if u is None else -u, d)


bet_log.grade_bet = flipped
before = F
r, u, _ = bet_log.grade_bet(bet(team="Georgia", line=-10.0), GAME)
ok("[control] a sign-flipped grader must FAIL this", r == "L" and near(u, -1.0))
bet_log.grade_bet = _real
if F > before:
    print("  ...it failed, as required. The spread tests are real.")
    F = before
else:
    F += 1
    print("  [FAIL] the corrupted grader still passed — these tests prove nothing")

# ── ambiguity: ambiguity must be refused, not guessed ──────────────────────────
print("\n── a team with two games in one week is refused, not guessed ──")
import db as _db, tempfile as _tf, os as _os

_tmp = _tf.mkdtemp()
_c = _db.connect(_os.path.join(_tmp, "amb.db"))
_c.executemany(
    "INSERT INTO games (game_id, sport, season, week, season_type, kickoff, "
    "home_team, away_team, home_score, away_score, neutral_site) "
    "VALUES (:id,'cfb',2025,:wk,:st,:ko,:h,:a,:hs,:as_,0)",
    [{"id": "reg", "wk": 1, "st": "regular", "ko": "2025-08-30T18:00",
      "h": "Georgia", "a": "Auburn", "hs": 31, "as_": 24},
     {"id": "bowl", "wk": 1, "st": "postseason", "ko": "2025-12-27T18:00",
      "h": "Georgia", "a": "Texas", "hs": 10, "as_": 40}])
_c.commit()

sheet = [["Week", "Team", "Market", "Line", "Units"],
         [1, "Georgia", "spread", -3, 1]]
res = bet_log.build(_c, "cfb", 2025, sheet)
ok("an ambiguous week+team produces no graded bet", len(res["bets"]) == 0,
   len(res["bets"]))
ok("...and says why, with the fix", 
   any("plays 2 games" in p and "Date column" in p for p in res["problems"]),
   res["problems"])

# A playoff team plays two or three games inside the bowls bucket, so week+team
# genuinely cannot identify one. The Date column resolves it — and must resolve it
# to the RIGHT one, which is the half that would be invisible if it were wrong.
dated = [["Date", "Week", "Team", "Market", "Line", "Units"],
         ["2025-12-27", 1, "Georgia", "spread", -3, 1]]
resd = bet_log.build(_c, "cfb", 2025, dated)
ok("a Date disambiguates two games in one week", len(resd["bets"]) == 1,
   resd["problems"])
ok("...to the game on that date", resd["bets"][0]["game_id"] == "bowl",
   resd["bets"][0].get("game_id"))
ok("...and grades against THAT game (Georgia -3, lost by 30)",
   resd["bets"][0]["result"] == "L", resd["bets"][0]["result"])
# The other date picks the other game, so this is not passing by luck of ordering.
dated2 = [["Date", "Week", "Team", "Market", "Line", "Units"],
          ["8/30", 1, "Georgia", "spread", -3, 1]]
res2d = bet_log.build(_c, "cfb", 2025, dated2)
ok("the other date picks the other game",
   len(res2d["bets"]) == 1 and res2d["bets"][0]["game_id"] == "reg"
   and res2d["bets"][0]["result"] == "W",
   res2d["bets"] and res2d["bets"][0].get("game_id"))
ok("a date matching neither is refused, not guessed",
   len(bet_log.build(_c, "cfb", 2025,
       [["Date","Week","Team","Market","Line","Units"],
        ["7/4", 1, "Georgia", "spread", -3, 1]])["bets"]) == 0)

# With the bowl labelled separately the same sheet grades cleanly — and against the
# REGULAR game, which is the one week 1 means.
labels = {"bowl": 99}
res2 = bet_log.build(_c, "cfb", 2025, sheet, week_labels=labels)
ok("labelling the bowl resolves it", len(res2["bets"]) == 1, len(res2["bets"]))
ok("...to the regular-season game",
   res2["bets"][0]["game_id"] == "reg", res2["bets"][0].get("game_id"))
ok("...and grades it correctly (Georgia -3, won by 7)",
   res2["bets"][0]["result"] == "W", res2["bets"][0]["result"])
# And the bowl itself is reachable under its own label.
sheet2 = [["Week", "Team", "Market", "Line", "Units"], [99, "Georgia", "spread", -3, 1]]
res3 = bet_log.build(_c, "cfb", 2025, sheet2, week_labels=labels)
ok("the bowl is bettable under week 99",
   len(res3["bets"]) == 1 and res3["bets"][0]["game_id"] == "bowl",
   res3["bets"] and res3["bets"][0].get("game_id"))
ok("...and grades against the BOWL result (lost by 30)",
   res3["bets"][0]["result"] == "L", res3["bets"][0]["result"])

_c.close()
for _f in _os.listdir(_tmp):
    _os.unlink(_os.path.join(_tmp, _f))
_os.rmdir(_tmp)

print("=" * 62)
print("%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
