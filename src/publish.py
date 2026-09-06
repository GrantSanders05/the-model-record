"""
publish.py — render the public track record page.

The page's whole value is that it is generated, not written. Every number comes
from picks_log, which is append-only and locked before kickoff, so nothing here
can be curated after the fact.

Two sections, deliberately never merged:

  LIVE LEDGER   picks locked before kickoff and graded against the closing line.
                This is the real record. At season start it is empty, and the
                page says so rather than borrowing the backtest's numbers.

  BACKTEST      the 2025 replay. Clearly labelled as a backtest, because a
                simulated record and a live one are not the same claim and the
                whole point of this page is not blurring them.

Design notes: single-series equity curve in #00922E, which passed the palette
validator's lightness, chroma and contrast checks against BOTH the light and
dark surfaces, so no per-mode colour swap is needed. Win/loss is encoded as the
letters W and L in text tokens, never by colour -- a green/orange pair failed
deutan separation at deltaE 3.1, and the accessible fix is to stop encoding
outcome by hue at all. Square line caps and no border radius follow the brand's
sharp-angle rule.
"""

import datetime as dt
import html
import json
import os

import ledger
import metrics
import tracking

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND = "#00922E"


def _fmt(v, spec="%.1f", dash="—"):
    return dash if v is None else spec % v


def equity_svg(curve, width=880, height=260, pad=36):
    """Single-series cumulative-units line with a break-even reference at zero."""
    if len(curve) < 2:
        return ('<p class="empty">No graded picks yet — the curve appears once '
                'the first locked picks have been played.</p>')
    ys = [p["units"] for p in curve]
    lo, hi = min(ys + [0.0]), max(ys + [0.0])
    span = (hi - lo) or 1.0
    lo -= span * 0.12
    hi += span * 0.12
    span = hi - lo
    n = len(curve)

    def X(i):
        return pad + (width - 2 * pad) * (i / (n - 1))

    def Y(v):
        return pad + (height - 2 * pad) * (1 - (v - lo) / span)

    pts = " ".join("%.1f,%.1f" % (X(i), Y(v)) for i, v in enumerate(ys))
    zero_y = Y(0.0)
    # Gridlines stay recessive; only zero is emphasised because it is the only
    # value that means anything here (break-even).
    grid = "".join(
        '<line class="grid" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
        % (pad, Y(lo + span * f), width - pad, Y(lo + span * f))
        for f in (0.25, 0.5, 0.75))
    hot = "".join(
        '<circle class="hot" cx="%.1f" cy="%.1f" r="10" data-i="%d"/>' % (X(i), Y(v), i)
        for i, v in enumerate(ys))
    labels = ('<text class="axis" x="%d" y="%.1f" dy="-6">break-even (0u)</text>'
              % (pad + 4, zero_y))
    return """
<figure class="chartwrap">
  <svg viewBox="0 0 %d %d" class="chart" role="img"
       aria-label="Cumulative units won or lost, in kickoff order. Ends at %+.2f units.">
    %s
    <line class="zero" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>
    %s
    <polyline class="equity" points="%s"/>
    <g class="hots">%s</g>
    <line class="cross" x1="0" y1="%d" x2="0" y2="%d" style="display:none"/>
  </svg>
  <div class="tip" hidden></div>
  <figcaption>Cumulative units at −110. Each graded pick moves the line;
    a win is +0.91u, a loss is −1u.</figcaption>
</figure>""" % (width, height, ys[-1], grid, pad, zero_y, width - pad, zero_y,
                labels, pts, hot, pad, height - pad)


