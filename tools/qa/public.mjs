/**
 * qa_public.mjs — the public track-record page, in a real DOM.
 *
 * Two things get checked that reading the HTML cannot settle: the equity chart's
 * hover script actually binds and fires, and the page tells the truth about an
 * empty ledger instead of borrowing the backtest's numbers.
 */
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "node:fs";

const path = process.env.QA_PAGE ||
  new URL("../../output/site/index.html", import.meta.url).pathname;
const html = fs.readFileSync(path, "utf8");
// The committed summary the page renders from, so the gate compares the printed
// figures against their source rather than against numbers typed in here.
const VALIDATION = new URL("../../data/validation/cfb_2025.json",
                           import.meta.url).pathname;

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push(e.message));

const dom = new JSDOM(html, { runScripts: "dangerously", virtualConsole: vc,
  url: "https://example.test/" });
const { window } = dom;
const $ = s => window.document.querySelector(s);
const $$ = s => [...window.document.querySelectorAll(s)];
await new Promise(r => setTimeout(r, 250));

let pass = 0, fail = 0;
const ok = (n, c, d = "") => { if (c) { pass++; console.log(`  [PASS] ${n}`); }
  else { fail++; console.log(`  [FAIL] ${n}${d ? " — " + d : ""}`); } };

console.log("── structure ──");
ok("title is set", window.document.title.includes("Track Record"));
ok("h1 renders", ($("h1")?.textContent || "").includes("The Model"));
ok("a no-JS reader still gets an absolute stamp",
   /<div class="upd"[^>]*>Generated automatically · \d{2} \w{3} \d{4}/.test(html));
ok("a JS reader gets a relative one", /ago|just now/.test($(".upd")?.textContent || ""),
   $(".upd")?.textContent);
ok("hero stats render", $$(".hero .stat").length >= 3, String($$(".hero .stat").length));
ok("every stat has a label and a value",
   $$(".hero .stat").every(s => s.querySelector(".k") && s.querySelector(".v")));
// They are <span>s. Without display:block the label and the number share a line and
// the hero reads "LOCKED PICKS40" / "ROI+4.58%" — on the most prominent element on
// the page. margin-bottom on an inline box does nothing, so assert the computed value.
ok("the stat label sits above its number, not beside it",
   $$(".hero .stat .k").every(k => window.getComputedStyle(k).display === "block"),
   window.getComputedStyle($(".hero .stat .k")).display);

