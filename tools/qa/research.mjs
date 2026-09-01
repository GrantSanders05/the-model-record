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
// A table showing "nothing matches that filter" still contains one <tr>. Counting
// rows naively turns an empty result into a count of 1, which reads as "the filter
// kept one row" when it actually kept none — and passes.
const listRows = sel =>
  $$(`${sel} tbody tr`).filter(tr => !tr.querySelector(".empty")).length;
const fire = (el, type = "input") => el.dispatchEvent(new window.Event(type, { bubbles: true }));

// Resolve columns by their HEADER, never by a hard-coded index. Index-based reads
// broke every assertion in two sections the moment a column was inserted, and the
// failures looked like app bugs ("0 plays sized") when the app was correct -- the
// summary card next to the column had the right number the whole time.
const col = (sel, header) => {
  const i = $$(`${sel} thead th`).findIndex(
    th => th.textContent.trim().toLowerCase() === header.toLowerCase());
  if (i < 0) throw new Error(`no "${header}" column in ${sel} — headers: ` +
    $$(`${sel} thead th`).map(t => t.textContent.trim()).join(" | "));
  return i;
};
const cellText = (tr, i) => (tr.children[i]?.textContent || "").trim();
// The matchup cell holds both teams; the away one is the first .mu-t span.
const teamsOf = tr => [...(tr.querySelector("td.mu")?.querySelectorAll(".mu-t") || [])]
  .map(s => s.textContent.trim());

console.log("\n── app boots ──");
ok("app is visible", $("#app") && !$("#app").hasAttribute("hidden"));
ok("gate is hidden", $("#gate").hasAttribute("hidden"));
ok("nav is visible", !$("#nav").hasAttribute("hidden"));