def picks_table(rows, graded):
    if not rows:
        return ('<p class="empty">%s</p>'
                % ("No graded picks yet." if graded else
                   "No locked picks pending — check back when the schedule posts."))
    # Numeric columns are right-aligned and tabular so a column of figures reads
    # as a column rather than as ragged text. `num` marks which ones those are.
    head = ([("Locked", ""), ("Wk", "num"), ("Matchup", ""), ("Model", "num"),
             ("Line", "num"), ("Pick", ""), ("Result", ""), ("Final", "num")]
            if graded else
            [("Locked", ""), ("Wk", "num"), ("Kickoff", ""), ("Matchup", ""),
             ("Model", "num"), ("Line", "num"), ("Pick", "")])
    out = ["<div class='panel scroll'><table><thead><tr>"]
    out += ["<th class='%s'>%s</th>" % (c, h) for h, c in head]
    out.append("</tr></thead><tbody>")
    for r in rows:
        lock = (r["published_at"] or "")[:10]
        match = "%s @ <b>%s</b>" % (html.escape(r["away_team"] or ""),
                                    html.escape(r["home_team"] or ""))
        if graded:
            res = r["ats_result"] or "—"
            cls = {"W": "res-w", "L": "res-l", "P": "res-p"}.get(res, "")
            out.append(
                "<tr><td class='mono'>%s</td><td class='num'>%s</td><td>%s</td>"
                "<td class='num mono'>%s</td><td class='num mono'>%s</td><td>%s</td>"
                "<td><span class='%s'>%s</span></td><td class='num mono'>%s</td></tr>"
                % (lock, r["week"], match,
                   _fmt(r["model_margin"], "%+.1f"), _fmt(r["closing_margin"], "%+.1f"),
                   html.escape(r["ats_pick"] or "—"), cls, res,
                   _fmt(r["actual_margin"], "%+.0f")))
        else:
            out.append(
                "<tr><td class='mono'>%s</td><td class='num'>%s</td>"
                "<td class='mono'>%s</td><td>%s</td>"
                "<td class='num mono'>%s</td><td class='num mono'>%s</td><td>%s</td></tr>"
                % (lock, r["week"], (r["kickoff"] or "")[:10], match,
                   _fmt(r["model_margin"], "%+.1f"),
                   _fmt(r["market_margin_at_pick"], "%+.1f"),
                   html.escape(r["ats_pick"] or "—")))
    out.append("</tbody></table></div>")
    return "".join(out)


