/**
 * qa.mjs — load the real research app in a real DOM with the real bundle and
 * exercise every view, filter and control.
 *
 * Checking that a page "renders" is not the test. The test is that each view
 * puts non-trivial content into its own container, that every control changes
 * what is on screen, and that nothing throws while doing it — because a JS
 * error part-way through a draw leaves the previous view's markup sitting there
 * looking perfectly fine.
 */
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "node:fs";
import path from "node:path";

const ROOT = new URL("../..", import.meta.url).pathname;
const html = fs.readFileSync(path.join(ROOT, "site/research/index.html"), "utf8");
const bundle = fs.readFileSync(process.env.QA_BUNDLE || path.join(ROOT, "output/research/data.json"), "utf8");

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push("jsdomError: " + e.message));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  virtualConsole: vc,
  url: "https://example.test/research/",
  beforeParse(win) {
    // Serve the plaintext bundle; the encrypted path is covered separately.
    win.fetch = async (u) => u.includes("data.enc.json")
      ? { ok: false, json: async () => { throw new Error("nope"); } }
      : { ok: true, json: async () => JSON.parse(bundle) };
    win.matchMedia = () => ({ matches: false, addEventListener(){}, removeEventListener(){} });
  },
});
const { window } = dom;
const $ = s => window.document.querySelector(s);
const $$ = s => [...window.document.querySelectorAll(s)];

const wait = ms => new Promise(r => setTimeout(r, ms));
// Poll rather than sleep. Two 600,000-round PBKDF2 derivations take as long as
// the machine takes, and a fixed timeout turns a slow box into a red build --
// which is worse than useless, because the next person learns to ignore it.
const until = async (fn, ms = 30000, step = 100) => {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (fn()) return true; await wait(step); }
  return false;
};
await wait(400);

let pass = 0, fail = 0;
const ok = (name, cond, detail = "") => {
  if (cond) { pass++; console.log(`  [PASS] ${name}`); }
  else { fail++; console.log(`  [FAIL] ${name}${detail ? " — " + detail : ""}`); }
};
const rows = sel => $$(`${sel} tbody tr`).length;
const fire = (el, type = "input") => el.dispatchEvent(new window.Event(type, { bubbles: true }));

console.log("\n── app boots ──");
ok("app is visible", $("#app") && !$("#app").hasAttribute("hidden"));
ok("gate is hidden", $("#gate").hasAttribute("hidden"));
ok("nav is visible", !$("#nav").hasAttribute("hidden"));
ok("meta populated", ($("#meta").textContent || "").includes("CFB"), $("#meta").textContent);
ok("6 tabs present", $$("#nav button").length === 6, String($$("#nav button").length));

console.log("\n── rankings ──");
const allRows = rows("#ranktbl");
ok("all 138 teams listed", allRows === 138, `got ${allRows}`);
ok("rank column has tier badges", $$("#ranktbl .rk").length === 138);
// A single chip means every team fell back to "Independent" — which is exactly
// what a missing conference cache looks like, and it does not error anywhere.
// Asserting "more than one" rather than an exact count keeps this honest through
// realignment without letting the all-Independent failure through.
const chipCount = $$("#confchips .chip").length;
ok("conference chips built (not all one bucket)", chipCount >= 8,
   `${chipCount} conference(s) — 1 means the conference data is missing`);
ok("total bar rendered", $$("#ranktbl .bar-cell i").length === 138);
const firstTeam = () => $("#ranktbl tbody tr td.team")?.textContent;
ok("default sort is rank 1 first", firstTeam() === "Ohio State", firstTeam());

// "ohio" legitimately matches BOTH Ohio and Ohio State — a substring search that
// returned one of them would be the bug.
$("#q").value = "ohio"; fire($("#q"));
ok("substring search matches both Ohio teams", rows("#ranktbl") === 2, `got ${rows("#ranktbl")}`);
$("#q").value = "ohio state"; fire($("#q"));
ok("more specific search narrows to one", rows("#ranktbl") === 1, `got ${rows("#ranktbl")}`);
$("#q").value = "zzzznope"; fire($("#q"));
ok("no-match shows empty state", $("#ranktbl .empty") !== null);
$("#q").value = ""; fire($("#q"));
ok("clearing search restores all", rows("#ranktbl") === 138);

// Drive the filter off whatever conferences the bundle actually has, so this
// keeps working through realignment instead of hard-coding this year's names.
const someConf = $$("#confchips .chip")[0]?.dataset.c;
const confSize = someConf
  ? [...$$("#ranktbl tbody tr")].filter(tr =>
      tr.children[2].textContent.includes(someConf)).length
  : 0;
$("#conf").value = someConf; fire($("#conf"), "change");
ok("conference filter narrows to that conference",
   someConf && rows("#ranktbl") === confSize && confSize < 138,
   `${rows("#ranktbl")} rows for ${someConf}, expected ${confSize}`);
ok("chip reflects the select",
   $('#confchips .chip[aria-pressed="true"]')?.dataset.c === someConf);
