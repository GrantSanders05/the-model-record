"""
bet_log.py — the bets Grant actually placed, in units, graded automatically.

This is deliberately separate from the model's own record. They answer different
questions and conflating them is how people end up believing a model is profitable
because they got lucky on the four games they actually bet.

    picks_log   every pick the model made, locked before kickoff, graded.
                Cannot be edited. It is the model's record.

    this        what Grant chose to put money on, at the price HE got, in the size
                HE chose. It is his record. It will not match the model's, and the
                gap between them is itself worth looking at.

WHERE IT LIVES -- AND THIS FILE IS NO LONGER THE MAIN ANSWER. Bets are entered on
the research site now and stored in the browser, because "open a spreadsheet, find
the tab, type a row, wait half an hour" is a workflow people abandon. A bet logged
there carries the game's own id, so none of the matching below applies to it.

This module handles the LEGACY path: a tab called "My Bets" in the same workbook
the film grades are in. It stays because rows already typed there must not vanish,
and because a spreadsheet is a reasonable way to bulk-enter a backlog. It is
read-only from here -- the sheet is the source of truth for its own rows, and every
run re-derives from it. Deleting a row deletes the bet; that is correct, it is his
log. The site merges both and grades them together.

PARSING AND MATCHING HAPPEN HERE; GRADING HAPPENS IN THE BROWSER. `grade_bet` and
`closing_clv` below are still the reference implementation and are still exercised,
but the numbers a human reads come from the JavaScript port, so that one book is
never half computed each way. tools/grading_cases.json is the shared table both
sides are held to, and either drifting fails a build.

UNITS, NOT DOLLARS. A unit is a percentage of bankroll, so a record in units stays
comparable as the bankroll changes and cannot be flattered by bet sizing. Dollars
are shown too, but they are a display multiplication of units by one number he sets.

MATCHING. By (week, team). A team plays once a week, so that pair identifies a game
without anyone typing a game id. Names go through the same alias table the rest of
the pipeline uses, so "Ole Miss", "Mississippi" and "Ole Miss Rebels" all land.

The expected columns, in any order, case-insensitive:

    Week      required   the week number as the site shows it (0 is allowed)
    Team      required   the side bet. For a total, either team in the game.
    Market    required   spread | moneyline | total     (ml/ats/ou also accepted)
    Side                 for totals: over | under. Ignored otherwise.
    Line                 the number taken: -7.5, +3, or the total. Blank for ML.
    Odds                 American. Blank means -110, which is what spreads and
                         totals are unless a book says otherwise.
    Units     required   stake, in units. 1, 0.5, 2.
    Date                 informational
    Book                 informational
    Notes                informational
"""

import re

import metrics
import team_aliases

TAB = "my bets"
BOWLS = 99          # matches research_export.BOWLS: the December/January bucket


def _week_name(w):
    return "the bowls" if int(w) == BOWLS else "week %s" % w

MARKETS = {
    "spread": "spread", "ats": "spread", "line": "spread", "sides": "spread",
    "moneyline": "moneyline", "ml": "moneyline", "money line": "moneyline",
    "total": "total", "totals": "total", "ou": "total", "o/u": "total",
}
OVER = {"over", "o", "ov"}
UNDER = {"under", "u", "un"}


def _norm(s):
    return re.sub(r"[^a-z0-9/ ]", "", str(s or "").strip().lower())