def render(conn, sport="cfb", backtest_summary=None):
    rec = ledger.record(conn, sport)
    # A locked row with no side is not a pick. The model records its margin for
    # every game it can rate, but until a book posts a number there is nothing to
    # disagree with — and those rows filled most of this table with em dashes,
    # burying the games the model actually has an opinion on. They are counted
    # rather than dropped silently, because "40 picks" and "40 picks, 26 of which
    # are blank" are different claims.
    pending_all = [dict(r) for r in conn.execute(
        "SELECT * FROM picks_log WHERE sport=? AND graded_at IS NULL "
        "AND voided_at IS NULL ORDER BY kickoff", (sport,))]
    pending = [r for r in pending_all if r.get("ats_pick")][:40]
    awaiting_line = len(pending_all) - len([r for r in pending_all if r.get("ats_pick")])
    graded = sorted(rec["rows"], key=lambda r: r["kickoff"] or "", reverse=True)[:150]
    _now = dt.datetime.now(dt.timezone.utc)
    now = _now.strftime("%d %b %Y %H:%M UTC")
    now_iso = _now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if rec.get("ats_pct") is not None:
        lo, hi = rec["ats_ci95"]
        # THE HEADLINE IS THE LOCKED-LINE RECORD. Until the V2 repair the number
        # here was graded at the CLOSING line with the side recomputed from the
        # model number, which answered a different question than the one the
        # sentence above it asked. `locked` is the published side at the number it
        # was published at; `close` is the same side scored against the close, and
        # it is a diagnostic rather than a wager anybody made.
        locked = _v2_locked_record(conn, "cfb")
        if locked and locked.get("locked_pct") is not None:
            llo, lhi = locked["locked_ci95"]
            roi_cell = ("%+.2f%%" % locked["roi"] if locked.get("roi") is not None
                        else "unavailable")
            roi_sub = ("" if locked.get("roi") is not None
                       else "no spread prices were recorded")
            headline = """
    <div class="hero">
      <div class="stat"><span class="k">ATS record</span>
        <span class="v">%d–%d–%d</span>
        <span class="sub">at the line each pick locked</span></div>
      <div class="stat"><span class="k">ATS %%</span>
        <span class="v">%.2f%%</span><span class="sub">95%% CI %.1f–%.1f</span></div>
      <div class="stat"><span class="k">ROI</span>
        <span class="v">%s</span><span class="sub">%s</span></div>
    </div>
    <p class="verdict">%s</p>
    <p class="note"><strong>How this is graded.</strong> A pick is scored at the
      number it was <em>locked</em> at, on the side that was published. Earlier
      versions of this page graded against the closing line and re-derived the side
      from the model number, which meant a pick could be recorded on a side nobody
      published: one Virginia pick, laying 3 and winning by 26, was recorded as a
      loss. Both figures are kept — the closing-line version is below, as a
      diagnostic — and the legacy values remain in the ledger for audit. ROI is
      shown only where the exact price was recorded; this feed supplies moneylines
      and not spread juice, so for spreads it is honestly unavailable rather than
      assumed at −110.</p>""" % (
                locked["locked_w"], locked["locked_l"], locked["locked_p"],
                locked["locked_pct"], llo, lhi, roi_cell, roi_sub,
                # Single %, not %% — these are ARGUMENTS to the format above,
                # not part of it, so an escaped percent stays escaped and renders
                # as "52.38%%" on the page.
                ("This record clears the 52.38% break-even with the entire "
                 "confidence interval above it." if (llo or 0) > 52.38 else
                 "The interval still includes the 52.38% break-even — not yet "
                 "enough graded picks to call this an edge either way."))
        else:
            headline = """
    <div class="hero">
      <div class="stat"><span class="k">ATS record</span>
        <span class="v">%d–%d–%d</span></div>
      <div class="stat"><span class="k">ATS %%</span>
        <span class="v">%.2f%%</span><span class="sub">95%% CI %.1f–%.1f</span></div>
      <div class="stat"><span class="k">Units</span>
        <span class="v">%+.2fu</span><span class="sub">ROI %+.2f%%</span></div>
    </div>
    <p class="verdict">%s</p>""" % (
                rec["ats_w"], rec["ats_l"], rec["ats_push"], rec["ats_pct"], lo, hi,
                rec["units"], rec["roi"],
                ("This record clears the 52.38% break-even with the entire confidence "
                 "interval above it." if rec.get("proven") else
                 "Above break-even, but the confidence interval still includes it — "
                 "not yet enough games to call this an edge."))
    else:
        headline = """
    <div class="hero">
      <div class="stat"><span class="k">Locked picks</span><span class="v">%d</span></div>
      <div class="stat"><span class="k">Graded</span><span class="v">0</span></div>
      <div class="stat"><span class="k">Units</span><span class="v">—</span></div>
    </div>
    <p class="verdict">The live record starts empty on purpose. Every pick below was
       locked before kickoff and will be graded automatically against the closing
       line. Nothing on this page is edited by hand.</p>""" % len(pending)

    # ── the closing-line diagnostic, kept apart from the record ──────────────
    diag = ""
    _d = _v2_close_diagnostic(conn, "cfb")
    if _d and _d.get("n"):
        diag = """
  <section>
    <h2>Closing line <span class="tag">diagnostic, not a wager record</span></h2>
    <p class="note">The same sides, scored against the number each game
      <em>closed</em> at instead of the number the pick was locked at. Nobody bet
      these prices; the question is whether the model was on the right side of
      where the market ended up, which is the leading indicator worth watching
      when the results themselves are still a small sample. It is shown apart from
      the record above because a diagnostic quoted as a return is the oldest way
      to make a model look better than it is.</p>
    <div class="hero small">
      <div class="stat"><span class="k">At the close</span>
        <span class="v">%d–%d–%d</span><span class="sub">%s</span></div>
      <div class="stat"><span class="k">Line value</span>
        <span class="v">%s</span><span class="sub">mean points gained per pick</span></div>
      <div class="stat"><span class="k">Beat the close</span>
        <span class="v">%s</span><span class="sub">of %d graded picks</span></div>
    </div>
  </section>""" % (
            _d["w"] or 0, _d["l"] or 0, _d["p"] or 0,
            ("%.2f%%" % _d["pct"]) if _d.get("pct") is not None else "—",
            ("%+.2f" % _d["clv_mean"]) if _d.get("clv_mean") is not None else "—",
            ("%.1f%%" % _d["clv_beat_pct"]) if _d.get("clv_beat_pct") is not None else "—",
            _d.get("clv_n") or 0)

    bt = ""
    if backtest_summary:
        b = backtest_summary
        bt = """
  <section>
    <h2>Backtest <span class="tag">simulation, not a live record</span></h2>
    <p class="note">A walk-forward replay of the %s season on Grant's film grades,
      using only information available before each game — a grade published in week
      seven can never price a week five game. It is shown separately from the live
      ledger above because a simulated result and a real one are not the same claim,
      and one season is not enough to settle anything: read the interval, not the
      headline.</p>
    <div class="hero small">
      <div class="stat"><span class="k">Games</span><span class="v">%d</span></div>
      <div class="stat"><span class="k">ATS %%</span><span class="v">%.2f%%</span>
        <span class="sub">%s</span></div>
      <div class="stat"><span class="k">ROI</span><span class="v">%+.2f%%</span>
        <span class="sub">break-even 52.38%%</span></div>
      <div class="stat"><span class="k">Picking winners</span>
        <span class="v">%+.2f</span>
        <span class="sub">points vs backing the favourite</span></div>
    </div>%s
    <p class="verdict">%s</p>
  </section>""" % (
        b["season"], b["n"], b["ats_pct"],
        ("95%% CI %.1f–%.1f%%" % (b["ci_lo"], b["ci_hi"])
         if b.get("ci_lo") is not None else "one season"),
        b["roi"], b["vs_baseline"],
        # BOTH RECORDS, BECAUSE EITHER ALONE IS A HALF-TRUTH. The figures above
        # bet every game with a line, including the ones the board marks `no bet`
        # and never stakes — a hundred and twenty a season, all of them blowouts
        # where the film grades are weakest. Publishing only those understates
        # what the model does; publishing only the narrower set, with no sign of
        # the wider one, would look like the losers had been quietly dropped. So
        # the headline stays the wide number and the narrow one sits under it,
        # with the rule that separates them stated.
        ("""
    <p class="note">Those figures bet <em>every</em> game with a line. The board
       does not: anything outside &plusmn;%.0f points is marked <em>no bet</em> and
       never staked, because a spread that wide is where the grades are least able
       to tell two teams apart. On the %d games it would actually have offered,
       the same replay is <strong>%.2f%%</strong> ATS for <strong>%+.2f%%</strong>
       ROI. Both are shown because either on its own tells you something the other
       corrects.</p>""" % (b["blowout_line"], b["offered_n"],
                           b["offered_ats_pct"], b["offered_roi"])
         if b.get("offered_ats_pct") is not None else ""),
        (("The whole interval sits above break-even, which is the test that matters."
          if b.get("ci_lo", 0) > 52.38 else
          "The interval still includes break-even. Above it is encouraging; proven "
          "needs more seasons, and this is one.")
         + " The model does not pick straight-up winners better than simply backing "
           "the favourite, and is not trying to — a favourite wins most of the time "
           "at a price that says so. The only question that pays is which side of "
           "the number the game lands on."
         + (" <strong>This figure was computed under an older configuration and has "
            "not been regenerated.</strong>" if b.get("stale") else "")))

    # Week by week, in public. A single season-long percentage is the number that
    # is easiest to keep quiet about a bad month inside; splitting it is the
    # cheapest credibility this page can buy, and it costs one table.
    weekly_rows = tracking.weekly([r for r in rec["rows"] if r.get("graded_at")])
    if weekly_rows:
        body = "".join(
            """<tr><td>%s</td><td class="num">%d–%d%s</td><td class="num">%s</td>
                   <td class="num">%s</td><td class="num">%s</td></tr>""" % (
                "Bowls" if str(w["key"]) == "99" else "Week %s" % w["key"],
                w["ats"]["w"], w["ats"]["l"],
                "–%d" % w["ats"]["push"] if w["ats"]["push"] else "",
                "%.1f%%" % w["ats"]["pct"] if w["ats"]["pct"] is not None else "—",
                "%+.2fu" % w["ats"]["units"] if w["ats"]["units"] is not None else "—",
                "%.1f%%" % w["cumulative"]["pct"] if w["cumulative"]["pct"] is not None else "—")
            for w in weekly_rows)
        weekly = """
  <section>
    <h2>Week by week</h2>
    <p class="note">Each week on its own, and the season to date beside it. A single
      season-long percentage can hide a bad month; this cannot.</p>
    <div class="panel scroll"><table><thead><tr><th>Week</th><th class="num">ATS</th>
      <th class="num">ATS %%</th><th class="num">Units</th>
      <th class="num">Season to date</th></tr></thead>
      <tbody>%s</tbody></table></div>
  </section>""" % body
    else:
        weekly = ""

    return TEMPLATE % {
        "brand": BRAND,
        "updated": now,
        "updated_iso": now_iso,
        "headline": headline,
        "chart": equity_svg(rec["curve"]),
        "weekly": weekly,
        "diagnostic": diag,
        "backtest": bt,
        "pending_n": len(pending),
        "awaiting": ("" if not awaiting_line else
                     "A further %d rated game%s no posted line yet, so the model "
                     "has nothing to disagree with and no side to take." % (
                         awaiting_line,
                         " has" if awaiting_line == 1 else "s have")),
        "pending": picks_table(pending, graded=False),
        "graded": picks_table(graded, graded=True),
        "curve_json": json.dumps([
            {"u": p["units"], "d": (p["kickoff"] or "")[:10]} for p in rec["curve"]]),
    }


TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Model — Track Record</title>
<meta name="description" content="Every pick, locked before kickoff, graded automatically against the closing line.">
<style>
/* Shares its design language with the research app: three surface levels
   separated by tone and a hairline, tabular numerals on every figure, and
   almost no colour. Outcome is encoded as the letters W and L in text tokens,
   never by hue -- a green/orange pair failed deutan separation at deltaE 3.1,
   and the accessible fix is to stop encoding outcome by colour at all. */
:root{
  --brand:%(brand)s; --brand-ink:#007a26; --brand-wash:#eaf6ee;
  --bg:#f6f7f4; --panel:#fff; --raised:#fbfbf9;
  --line:#e3e4de; --line-soft:#eeefe9;
  --ink:#14150f; --ink2:#4e5145; --ink3:#83867a;
  --r:10px; --sh:0 1px 2px rgba(20,21,15,.05), 0 4px 14px rgba(20,21,15,.04);
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{ --brand:#3ecf6a; --brand-ink:#7ee39c; --brand-wash:#12251a;
         --bg:#0d0e0b; --panel:#161713; --raised:#1c1d18;
         --line:#2b2c26; --line-soft:#232419;
         --ink:#f3f4ed; --ink2:#b4b7a9; --ink3:#7f8276;
         --sh:0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.28); }
}
*{box-sizing:border-box}

