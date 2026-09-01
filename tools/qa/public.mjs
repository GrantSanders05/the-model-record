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
ok("updated timestamp present", /\d{2} \w{3} \d{4}/.test($(".upd")?.textContent || ""));
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

console.log("\n── no runtime errors ──");
ok("zero JS errors", errors.length === 0, errors.slice(0, 2).join(" | "));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
