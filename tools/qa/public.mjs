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

// The chart tooltip ships with `hidden` set and is only meant to appear under the
// cursor. `[hidden]{display:none}` is a user-agent rule and origin outranks
// specificity, so a single author `display` declaration on `.tip` would leave it
// painted on load — which is what happened on the research page via `#gate`. `.tip`
// happens not to declare display today; the stylesheet guard is what keeps that from
// being luck, so assert the guard rather than the accident.
ok("stylesheet forces [hidden] to outrank author display rules",
   /\[hidden\]\s*\{\s*display\s*:\s*none\s*!important/.test(html));
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