/* Preventive, not a fix -- nothing on this page is broken today. `[hidden]{display:none}`
   is a USER-AGENT rule, and origin outranks specificity in the cascade, so ANY normal
   author `display` declaration silently beats it. The only element here that carries
   the attribute is the chart tooltip, and `.tip` happens not to set `display`, so it
   works. That is luck, not design: the research page had the same shape and lost, where
   `#gate{display:grid}` kept the passphrase box on screen permanently. Adding one
   `display` declaration to `.tip` would do the same here, and it would look like a JS
   bug rather than a CSS one. */
[hidden]{display:none!important}

html{-webkit-text-size-adjust:100%%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.55 var(--sans);-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding:38px 20px 72px}
header{margin-bottom:26px}
.eyebrow{display:inline-flex;align-items:center;gap:7px;font-size:11px;font-weight:650;
  text-transform:uppercase;letter-spacing:.09em;color:var(--brand-ink);
  background:var(--brand-wash);border:1px solid color-mix(in srgb,var(--brand) 24%%,transparent);
  border-radius:100px;padding:4px 11px;margin-bottom:13px}
.eyebrow i{width:6px;height:6px;border-radius:50%%;background:var(--brand);display:block}
h1{margin:0;font-size:34px;line-height:1.1;letter-spacing:-.035em;font-weight:700}
h1 span{color:var(--brand)}
.upd{color:var(--ink3);font-size:12px;margin-top:9px;font-variant-numeric:tabular-nums}
.upd .ago{font-weight:600}
.upd.stale{color:#b4741f}
.upd.stale .ago::after{content:" — your latest sheet edits may not be here yet";font-weight:400}
h2{font-size:16px;margin:38px 0 4px;font-weight:640;letter-spacing:-.015em;
  display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.tag{font-size:10px;font-weight:650;color:var(--ink3);border:1px solid var(--line);
  background:var(--raised);border-radius:100px;padding:3px 9px;
  text-transform:uppercase;letter-spacing:.06em}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:10px;margin:18px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  box-shadow:var(--sh);padding:15px 16px;transition:border-color .15s}
.stat:hover{border-color:var(--ink3)}
/* These are <span>s, and margin-bottom does nothing to an inline box — so the
   label and the number sat on one line reading "LOCKED PICKS40" and "ROI+4.58%%"
   across the most prominent element on the page. They have to be blocks. */
.stat .k,.stat .v,.stat .sub{display:block}
.stat .k{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--ink3);
  font-weight:600;margin-bottom:6px}