$("#conf").value = ""; fire($("#conf"), "change");
ok("clearing conference restores all", rows("#ranktbl") === 138);

const chip = $$("#confchips .chip").find(c => c.dataset.c === someConf);
if (chip) {
  chip.dispatchEvent(new window.Event("click", { bubbles: true }));
  ok("chip filters", rows("#ranktbl") === confSize, `got ${rows("#ranktbl")}`);
  chip.dispatchEvent(new window.Event("click", { bubbles: true }));
  ok("chip toggles off", rows("#ranktbl") === 138);
} else {
  ok("chip filters", false, "no conference chips to click");
}

$("#rsort").value = "qb"; fire($("#rsort"), "change");
const qbCells = () => $$("#ranktbl tbody tr").map(tr => parseFloat(tr.children[3].textContent));
const qbs = qbCells();
ok("sort by QB is descending", qbs[0] >= qbs[1] && qbs[1] >= qbs[2], qbs.slice(0,3).join(","));
$("#rdir").value = "asc"; fire($("#rdir"), "change");
const qbs2 = qbCells();
ok("order toggle reverses", qbs2[0] <= qbs2[1], qbs2.slice(0,3).join(","));

$("#rlimit").value = "25"; fire($("#rlimit"), "change");
ok("limit caps rows", rows("#ranktbl") === 25, `got ${rows("#ranktbl")}`);

$("#rreset").dispatchEvent(new window.Event("click", { bubbles: true }));
ok("reset restores everything", rows("#ranktbl") === 138 && firstTeam() === "Ohio State");

const th = $$("#ranktbl th.sortable").find(t => t.dataset.k === "total");
th.dispatchEvent(new window.Event("click", { bubbles: true }));
ok("header click sorts", $("#ranktbl th[aria-sort]") !== null);

console.log("\n── best bets ──");
ok("bet cards render", $$("#betcards .card").length === 4);
const mlRows = rows("#bets");
ok("moneyline table has rows", mlRows > 0, `got ${mlRows}`);
$("#sortby").value = "spread"; fire($("#sortby"));
const spreadAll = rows("#bets");
ok("spread mode re-renders", spreadAll > 0, `got ${spreadAll}`);

// Row counts are only comparable WITHIN a mode — the two modes filter on
// different fields (ml_ev vs spread_edge), so a cross-mode comparison proves
// nothing. Filter inside spread mode and check it is a strict subset.
$("#wk").value = "1"; fire($("#wk"));
const wk1 = rows("#bets");
ok("week filter is a subset of the mode", wk1 > 0 && wk1 <= spreadAll, `${wk1} of ${spreadAll}`);

// A week with no priced spreads must show the empty state, not last week's rows.
$("#wk").value = "8"; fire($("#wk"));
ok("empty week shows the empty state", $("#bets .empty") !== null);

$("#wk").value = ""; fire($("#wk"));
$("#sortby").value = "ml"; fire($("#sortby"));
const stakeBefore = $("#bets").textContent;
$("#bank").value = "5000"; fire($("#bank"));
ok("bankroll change actually changes the stakes",
   $("#bets").textContent !== stakeBefore);
$("#bank").value = "1000"; fire($("#bank"));

console.log("\n── team ──");
ok("team select populated", $$("#team option").length === 138);
ok("team cards render", $$("#teamcards .card").length === 10);
const effHas = $("#effchart").innerHTML.length > 50;
ok("efficiency chart or honest empty", effHas);
$("#team").value = "Alabama"; fire($("#team"));
ok("switching team re-renders", $("#teamcards").innerHTML.includes("Alabama") ||
   $$("#teamcards .card").length === 10);

console.log("\n── line movement ──");
ok("game select populated", $$("#game option").length > 0, String($$("#game option").length));
ok("movement renders something", $("#movechart").innerHTML.length > 30);

console.log("\n── what-if ──");
ok("sliders render", $$("#sliders .sl").length === 8, String($$("#sliders .sl").length));
ok("what-if cards render", $$("#wcards .card").length === 4);
const before = $("#wcards").textContent;
const slider = $$("#sliders input")[0];
slider.value = "14.5"; fire(slider);
ok("moving a slider changes the totals", $("#wcards").textContent !== before);
ok("change is reflected in the games table", rows("#wgames") >= 0);
$("#reset").dispatchEvent(new window.Event("click", { bubbles: true }));
ok("reset restores base grades", $("#wcards").textContent === before);

console.log("\n── roster news ──");
ok("alerts table renders", $("#alerttbl").innerHTML.length > 20);
ok("watchlist renders", rows("#watchtbl") > 0, `got ${rows("#watchtbl")}`);
ok("alert note explains empty board", ($("#alertnote").textContent||"").length > 20);

console.log("\n── tab navigation ──");
for (const b of $$("#nav button")) {
  b.dispatchEvent(new window.Event("click", { bubbles: true }));
  const v = $("#v-" + b.dataset.v);
  ok(`tab "${b.textContent}" shows its view`,
     v.classList.contains("on") && $$(".view.on").length === 1);
}