// Asserting the ATTRIBUTE is not the same as asserting the element is gone, and the
// difference shipped: `[hidden]{display:none}` is a user-agent rule, origin beats
// specificity, so `#gate{display:grid}` outranked it and the passphrase box stayed
// painted over a booted app forever. Every `hidden` assertion above passed the whole
// time. Check what the cascade actually computes.
const disp = s => window.getComputedStyle($(s)).display;
ok("gate is really not displayed", disp("#gate") === "none", `computed: ${disp("#gate")}`);
ok("app is really displayed", disp("#app") !== "none", `computed: ${disp("#app")}`);
// jsdom resolves this collision by specificity only, so it catches `#gate` but NOT
// `nav{display:flex}` — which real Chrome confirmed also loses. The stylesheet-level
// guard is what covers the whole class, so assert the guard itself is present.
ok("stylesheet forces [hidden] to outrank author display rules",
   /\[hidden\]\s*\{\s*display\s*:\s*none\s*!important/.test(html));
ok("meta populated", ($("#meta").textContent || "").includes("CFB"), $("#meta").textContent);
// Naming them beats counting them: a count passes just as happily when a tab has
// been renamed into nonsense or two tabs point at the same view.
{
  const want = ["Rankings","Schedule","Best bets","Results","My bets",
                "Team","Line movement","What-if","Roster news"];
  const got = $$("#nav button").map(b => b.textContent.trim());
  ok("every tab is present and named", JSON.stringify(got) === JSON.stringify(want), got.join(" | "));
  ok("every tab points at a view that exists",
     $$("#nav button").every(b => $("#v-" + b.dataset.v) !== null));
}

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

console.log("\n── schedule ──");
{
  const B = JSON.parse(bundle);
  ok("schedule shipped in the bundle", Array.isArray(B.schedule) && B.schedule.length > 0,
     `${B.schedule?.length} games`);
  // The point of this view is that it is WIDER than the bets board. If it ever ends
  // up the same size, it has quietly become a second copy of the bets board.
  ok("schedule is wider than the bets board", B.schedule.length > B.bets.length,
     `${B.schedule.length} scheduled vs ${B.bets.length} on the board`);
  ok("games the model will not price are present, not dropped",
     B.schedule.some(g => g.status === "blowout"));

  // Week 0 is DERIVED, and whether one exists is a fact about this season's
  // calendar, not about this code. Asserting "a week 0 exists" made the schedule
  // gate the deploy on the shape of a third party's data: when the 2026 opening
  // weekend shifted and the split stopped firing, this went red and took every
  // scheduled update with it for ten days. Assert instead that the label and the
  // bundle agree, and that IF there is a week 0 it has the right shape.
  const w0 = B.schedule.filter(g => g.week === 0).length;
  const w1 = B.schedule.filter(g => g.week === 1).length;
  ok("the week-0 flag matches the schedule", B.week0 === (w0 > 0),
     `flag ${B.week0}, ${w0} games labelled week 0`);
  if (w0)
    ok("week 0 is the small early slate, not half of week 1", w0 < w1,
       `week 0 has ${w0}, week 1 has ${w1}`);
  else
    ok("a season with no week 0 still starts at week 1", w1 > 0, `${w1} games`);
  // CFBD files both under week 1; the split must be a LABEL, never a rewrite of the
  // key the ledger stamps its locked picks with.
  ok("week 0 keeps its original CFBD week for the model",
     B.schedule.filter(g => g.week === 0).every(g => g.cfbd_week === 1));
  ok("the bets board uses the same labels",
     B.bets.every(b => {
       const g = B.schedule.find(s => s.game_id === b.game_id);
       return !g || g.week === b.week;
     }));

  ok("week selector offers every scheduled week",
     $$("#swk option").length === new Set(B.schedule.map(g => g.week)).size + 1,
     `${$$("#swk option").length} options`);
  ok("it opens on a week with games still to play",
     $("#swk").value !== "" && rows("#schedtbl") > 0, `week ${$("#swk").value}`);

  const oneWeek = rows("#schedtbl");
  $("#swk").value = ""; fire($("#swk"), "change");
  ok("all-weeks shows every game", rows("#schedtbl") === B.schedule.length,
     `${rows("#schedtbl")} of ${B.schedule.length}`);
  ok("a single week is a strict subset", oneWeek < rows("#schedtbl"));

  if (w0) {
    $("#swk").value = "0"; fire($("#swk"), "change");
    ok("week 0 is selectable and renders", rows("#schedtbl") === w0,
       `${rows("#schedtbl")} rows`);
  }

  $("#swk").value = ""; fire($("#swk"), "change");
  $("#sshow").value = "line"; fire($("#sshow"), "change");
  const lined = rows("#schedtbl");
  ok("the with-a-line filter narrows", lined > 0 && lined < B.schedule.length, `${lined} rows`);
  $("#sshow").value = "bet"; fire($("#sshow"), "change");
  ok("the on-the-board filter matches the bets board",
     rows("#schedtbl") === B.bets.filter(b =>
       B.schedule.some(s => s.game_id === b.game_id)).length, `${rows("#schedtbl")} rows`);
  $("#sshow").value = "all"; fire($("#sshow"), "change");

  const conf0 = $$("#sconf option")[1]?.value;
  $("#sconf").value = conf0; fire($("#sconf"), "change");
  const inConf = rows("#schedtbl");
  ok("conference filter narrows the slate", inConf > 0 && inConf < B.schedule.length,
     `${inConf} rows for ${conf0}`);
  // A conference filter on a SCHEDULE must match either side — filtering on the home
  // team only would silently hide every road game a conference plays.
  ok("conference filter matches away teams too",
     [...$$("#schedtbl tbody tr")].some(tr =>
       B.teams[teamsOf(tr)[0]]?.conference === conf0),
     `sample away team: ${teamsOf($$("#schedtbl tbody tr")[0] || {})[0]}`);
  $("#sconf").value = ""; fire($("#sconf"), "change");

  $("#sq").value = "ohio state"; fire($("#sq"));
  const found = rows("#schedtbl");
  ok("team search finds that team's games", found > 0 && found < 20, `${found} games`);
  $("#sq").value = "zzzznope"; fire($("#sq"));
  ok("no match shows the empty state", $("#schedtbl .empty") !== null);
  $("#sreset").dispatchEvent(new window.Event("click", { bubbles: true }));
  ok("reset returns to the default week",
     $("#sq").value === "" && $("#swk").value !== "" && rows("#schedtbl") > 0);
}

console.log("\n── readable without decoding a sign ──");
{
  const B = JSON.parse(bundle);
  $("#swk").value = ""; fire($("#swk"), "change");
  $("#sshow").value = "line"; fire($("#sshow"), "change");
  // Must be a HOSTED game: at a neutral site nobody is emphasised, on purpose, so
  // taking row 0 blindly tested the wrong rule (row 0 here is a Dublin kickoff).
  const neutralIds = new Set(B.schedule.filter(x => x.neutral).map(x => x.home + "|" + x.away));
  const tr = $$("#schedtbl tbody tr").find(
    t => !neutralIds.has(teamsOf(t)[1] + "|" + teamsOf(t)[0]));
  const [away, home] = teamsOf(tr);
  const g = B.schedule.find(x => x.away === away && x.home === home && x.line != null);
  ok("matchup names both teams", !!away && !!home, `${away} @ ${home}`);
  ok("the host is the emphasised team",
     tr.querySelector(".mu-home")?.textContent.trim() === home, `${away} @ ${home}`);
  ok("a hosted game shows @", tr.querySelector(".mu-at").textContent.trim() === "@");

  // The point of the change: the spread is shown as FAVOURITE and a negative
  // number, so it reads the way it is spoken. A home-perspective "+6.5" against a
  // column header is what it replaced.
  const cM = col("#schedtbl", "Market has it");
  const shown = cellText(tr, cM);
  const wantTeam = g.line > 0 ? home : away;
  ok("the market column names the favourite, not the home team",
     shown.startsWith(wantTeam), `shows "${shown}", favourite is ${wantTeam}`);
  ok("the favourite's number is negative",
     shown.includes("−") && Math.abs(parseFloat(shown.replace(/[^0-9.]/g, "")) - Math.abs(g.line)) < 0.06,
     shown);

  // Edge is a magnitude now; the side lives in the pick, so a sign would be noise.
  const cE = col("#schedtbl", "Edge");
  ok("edge is shown as a magnitude", !cellText(tr, cE).includes("−") &&
     !cellText(tr, cE).includes("+"), cellText(tr, cE));

  // A pick without its number does not tell you if you are laying or taking points.
  const withPick = $$("#schedtbl tbody tr").find(t => {
    const c = cellText(t, col("#schedtbl", "Spread pick"));
    return c && c !== "—";
  });
  const pickTxt = cellText(withPick, col("#schedtbl", "Spread pick"));
  ok("the spread pick carries its number", /[+−]\d|PK/.test(pickTxt), pickTxt);

  // A "No bet" game must not also show an edge and a pick. Those edges are the
  // largest on the board and they are artifacts — the exact number the rule exists
  // to throw away. A label beside a big number loses to the big number.
  {
    const nb = B.schedule.find(x => x.no_bet);
    $("#sshow").value = "all"; fire($("#sshow"), "change");
    $("#sq").value = nb.home; fire($("#sq"));
    const ntr = $$("#schedtbl tbody tr").find(t => teamsOf(t)[1] === nb.home &&
                                                   teamsOf(t)[0] === nb.away);
    ok("a No-bet game withholds its edge and pick",
       ntr && cellText(ntr, col("#schedtbl", "Edge")) === "—" &&
       cellText(ntr, col("#schedtbl", "Spread pick")) === "—",
       ntr ? `edge="${cellText(ntr, col("#schedtbl","Edge"))}" pick="${cellText(ntr, col("#schedtbl","Spread pick"))}"` : "row not found");
    ok("but it still says why", ntr && cellText(ntr, 8).includes("No bet"));
    $("#sq").value = ""; fire($("#sq"));
  }

  $("#sshow").value = "all"; fire($("#sshow"), "change");
  const neu = B.schedule.find(x => x.neutral);
  if (neu) {
    $("#sq").value = neu.home; fire($("#sq"));
    const ntr = $$("#schedtbl tbody tr").find(t => teamsOf(t)[1] === neu.home);
    ok("a neutral-site game says vs, not @, and flags neutral",
       ntr && ntr.querySelector(".mu-at").textContent.trim() === "vs" &&
       ntr.querySelector(".mu-n") !== null);
    ok("neither team is emphasised at a neutral site",
       ntr && ntr.querySelector(".mu-home") === null);
    $("#sq").value = ""; fire($("#sq"));
  }
  $("#sreset").dispatchEvent(new window.Event("click", { bubbles: true }));

  // Best bets: the side you are backing must say whether it is home or away.
  $$("#nav button").find(b => b.dataset.v === "bets")
    .dispatchEvent(new window.Event("click", { bubbles: true }));
  $("#sortby").value = "ml"; fire($("#sortby"), "change");
  const btr = $$("#bets tbody tr")[0];
  const bet = JSON.parse(bundle).bets.find(x =>
    x.away === teamsOf(btr)[0] && x.home === teamsOf(btr)[1]);
  const sideTag = btr.querySelector(".side")?.textContent.trim();
  ok("the bet names home or away", ["home", "away"].includes(sideTag), String(sideTag));
  ok("that tag matches which team the bet is on",
     sideTag === (bet.ml_pick === bet.home ? "home" : "away"),
     `${bet.ml_pick} tagged ${sideTag}`);
  ok("both tables carry a legend", $("#schednote .legend") !== null &&
     $("#betnote .legend") !== null);
}

console.log("\n── results / tracking ──");
{
  const B = JSON.parse(bundle);
  const T = B.tracking;
  ok("tracking is in the bundle", !!T);
  $$("#nav button").find(b => b.dataset.v === "results")
    .dispatchEvent(new window.Event("click", { bubbles: true }));

  if (T.empty) {
    // An empty ledger is the live state until games are played, and it is the state
    // most likely to be shipped untested. It must read as "nothing has finished",
    // never as a 0% record.
    ok("an empty record says so instead of showing 0%",
       $("#resultstate .banner") !== null &&
       $("#resultstate").textContent.includes("No games have finished"));
    ok("it names how many picks are waiting",
       /\d+ pick/.test($("#resultstate").textContent), $("#resultstate").textContent.slice(0, 70));
    ok("no fabricated percentage anywhere in the cards",
       !/\d+\.\d%/.test($("#rescards").textContent), $("#rescards").textContent.slice(0, 60));
  } else {
    const a = T.overall.ats;
    ok("headline cards render", $$("#rescards .card").length === 6);
    ok("the ATS rate matches the bundle",
       $("#rescards").textContent.includes(a.pct.toFixed(1) + "%"), a.pct);
    // The whole point of the page: never a rate without its interval.
    ok("the interval is shown beside the rate",
       $("#rescards").textContent.includes(a.ci95[0].toFixed(1)), String(a.ci95));
    ok("a verdict banner states whether it clears",
       $("#resproof .banner") !== null &&
       /clears break-even|Not proven yet/.test($("#resproof").textContent));
    ok("the verdict agrees with the arithmetic",
       a.clears === $("#resproof").textContent.includes("This clears break-even"),
       `clears=${a.clears}`);

    const wkRows = rows("#restbl");
    ok("one row per graded week", wkRows === T.weekly.length, `${wkRows} vs ${T.weekly.length}`);
    // CFBD numbers the postseason from 1, so bowls and the opening Saturday shared a
    // week number until they were split. A row literally labelled "Week 99" would
    // mean the split happened and the label did not.
    const labels = $$("#restbl tbody tr").map(tr => cellText(tr, 0));
    ok("no raw week-99 label leaks into the UI", !labels.includes("Week 99"),
       labels.join(", "));
    if (T.weekly.some(w => String(w.key) === "99"))
      ok("the bowls bucket is named", labels.includes("Bowls"), labels.at(-1));
    ok("weekly rows carry a running season total",
       $$("#restbl thead th").some(t => t.textContent.includes("Season to date")));
    // A cumulative column that does not accumulate is worse than none.
    const cumCol = col("#restbl", "Season to date");
    const firstCum = cellText($$("#restbl tbody tr")[0], cumCol);
    const lastCum = cellText($$("#restbl tbody tr").at(-1), cumCol);
    ok("the running total ends at the overall rate",
       lastCum.includes(a.pct.toFixed(1)), `${firstCum} … ${lastCum}`);

    // Segments must actually re-render, and must not all be one bucket.
    const segBefore = $("#segtbl").innerHTML;
    $("#segcut").value = "by_side"; fire($("#segcut"), "change");
    ok("changing the cut re-renders", $("#segtbl").innerHTML !== segBefore);
    ok("favourite/underdog splits into two", rows("#segtbl") === 2, `${rows("#segtbl")} rows`);
    $("#segcut").value = "by_edge"; fire($("#segcut"), "change");
    ok("edge buckets render", rows("#segtbl") >= 2, `${rows("#segtbl")} rows`);

    ok("CLV is reported", $$("#clvcards .card").length === 4 &&
       $("#clvcards").textContent.includes(String(T.overall.clv.n)));
    ok("calibration is reported", $$("#calcards .card").length >= 1 &&
       (!T.calibration || $("#calcards").textContent.includes(T.calibration.slope.toFixed(2))));

    const all = rows("#rpicks");
    ok("every graded pick is listed", all > 0, `${all} rows`);
    // The table is capped for the DOM's sake, so compare the STATED match count
    // rather than row counts — two different filters both capped at 400 look
    // identical and neither is.
    const matched = () => +($("#rpicksnote").textContent.match(/of (\d+)|all (\d+)/) || [])
      .slice(1).find(Boolean);
    ok("the table states how many matched", matched() > 0, $("#rpicksnote").textContent);
    const allMatched = matched();
    $("#rfilter").value = "L"; fire($("#rfilter"), "change");
    const losses = matched();
    ok("filtering to losses narrows", losses > 0 && losses < allMatched,
       `${losses} of ${allMatched}`);
    ok("...and only shows losses",
       $$("#rpicks tbody tr").every(tr => tr.querySelector(".res-l") !== null));
    $("#rfilter").value = "all"; fire($("#rfilter"), "change");
    // "narrows" is only true when more than one week has been graded. In the first
    // week of a season, selecting the only week matches everything — correctly —
    // and this failed for that reason, taking the deploy with it. Assert what the
    // filter is actually for: it selects exactly that week.
    const wk = T.weekly[0];
    $("#rweek").value = String(wk.key); fire($("#rweek"), "change");
    const wkMatched = matched();
    ok("the week filter selects exactly that week's picks",
       wkMatched === (wk.n ?? wk.graded ?? wkMatched),
       `${wkMatched} shown, week ${wk.key} has ${wk.n ?? wk.graded}`);
    if (T.weekly.length > 1)
      ok("...which is fewer than the whole record", wkMatched < allMatched,
         `${wkMatched} of ${allMatched}`);
    else
      ok("...and with one graded week that is the whole record so far",
         wkMatched === allMatched, `${wkMatched} of ${allMatched}`);
    $("#rweek").value = ""; fire($("#rweek"), "change");
  }
}

console.log("\n── my bets: the browser's grader ──");
// Bets are entered and graded on this page now, while the legacy Google Sheet path
// is still parsed and matched in Python. That means one book, two implementations,
// so the SAME hand-worked table is run through both. Either side drifting shows up
// as a red build here or in tools/test_tracking.py, rather than as two totals that
// cannot be reconciled a season later.
{
  const T = JSON.parse(fs.readFileSync(path.join(ROOT, "tools/grading_cases.json"), "utf8"));
  const gameOf = n => {
    const g = T.games[n];
    return { home: g.home, away: g.away, home_score: g.home_score,
             away_score: g.away_score, line: g.line, total: g.total,
             home_ml: g.home_ml, away_ml: g.away_ml };
  };
  const run = () => {
    const wrong = [];
    for (const c of T.cases) {
      const g = gameOf(c.game);
      const b = { team: c.team, market: c.market, side: c.side || null,
                  line: c.line, odds: c.odds, units: c.units };
      const r = window.gradeBet(b, g), clv = window.betCLV(b, g);
      const okR = r.result === c.result;
      const okU = c.units_won == null ? r.units_won == null
                                      : Math.abs(r.units_won - c.units_won) < 5e-4;
      const okC = c.clv == null ? clv == null : Math.abs(clv - c.clv) < 5e-4;
      if (!(okR && okU && okC))
        wrong.push(`${c.why} → ${r.result}/${r.units_won}/clv ${clv}`);
    }
    return wrong;
  };
  const wrong = run();
  ok(`all ${T.cases.length} shared cases grade identically in the browser`,
     wrong.length === 0, wrong[0] || "");

  // The same anti-vacuity proof the Python suite ends with. A parity test that
  // cannot fail is not a parity test, and this one is only meaningful if flipping
  // the grader's sign turns it red.
  const real = window.gradeBet;
  window.gradeBet = (b, g) => {
    const r = real(b, g);
    return { ...r, result: { W: "L", L: "W" }[r.result] || r.result,
             units_won: r.units_won == null ? null : -r.units_won };
  };
  const broke = run().length;
  window.gradeBet = real;
  ok("[control] a sign-flipped grader fails the table", broke > 0,
     "a corrupted grader passed — the parity test proves nothing");
  ok("...and the real one is restored", run().length === 0);
}

console.log("\n── my bets: agreement with Python on real bets ──");
{
  const M = JSON.parse(bundle).mybets || {};
  $$("#nav button").find(b => b.dataset.v === "mybets")
    .dispatchEvent(new window.Event("click", { bubbles: true }));

  if (M.state === "ok" && (M.bets || []).length) {
    // Python matched and graded these rows; the page re-grades them from the
    // schedule. Same book, computed twice, over a whole season.
    const js = window.bookTotals(window.allBets().filter(b => b.source === "sheet"), 0);
    const t = M.totals;
    const close = (a, b, tol = 0.02) =>
      (a == null && b == null) || (a != null && b != null && Math.abs(a - b) <= tol);
    ok("same number of bets", js.n === t.n, `${js.n} vs ${t.n}`);
    ok("same W–L–P", js.w === t.w && js.l === t.l && js.push === t.push,
       `${js.w}-${js.l}-${js.push} vs ${t.w}-${t.l}-${t.push}`);
    ok("same units won", close(js.units_won, t.units_won),
       `${js.units_won} vs ${t.units_won}`);
    ok("same units risked", close(js.units_risked, t.units_risked),
       `${js.units_risked} vs ${t.units_risked}`);
    ok("same ROI", close(js.roi, t.roi, 0.05), `${js.roi} vs ${t.roi}`);
    ok("same break-even from the prices taken", close(js.break_even, t.break_even, 0.05),
       `${js.break_even} vs ${t.break_even}`);
    ok("same CLV", close(js.clv_mean, t.clv_mean, 0.01),
       `${js.clv_mean} vs ${t.clv_mean}`);
    ok("bad sheet rows are surfaced, not swallowed",
       (M.problems || []).length === 0 || $("#mbproblems .banner") !== null);
  } else {
    ok("an empty sheet log is a footnote, not a wall",
       $("#mbfoot").textContent.length > 0 && $("#mbstate").textContent.length < 600);
    // A `My Bets` tab that exists with nothing in it is an empty log, not a fault.
    // It was reported as a problem row, so the site told somebody with a perfectly
    // good book that one of their bets could not be graded.
    ok("an empty sheet tab is not reported as a broken bet",
       !$("#mbproblems").textContent.includes("could not be graded"),
       $("#mbproblems").textContent.slice(0, 80));
  }
}

console.log("\n── my bets: logging one from the site ──");
{
  // Start from an empty book so the counts below mean what they say.
  window.localStorage.removeItem("tm_bets_v1");
  window.eval("BOOK={v:1,bets:[]}");
  window.drawMyBets();

  const sched = JSON.parse(bundle).schedule;
  const G = sched.find(g => g.home_score != null && g.line != null)
         || sched.find(g => g.line != null) || sched[0];
  const isFinal = G.home_score != null;
  // Whatever is already here from the sheet. Asserting "one bet" from a bundle that
  // ships forty of them would fail for the right reason and read like a bug.
  const base = listRows("#mblist");

  // THE INVARIANT the browser's grading rests on: every bet the bundle ships has its
  // game in the bundle. Without it a real result is silently replaced by a blank,
  // which is the failure this whole tab is built to avoid.
  ok("every bet in the bundle has a game to grade against",
     window.allBets().every(b => !b.orphan),
     String(window.allBets().filter(b => b.orphan).length) + " orphaned");

  if (!base)
    ok("an empty book teaches the form, not a spreadsheet",
       $("#mbstate").textContent.includes("Log bet") &&
       !$("#mbstate").textContent.includes("Google Sheet"));
  else
    ok("a book with bets in it drops the teaching banner",
       $("#mbstate").textContent.trim() === "", $("#mbstate").textContent.slice(0, 60));
  ok("the form is open, not behind a setup step",
     !$("#mbform").hasAttribute("hidden") && $$("#fbweek option").length > 0);

  $("#fbweek").value = String(G.week); fire($("#fbweek"), "change");
  ok("choosing a week populates its games", $$("#fbgame option").length > 0);
  $("#fbgame").value = String(G.game_id); fire($("#fbgame"), "change");
  ok("the game selected is the one asked for", $("#fbgame").value === String(G.game_id));

  const chips = $$("#fbside .chip");
  ok("both sides offered", chips.length === 2,
     chips.map(c => c.textContent.trim()).join(" / "));
  ok("the chips carry the market number so it need not be retyped",
     G.line == null || chips.some(c => c.querySelector(".n")),
     chips.map(c => c.textContent.trim()).join(" / "));

  // Back the home team. The market number for that side is the negation of the
  // home-margin convention, and the form must fill exactly that in.
  chips[1].dispatchEvent(new window.Event("click", { bubbles: true }));
  if (G.line != null)
    ok("the line is prefilled from the home side of the market",
       Math.abs(+$("#fbline").value + G.line) < 0.051,
       `${$("#fbline").value} vs market ${G.line}`);

  $("#fbunits").value = "2"; fire($("#fbunits"));
  $("#fbodds").value = "-105"; fire($("#fbodds"));
  ok("the hint says what is being risked and what it returns",
     /Risking 2u to win 1\.9\d+u/.test($("#fbhint").textContent), $("#fbhint").textContent);

  $("#fbsave").dispatchEvent(new window.Event("click", { bubbles: true }));

  ok("the bet is in the table", listRows("#mblist") === base + 1,
     `${listRows("#mblist")} vs ${base + 1}`);
  const stored = JSON.parse(window.localStorage.getItem("tm_bets_v1") || "{}").bets || [];
  ok("...and in storage, so a reload keeps it", stored.length === 1);
  ok("...carrying the game's own id, so it can never match the wrong fixture",
     stored[0] && String(stored[0].game_id) === String(G.game_id));
  ok("...at the price and size entered",
     stored[0] && stored[0].odds === -105 && stored[0].units === 2,
     JSON.stringify(stored[0]));
  ok("the summary counts every bet in the book",
     $("#mbcards").textContent.includes(String(base + 1)), String(base + 1));

  // A logged bet grades itself. On a bundle whose season has not started there is
  // nothing final to grade, and "open" is then the correct answer -- asserting a
  // result there would be asserting a bug.
  if (isFinal) {
    const expect = window.gradeBet(stored[0], G);
    const mine = $$("#mblist tbody tr").find(tr =>
      tr.querySelector('button[data-act="edit"][data-id="' + stored[0].id + '"]'));
    const cell = mine.children[col("#mblist", "Result")];
    ok("a finished game grades on the spot", cell.textContent.trim() === expect.result,
       `${cell.textContent.trim()} vs ${expect.result}`);
    const wonCell = mine.children[col("#mblist", "Won")];
    ok("...and the units won are the price times the stake",
       wonCell.textContent.includes(Math.abs(expect.units_won).toFixed(2)),
       `${wonCell.textContent} vs ${expect.units_won}`);
  } else {
    const mine = $$("#mblist tbody tr").find(tr =>
      tr.querySelector('button[data-act="edit"][data-id="' + stored[0].id + '"]'));
    const cell = mine.children[col("#mblist", "Result")];
    ok("an unplayed game stays open rather than defaulting to a loss",
       cell.textContent.trim() === "open", cell.textContent.trim());
  }

  // Logging the identical bet twice is usually a double click and occasionally
  // real, so it is a confirmation rather than a refusal.
  $("#fbsave").dispatchEvent(new window.Event("click", { bubbles: true }));
  ok("an identical bet is questioned, not silently doubled",
     listRows("#mblist") === base + 1);
  ok("...and says so", $("#fbmsg").textContent.includes("already in the book"));
  $("#fbsave").dispatchEvent(new window.Event("click", { bubbles: true }));
  ok("...but pressing again accepts it", listRows("#mblist") === base + 2);

  // Edit, then delete.
  const id = (JSON.parse(window.localStorage.getItem("tm_bets_v1")).bets)[0].id;
  window.editBet(id);
  ok("editing loads the bet back into the form", $("#fbunits").value === "2");
  $("#fbunits").value = "5"; fire($("#fbunits"));
  $("#fbsave").dispatchEvent(new window.Event("click", { bubbles: true }));
  const after = JSON.parse(window.localStorage.getItem("tm_bets_v1")).bets;
  ok("an edit replaces the bet rather than adding one", after.length === 2);
  ok("...with the new stake", after.find(b => b.id === id).units === 5);

  window.deleteBet(id);
  ok("deleting removes it from the table", listRows("#mblist") === base + 1);
  ok("...and from storage",
     JSON.parse(window.localStorage.getItem("tm_bets_v1")).bets.length === 1);
}

console.log("\n── my bets: export, import, and bets with no game ──");
{
  const text = window.exportText();
  let parsed = null;
  try { parsed = JSON.parse(text); } catch (e) {}
  ok("export produces valid JSON", parsed !== null);
  ok("...containing the bets", parsed && Array.isArray(parsed.bets) && parsed.bets.length === 1);

  // Re-importing the same file must not duplicate the book. This is the whole
  // safety of "export before you clear your browser, import afterwards".
  const again = window.importBets(text);
  ok("re-importing the same file adds nothing", again.added === 0 && again.skipped === 1,
     JSON.stringify(again));

  const baseAfter = listRows("#mblist");
  const other = JSON.parse(text);
  other.bets = [{ ...other.bets[0], id: "imported1", units: 7 }];
  const r2 = window.importBets(JSON.stringify(other));
  ok("a genuinely different bet imports", r2.added === 1, JSON.stringify(r2));
  window.drawMyBets();
  ok("...and shows up", listRows("#mblist") === baseAfter + 1);

  // The clipboard route matters more than the file one on a phone, where finding a
  // downloaded .json again in a second browser is genuinely hard.
  $("#mbpaste").dispatchEvent(new window.Event("click", { bubbles: true }));
  ok("Paste in offers a box to paste a bet list into", $("#mbtext") !== null);
  const three = JSON.parse(text);
  three.bets = [{ ...three.bets[0], id: "pasted1", units: 9 }];
  $("#mbtext").value = JSON.stringify(three);
  $("#mbtextgo").dispatchEvent(new window.Event("click", { bubbles: true }));
  ok("...and pasted bets load", window.allBets().some(b => b.id === "pasted1"));
  window.eval(`BOOK.bets=BOOK.bets.filter(b=>b.id!=="pasted1");saveBook()`);
  window.drawMyBets();

  ok("a file that is not JSON is refused with a reason",
     (window.importBets("not json").error || "").length > 0);
  ok("a JSON file with no bets in it is refused too",
     (window.importBets('{"hello":1}').error || "").length > 0);

  // A bet whose game is not in this season's bundle cannot be graded. It must be
  // reported, never dropped: a log that quietly discards what it cannot use is a
  // log that is wrong and looks complete.
  window.importBets(JSON.stringify({ bets: [{ id: "orphan1", game_id: "no-such-game",
    market: "spread", team: "Nowhere", line: -3, odds: -110, units: 1 }] }));
  window.drawMyBets();
  ok("a bet with no matching game is reported, not dropped",
     $("#mbproblems").textContent.includes("not in the"), $("#mbproblems").textContent.slice(0, 90));
  window.eval(`BOOK.bets=BOOK.bets.filter(b=>b.id!=="orphan1");saveBook()`);
  window.drawMyBets();
}

console.log("\n── my bets: filters, units and the Log buttons ──");
{
  const t = window.bookTotals(window.allBets(), 0);
  const all = listRows("#mblist");
  ok("every bet is listed", all === t.n, `${all} vs ${t.n}`);
  const filterIs = (sel, value, want, name) => {
    $(sel).value = value; fire($(sel), "change");
    ok(name, listRows("#mblist") === want, `${listRows("#mblist")} vs ${want}`);
    $(sel).value = ""; fire($(sel), "change");
  };
  const B = () => window.allBets();
  filterIs("#mbmarket", "spread", B().filter(b => b.market === "spread").length,
           "the market filter narrows to spreads");
  filterIs("#mbmarket", "moneyline", B().filter(b => b.market === "moneyline").length,
           "...and to moneylines, of which there may be none");
  filterIs("#mbresult", "open", B().filter(b => !b.result).length,
           "the result filter finds unsettled bets");
  filterIs("#mbresult", "W", B().filter(b => b.result === "W").length,
           "...and winners");
  ok("clearing the filters restores every row", listRows("#mblist") === all);

  // Units are the unit of account; dollars are a display multiplication only. With
  // nothing settled there are no dollars to move, so that half is asserted only when
  // there is something to assert -- a test that passes because both sides are "$0"
  // is not testing the multiplication.
  const unitsText = $("#mbcards").textContent.match(/[-+]?\d+\.\d\du risked/);
  $("#unitsize").value = "250"; fire($("#unitsize"));
  const rich = $("#mbcards").textContent;
  $("#unitsize").value = "10"; fire($("#unitsize"));
  if (t.units_won) {
    ok("changing the unit size changes the dollars", rich !== $("#mbcards").textContent);
    ok("...but never the units",
       !unitsText || $("#mbcards").textContent.includes(unitsText[0]));
  } else {
    ok("with nothing settled the dollar figure is honestly absent",
       !$("#mbcards").textContent.includes("$"), $("#mbcards").textContent.slice(0, 120));
  }
  $("#unitsize").value = "50"; fire($("#unitsize"));

  // The Log button on the bets board is the shortest path from "the model likes
  // this" to "it is in my book", so it has to actually arrive filled in.
  $$("#nav button").find(b => b.dataset.v === "bets")
    .dispatchEvent(new window.Event("click", { bubbles: true }));
  const logBtn = $("#bets button[data-log]");
  ok("the bets board offers a Log button per row", logBtn !== null);
  if (logBtn) {
    const want = logBtn.dataset.log;
    logBtn.dispatchEvent(new window.Event("click", { bubbles: true }));
    ok("...which jumps to the bet form", $("#v-mybets").classList.contains("on"));
    ok("...on the right game", $("#fbgame").value === want,
       `${$("#fbgame").value} vs ${want}`);
    ok("...with the side it recommended selected",
       $("#fbside .chip[aria-pressed=\"true\"]") !== null);
    ok("...and only one tab marked selected",
       $$("#nav button[aria-selected=\"true\"]").length === 1);
  }
  const schedLog = $("#schedtbl button[data-log]");
  ok("the schedule offers one too, for games off the board", schedLog !== null);
}

console.log("\n── grade freshness ──");
ok("the header names the tabs the grades came from",
   ($("#meta").textContent || "").includes("sheet read"), $("#meta").textContent.slice(0, 80));
{
  const B = JSON.parse(bundle);
  const live = B.grades_sync?.tabs?.some(t => t.kind === "live tab");
  ok("the live tab is read, or the page says it is missing",
     live ? !$("#meta").textContent.includes("no live tab")
          : $("#meta").innerHTML.includes("no live tab"),
     `live tab present: ${live}`);
}

console.log("\n── best bets ──");
ok("bet cards render", $$("#betcards .card").length === 6, String($$("#betcards .card").length));
const mlRows = rows("#bets");
ok("moneyline table has rows", mlRows > 0, `got ${mlRows}`);
$("#sortby").value = "spread"; fire($("#sortby"));
const spreadAll = rows("#bets");
ok("spread mode re-renders", spreadAll > 0, `got ${spreadAll}`);
ok("spread mode says sizing does not apply",
   !$("#sizenote").hasAttribute("hidden") &&
   $("#sizenote").textContent.includes("does not apply"));

// Row counts are only comparable WITHIN a mode — the two modes filter on
// different fields (ml_ev vs spread_edge), so a cross-mode comparison proves
// nothing. Filter inside spread mode and check it is a strict subset.
$("#wk").value = "1"; fire($("#wk"));
const wk1 = rows("#bets");
ok("week filter is a subset of the mode", wk1 > 0 && wk1 <= spreadAll, `${wk1} of ${spreadAll}`);

// A week with no priced spreads must show the empty state, not last week's rows.
// Week 8 was hard-coded here and stopped being empty the moment books posted lines
// that far out, so the week is derived from the bundle instead. Which branch ran is
// stated, because an assertion that quietly stops applying is worse than no
// assertion at all.
{
  const offered = $$("#wk option").map(o => o.value).filter(Boolean);
  const BUN = JSON.parse(bundle);
  const priced = w => BUN.bets.filter(b => String(b.week) === w && b.spread_edge != null).length;
  const emptyWeek = offered.find(w => priced(w) === 0);
  if (emptyWeek) {
    $("#wk").value = emptyWeek; fire($("#wk"));
    ok("a week with no priced spreads shows the empty state",
       $("#bets .empty") !== null, `week ${emptyWeek}`);
  } else {
    ok("every offered week has priced spreads, so none can be empty",
       offered.every(w => priced(w) > 0), `${offered.length} weeks, all priced`);
  }
}

$("#wk").value = ""; fire($("#wk"));
$("#sortby").value = "ml"; fire($("#sortby"));
const stakeBefore = $("#bets").textContent;
$("#bank").value = "5000"; fire($("#bank"));
ok("bankroll change actually changes the stakes",
   $("#bets").textContent !== stakeBefore);
$("#bank").value = "1000"; fire($("#bank"));

console.log("\n── stake sizing ──");
// Read the money out of the table rather than trusting a summary card — the card and
// the column are computed from the same map, so a card alone would agree with itself.
const stakeRows = () => {
  const cW = col("#bets", "Wk"), cS = col("#bets", "Stake");
  return $$("#bets tbody tr")
    .map(tr => ({ week: cellText(tr, cW), stake: cellText(tr, cS) }))
    .filter(r => r.stake && r.stake !== "—")
    .map(r => ({ week: r.week, stake: +r.stake.replace(/[$,]/g, "") }));
};
const totalStaked = () => stakeRows().reduce((s, r) => s + r.stake, 0);
const near = (a, b, tol = 0.05) => Math.abs(a - b) < tol;

$("#sizing").value = "kelly"; fire($("#sizing"), "change");
ok("weekly box is disabled in Kelly mode", $("#weekly").disabled);
const kellyTotal = totalStaked();
const nPlays = stakeRows().length;
ok("every positive-EV play is sized", nPlays > 0 && stakeRows().every(r => r.stake > 0),
   `${nPlays} plays`);
// The whole reason the sizing bug was invisible: a $0 stake renders as an em dash and
// looks exactly like "this game has no moneyline". Positive EV and no stake is a
// contradiction, so assert the two agree.
const posEvCount = JSON.parse(bundle).bets.filter(b => (b.ml_ev || 0) > 0).length;
ok("sized plays match the positive-EV count", nPlays === posEvCount,
   `${nPlays} sized vs ${posEvCount} with EV > 0`);

const playWeeks = new Set(stakeRows().map(r => r.week));
ok("plays span more than one week (needed to test per-week budgeting)",
   playWeeks.size > 1, `${playWeeks.size} week(s)`);

$("#weekly").value = "200";
$("#sizing").value = "weekly"; fire($("#sizing"), "change");
ok("weekly box is enabled in budget mode", !$("#weekly").disabled);
ok("budget is allocated PER WEEK, not once overall",
   near(totalStaked(), 200 * playWeeks.size),
   `staked ${totalStaked().toFixed(2)}, expected ${200 * playWeeks.size}`);

// The summary card and the column must be the SAME number to the cent. They are
// computed from one map, so this is really a rounding test: round on the way out and
// seven $28.57 rows sit under a $400.00 total that does not add up.
const stakedCard = $$("#betcards .card").find(c => c.querySelector(".k")?.textContent === "Staked");
// Compare NUMBERS, not strings. The card is rendered with toLocaleString, so it
// grows a thousands separator above $999.99 and a string match then fails on two
// values that are equal — which is exactly what happened once the budget spanned
// fourteen live weeks.
const cardAmount = +(stakedCard?.querySelector(".v").textContent || "")
  .replace(/[$,]/g, "");
ok("the Staked card equals the column, to the cent",
   near(cardAmount, totalStaked(), 0.005),
   `card ${cardAmount.toFixed(2)} vs column ${totalStaked().toFixed(2)}`);

// The card sums a Map keyed by game_id; the column sums rendered rows. They can
// only disagree if a game appears on the board TWICE — one map entry, two rows —
// which would also double-count that game's edge in every summary above it.
{
  const ids = JSON.parse(bundle).bets.map(b => b.game_id);
  const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
  ok("no game appears on the bets board twice",
     dupes.length === 0, `${dupes.length} duplicated, e.g. ${dupes[0] || "-"}`);
}

// Kelly-weighted and flat must differ wherever a week's edges differ. If they agree
// everywhere the mode switch is decorative.
const byWeek = m => { const o = {}; stakeRows().forEach(r => (o[r.week] ??= []).push(r.stake)); return o; };
const weightedByWeek = byWeek();
$("#sizing").value = "flat"; fire($("#sizing"), "change");
const flatByWeek = byWeek();
ok("flat mode still spends the same budget",
   near(totalStaked(), 200 * playWeeks.size), `staked ${totalStaked().toFixed(2)}`);
ok("flat mode gives every play in a week the same stake",
   Object.values(flatByWeek).every(v => v.every(x => near(x, v[0]))));
const differs = Object.keys(flatByWeek).some(w =>
  weightedByWeek[w].some((x, i) => !near(x, flatByWeek[w][i])));
ok("Kelly-weighted differs from flat where the edges differ", differs);

// The over-betting multiple is the one number this UI must not soften.
$("#sizing").value = "weekly"; fire($("#sizing"), "change");
const multCard = $$("#betcards .card").find(c => c.querySelector(".k")?.textContent === "vs Kelly");
ok("a vs-Kelly card is shown", !!multCard);
const mult = parseFloat(multCard.querySelector(".v").textContent);
ok("the multiple equals budget ÷ what Kelly wanted",
   near(mult, (200 * playWeeks.size) / kellyTotal, 0.02),
   `card says ${mult}, computed ${((200 * playWeeks.size) / kellyTotal).toFixed(2)}`);
ok("over-betting raises a visible warning",
   mult > 1.05 && !$("#sizenote").hasAttribute("hidden") &&
   $("#sizenote").textContent.includes("Kelly advises"));

// …and must go away when the budget is genuinely conservative. A warning that is
// always on is the same as no warning.
$("#weekly").value = String(Math.max(1, Math.floor(kellyTotal / playWeeks.size / 4)));
fire($("#weekly"));
ok("a conservative budget clears the warning", $("#sizenote").hasAttribute("hidden"),
   $("#sizenote").textContent.slice(0, 60));

$("#weekly").value = "200"; fire($("#weekly"));
$("#sizing").value = "kelly"; fire($("#sizing"), "change");
ok("returning to Kelly restores the Kelly total", near(totalStaked(), kellyTotal));
ok("preferences are persisted", (window.localStorage.getItem("tm_bet_prefs") || "").includes("kelly"));

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
// roster_watch is a separate job that hits ESPN 138 times, and it deliberately does
// not run on the fast refresh path. So "no alerts file" is a legitimate state, and
// asserting rows unconditionally turned that into a red build that blocked a deploy
// for a file the job was never going to produce. Assert the branch that applies --
// and never let "absent" pass silently as if the board were simply quiet.
{
  const wl = JSON.parse(bundle).alerts?.watchlist;
  ok(wl ? "watchlist renders" : "no alerts file, and the page says so rather than showing an empty board",
     wl ? rows("#watchtbl") > 0 : ($("#alertnote").textContent || "").length > 20,
     wl ? `got ${rows("#watchtbl")}` : $("#alertnote").textContent.slice(0, 60));
}
ok("alert note explains empty board", ($("#alertnote").textContent||"").length > 20);

console.log("\n── tab navigation ──");
for (const b of $$("#nav button")) {
  b.dispatchEvent(new window.Event("click", { bubbles: true }));
  const v = $("#v-" + b.dataset.v);
  ok(`tab "${b.textContent}" shows its view`,
     v.classList.contains("on") && $$(".view.on").length === 1);
}

console.log("\n── pipeline health ──");
{
  // Absence of news is not good news: a stale bundle looks completely normal, and
  // did for ten days. These two counts are the ones that cannot look healthy while
  // the pipeline is behind, so assert the page reacts to them rather than only
  // carrying them.
  const H = JSON.parse(bundle).health;
  ok("the bundle reports pipeline health", H && typeof H.ungraded_finals === "number",
     JSON.stringify(H));
  const txt = $("#banner").textContent;
  ok("a backlog of ungraded finals is announced",
     !H.ungraded_finals || txt.includes("not graded yet"), `${H.ungraded_finals} ungraded`);
  ok("games that kicked off unlocked are announced",
     !H.missed_locks || txt.includes("no pick"), `${H.missed_locks} missed`);
  ok("a healthy pipeline says nothing about it",
     (H.ungraded_finals || H.missed_locks) || !txt.includes("not graded yet"));
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

  const disp2 = s => w2.getComputedStyle(q2(s)).display;
  ok("gate is shown for an encrypted bundle", !q2("#gate").hasAttribute("hidden"));
  // The guard is `display:none!important`, so it is worth proving it does not hide the
  // gate when the gate is supposed to be up. An !important rule that over-applies would
  // lock Grant out of his own site, which is a worse failure than the one it fixed.
  ok("the gate is genuinely visible when it is up", disp2("#gate") === "grid",
     `computed: ${disp2("#gate")}`);
  ok("app stays hidden before unlock", q2("#app").hasAttribute("hidden"));
  ok("app is genuinely not displayed before unlock", disp2("#app") === "none");
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
  // THE REPORTED BUG, in its own words: the passphrase worked, the app booted
  // underneath, and the "Unlock" box never went away. `hidden` was set correctly the
  // whole time; the cascade simply ignored it.
  ok("the gate disappears once unlocked", disp2("#gate") === "none",
     `computed: ${disp2("#gate")}`);
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