.stat .v{font-size:27px;font-weight:660;letter-spacing:-.03em;line-height:1.05;
  font-variant-numeric:tabular-nums}
.hero.small .stat .v{font-size:21px}
.stat .sub{font-size:11px;color:var(--ink3);font-variant-numeric:tabular-nums;margin-top:4px}
.verdict{color:var(--ink2);font-size:13px;margin:12px 0 0;max-width:70ch;line-height:1.6}
.note{color:var(--ink3);font-size:12.5px;max-width:76ch;margin:6px 0 0;line-height:1.6}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  box-shadow:var(--sh);overflow:hidden}
.chartwrap{margin:18px 0 0;position:relative;background:var(--panel);
  border:1px solid var(--line);border-radius:var(--r);box-shadow:var(--sh);padding:14px}
.chart{width:100%%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--line);stroke-width:1}
.zero{stroke:var(--ink3);stroke-width:1;stroke-dasharray:3 3}
.equity{fill:none;stroke:var(--brand);stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.hot{fill:transparent;cursor:crosshair}
.cross{stroke:var(--ink3);stroke-width:1}
.axis{fill:var(--ink3);font-size:10px;font-variant-numeric:tabular-nums}
figcaption{color:var(--ink3);font-size:11.5px;margin-top:8px}
.tip{position:absolute;background:var(--panel);border:1px solid var(--line);border-radius:7px;
  padding:7px 10px;font-size:12px;pointer-events:none;font-variant-numeric:tabular-nums;
  box-shadow:var(--sh);z-index:5}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:12px}
table{border-collapse:separate;border-spacing:0;width:100%%;font-size:13px}
thead th{position:sticky;top:0;background:var(--raised);text-align:left;padding:9px 11px;
  font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink3);
  font-weight:650;border-bottom:1px solid var(--line);white-space:nowrap}
