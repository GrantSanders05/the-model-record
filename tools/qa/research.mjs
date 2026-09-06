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
    // One bundle, served in the clear -- the only path there is now.
    win.fetch = async () => ({ ok: true, json: async () => JSON.parse(bundle) });
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
ok("nav is visible", !$("#nav").hasAttribute("hidden"));

// Asserting the ATTRIBUTE is not the same as asserting the element is gone, and the
// difference shipped once: `[hidden]{display:none}` is a user-agent rule, origin
// beats specificity, so an author `display` rule outranked it and a hidden element
// stayed painted over a booted app forever. Every `hidden` assertion passed the
// whole time. Check what the cascade actually computes.
const disp = s => window.getComputedStyle($(s)).display;
ok("app is really displayed", disp("#app") !== "none", `computed: ${disp("#app")}`);
// jsdom resolves this collision by specificity only, so it would not catch
// `nav{display:flex}` — which real Chrome confirmed loses to the guard. The
// stylesheet-level rule is what covers the whole class, so assert it is present.
ok("stylesheet forces [hidden] to outrank author display rules",
   /\[hidden\]\s*\{\s*display\s*:\s*none\s*!important/.test(html));
ok("meta populated", ($("#meta").textContent || "").includes("CFB"), $("#meta").textContent);
// Naming them beats counting them: a count passes just as happily when a tab has
// been renamed into nonsense or two tabs point at the same view.
{
  const want = ["Rankings","Schedule","Best bets","Results","My bets",
                "Team","Line movement","What-if","Roster news","Model lab"];
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

// Columns are found by their heading, never by position. Two of these tests
// read a cell out of the rankings row, and both broke the day a column was
// inserted to their left -- reporting "conference filter is broken" and "QB
// sort is NaN" when the filter and the sort were fine and the test was reading
// the wrong cell. A heading is what the page promises; an index is an accident
// of layout.
const colIdx = (table, heading) =>
  $$(`${table} thead th`).findIndex(th =>
    th.textContent.replace(/[▲▼]/g, "").trim() === heading);

ok("every rankings column the tests read is present",
   ["Conference", "QB", "W-L", "Win Pts", "Loss Pts", "Total"]
     .every(h => colIdx("#ranktbl", h) >= 0),
   ["Conference", "QB", "W-L", "Win Pts", "Loss Pts", "Total"]
     .filter(h => colIdx("#ranktbl", h) < 0).join(",") || "all present");

// Drive the filter off whatever conferences the bundle actually has, so this
// keeps working through realignment instead of hard-coding this year's names.
const someConf = $$("#confchips .chip")[0]?.dataset.c;
const CONF_COL = colIdx("#ranktbl", "Conference");
const confSize = someConf
  ? [...$$("#ranktbl tbody tr")].filter(tr =>
      tr.children[CONF_COL].textContent.includes(someConf)).length
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
const QB_COL = colIdx("#ranktbl", "QB");
const qbCells = () => $$("#ranktbl tbody tr").map(tr => parseFloat(tr.children[QB_COL].textContent));
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

// ── the record and the points a team's results earned ────────────────────────
//
// The rankings table totalled a team from the spreadsheet's hand-entered
// Win/Loss Points columns while the engine rated it from quality accrued off
// real games. Both produce a number, so nothing was visibly wrong; the page was
// simply several rating points adrift of the picks beside it, and said in a
// footnote that the two were the same.
{
  const B = JSON.parse(bundle);
  $("#rreset").dispatchEvent(new window.Event("click", { bubbles: true }));
  const WL_COL = colIdx("#ranktbl", "W-L");
  const WP_COL = colIdx("#ranktbl", "Win Pts");
  const LP_COL = colIdx("#ranktbl", "Loss Pts");
  const TOT_COL = colIdx("#ranktbl", "Total");
  const cellsFor = team => {
    const tr = $$("#ranktbl tbody tr").find(r => r.children[1].textContent.trim() === team);
    return tr ? [...tr.children].map(td => td.textContent.trim()) : null;
  };

  // Every team carries a record, and it is the bundle's, not a guess.
  const anyTeam = Object.keys(B.teams)[0];
  const rec0 = B.teams[anyTeam].record;
  ok("every team ships a record", rec0 && typeof rec0.wins === "number",
     JSON.stringify(rec0));
  ok("the W-L column shows it",
     cellsFor(anyTeam)?.[WL_COL] === `${rec0.wins}-${rec0.losses}`,
     `${cellsFor(anyTeam)?.[WL_COL]} vs ${rec0.wins}-${rec0.losses}`);

  // A team that has not played reads 0-0 rather than blank: 0-0 is a fact, and
  // an em dash would say "we do not know".
  const unplayed = Object.entries(B.teams)
    .find(([, v]) => !v.record.wins && !v.record.losses)?.[0];
  if (unplayed) {
    ok("an unplayed team reads 0-0, not blank", cellsFor(unplayed)?.[WL_COL] === "0-0",
       cellsFor(unplayed)?.[WL_COL]);
  } else {
    ok("an unplayed team reads 0-0, not blank", true, "every team has played");
  }

  // A team that HAS played shows the points its results earned.
  const charged = Object.entries(B.teams)
    .find(([, v]) => v.record.loss_points)?.[0];
  if (charged) {
    const c = cellsFor(charged), r = B.teams[charged].record;
    ok("loss points are shown where they were charged",
       parseFloat(c[LP_COL]) === r.loss_points, `${c[LP_COL]} vs ${r.loss_points}`);
    ok("...and the total already contains them",
       parseFloat(c[TOT_COL]) === B.teams[charged].total,
       `${c[TOT_COL]} vs ${B.teams[charged].total}`);
  } else {
    ok("loss points are shown where they were charged", true, "nobody has lost yet");
    ok("...and the total already contains them", true, "nobody has lost yet");
  }

  const credited = Object.entries(B.teams).find(([, v]) => v.record.win_points)?.[0];
  ok("win points are shown where they were credited",
     !credited || parseFloat(cellsFor(credited)[WP_COL]) === B.teams[credited].record.win_points,
     credited ? `${cellsFor(credited)[WP_COL]} vs ${B.teams[credited].record.win_points}` : "no ranked wins yet");

  // The footnote has to describe the formula that actually ran. The old one
  // named the spreadsheet arithmetic whatever the config said.
  const note = $("#ranknote").textContent;
  ok("the footnote names the live formula",
     B.config.formula === "computed"
       ? /taken from the rating engine itself/.test(note)
       : /hand-entered/.test(note), note.slice(0, 90));

  // The quality half enters the total at its own fitted weight. A note that lists
  // "+5 for a top-5 win" while the total moves by 2.3 is describing arithmetic the
  // page is not doing -- the same defect as the sentence above, one layer down.
  const qs = B.config.quality_scale == null ? 1 : +B.config.quality_scale;
  ok("the footnote says the weight when quality points are not counted 1-for-1",
     Math.abs(qs - 1) < 1e-9 ? !/enter the TOTAL at/.test(note)
                             : /enter the TOTAL at ×/.test(note),
     `quality_scale ${qs}`);
  if (Math.abs(qs - 1) >= 1e-9) {
    const want = (B.config.quality_rule.wq_top5 * qs).toFixed(1).replace(/\.0$/, "");
    ok("...and states what a top-5 win is actually worth in rating",
       note.includes(want), `looking for ${want} in the note`);
  }

  // And the caption must not print the Team Data sentinel as a week number.
  ok("the grades caption never says week 99", !/week 99/.test($("#ranksub").textContent),
     $("#ranksub").textContent);
}

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

  // Records ride on the shared matchup cell, so this covers the bets board and
  // the three results tables as well as the schedule. Only teams that have
  // played carry one — an 0-0 tag on every name in week one would be noise.
  {
    const played = Object.entries(B.teams)
      .filter(([, v]) => v.record.wins || v.record.losses).map(([t]) => t);
    if (played.length) {
      const withRec = $$("#schedtbl tbody td.mu")
        .filter(td => td.querySelector(".rec")).length;
      ok("a played team's record shows beside its name in a matchup",
         withRec > 0, `${withRec} matchup cells carry a record`);
      const tags = $$("#schedtbl tbody td.mu .rec").map(e => e.textContent.trim());
      ok("...and it reads as a record, not a stray number",
         tags.every(t => /^\d+-\d+$/.test(t)), tags.slice(0, 5).join(" "));
    } else {
      ok("a played team's record shows beside its name in a matchup", true,
         "no games played yet");
      ok("...and it reads as a record, not a stray number", true, "no games played yet");
    }
  }

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

console.log("\n── the board leads with the market it has an edge in ──");
{
  // The moneyline board was measured at -2.5% ROI over 720 bets in 2025, and worse
  // the bigger the claimed edge. It topped the board with a +196% play on a
  // 20-point underdog because win probability came from the RAW model margin. The
  // spread board is where the measured edge is, so that is what opens.
  // Assert the MARKUP's default, not the live value — earlier sections in this file
  // drive the control, so reading it here would test the test.
  ok("the board opens on spread disagreement, not moneyline EV",
     $$("#sortby option")[0].value === "spread",
     $$("#sortby option").map(o => o.value).join(", "));
  $("#sortby").value = "ml"; fire($("#sortby"), "change");
  const w = $("#sizenote");
  ok("the moneyline view states its measured record", !w.hasAttribute("hidden") &&
     /no measured edge/i.test(w.textContent), w.textContent.slice(0, 70));
  ok("...with the number, not just a caution", /720 bets/.test(w.textContent));
  $("#sortby").value = "spread"; fire($("#sortby"), "change");

  // Probability and EV must come from the SHRUNK margin. Left raw, a game the model
  // makes a 9-point dog and the market makes a 20-point dog priced at +196% EV.
  const B2 = JSON.parse(bundle);
  ok("the bundle carries the realised-edge share",
     typeof B2.config.edge_realised === "number" && B2.config.edge_realised <= 1,
     String(B2.config.edge_realised));
  const evs = B2.bets.map(b => b.ml_ev).filter(v => v != null);
  ok("no moneyline play claims a three-figure edge",
     evs.every(v => v < 100), `max ${Math.max(...evs).toFixed(1)}%`);
  const withEdge = B2.bets.filter(b => b.spread_edge != null && b.realised_edge != null);
  ok("every priced game reports what its edge is worth", withEdge.length > 0);
  ok("...and worth is a fraction of the raw gap",
     withEdge.every(b => Math.abs(b.realised_edge) <= Math.abs(b.spread_edge) + 1e-9));
}

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

// Staking lives on the moneyline view, so select it explicitly rather than relying
// on whatever the previous section left behind — the default moved and this section
// blew up on a missing Stake column.
$("#sortby").value = "ml"; fire($("#sortby"), "change");
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
// always on is the same as no warning. The moneyline view now always carries its
// measured record, so what has to disappear is the OVER-BETTING warning, not the
// panel — the two say different things and only one is conditional.
$("#weekly").value = String(Math.max(1, Math.floor(kellyTotal / playWeeks.size / 4)));
fire($("#weekly"));
ok("a conservative budget clears the over-betting warning",
   !$("#sizenote").textContent.includes("Kelly advises"),
   $("#sizenote").textContent.slice(0, 60));
ok("...while the measured moneyline record stays put",
   $("#sizenote").textContent.includes("no measured edge"));

$("#weekly").value = "200"; fire($("#weekly"));
$("#sizing").value = "kelly"; fire($("#sizing"), "change");
ok("returning to Kelly restores the Kelly total", near(totalStaked(), kellyTotal));
ok("preferences are persisted", (window.localStorage.getItem("tm_bet_prefs") || "").includes("kelly"));

console.log("\n── team ──");
ok("team select populated", $$("#team option").length === 138);
// By the labels they carry, not by how many there happen to be — a count is a
// test that fails when a card is ADDED, which is the wrong thing to defend.
const cardLabels = () => $$("#teamcards .card .k").map(k => k.textContent.trim());
ok("team cards render",
   ["National rank", "Record", "Quality points", "QB", "Total"]
     .every(l => cardLabels().includes(l)), cardLabels().join(","));
const effHas = $("#effchart").innerHTML.length > 50;
ok("efficiency chart or honest empty", effHas);
$("#team").value = "Alabama"; fire($("#team"));
ok("switching team re-renders", cardLabels().includes("Total"), cardLabels().join(","));

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

/* ── the model lab (§27.4) ────────────────────────────────────────────
   The four scoreboards exist so that a good model and a bad staking plan cannot
   cancel out into one flattering number. A test that only asked "does the table
   render" would pass just as happily if the page added them together, so what is
   asserted here is that they stay APART, that the numbers on screen are the
   numbers in the bundle, and that a challenger with no prospective record says
   zero rather than borrowing the Champion's. */
console.log("\n── model lab: four scoreboards, kept apart ──");
{
  const V = JSON.parse(bundle).v2;
  ok("the bundle carries a V2 block", !!V && V.available === true);

  const cards = $$("#labboards .card");
  ok("four scoreboards, one card each", cards.length === 4, String(cards.length));
  const keys = cards.map(c => c.querySelector(".k").textContent.trim());
  ok("each scoreboard is named for the question it answers",
     JSON.stringify(keys) === JSON.stringify(
       ["Forecast quality","Signals · locked line","Close diagnostic","Your bets"]),
     keys.join(" | "));
  // The whole point of four boards is that no fifth number claims to combine
  // them. A "total" or "overall" card is the failure this section prevents.
  ok("CONTROL: nothing on the board is a combined total",
     !/\b(overall|combined|total record|net)\b/i.test($("#labboards").textContent),
     $("#labboards").textContent.slice(0,80));
  ok("user bets are shown as separate, not as a record",
     ($("#labboards").textContent || "").includes("never mixed"));

  // Every strategy version that ever published, with the numbers from the bundle
  // and not from an average of them.
  const strats = V.strategies || [];
  ok("every strategy version has a row", listRows("#labstrats") === strats.length,
     `${listRows("#labstrats")} rows for ${strats.length} strategies`);
  if (strats.length) {
    const t = strats[0], g = t.signals;
    const row = [...$$("#labstrats tbody tr")]
      .find(tr => tr.textContent.includes(t.strategy_version));
    ok("the strategy row is present and named", !!row, t.strategy_version);
    ok("...and prints the locked record from the bundle, unrounded",
       row.textContent.includes(`${g.locked_w}–${g.locked_l}`),
       row.textContent.replace(/\s+/g," ").slice(0,90));
    ok("...and its signal count",
       row.querySelectorAll("td")[1].textContent.trim() === String(g.n),
       row.querySelectorAll("td")[1].textContent);
    // A blank ROI is the honest output when no price was recorded. A 0% or a
    // −110 assumption here would be the exact thing §2.5 forbids.
    if (g.roi == null)
      ok("no price recorded, so ROI is explained rather than invented",
         /no prices/i.test(row.textContent) && !/%\s*ROI|\bROI 0/.test(row.textContent),
         row.textContent.slice(-70));
  }
  ok("break-even is stated so a % can be read against something",
     ($("#labboardnote").textContent || "").includes("52.38"));
}

console.log("\n── model lab: challengers, not ranked by win rate ──");
{
  const V = JSON.parse(bundle).v2;
  const models = V.models || [];
  ok("every registered model has a row", listRows("#labmodels") === models.length,
     `${listRows("#labmodels")} rows for ${models.length} models`);

  const first = $("#labmodels tbody tr");
  ok("the Champion is the first row", first.textContent.includes(V.champion),
     first.textContent.replace(/\s+/g," ").slice(0,70));
  // §27.4: do not sort challengers by ATS percentage by default. The ordering is
  // role, then version -- assert the ROLE ordering directly, because a table that
  // happens to be in the right order for one bundle is not an ordering rule.
  {
    const rank = { Champion:0, Baseline:1, Shadow:2, Retired:3 };
    const seen = $$("#labmodels tbody tr")
      .map(tr => tr.querySelectorAll("td")[2].textContent.trim())
      .map(r => rank[r]);
    ok("ordered by role — Champion, baseline, shadow, retired",
       seen.every((v, i) => i === 0 || v >= seen[i-1]) && seen.every(v => v != null),
       seen.join(","));
  }
  // A model registered this week has forecast nothing that has finished. Zero is
  // the honest number and the table must print it rather than leaving the cell
  // to be read as "not measured".
  {
    const zero = models.filter(m => !(m.quality && m.quality.n));
    if (zero.length) {
      const tr = [...$$("#labmodels tbody tr")]
        .find(x => x.textContent.includes(zero[0].model_version));
      ok("a model with no graded forecast shows 0, not a blank",
         tr.querySelectorAll("td")[3].textContent.trim() === "0",
         tr.querySelectorAll("td")[3].textContent);
    }
  }
  ok("every challenger row says it is shadow only",
     $$("#labmodels tbody tr")
       .filter(tr => tr.querySelectorAll("td")[2].textContent.trim() === "Shadow")
       .every(tr => /shadow only/i.test(tr.textContent)));
  ok("the note explains why CLV is not in this table",
     /CLV/.test($("#labmodelnote").textContent) &&
     /shadow forecast/.test($("#labmodelnote").textContent));
}

console.log("\n── model lab: the decision policy is on the page ──");
{
  const V = JSON.parse(bundle).v2;
  const txt = $("#labstrategy").textContent || "";
  ok("every strategy rule is listed",
     Object.keys(V.strategy_config || {}).every(k => txt.includes(k)),
     Object.keys(V.strategy_config || {}).filter(k => !txt.includes(k)).join(","));
  ok("...including the hash, so a changed rule is visible",
     txt.includes(V.strategy_hash.slice(0, 16)));
  // Booleans are the ones that decide whether a market is offered at all, so
  // they must read as words rather than as "true"/"false" stringified objects.
  ok("a switched-off market reads as a word, not a raw boolean",
     !/\b(true|false)\b/.test(txt), txt.slice(0, 90));

  const dr = V.declined_reasons || {};
  ok("every decline reason has a row", listRows("#labdeclined") === Object.keys(dr).length,
     `${listRows("#labdeclined")} rows for ${Object.keys(dr).length} reasons`);
  ok("...and every one is glossed in English, not left as a code",
     $$("#labdeclined tbody tr").every(tr =>
       (tr.querySelectorAll("td")[2].textContent || "").trim().length > 8));
  for (const k of Object.keys(dr)) {
    const tr = [...$$("#labdeclined tbody tr")].find(x => x.textContent.includes(k));
    ok(`decline reason ${k} shows its count`,
       tr && tr.querySelectorAll("td")[1].textContent.trim() === String(dr[k]));
  }
}

console.log("\n── model lab: the shadow inputs say they adjust nothing ──");
{
  const V = JSON.parse(bundle).v2;
  const av = $("#labavail").textContent || "";
  ok("the availability layer is described", av.length > 40, av.slice(0, 60));
  // The layer records a status stream and moves no model. A panel that showed
  // counts without saying so would read as an input the model uses.
  ok("...and says outright that it adjusts nothing", /adjusts nothing/i.test(av));
  ok("...and names the tiers that could ever be eligible",
     (V.availability?.auto_adjust_eligible_tiers || []).every(t => av.includes(String(t))));
  if (!(V.availability?.observations)) {
    // An empty stream and a broken feed look identical. The timestamp is the
    // only thing that separates them, so the panel must not read as "all clear".
    ok("an empty stream is explained, not presented as a clean bill of health",
       /expected answer|broken feed/i.test(av), av.slice(0, 80));
  }

  const wx = $("#labwx").textContent || "";
  ok("the weather layer is described", wx.length > 40, wx.slice(0, 60));
  // §23 names wind as the variable that matters and this source has none.
  // A panel showing a temperature without that sentence would overclaim.
  ok("...and states the wind gap on the page, not only in a docstring",
     /wind is missing/i.test(wx), wx.slice(0, 90));
  ok("...and reports how many snapshots actually carry wind",
     wx.includes(String(V.weather?.with_wind ?? 0)));
  ok("...and claims no model adjustment", /adjusts a model|adjusts nothing/i.test(wx));
}

console.log("\n── model lab: the methodology transition is stated (§27.5) ──");
{
  const V = JSON.parse(bundle).v2;
  const m = $("#labmethod").textContent || "";
  ok("the page explains the legacy close-based grading", /closing line/i.test(m));
  ok("...and that the side used to be recomputed", /re-derived|recomputed/i.test(m));
  ok("...and that it is now the published side at the locked line",
     /published, at the\s+line it was locked at|locked at/i.test(m.replace(/\s+/g," ")));
  ok("...and that the close is kept as a separate diagnostic",
     /separate diagnostic/i.test(m));
  ok("...and that legacy is preserved rather than overwritten", /preserved/i.test(m));
  ok("...and never added to the new one", /never added together/i.test(m));
  ok("the champion version is named", m.includes(V.champion));
  ok("promotion is described as a decision, not a threshold",
     /never by a threshold/i.test(m));
}

/* CONTROL. A missing V2 block must announce itself. The failure this guards
   against is the one that keeps recurring in this repo: a reader with no writer,
   rendering a clean empty table that is indistinguishable from a quiet week. */
console.log("\n── model lab: CONTROL, an absent V2 block cannot look like a quiet week ──");
{
  const broken = JSON.parse(bundle);
  broken.v2 = { available: false, why: "ImportError: no module named metrics_v2" };
  const d4 = new JSDOM(html, {
    runScripts: "dangerously", virtualConsole: new VirtualConsole(),
    url: "https://example.test/research/",
    beforeParse(win) {
      win.fetch = async () => ({ ok: true, json: async () => broken });
      win.matchMedia = () => ({ matches:false, addEventListener(){}, removeEventListener(){} });
    },
  });
  await wait(400);
  const w4 = d4.window;
  const note = w4.document.querySelector("#labboardnote").textContent || "";
  ok("CONTROL: the page says the V2 record is missing", /not in this bundle/i.test(
     w4.document.querySelector("#labmodels").textContent));
  ok("CONTROL: ...naming the reason it could not be read",
     w4.document.querySelector("#labmodels").textContent.includes("ImportError"));
  ok("CONTROL: ...and warns that this is not “no challengers ran”",
     /not the same as/i.test(note), note.slice(0, 70));
  ok("CONTROL: no scoreboard card is drawn from an unreadable block",
     w4.document.querySelectorAll("#labboards .card").length === 0);
}

/* The sign convention on "vs market" is inverted relative to the rest of the
   page -- a NEGATIVE number is the model doing better -- so it is coloured by
   meaning. Getting that backwards would paint a winning model red for a season
   and nobody would query it, because the number would still be correct. */
console.log("\n── model lab: a better-than-market model is not painted as a loss ──");
{
  const tweaked = JSON.parse(bundle);
  tweaked.v2.models = [
    { model_version:"C0-x", model_id:"champion-grade", role:"champion",
      experiment_id:null, quality:{ n:40, mae:12.1, paired_delta:-0.44, paired_n:40, bias:0.2 } },
    { model_version:"E009-x", model_id:"residual-grade", role:"challenger",
      experiment_id:"E009", quality:{ n:40, mae:13.9, paired_delta:1.30, paired_n:40, bias:-0.1 },
      vs_champion:{ champion:"C0-x", challenger:"E009-x", paired_n:40, mean_improvement:-1.80 } },
  ];
  const d5 = new JSDOM(html, {
    runScripts:"dangerously", virtualConsole:new VirtualConsole(),
    url:"https://example.test/research/",
    beforeParse(win){
      win.fetch = async () => ({ ok:true, json:async()=>tweaked });
      win.matchMedia = () => ({matches:false,addEventListener(){},removeEventListener(){}});
    },
  });
  await wait(400);
  const w5 = d5.window;
  const trs = [...w5.document.querySelectorAll("#labmodels tbody tr")];
  const champ = trs.find(t => t.textContent.includes("C0-x"));
  const chall = trs.find(t => t.textContent.includes("E009-x"));
  ok("a model closer than the market is marked good",
     champ.querySelectorAll("td")[5].classList.contains("pos"),
     champ.querySelectorAll("td")[5].className);
  ok("CONTROL: a model further from the market than the market is not",
     chall.querySelectorAll("td")[5].classList.contains("neg"),
     chall.querySelectorAll("td")[5].className);
  ok("a challenger worse than the Champion is marked as such",
     chall.querySelectorAll("td")[7].classList.contains("neg"));
  ok("...and the paired count travels with the number",
     chall.querySelectorAll("td")[7].textContent.includes("(40)"),
     chall.querySelectorAll("td")[7].textContent);
  // Sorted by role, so the Champion leads even though the challenger would
  // outrank it on nothing at all -- the ordering must not depend on the numbers.
  ok("the Champion still leads a table where it is not the best row",
     trs[0].textContent.includes("C0-x"));
}

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

console.log("\n── stale locks are announced ──");
{
  const B = JSON.parse(bundle);
  ok("the bundle carries the stale-lock count", B.health && "stale_locks" in B.health,
     JSON.stringify(B.health));
  ok("a healthy pipeline reports zero of them", B.health.stale_locks === 0,
     `${B.health.stale_locks} stale`);

  // A count that is only ever zero proves nothing. Load a SECOND copy of the page
  // against a doctored bundle and require the banner to appear -- this is the
  // check the August lock bug needed and did not have: for a month the count
  // would have been 861 and no surface anywhere would have said so.
  const doctored = JSON.parse(bundle);
  doctored.health.stale_locks = 7;
  const probe = new JSDOM(html, {
    runScripts: "dangerously", virtualConsole: new VirtualConsole(),
    url: "https://example.test/research/",
    beforeParse(win) {
      win.fetch = async () => ({ ok: true, json: async () => doctored });
      win.matchMedia = () => ({ matches: false, addEventListener(){}, removeEventListener(){} });
    },
  });
  await wait(400);
  const txt = probe.window.document.body.textContent;
  ok("...and a non-zero count raises a banner on the page",
     /7 picks locked far ahead of kickoff/.test(txt),
     txt.slice(0, 120).replace(/\s+/g, " "));
  ok("...that says how to clear it", /void_picks/.test(txt));
  probe.window.close();
}

console.log("\n── the board says WHY it left games off ──");
{
  const B = JSON.parse(bundle);
  ok("exclusions are counted by reason", !!B.excluded_by_reason,
     JSON.stringify(B.excluded_by_reason));
  const EX = B.excluded_by_reason || {};
  ok("...and the two reasons add up to the total",
     (EX.blowout || 0) + (EX.unrated || 0) === B.excluded_blowouts,
     `${EX.blowout}+${EX.unrated} vs ${B.excluded_blowouts}`);
  ok("no game left on the board was declined",
     B.bets.every(b => !b.no_bet), "a no_bet row is on the board");
  const t = window.document.body.textContent;
  if (EX.unrated) {
    ok("the page explains the unrated ones as a missing grade, not a wide line",
       /no film grade/.test(t), "banner text");
    // Not "does the word blowout appear" -- both reasons can be on screen at
    // once. The check that matters is that the unrated COUNT is attached to the
    // grade sentence and not to the line-width one. Built with RegExp, not a
    // regex literal: `${...}` inside /.../ is literal text, so the first version
    // of this line could never match and passed on every input.
    ok("...and the unrated count is attached to the grade sentence",
       new RegExp(EX.unrated + " because a team has no film grade").test(t),
       `looking for "${EX.unrated} because a team has no film grade"`);
  }
  if (EX.blowout) {
    ok("the page explains the blowouts as a wide line",
       /line is wider than/.test(t), "banner text");
  }
}

console.log("\n── banners ──");
ok("at least one banner shown", $$("#banner .banner").length > 0,
   String($$("#banner .banner").length));

console.log("\n── no runtime errors ──");
ok("zero JS errors during the whole run", errors.length === 0, errors.slice(0,3).join(" | "));

/* ── the bundle is public, and the page is built for that ────────────────
   This used to be an encrypted-bundle + passphrase-gate block. Grant removed the
   gate on 2026-09-02 after reviewing what the bundle holds. What replaces it is
   NOT nothing: the old failure was "the gate stayed painted over a booted app",
   and the new equivalent is "a leftover gate blocks a page that needs no
   unlocking". So assert the removal is total. */
console.log("\n── the bundle loads with no gate ──");
{
  ok("no passphrase gate is left in the page", !html.includes('id="gate"'));
  ok("no decryption code is left either",
     !/decryptBundle|deriveKey|PBKDF2/.test(html));
  ok("nothing still asks for data.enc.json", !html.includes("data.enc.json"));
  // The page must boot from the plaintext fetch alone -- the run at the top of
  // this file served exactly that, so if it had needed a second source the whole
  // suite above would have had nothing to assert against.
  ok("the app booted from data.json with no interaction",
     !$("#app").hasAttribute("hidden"));
  ok("...and rendered all 138 teams",
     $("#ranktbl")?.querySelectorAll("tbody tr").length === 138,
     String($("#ranktbl")?.querySelectorAll("tbody tr").length));
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
      win.fetch = async () => ({ ok:true, json:async()=>evil });
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