def _num(v):
    """A number from a spreadsheet cell, or None. Tolerates '+3.5', '−7', '1u', ''."""
    if v is None:
        return None
    s = str(v).strip().replace("−", "-").replace("−", "-")
    s = s.replace("$", "").replace(",", "").replace("u", "").replace("U", "")
    s = s.replace("pk", "0").replace("PK", "0").replace("EVEN", "").replace("even", "")
    if not s or s in ("-", "+"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_rows(rows):
    """
    Sheet rows -> bet dicts. Returns (bets, problems).

    Problems are RETURNED, not raised and not swallowed. A typo in one row must not
    lose the other forty, and it must not vanish either -- a bet that silently fails
    to parse is a bet missing from a record that claims to be complete.
    """
    if not rows:
        # An empty tab is an empty log, not a fault. It used to be reported as a
        # problem, which was reasonable when the sheet was the only way to log a
        # bet and is wrong now that it is the optional one: the site would print
        # "1 bet could not be graded — the tab is empty" at somebody who has a
        # perfectly good book and simply is not using the spreadsheet.
        return [], []
    header, body = None, []
    for i, r in enumerate(rows):
        cells = [_norm(c) for c in r]
        if "week" in cells and "team" in cells:
            header, body = {c: j for j, c in enumerate(cells) if c}, rows[i + 1:]
            break
    if header is None:
        return [], ["no header row found — it needs at least the columns Week and Team"]

    def cell(r, name):
        j = header.get(name)
        return r[j] if j is not None and j < len(r) else None

    bets, problems = [], []
    for n, r in enumerate(body, start=2):
        if not any(str(c).strip() for c in r):
            continue
        # The site labels the postseason bucket "Bowls", so the sheet has to accept
        # what the site shows. Typing 99 works too, but nobody should have to know
        # that a magic number exists.
        raw_week = _norm(cell(r, "week"))
        week = (BOWLS if raw_week in ("bowls", "bowl", "playoff", "playoffs",
                                      "postseason", "post season")
                else _num(cell(r, "week")))
        team = str(cell(r, "team") or "").strip()
        if week is None or not team:
            problems.append("row %d: needs a Week and a Team" % n)
            continue
        market = MARKETS.get(_norm(cell(r, "market")) or "spread")
        if market is None:
            problems.append("row %d: market %r is not spread, moneyline or total"
                            % (n, cell(r, "market")))
            continue
        units = _num(cell(r, "units"))
        if units is None or units <= 0:
            problems.append("row %d: Units must be a positive number" % n)
            continue
        side = _norm(cell(r, "side"))
        if market == "total":
            if side in OVER:
                side = "over"
            elif side in UNDER:
                side = "under"
            else:
                problems.append("row %d: a total needs Side = over or under" % n)
                continue
        line = _num(cell(r, "line"))
        if market in ("spread", "total") and line is None:
            problems.append("row %d: a %s bet needs the Line you took" % (n, market))
            continue
        odds = _num(cell(r, "odds"))
        if market == "moneyline" and odds is None:
            problems.append("row %d: a moneyline bet needs the Odds you got" % n)
            continue
        bets.append({
            "row": n,
            "week": int(week),
            "team_raw": team,
            "team": team_aliases.canonical("cfb", team) or team,
            "market": market,
            "side": side if market == "total" else None,
            "line": line,
            "odds": odds if odds is not None else -110.0,
            "units": units,
            "date": str(cell(r, "date") or "").strip() or None,
            "book": str(cell(r, "book") or "").strip() or None,
            "notes": str(cell(r, "notes") or "").strip() or None,
        })
    return bets, problems


def _index_games(conn, sport, season, week_labels=None):
    """
    (week, team) -> game row, plus the set of keys that matched more than one game.

    The naive version assigned into the dict and let a second match overwrite the
    first. That is silent and it fired: CFBD numbers the postseason from 1, so a
    December bowl and the opening Saturday are both "week 1", and a bet on the bowl
    was graded against a September game. It won, so nothing looked wrong.

    Bowls now carry their own label, which removes the collision — and the ambiguity
    check stays anyway, because "one team, one game, one week" is an assumption about
    a third party's data, and the cost of it being wrong is a record that is quietly
    false rather than obviously broken.
    """
    labels = week_labels or {}
    idx = {}
    for r in conn.execute(
            """SELECT g.game_id, g.week, g.kickoff, g.home_team, g.away_team,
                      g.home_score, g.away_score, g.neutral_site,
                      l.home_margin, l.total, l.home_ml, l.away_ml
               FROM games g LEFT JOIN lines l ON l.game_id=g.game_id
               WHERE g.sport=? AND g.season=? ORDER BY g.kickoff""", (sport, season)):
        g = dict(r)
        wk = labels.get(g["game_id"], g["week"])
        for t in (g["home_team"], g["away_team"]):
            idx.setdefault((wk, t), []).append(g)
    return idx


def _same_day(kickoff, date_text):
    """Does a kickoff fall on the date in the sheet? Tolerant of how people type."""
    if not kickoff or not date_text:
        return False
    ko = str(kickoff)[:10]
    s = str(date_text).strip()
    if s[:10] == ko:
        return True
    y, m, d = ko[:4], ko[5:7], ko[8:10]
    parts = re.split(r"[/\-.]", s)
    if len(parts) < 2:
        return False
    try:
        nums = [int(p) for p in parts[:3]]
    except ValueError:
        return False
    # 9/5, 9/5/25, 9/5/2025, 2025-09-05 — month and day are what disambiguate, and
    # the year is implied by the season anyway.
    if len(nums) >= 3 and nums[0] > 31:
        return "%04d%02d%02d" % (nums[0], nums[1], nums[2]) == y + m + d
    return nums[0] == int(m) and nums[1] == int(d)


def grade_bet(b, g):
    """
    -> (result, units_won, detail). result is 'W' | 'L' | 'P' | None (not final).

    Graded at the line and price in the bet, never at the closing number. That is
    the whole point of writing it down: what he got, not what was available.
    """
    if g["home_score"] is None or g["away_score"] is None:
        return None, None, "not final"
    margin = g["home_score"] - g["away_score"]          # home perspective
    total = g["home_score"] + g["away_score"]
    win_units = metrics_units(b["odds"])

    if b["market"] == "moneyline":
        if margin == 0:
            return "P", 0.0, "tie"
        won = (margin > 0) if b["team"] == g["home_team"] else (margin < 0)
        return ("W", b["units"] * win_units, "") if won else ("L", -b["units"], "")

    if b["market"] == "spread":
        # `line` is from the BET's side: +3.5 means taking 3.5 points.
        own = margin if b["team"] == g["home_team"] else -margin
        edge = own + b["line"]
        if abs(edge) < 1e-9:
            return "P", 0.0, "push"
        return (("W", b["units"] * win_units, "") if edge > 0
                else ("L", -b["units"], ""))

    diff = total - b["line"]
    if abs(diff) < 1e-9:
        return "P", 0.0, "push"
    won = (diff > 0) if b["side"] == "over" else (diff < 0)
    return ("W", b["units"] * win_units, "") if won else ("L", -b["units"], "")


def metrics_units(odds):
    """Profit on a 1-unit win at American odds."""
    a = float(odds)
    return (100.0 / -a) if a < 0 else (a / 100.0)


def closing_clv(b, g):
    """Points of closing line value, in the bet's own direction."""
    if b["market"] == "spread":
        close = g["home_margin"]
        if close is None or b["line"] is None:
            return None
        # `own_close` is the number the CLOSING line would have given this side,
        # in the same sign convention the bet is written in: laying is negative,
        # receiving is positive. CLV is then simply how much better his number was.
        #   home laid 7, closed -10  ->  -7 - (-10) = +3   (beat the close by 3)
        #   away took +12, closed +10 -> +12 - (+10) = +2
        own_close = -close if b["team"] == g["home_team"] else close
        return round(b["line"] - own_close, 2)
    if b["market"] == "total":
        if g["total"] is None or b["line"] is None:
            return None
        d = g["total"] - b["line"]
        return round(d if b["side"] == "over" else -d, 2)
    return None


def build(conn, sport, season, rows, week_labels=None, unit_size=None):
    """Parse, match, grade and total. Everything the app needs about his bets."""
    bets, problems = parse_rows(rows)
    idx = _index_games(conn, sport, season, week_labels)

    out = []
    for b in bets:
        cands = idx.get((b["week"], b["team"]))
        if not cands:
            problems.append("row %d: no week-%s game found for %r"
                            % (b["row"], _week_name(b["week"]), b["team_raw"]))
            continue
        if len(cands) == 1:
            g = cands[0]
        else:
            # A playoff team plays two or three games inside the bowls bucket, so
            # week+team genuinely does not identify one. Date does. Refuse rather
            # than guess when it is missing or matches nothing: a bet graded against
            # the wrong game still produces a W or an L, and a wrong result that
            # looks right is worse than a row the log tells you to fix.
            byday = [g for g in cands if _same_day(g["kickoff"], b["date"])]
            if len(byday) == 1:
                g = byday[0]
            else:
                problems.append(
                    "row %d: %r plays %d games in %s — %s"
                    % (b["row"], b["team_raw"], len(cands), _week_name(b["week"]),
                       "put the kickoff date in the Date column to say which"
                       if not byday else
                       "the Date given matches %d of them" % len(byday)))
                continue
        result, won, detail = grade_bet(b, g)
        rec = dict(b)
        rec.update({
            "game_id": g["game_id"], "home": g["home_team"], "away": g["away_team"],
            "kickoff": g["kickoff"],
            "home_score": g["home_score"], "away_score": g["away_score"],
            "result": result, "units_won": None if won is None else round(won, 3),
            "detail": detail, "clv": closing_clv(b, g),
            "closing_line": g["home_margin"] if b["market"] == "spread" else (
                g["total"] if b["market"] == "total" else None),
        })
        out.append(rec)

    out.sort(key=lambda r: (r["kickoff"] or "", r["row"]))
    return {"bets": out, "problems": problems, "totals": totals(out, unit_size)}


def totals(bets, unit_size=None):
    """
    Units risked, units won, ROI, and a bankroll curve.

    ROI is per unit RISKED, not per bet, because a 3-unit loss and a 0.5-unit loss
    are not the same event. That is the number that survives varying bet sizes,
    which is the entire reason for betting in units.
    """
    settled = [b for b in bets if b["result"] in ("W", "L", "P")]
    w = sum(1 for b in settled if b["result"] == "W")
    l = sum(1 for b in settled if b["result"] == "L")
    p = sum(1 for b in settled if b["result"] == "P")
    risked = sum(b["units"] for b in settled if b["result"] != "P")
    won = sum(b["units_won"] or 0.0 for b in settled)

    curve, run = [], 0.0
    for b in settled:
        run += b["units_won"] or 0.0
        curve.append({"kickoff": b["kickoff"], "units": round(run, 3)})

    out = {
        "n": len(bets), "settled": len(settled), "open": len(bets) - len(settled),
        "w": w, "l": l, "push": p,
        "units_risked": round(risked, 2),
        "units_won": round(won, 2),
        "roi": round(100.0 * won / risked, 2) if risked else None,
        "curve": curve,
        "unit_size": unit_size,
        "dollars_won": round(won * unit_size, 2) if unit_size else None,
        "dollars_risked": round(risked * unit_size, 2) if unit_size else None,
    }
    if w + l:
        lo, hi = metrics.wilson_ci(w, w + l)
        out["pct"] = round(100.0 * w / (w + l), 2)
        out["ci95"] = [round(100 * lo, 2), round(100 * hi, 2)]
        # Break-even depends on the prices HE took, not on a textbook -110. A book
        # of plus-money dogs needs a far lower hit rate to profit, and judging it
        # against 52.38% would call a profitable book a losing one.
        avg_win = (sum(metrics_units(b["odds"]) * b["units"] for b in settled
                       if b["result"] != "P")
                   / risked) if risked else metrics.WIN_UNITS
        out["break_even"] = round(100.0 / (1.0 + avg_win), 2)
        out["clears"] = bool(100 * lo > out["break_even"])
    clvs = [b["clv"] for b in bets if b["clv"] is not None]
    if clvs:
        out["clv_mean"] = round(sum(clvs) / len(clvs), 3)
        out["clv_beat"] = round(100.0 * sum(1 for c in clvs if c > 0) / len(clvs), 1)
        out["clv_n"] = len(clvs)

    by_market, by_week = {}, {}
    for b in settled:
        for bucket, key in ((by_market, b["market"]), (by_week, b["week"])):
            e = bucket.setdefault(key, {"w": 0, "l": 0, "push": 0,
                                        "units": 0.0, "risked": 0.0})
            e["w"] += b["result"] == "W"
            e["l"] += b["result"] == "L"
            e["push"] += b["result"] == "P"
            e["units"] += b["units_won"] or 0.0
            if b["result"] != "P":
                e["risked"] += b["units"]
    def fin(d):
        return [{"key": str(k), **{kk: (round(vv, 2) if isinstance(vv, float) else vv)
                                   for kk, vv in v.items()},
                 "roi": round(100.0 * v["units"] / v["risked"], 2) if v["risked"] else None}
                for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))]
    out["by_market"] = fin(by_market)
    out["by_week"] = fin(by_week)
    return out