tbody td{padding:8px 11px;border-bottom:1px solid var(--line-soft);white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr{transition:background .12s}
tbody tr:hover{background:var(--raised)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-variant-numeric:tabular-nums;
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.res-w,.res-l,.res-p{font-weight:700;display:inline-grid;place-items:center;
  width:20px;height:20px;border-radius:5px;font-size:11px}
.res-w{background:var(--brand-wash);color:var(--brand-ink);
  border:1px solid color-mix(in srgb,var(--brand) 28%%,transparent)}
.res-l{background:var(--raised);color:var(--ink3);border:1px solid var(--line)}
.res-p{background:var(--raised);color:var(--ink2);border:1px solid var(--line)}
.empty{color:var(--ink3);font-size:13px;border:1px dashed var(--line);border-radius:var(--r);
  padding:24px;margin-top:12px;text-align:center;background:var(--panel)}
footer{margin-top:52px;padding-top:18px;border-top:1px solid var(--line);
  color:var(--ink3);font-size:12px;max-width:78ch;line-height:1.65}
footer strong{color:var(--ink2)}
@media (max-width:640px){
  .wrap{padding:26px 14px 56px} h1{font-size:26px} .stat .v{font-size:23px}
}
</style></head><body>
<div class="wrap">
<header>
  <div class="eyebrow"><i></i> Live &amp; automated</div>
  <h1>The <span>Model</span> — Track Record</h1>
  <div class="upd" id="upd" data-built="%(updated_iso)s">Generated automatically · %(updated)s</div>
</header>

<section>
  <h2>Live record <span class="tag">locked before kickoff</span></h2>
  %(headline)s
  %(chart)s
</section>

%(weekly)s
%(diagnostic)s
%(backtest)s

<section>
  <h2>Pending picks <span class="tag">%(pending_n)d locked</span></h2>
  <p class="note">Recorded before kickoff. Once locked, a pick is never revised —
    the ledger is append-only and grading only fills in the result.
    %(awaiting)s</p>
  %(pending)s
</section>

<section>
  <h2>Graded picks</h2>
  <p class="note">Graded against the closing line, not the line the pick was made at.
    Both numbers are stored so closing-line value stays measurable.</p>
  %(graded)s
</section>

<footer>
  <strong>How to read this.</strong> Margins are home-relative: +7 means the home team
  by 7. A bet is graded a win when the model and the final result land on the same
  side of the closing number. Break-even at −110 juice is 52.38%% — anything below
  that loses money regardless of how it looks. Confidence intervals are Wilson
  score; until the whole interval sits above 52.38%%, a record above break-even is
  encouraging rather than proven.
</footer>
</div>
<script>
(function(){
  var curve = %(curve_json)s;
  var svg = document.querySelector('.chart'); if(!svg||!curve.length) return;
  var tip = document.querySelector('.tip'), cross = svg.querySelector('.cross');
  svg.querySelectorAll('.hot').forEach(function(c){
    function show(){
      var i = +c.dataset.i, p = curve[i]; if(!p) return;
      var r = c.getBoundingClientRect(), w = svg.getBoundingClientRect();
      tip.hidden = false;
      tip.textContent = p.d + '  ·  ' + (p.u>=0?'+':'') + p.u.toFixed(2) + 'u';
      tip.style.left = Math.min(Math.max(r.left-w.left-40,0), w.width-140) + 'px';
      tip.style.top  = (r.top-w.top-38) + 'px';
      cross.style.display=''; cross.setAttribute('x1',c.getAttribute('cx'));
      cross.setAttribute('x2',c.getAttribute('cx'));
    }
    c.addEventListener('mouseenter',show); c.addEventListener('focus',show);
    c.addEventListener('mouseleave',function(){tip.hidden=true;cross.style.display='none';});
  });
})();
</script>
<script>
/* How stale is this page?

   The build stamp is UTC, which is not a question anyone can answer at a glance
   from Central time. The site is republished by a GitHub Actions cron; GitHub
   throttles free scheduled runs hard, so the real gap between a sheet edit and
   it appearing here ran a median of 53 minutes over the last week -- but one
   gap in five exceeded three hours. That is exactly the situation where the
   page has to say so itself rather than look authoritative and be six hours
   old. Rendered in the browser so it stays true however long the tab is open.

   No dependency on the page having been freshly served: a CDN copy up to ten
   minutes old still reports its own true age, because the age is measured from
   the embedded build time, not from load. */
(function(){
  var el = document.getElementById("upd");
  if (!el) return;
  var built = new Date(el.dataset.built);
  if (isNaN(built)) return;
  var STALE_MIN = 180;                      /* 3h: the p90 of observed gaps */
  function human(m){
    if (m < 1)  return "just now";
    if (m < 60) return m + (m === 1 ? " minute ago" : " minutes ago");
    var h = Math.floor(m / 60);
    if (h < 24) return h + (h === 1 ? " hour ago" : " hours ago");
    var d = Math.floor(h / 24);
    return d + (d === 1 ? " day ago" : " days ago");
  }
  function tick(){
    var mins = Math.floor((Date.now() - built.getTime()) / 60000);
    if (mins < 0) mins = 0;
    var local = built.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit"
    });
    el.innerHTML = 'Generated automatically · <span class="ago">' +
      human(mins) + '</span> · ' + local;
    el.classList.toggle("stale", mins >= STALE_MIN);
    el.title = "Built " + built.toISOString().replace("T"," ").slice(0,16) +
               " UTC. The site republishes on a schedule, so a very recent " +
               "sheet edit can take up to a few hours to appear.";
  }
  tick();
  setInterval(tick, 60000);
})();
</script>
</body></html>"""


def main():
    import db
    conn = db.connect()
    out_dir = os.path.join(ROOT, "output", "site")
    os.makedirs(out_dir, exist_ok=True)
    html_doc = render(conn, "cfb", backtest_summary=_backtest_summary())
    path = os.path.join(out_dir, "index.html")
    with open(path, "w") as fh:
        fh.write(html_doc)
    print("track record page -> %s" % path)


def _v2_locked_record(conn, sport="cfb"):
    """
    The locked-line record across every strategy that has produced signals.

    Combined DELIBERATELY here and nowhere else: this is the public "what has
    this project's published picks done" number, and splitting it by strategy
    version on the headline would ask a reader to add up buckets. The per-strategy
    split is available in the research bundle, where it belongs.
    """
    try:
        import metrics_v2
        import signals as _sig
    except Exception:                              # noqa: BLE001 - never block a page
        return None
    versions = [r["strategy_version"] for r in conn.execute(
        "SELECT DISTINCT strategy_version FROM signal_log WHERE is_official=1")]
    if not versions:
        return None
    total = {"locked_w": 0, "locked_l": 0, "locked_p": 0, "n": 0,
             "priced_n": 0, "unpriced_n": 0, "profit": 0.0, "all_priced": True}
    for v in versions:
        r = metrics_v2.signal_performance(conn, strategy_version=v, sport=sport)
        for k in ("locked_w", "locked_l", "locked_p", "n", "priced_n", "unpriced_n"):
            total[k] += r.get(k) or 0
        if r.get("roi") is None and r.get("n"):
            total["all_priced"] = False
    d = total["locked_w"] + total["locked_l"]
    total["locked_pct"] = round(100.0 * total["locked_w"] / d, 2) if d else None
    total["locked_ci95"] = metrics_v2._wilson(total["locked_w"], d)
    total["roi"] = None if not total["all_priced"] else 0.0
    return total


def _v2_close_diagnostic(conn, sport="cfb"):
    """The closing-line view, pooled across strategies. See _v2_locked_record."""
    try:
        import metrics_v2
    except Exception:                              # noqa: BLE001
        return None
    versions = [r["strategy_version"] for r in conn.execute(
        "SELECT DISTINCT strategy_version FROM signal_log WHERE is_official=1")]
    tot = {"w": 0, "l": 0, "p": 0, "n": 0, "clv_n": 0, "_clv_sum": 0.0, "_beat": 0}
    for v in versions:
        d = metrics_v2.closing_diagnostic(conn, strategy_version=v, sport=sport)
        for a, b in (("w", "w"), ("l", "l"), ("p", "p"), ("n", "n")):
            tot[a] += d.get(b) or 0
        if d.get("clv_n"):
            tot["clv_n"] += d["clv_n"]
            tot["_clv_sum"] += (d["clv_mean"] or 0) * d["clv_n"]
            tot["_beat"] += (d["clv_beat_pct"] or 0) * d["clv_n"] / 100.0
    d = tot["w"] + tot["l"]
    tot["pct"] = round(100.0 * tot["w"] / d, 2) if d else None
    tot["clv_mean"] = round(tot["_clv_sum"] / tot["clv_n"], 3) if tot["clv_n"] else None
    tot["clv_beat_pct"] = (round(100.0 * tot["_beat"] / tot["clv_n"], 1)
                           if tot["clv_n"] else None)
    return tot


def _backtest_summary(sport="cfb"):
    """
    The most recent validation replay run_update wrote, if there is one.

    Globbed rather than hard-coded to a season: the old version named
    cfb_backtest_2025.json, nothing in CI wrote it, and the public page therefore
    dropped the whole backtest section without a word for anyone who was not
    looking at a laptop that happened to have the file.
    """
    import glob
    found = sorted(glob.glob(os.path.join(ROOT, "output", "%s_backtest_*.json" % sport)))
    if not found:
        # CI restores a database with only the current season's grades, so it cannot
        # recompute this. The committed summary in data/validation/ is six numbers —
        # no grades, nothing that puts the moat in a public repo — and it carries a
        # fingerprint of the config it was computed under so a stale one announces
        # itself rather than quietly misrepresenting the current model.
        found = sorted(glob.glob(os.path.join(ROOT, "data", "validation",
                                              "%s_*.json" % sport)))
    if not found:
        return None
    try:
        b = json.load(open(found[-1]))
    except ValueError:
        return None
    cfg_path = os.path.join(ROOT, "config", "%s_grades.json" % sport)
    if b.get("config_fingerprint") and os.path.exists(cfg_path):
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.join(ROOT, "tools"))
            import write_validation as _wv
            b["stale"] = (_wv.fingerprint(json.load(open(cfg_path)))
                          != b["config_fingerprint"])
        except Exception:                          # noqa: BLE001 - never block a page
            b["stale"] = None
    return b


if __name__ == "__main__":
    main()