// The chart tooltip ships with `hidden` set and is only meant to appear under the
// cursor. `[hidden]{display:none}` is a user-agent rule and origin outranks
// specificity, so a single author `display` declaration on `.tip` would leave it
// painted on load — which is what happened on the research page via `#gate`. `.tip`
// happens not to declare display today; the stylesheet guard is what keeps that from
// being luck, so assert the guard rather than the accident.
ok("stylesheet forces [hidden] to outrank author display rules",
   /\[hidden\]\s*\{\s*display\s*:\s*none\s*!important/.test(html));

console.log("\n── week by week ──");
{
  // This section only exists once something is graded. Run with QA_PAGE pointed at
  // the fixture to exercise the populated branch; against the live (empty) ledger
  // the correct behaviour is for it to be absent rather than an empty table.
  const hasWeekly = [...window.document.querySelectorAll("h2")]
    .some(h => h.textContent.includes("Week by week"));
  const graded = /ATS record/.test(window.document.body.textContent);
  if (graded) {
    ok("a graded record publishes a week-by-week table", hasWeekly);
    const tbl = [...window.document.querySelectorAll("table")].find(
      t => /Season to date/.test(t.textContent));
    ok("the weekly table has a running season column", !!tbl);
    const trs = [...(tbl?.querySelectorAll("tbody tr") || [])];
    // ">1" could not pass in the first week of any season, and did not: one graded
    // week is one row, correctly. The real failure it was reaching for is a table
    // that collapsed every week into one line, which is a DUPLICATE-label problem.
    const weeks = trs.map(tr => tr.firstElementChild.textContent.trim());
    ok("it has a row per graded week", trs.length >= 1, `${trs.length} rows`);
    ok("...and no week appears twice", new Set(weeks).size === weeks.length,
       weeks.join(", "));
    // A running total that does not run is worse than no column at all.
    const cum = trs.map(tr => parseFloat(tr.lastElementChild.textContent));
    ok("the running total is populated on every row",
       cum.every(v => !Number.isNaN(v)), cum.slice(0, 4).join(", "));
    ok("weeks are labelled", /Week /.test(trs[0]?.textContent || ""));
  } else {
    ok("an empty ledger omits the weekly table rather than showing an empty one",
       !hasWeekly);
  }
}
// The tooltip only exists once there is an equity chart to hover, so an empty ledger
// legitimately has none. Skipping silently would let this assertion quietly stop
// running the day it matters most, so say which case ran.
const tip = $(".tip");
ok(tip ? "the tooltip starts hidden and stays hidden"
       : "no chart yet, so no tooltip to hide (empty ledger)",
   tip ? window.getComputedStyle(tip).display === "none" : $(".chart") === null,
   tip ? `computed: ${window.getComputedStyle(tip).display}` : "a chart exists but no .tip");

console.log("\n── honesty about an empty ledger ──");
const text = window.document.body.textContent;
const locked = $$(".hero .stat").map(s => s.querySelector(".v")?.textContent.trim());
ok("live record section exists", text.includes("Live record"));
ok("does not claim a win rate it has not earned",
   !/\b(6[0-9]|7[0-9]|8[0-9])(\.\d)?% *(ATS|win)/i.test(text.split("Backtest")[0]),
   locked.join(" / "));
ok("empty states are explicit when there is nothing to show",
   $$(".empty").length === 0 || $$(".empty").every(e => e.textContent.trim().length > 12));

console.log("\n── the backtest states its own uncertainty ──");
{
  // The prose tells the reader to read the interval, so the interval has to be
  // there. It also has to be PRODUCED: the summary was read from a file nothing in
  // CI ever wrote, so this whole section silently vanished in production.
  const hasBacktest = [...window.document.querySelectorAll("h2")]
    .some(h => h.textContent.includes("Backtest"));
  ok("a validation backtest is published", hasBacktest,
     "no backtest section — is run_update writing output/*_backtest_*.json?");
  if (hasBacktest) {
    const t = window.document.body.textContent;
    ok("...with a confidence interval beside the headline", /95% CI [\d.]+–[\d.]+%/.test(t),
       t.slice(t.indexOf("ATS %"), t.indexOf("ATS %") + 90));
    ok("...and a verdict that does not overclaim",
       /interval still includes break-even|whole interval sits above/.test(t));

    // The headline bets every game with a line; the board does not. Publishing
    // only the wide number understates the model, and only the narrow one would
    // read as dropping the losers. If the summary carries both, the page has to
    // print both -- and print the wide one FIRST, so the better figure can never
    // be the headline.
    const V = JSON.parse(fs.readFileSync(VALIDATION, "utf8"));
    if (V.offered_ats_pct != null) {
      ok("both records are published, not just the flattering one",
         t.includes(V.offered_ats_pct.toFixed(2)) && t.includes(V.ats_pct.toFixed(2)),
         `looking for ${V.ats_pct.toFixed(2)} and ${V.offered_ats_pct.toFixed(2)}`);
      ok("...the wide one is the headline, the narrow one sits under it",
         t.indexOf(V.ats_pct.toFixed(2)) < t.indexOf(V.offered_ats_pct.toFixed(2)));
      ok("...and the page says which games the narrow one leaves out",
         /no bet/i.test(t) && t.includes(String(Math.round(V.blowout_line))),
         `blowout line ${V.blowout_line}`);
    }
  }
}

console.log("\n── pending picks are picks ──");
{
  // A locked row with no side is not a pick. They used to fill this table with em
  // dashes and bury the games the model actually has an opinion on.
  const tbl = [...window.document.querySelectorAll("table")].find(
    t => /Kickoff/.test(t.textContent) && /Pick/.test(t.textContent));
  if (tbl) {
    const heads = [...tbl.querySelectorAll("thead th")].map(t => t.textContent.trim());
    const pi = heads.indexOf("Pick");
    const trs = [...tbl.querySelectorAll("tbody tr")];
    const blank = trs.filter(tr =>
      ["—", "-", ""].includes((tr.children[pi]?.textContent || "").trim()));
    ok("every pending row carries an actual side", blank.length === 0,
       `${blank.length} of ${trs.length} rows show no pick`);
  } else {
    ok("no pending table is fine when nothing is locked",
       /No locked picks pending/.test(text));
  }
  // Anything excluded has to be counted, not silently dropped.
  const note = window.document.body.textContent;
  ok("games still waiting on a line are counted, not hidden",
     !/has no posted line yet/.test(note) || /A further \d+ rated game/.test(note));
}

console.log("\n── tables ──");
const tables = $$("table");
ok("tables are wrapped for horizontal scroll",
   tables.every(t => t.closest(".scroll") !== null), `${tables.length} tables`);
ok("numeric cells are right-aligned where present",
   $$("td.num").length > 0 || tables.length === 0);
ok("every table has a thead", tables.every(t => t.querySelector("thead")));

console.log("\n── equity chart ──");
const chart = $(".chart");
if (chart) {
  const hots = $$(".hot");
  ok("chart has hover targets", hots.length > 0, String(hots.length));
  ok("tooltip element exists", $(".tip") !== null);
  if (hots.length) {
    const tip = $(".tip");
    hots[0].dispatchEvent(new window.Event("mouseenter", { bubbles: true }));
    ok("hover reveals the tooltip", tip.hidden === false);
    ok("tooltip shows a unit figure", /[+-]?\d+\.\d{2}u/.test(tip.textContent), tip.textContent);
    hots[0].dispatchEvent(new window.Event("mouseleave", { bubbles: true }));
    ok("leaving hides it again", tip.hidden === true);
  }
} else {
  ok("no chart is correct when there are no graded picks",
     text.includes("curve appears once") || text.includes("No graded picks"));
}

console.log("\n── accessibility & polish ──");
ok("viewport meta present", $('meta[name=viewport]') !== null);
ok("lang is declared", window.document.documentElement.getAttribute("lang") === "en");
ok("dark mode is styled", html.includes("prefers-color-scheme:dark"));
ok("no raw template placeholders leaked", !/%\([a-z_]+\)s/.test(html));
ok("footer explains break-even", text.includes("52.38"));


// ---------------------------------------------------------------------------
// The page reports its own age.
//
// The site republishes on a GitHub Actions cron. GitHub throttles free
// scheduled runs hard: measured over 2026-08-24..09-01 the median gap between
// runs was 53 minutes against a stated */30, the p90 was 5.6 hours and the
// worst was 11.8. So "is what I'm looking at current?" is a real question with
// a non-obvious answer, and the page has to answer it rather than show a UTC
// stamp and look authoritative. These re-render the page at controlled clock
// offsets, which is the only way to test a relative timestamp.
// ---------------------------------------------------------------------------
console.log("\n── the page reports its own age ──");

const AGE_NOW = Date.parse("2026-09-01T12:00:00Z");
function atAge(builtIso) {
  const h = html.replace(/data-built="[^"]*"/, `data-built="${builtIso}"`);
  const d = new JSDOM(h, { runScripts: "dangerously", virtualConsole: vc,
    url: "https://example.test/",
    beforeParse(w) {
      const R = w.Date;
      w.Date = class extends R {
        constructor(...a) { return a.length ? new R(...a) : new R(AGE_NOW); }
        static now() { return AGE_NOW; }
      };
    } });
  return d.window.document.getElementById("upd");
}

ok("the build stamp is machine-readable", /data-built="\d{4}-\d{2}-\d{2}T/.test(html));

let u = atAge("2026-09-01T11:52:00Z");
ok("a page built 8 minutes ago says so", /8 minutes ago/.test(u.textContent), u.textContent);
ok("...and is not flagged stale", !u.classList.contains("stale"));

u = atAge("2026-09-01T06:00:00Z");
ok("six hours old reads in hours", /6 hours ago/.test(u.textContent), u.textContent);
ok("...and IS flagged stale", u.classList.contains("stale"));
ok("...so the warning rule applies to it",
   html.includes("your latest sheet edits may not be here yet"));

ok("3h trips the threshold", atAge("2026-09-01T09:00:00Z").classList.contains("stale"));
ok("...2h59m does not", !atAge("2026-09-01T09:01:00Z").classList.contains("stale"));
ok("a two-day-old page reads in days",
   /2 days ago/.test(atAge("2026-08-30T12:00:00Z").textContent));

// CONTROL. A relative timestamp that silently stopped updating would still
// contain the words "Generated automatically", so assert it can be WRONG.
ok("CONTROL: a fresh page never claims to be hours old",
   !/hours ago|days ago/.test(atAge("2026-09-01T11:59:40Z").textContent),
   atAge("2026-09-01T11:59:40Z").textContent);

console.log("\n── no runtime errors ──");
ok("zero JS errors", errors.length === 0, errors.slice(0, 2).join(" | "));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
