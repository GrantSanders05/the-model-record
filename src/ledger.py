"""
ledger.py — lock picks before kickoff, grade them after, never revise them.

The published track record is only worth something if it cannot be edited after
the fact. Two functions enforce that:

    lock()   writes a pick for any game that has NOT started yet.
             Uses INSERT OR IGNORE, so re-running never overwrites an existing
             pick. The first number recorded is the permanent one.

    grade()  fills in the result columns once a game is final, and refuses to
             touch model_margin, market_margin_at_pick or ats_pick.

Anything that has already kicked off is skipped by lock() entirely. That is the
rule that makes the record honest: a pick that appears after the game started
is not a prediction.
"""

import datetime as dt

import metrics


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _parse_kickoff(s):
    if not s:
        return None
    txt = str(s).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(txt)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M"):
            try:
                d = dt.datetime.strptime(txt[:len(fmt) + 2], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def lock(conn, sport, picks, config_label, now=None):
    """
    Record picks for games that haven't kicked off. Returns (locked, skipped_started).

    `picks` are rows from predict.generate().
    """
    now = now or _now()
    rows, started = [], 0
    for p in picks:
        ko = _parse_kickoff(p.get("kickoff"))
        if ko is not None and ko <= now:
            started += 1
            continue
        rows.append({
            "game_id": p.get("game_id"),
            "sport": sport,
            "season": p.get("season"),
            "week": p.get("week"),
            "home_team": p.get("home"),
            "away_team": p.get("away"),
            "kickoff": p.get("kickoff"),
            "published_at": now.isoformat(),
            "config_label": config_label,
            "model_margin": p.get("model_margin"),
            "market_margin_at_pick": p.get("market_margin"),
            "model_total": p.get("model_total"),
            "market_total_at_pick": p.get("market_total"),
            "ats_pick": p.get("ats_pick"),
            "ml_pick": p.get("ml_pick"),
            "ou_pick": p.get("ou_pick"),
        })
    if rows:
        # OR IGNORE, not ON CONFLICT UPDATE: an existing pick is final.
        conn.executemany(
            """INSERT OR IGNORE INTO picks_log
               (game_id, sport, season, week, home_team, away_team, kickoff,
                published_at, config_label, model_margin, market_margin_at_pick,
                model_total, market_total_at_pick, ats_pick, ml_pick, ou_pick)
               VALUES (:game_id, :sport, :season, :week, :home_team, :away_team,
                       :kickoff, :published_at, :config_label, :model_margin,
                       :market_margin_at_pick, :model_total, :market_total_at_pick,
                       :ats_pick, :ml_pick, :ou_pick)""", rows)
        conn.commit()
    inserted = conn.execute(
        "SELECT COUNT(*) c FROM picks_log WHERE sport=? AND published_at=?",
        (sport, now.isoformat())).fetchone()["c"]
    return inserted, started


def grade(conn, sport, now=None):
    """Grade every locked pick whose game is now final. Returns how many were graded."""
    now = now or _now()
    rows = conn.execute(
        """SELECT p.game_id, p.model_margin, p.market_margin_at_pick,
                  p.model_total, p.market_total_at_pick,
                  g.home_score, g.away_score,
                  l.home_margin AS closing_margin, l.total AS closing_total
           FROM picks_log p
           JOIN games g ON g.game_id = p.game_id
           LEFT JOIN lines l ON l.game_id = p.game_id
           WHERE p.sport = ? AND p.graded_at IS NULL
             AND g.home_score IS NOT NULL""", (sport,)).fetchall()

    updates = []
    for r in rows:
        actual_margin = r["home_score"] - r["away_score"]
        actual_total = r["home_score"] + r["away_score"]
        # Grade against the closing line where we have it, otherwise the number
        # the pick was actually made at. Never against a line chosen later.
        close_m = r["closing_margin"] if r["closing_margin"] is not None else r["market_margin_at_pick"]
        close_t = r["closing_total"] if r["closing_total"] is not None else r["market_total_at_pick"]

        ats = None
        if close_m is not None and r["model_margin"] is not None:
            edge = r["model_margin"] - close_m
            diff = actual_margin - close_m
            if edge == 0:
                ats = None
            elif diff == 0:
                ats = "P"
            else:
                ats = "W" if (edge > 0) == (diff > 0) else "L"

        ou = None
        if close_t is not None and r["model_total"] is not None:
            edge = r["model_total"] - close_t
            diff = actual_total - close_t
            if edge == 0:
                ou = None
            elif diff == 0:
                ou = "P"
            else:
                ou = "W" if (edge > 0) == (diff > 0) else "L"

        updates.append({
            "game_id": r["game_id"], "closing_margin": close_m, "closing_total": close_t,
            "actual_margin": actual_margin, "actual_total": actual_total,
            "ats_result": ats, "ou_result": ou, "graded_at": now.isoformat(),
        })

    if updates:
        # Only result columns are written. The pick itself is untouched.
        conn.executemany(
            """UPDATE picks_log SET
                 closing_margin=:closing_margin, closing_total=:closing_total,
                 actual_margin=:actual_margin, actual_total=:actual_total,
                 ats_result=:ats_result, ou_result=:ou_result, graded_at=:graded_at
               WHERE game_id=:game_id""", updates)
        conn.commit()
    return len(updates)


def record(conn, sport, season=None):
    """Summarize the graded ledger — the numbers the public page shows."""
    q = "SELECT * FROM picks_log WHERE sport=? AND graded_at IS NOT NULL"
    args = [sport]
    if season:
        q += " AND season=?"
        args.append(season)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY kickoff", args)]

    w = sum(1 for r in rows if r["ats_result"] == "W")
    l = sum(1 for r in rows if r["ats_result"] == "L")
    p = sum(1 for r in rows if r["ats_result"] == "P")
    ow = sum(1 for r in rows if r["ou_result"] == "W")
    ol = sum(1 for r in rows if r["ou_result"] == "L")

    out = {"n": len(rows), "ats_w": w, "ats_l": l, "ats_push": p,
           "ou_w": ow, "ou_l": ol, "rows": rows}
    if w + l:
        out["ats_pct"] = 100.0 * w / (w + l)
        lo, hi = metrics.wilson_ci(w, w + l)
        out["ats_ci95"] = (100 * lo, 100 * hi)
        out["roi"] = 100.0 * (w * metrics.WIN_UNITS - l) / (w + l)
        out["proven"] = 100 * lo > 100 * metrics.BREAK_EVEN
    if ow + ol:
        out["ou_pct"] = 100.0 * ow / (ow + ol)
        out["ou_roi"] = 100.0 * (ow * metrics.WIN_UNITS - ol) / (ow + ol)

    # Cumulative units, in kickoff order — the shape of the public chart.
    curve, units = [], 0.0
    for r in rows:
        if r["ats_result"] == "W":
            units += metrics.WIN_UNITS
        elif r["ats_result"] == "L":
            units -= 1.0
        curve.append({"kickoff": r["kickoff"], "units": round(units, 3)})
    out["curve"] = curve
    out["units"] = round(units, 2)
    return out