# ── fetching ───────────────────────────────────────────────────────────────────
#
# Split deliberately from build(): this half touches the network, that half needs
# the database and the week labels. Keeping them apart means the grading logic is
# testable without a sheet and a run that cannot reach Google still publishes a
# site -- it just says the bet log is stale instead of failing.

def fetch_rows(sheet_id):
    """Raw rows of the My Bets tab, or None if the workbook has no such tab."""
    import sync_grades
    _how, list_tabs, read_tab, all_titles = sync_grades._providers(sheet_id)
    list_tabs()                                   # populates the workbook cache
    title = next((t for t in all_titles() if t.strip().lower() == TAB), None)
    if not title:
        return None, all_titles()
    return read_tab(title), all_titles()


def main():
    import argparse
    import datetime as dt
    import json
    import os

    ap = argparse.ArgumentParser(description="Pull the My Bets tab into output/.")
    ap.add_argument("--sheet", default=os.environ.get("MODEL_GRADES_SHEET_ID"))
    ap.add_argument("--out", default="output/my_bets_raw.json")
    args = ap.parse_args()
    if not args.sheet:
        raise SystemExit("no --sheet and no MODEL_GRADES_SHEET_ID")

    rows, titles = fetch_rows(args.sheet)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    if rows is None:
        # Not an error. Most people never make the tab, and the site explains how.
        print("  no 'My Bets' tab in that workbook — nothing to log.")
        print("  tabs seen: %s" % ", ".join(titles))
        with open(out, "w") as fh:
            json.dump({"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "missing": True, "tabs_seen": titles}, fh)
        return

    with open(out, "w") as fh:
        json.dump({"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                   "missing": False, "rows": rows}, fh)
    print("  read 'My Bets': %d row(s) -> %s" % (len(rows), out))


if __name__ == "__main__":
    main()