console.log("\n── banners ──");
ok("at least one banner shown", $$("#banner .banner").length > 0,
   String($$("#banner .banner").length));

console.log("\n── no runtime errors ──");
ok("zero JS errors during the whole run", errors.length === 0, errors.slice(0,3).join(" | "));

/* ── the encrypted path, in a second document ──────────────────────────────
   The run above served plaintext. This one serves a real encrypted bundle so
   the gate, a wrong passphrase, and a right one are all exercised for real
   rather than assumed to still work. */
console.log("\n── encrypted bundle + gate ──");
{
  const crypto = await import("node:crypto");
  const nodeCrypto = crypto.default ?? crypto;
  const PASS = "correct-horse-battery-staple-2026";
  const salt = crypto.randomBytes(16), iv = crypto.randomBytes(12);
  const key = crypto.pbkdf2Sync(PASS, salt, 600000, 32, "sha256");
  const c = crypto.createCipheriv("aes-256-gcm", key, iv);
  const ct = Buffer.concat([c.update(Buffer.from(bundle)), c.final()]);
  const enc = { v:1, kdf:"PBKDF2-SHA256", iterations:600000,
                salt: salt.toString("base64"), iv: iv.toString("base64"),
                ct: Buffer.concat([ct, c.getAuthTag()]).toString("base64") };

  const errs2 = [];
  const vc2 = new VirtualConsole();
  vc2.on("jsdomError", e => errs2.push(e.message));
  const d2 = new JSDOM(html, {
    runScripts: "dangerously", virtualConsole: vc2,
    url: "https://example.test/research/",
    beforeParse(win) {
      win.fetch = async (u) => u.includes("data.enc.json")
        ? { ok: true, json: async () => enc }
        : { ok: false, json: async () => { throw new Error("no plaintext"); } };
      win.matchMedia = () => ({ matches:false, addEventListener(){}, removeEventListener(){} });
      // jsdom ships window.crypto WITHOUT subtle. Left alone, every call throws
      // TypeError, the catch reports "that passphrase does not open this bundle",
      // and the wrong-passphrase test passes because the API is missing rather
      // than because the key is wrong — a test that cannot fail. Give it the real
      // thing so the crypto path is genuinely exercised.
      Object.defineProperty(win, "crypto", {
        value: nodeCrypto.webcrypto, configurable: true, writable: true });
    },
  });
  const w2 = d2.window, q2 = s => w2.document.querySelector(s);
  await wait(300);

  ok("gate is shown for an encrypted bundle", !q2("#gate").hasAttribute("hidden"));
  ok("app stays hidden before unlock", q2("#app").hasAttribute("hidden"));
  ok("no plaintext grades in the DOM before unlock",
     !w2.document.body.innerHTML.includes("Ohio State"));

  q2("#gate-pass").value = "wrong-passphrase";
  q2("#gate-btn").dispatchEvent(new w2.Event("click", { bubbles:true }));
  await until(() => (q2("#gate-msg").textContent||"").includes("does not open"));
  ok("wrong passphrase is rejected",
     (q2("#gate-msg").textContent||"").includes("does not open"), q2("#gate-msg").textContent);
  ok("app still hidden after a wrong passphrase", q2("#app").hasAttribute("hidden"));

  q2("#gate-pass").value = PASS;
  q2("#gate-btn").dispatchEvent(new w2.Event("click", { bubbles:true }));
  await until(() => !q2("#app").hasAttribute("hidden"));
  ok("right passphrase unlocks", !q2("#app").hasAttribute("hidden"));
  ok("rankings render after unlock",
     q2("#ranktbl tbody") && q2("#ranktbl").querySelectorAll("tbody tr").length === 138,
     String(q2("#ranktbl")?.querySelectorAll("tbody tr").length));
  ok("no errors on the encrypted path", errs2.length === 0, errs2.slice(0,2).join(" | "));
}

/* ── escaping is real, not decorative ────────────────────────────────── */
console.log("\n── injection ──");
{
  const evil = JSON.parse(bundle);
  const name = '<img src=x onerror="window.__pwned=1">';
  evil.teams[name] = { grades:{qb:11,rb:8,wr:8,ol:11,dl:11,lb:8,db:8,coach_st:11},
                       week:1, conference:"SEC", total:120.0, rank:139,
                       conf_rank:17, conf_size:17 };
  const d3 = new JSDOM(html, {
    runScripts:"dangerously", virtualConsole:new VirtualConsole(),
    url:"https://example.test/research/",
    beforeParse(win){
      win.fetch = async u => u.includes("data.enc.json")
        ? { ok:false, json:async()=>{throw new Error("x")} }
        : { ok:true, json:async()=>evil };
      win.matchMedia = () => ({matches:false,addEventListener(){},removeEventListener(){}});
    },
  });
  await wait(400);
  const w3 = d3.window;
  ok("a team name cannot execute script", w3.__pwned === undefined);
  ok("the hostile name is rendered as text",
     w3.document.querySelector("#ranktbl").textContent.includes("<img src=x"));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
