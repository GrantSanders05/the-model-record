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
    head = (["Locked", "Wk", "Matchup", "Model", "Line", "Pick", "Result", "Final"]
            if graded else ["Locked", "Wk", "Kickoff", "Matchup", "Model", "Line", "Pick"])
    out = ["<div class='scroll'><table><thead><tr>"]
    out += ["<th>%s</th>" % h for h in head]
    out.append("</tr></thead><tbody>")
    for r in rows:
        lock = (r["published_at"] or "")[:10]
        match = "%s @ %s" % (html.escape(r["away_team"] or ""), html.escape(r["home_team"] or ""))
        if graded:
            res = r["ats_result"] or "—"
            cls = {"W": "res-w", "L": "res-l", "P": "res-p"}.get(res, "")
            out.append(
                "<tr><td class='mono'>%s</td><td>%s</td><td>%s</td>"
                "<td class='mono'>%s</td><td class='mono'>%s</td><td>%s</td>"
                "<td class='%s'>%s</td><td class='mono'>%s</td></tr>"
                % (lock, r["week"], match,
                   _fmt(r["model_margin"], "%+.1f"), _fmt(r["closing_margin"], "%+.1f"),
                   html.escape(r["ats_pick"] or "—"), cls, res,
                   _fmt(r["actual_margin"], "%+.0f")))
        else:
            out.append(
                "<tr><td class='mono'>%s</td><td>%s</td><td class='mono'>%s</td><td>%s</td>"
                "<td class='mono'>%s</td><td class='mono'>%s</td><td>%s</td></tr>"
                % (lock, r["week"], (r["kickoff"] or "")[:10], match,
                   _fmt(r["model_margin"], "%+.1f"),
                   _fmt(r["market_margin_at_pick"], "%+.1f"),
                   html.escape(r["ats_pick"] or "—")))
    out.append("</tbody></table></div>")
    return "".join(out)


def render(conn, sport="cfb", backtest_summary=None):
    rec = ledger.record(conn, sport)
    pending = [dict(r) for r in conn.execute(
        "SELECT * FROM picks_log WHERE sport=? AND graded_at IS NULL "
        "ORDER BY kickoff LIMIT 60", (sport,))]
    graded = sorted(rec["rows"], key=lambda r: r["kickoff"] or "", reverse=True)[:150]
    now = dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y %H:%M UTC")

    if rec.get("ats_pct") is not None:
        lo, hi = rec["ats_ci95"]
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

    bt = ""
    if backtest_summary:
        b = backtest_summary
        bt = """
  <section>
    <h2>Backtest <span class="tag">simulation, not a live record</span></h2>
    <p class="note">A replay of the %s season using only information available before
      each game. It is shown separately from the live ledger above because a
      simulated result and a real one are not the same claim.</p>
    <div class="hero small">
      <div class="stat"><span class="k">Games</span><span class="v">%d</span></div>
      <div class="stat"><span class="k">ATS %%</span><span class="v">%.2f%%</span></div>
      <div class="stat"><span class="k">ROI</span><span class="v">%+.2f%%</span></div>
      <div class="stat"><span class="k">vs results-only baseline</span>
        <span class="v">%+.2f</span><span class="sub">points of ATS</span></div>
    </div>
  </section>""" % (b["season"], b["n"], b["ats_pct"], b["roi"], b["vs_baseline"])

    return TEMPLATE % {
        "brand": BRAND,
        "updated": now,
        "headline": headline,
        "chart": equity_svg(rec["curve"]),
        "backtest": bt,
        "pending_n": len(pending),
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
:root{
  --brand:%(brand)s;
  --surface:#fcfcfb; --panel:#ffffff; --line:#e6e6e2;
  --ink:#16170f; --ink2:#4a4d40; --ink3:#7a7d70;
}
@media (prefers-color-scheme:dark){
  :root{ --surface:#0f100c; --panel:#1a1a19; --line:#2c2d27;
         --ink:#f2f3ec; --ink2:#b6b9ab; --ink3:#84887a; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);
  font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:960px;margin:0 auto;padding:32px 20px 64px}
header{border-bottom:2px solid var(--brand);padding-bottom:16px;margin-bottom:24px}
h1{margin:0;font-size:26px;letter-spacing:-.02em;font-weight:800}
h1 span{color:var(--brand)}
.upd{color:var(--ink3);font-size:12px;margin-top:4px}
h2{font-size:17px;margin:34px 0 8px;font-weight:700}
.tag{font-size:11px;font-weight:600;color:var(--ink2);border:1px solid var(--line);
  padding:2px 7px;margin-left:6px;vertical-align:middle;text-transform:uppercase;letter-spacing:.05em}
.hero{display:flex;flex-wrap:wrap;gap:1px;background:var(--line);border:1px solid var(--line);margin:16px 0}
.stat{flex:1 1 150px;background:var(--panel);padding:14px 16px;display:flex;flex-direction:column;gap:2px}
.stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink3)}
.stat .v{font-size:24px;font-weight:800;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums}
.hero.small .stat .v{font-size:19px}
.stat .sub{font-size:11px;color:var(--ink3);font-variant-numeric:tabular-nums}
.verdict{color:var(--ink2);font-size:13px;margin:10px 0 0;max-width:64ch}
.note{color:var(--ink3);font-size:12.5px;max-width:70ch;margin:6px 0 0}
.chartwrap{margin:20px 0 0;position:relative}
.chart{width:100%%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--line);stroke-width:1}
.zero{stroke:var(--ink3);stroke-width:1;stroke-dasharray:3 3}
.equity{fill:none;stroke:var(--brand);stroke-width:2;stroke-linecap:square;stroke-linejoin:miter}
.hot{fill:transparent;cursor:crosshair}
.cross{stroke:var(--ink3);stroke-width:1}
.axis{fill:var(--ink3);font-size:10px}
figcaption{color:var(--ink3);font-size:11.5px;margin-top:6px}
.tip{position:absolute;background:var(--panel);border:1px solid var(--line);
  padding:6px 9px;font-size:12px;pointer-events:none;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto;border:1px solid var(--line);margin-top:10px}
table{border-collapse:collapse;width:100%%;font-size:13px;background:var(--panel)}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--ink3);font-weight:600}
tbody tr:last-child td{border-bottom:0}
.mono{font-variant-numeric:tabular-nums;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.res-w,.res-l,.res-p{font-weight:800}
.res-l{color:var(--ink3)}
.empty{color:var(--ink3);font-size:13px;border:1px dashed var(--line);padding:16px;margin-top:10px}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);
  color:var(--ink3);font-size:12px;max-width:72ch}
</style></head><body>
<div class="wrap">
<header>
  <h1>The <span>Model</span> — Track Record</h1>
  <div class="upd">Generated automatically · %(updated)s</div>
</header>

<section>
  <h2>Live record <span class="tag">locked before kickoff</span></h2>
  %(headline)s
  %(chart)s
</section>

%(backtest)s

<section>
  <h2>Pending picks <span class="tag">%(pending_n)d locked</span></h2>
  <p class="note">Recorded before kickoff. Once locked, a pick is never revised —
    the ledger is append-only and grading only fills in the result.</p>
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


def _backtest_summary():
    """Read the 2025 grade-model replay if it has been recorded."""
    p = os.path.join(ROOT, "output", "cfb_backtest_2025.json")
    if os.path.exists(p):
        return json.load(open(p))
    return None


if __name__ == "__main__":
    main()
