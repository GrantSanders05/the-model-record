"""
health_check.py — is the site actually showing what the sheet says?

WHY THIS EXISTS
Grant's recurring question is not "did the job pass". Every job can pass and the
site can still be hours behind his last edit, because the rebuild runs on a
GitHub Actions cron that GitHub throttles and DROPS runs from: measured over 100
consecutive runs, median gap 53 min against a stated */30, p90 5.6 h, worst 11.8.

The existing failure notifier only fires when the nightly update goes red. It is
blind to the two failures that actually look fine:
  * every workflow green, site six hours stale
  * refresh.yml failing (it has no notifier at all) while the pages still serve
    the last good build, looking correct and being wrong

So this checks the PUBLISHED ARTEFACTS, not the jobs. It runs inside Actions
because that is the only place with both the GitHub API and the public site
reachable -- a cloud sandbox has neither (egress policy blocks github.io, and
api.github.com needs an app connection).

Exit code is the verdict: 0 all good, 1 problem. Prints a markdown report on
stdout for the workflow to put in an issue.
"""

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "GrantSanders05/the-model-record")
_owner, _name = REPO.split("/", 1)
SITE = "https://%s.github.io/%s/" % (_owner.lower(), _name)
BUNDLE = SITE + "research/data.json"
API = "https://api.github.com/repos/" + REPO

STALE_WARN_H = 3        # the amber threshold the page itself uses
STALE_FAIL_H = 12       # beyond any observed throttle gap; something is wrong
BUNDLE_MIN = 500 * 1024

OK, WARN, PROBLEM = "OK", "WARN", "PROBLEM"


def _get(url, raw=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": "the-model-health-check",
        "Accept": "application/vnd.github+json"})
    tok = os.environ.get("GH_TOKEN")
    if tok and url.startswith(API):
        req.add_header("Authorization", "Bearer " + tok)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    return body if raw else json.loads(body)


def check_site(now):
    """The one that answers his actual question."""
    try:
        html = _get(SITE, raw=True).decode("utf-8", "replace")
    except Exception as e:
        # A 404 on the whole site has ONE overwhelmingly likely cause, and it is
        # not a broken page: Pages gets switched off for the repo and every
        # deploy starts failing with "Ensure GitHub Pages has been enabled".
        # That happened on 2026-09-02 and cost ten hours. Naming it beats
        # "unreachable", which sends you to read five workflow logs first.
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            try:
                on = _get(API)["has_pages"]
            except Exception:
                on = None
            if on is False:
                return PROBLEM, ("the whole site is **404 — GitHub Pages is "
                                 "switched off for this repo**")
        return PROBLEM, "site unreachable: %s" % e
    marker = 'data-built="'
    i = html.find(marker)
    if i < 0:
        # Not cosmetic: without this attribute the page cannot report its own age
        # to a reader either, so the whole staleness signal is gone.
        return PROBLEM, "the page carries no build stamp (data-built is missing)"
    built = html[i + len(marker):html.index('"', i + len(marker))]
    age_h = (now - dt.datetime.strptime(built, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)).total_seconds() / 3600
    txt = "built %s UTC — **%.1f h ago**" % (built.replace("T", " ")[:16], age_h)
    if age_h >= STALE_FAIL_H:
        return PROBLEM, txt + " (nothing has republished in half a day)"
    if age_h >= STALE_WARN_H:
        return WARN, txt + " (throttled cron; usually catches up)"
    return OK, txt


def check_runs(now):
    try:
        runs = _get(API + "/actions/runs?per_page=50")["workflow_runs"]
    except Exception as e:
        return PROBLEM, "could not read run history: %s" % e
    if not runs:
        return PROBLEM, "no workflow runs at all"
    cutoff = now - dt.timedelta(hours=48)
    lines, verdict = [], OK
    seen = {}
    for r in runs:
        # Skip this workflow's own runs: the one asking the question is always
        # in flight, so it reported itself as "last: None" every single time.
        if "health" in r["name"].lower():
            continue
        # And skip anything still running anywhere -- a null conclusion is not a
        # pass and not a failure, and counting it as either is a lie. It would
        # also silently dilute the 48-hour failure ratio.
        if r["conclusion"] is None:
            continue
        seen.setdefault(r["name"], []).append(r)
    if not seen:
        return WARN, "no completed runs of the pipeline workflows in this window"
    for name, rs in seen.items():
        last = rs[0]
        recent = [r for r in rs
                  if dt.datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ")
                  .replace(tzinfo=dt.timezone.utc) > cutoff]
        fails = sum(1 for r in recent if r["conclusion"] == "failure")
        short = name.split("—")[-1].strip()
        lines.append("`%s`: last **%s** at %s · %d/%d failed in 48 h"
                     % (short, last["conclusion"], last["created_at"][:16].replace("T", " "),
                        fails, len(recent)))
        # The LAST run failing is the one that matters -- it means the current
        # state is broken, not that a blip happened and recovered.
        if last["conclusion"] == "failure":
            verdict = PROBLEM
        elif fails >= 2 and verdict == OK:
            verdict = WARN
    return verdict, "<br>".join(lines)


def check_bundle():
    """A 404 here means the research app silently stopped being published."""
    try:
        n = len(_get(BUNDLE, raw=True))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return PROBLEM, ("research bundle is **404** — the research app is not "
                             "being published at all")
        return PROBLEM, "research bundle HTTP %d" % e.code
    except Exception as e:
        return PROBLEM, "research bundle unreachable: %s" % e
    if n < BUNDLE_MIN:
        return PROBLEM, "research bundle is only %d KB — truncated" % (n // 1024)
    return OK, "research bundle served, %.1f MB" % (n / 1048576.0)


def check_issues():
    try:
        issues = [i for i in _get(API + "/issues?state=open")
                  if "pull_request" not in i]
    except Exception as e:
        return WARN, "could not list issues: %s" % e
    others = [i for i in issues if not i["title"].startswith(TITLE)]
    if not others:
        return OK, "no open issues"
    return WARN, "open: " + ", ".join("#%d %s" % (i["number"], i["title"])
                                      for i in others[:5])


TITLE = "Health check:"

FIX = {
    "pages_off": "Turn Pages back on: **Settings → Pages → Source: GitHub "
                 "Actions**, or `gh api -X POST repos/OWNER/REPO/pages "
                 "-f build_type=workflow`. Then re-run the refresh workflow. "
                 "Every deploy fails with a 404 while it is off.",
    "site": "GitHub throttled the rebuild cron. Force one now: **Actions → "
            "“refresh grades & republish” → Run workflow.** If that fixes it, "
            "nothing is broken — install the sheet trigger "
            "(`tools/SHEET-TRIGGER-SETUP.md`) so edits publish in ~1 min instead.",
    "runs": "Open the failed run and read the error. If it says Google refused / "
            "the sheet is not shared, the Google Sheet's sharing was changed — set "
            "it back to **Anyone with the link → Viewer**; the pipeline reads it "
            "through a link-shared export and has no other way in.",
    "bundle": "The assemble step did not copy `output/research/data.json` into the "
              "published site. Check the most recent run's *Assemble site* step — it "
              "hard-fails when the export produced no bundle.",
}


def check_v2(now):
    """
    The V2 invariants that cannot look healthy while the pipeline is broken.

    Local only: it opens the working database if there is one and says so if
    there is not, rather than reporting green on the absence of evidence.

    Everything here is a shape that produces NO error when it goes wrong. A
    forecast with no provenance still renders. A horizon that closed empty leaves
    a gap indistinguishable from a quiet week. A journal that has drifted from
    the database still serves. Those are the ones a monitor has to carry.
    """
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dbp = os.path.join(root, "data", "model.db")
    if not os.path.exists(dbp):
        return WARN, "no local database on this runner — V2 checks skipped"
    sys.path.insert(0, os.path.join(root, "src"))
    try:
        import db as _db
        import api_budget as _ab
        conn = _db.connect(dbp)
    except Exception as e:                         # noqa: BLE001
        return PROBLEM, "could not open the database — `%s`" % e

    notes, worst = [], OK

    # 1. Provenance on official signals. A signal that cannot name the model,
    #    the code and the config that produced it is not evidence.
    bad_prov = conn.execute(
        "SELECT COUNT(*) c FROM signal_log s"
        " LEFT JOIN forecast_log f ON f.forecast_id = s.forecast_id"
        " LEFT JOIN model_registry m ON m.model_version = f.model_version"
        " WHERE s.is_official=1 AND s.strategy_version != 'S-legacy'"
        "   AND (f.forecast_id IS NULL OR m.model_version IS NULL"
        "        OR m.git_sha IS NULL OR m.config_hash IS NULL)").fetchone()["c"]
    if bad_prov:
        worst = PROBLEM
        notes.append("%d official signal(s) without complete provenance" % bad_prov)

    # 2. Market freshness for games about to start.
    soon = (now + dt.timedelta(hours=3)).isoformat()
    stale = conn.execute(
        "SELECT COUNT(*) c FROM games g WHERE g.sport='cfb'"
        "  AND g.home_score IS NULL AND g.kickoff BETWEEN ? AND ?"
        "  AND NOT EXISTS (SELECT 1 FROM market_quotes q WHERE q.game_id=g.game_id"
        "                    AND q.observed_at >= ?)",
        (now.isoformat(), soon,
         (now - dt.timedelta(hours=6)).isoformat())).fetchone()["c"]
    if stale:
        worst = max(worst, WARN)
        notes.append("%d game(s) kick off within 3h with no quote in the last 6h"
                     % stale)

    # 3. Missed horizons. Recorded, so they are visible rather than absent.
    # Only misses for games that kicked off AFTER the model was registered. A
    # game played before V2 existed has no T2 forecast because there was no V2,
    # which is a fact about the calendar and not a pipeline failure. Counting
    # those makes the number permanently large and therefore permanently ignored.
    live_since = conn.execute(
        "SELECT MIN(created_at) t FROM model_registry WHERE role='champion'"
    ).fetchone()["t"]
    if live_since:
        misses = conn.execute(
            "SELECT COUNT(*) c FROM snapshot_misses m"
            "  JOIN games g ON g.game_id = m.game_id"
            " WHERE m.detected_at >= ? AND g.kickoff > ?",
            ((now - dt.timedelta(days=7)).isoformat(), live_since)).fetchone()["c"]
        if misses:
            worst = max(worst, WARN)
            notes.append("%d horizon(s) closed with no forecast since V2 went live"
                         % misses)

    # 4. Budget, warned BEFORE requests start failing.
    st = _ab.status(now)
    if st["band"] != "normal" or st["projected_month_end"] > st["cap"]:
        worst = max(worst, WARN)
        notes.append("CFBD %d/%d used (%s band), projecting %d"
                     % (st["used"], st["cap"], st["band"], st["projected_month_end"]))

    # 5. The journal reconciles with the database it is supposed to reproduce.
    state_dir = os.path.join(root, "state")
    if os.path.isdir(state_dir):
        r = subprocess.run(
            [sys.executable, os.path.join(root, "src", "replay_state.py"),
             "--verify-against", dbp], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            # BEHIND IS NOT THE SAME AS WRONG, and conflating them makes the
            # check useless. A journal with FEWER rows is simply not exported
            # yet — the nightly job fixes it. A journal whose rows DIFFER means
            # the database and the record of it have drifted apart, which is the
            # thing this check exists to catch.
            out = r.stdout or ""
            differs = "differs" in out
            if differs:
                worst = PROBLEM
                notes.append("the state journal DISAGREES with the database — "
                             "rows differ, not just missing")
            else:
                worst = max(worst, WARN)
                behind = [ln.split()[0] for ln in out.splitlines()
                          if ln.strip().endswith("NO")]
                notes.append("the state journal is behind on %s — the nightly "
                             "export will catch up" % (", ".join(behind) or "some tables"))
    else:
        notes.append("no local journal to reconcile")

    if worst == OK and not notes:
        n_sig = conn.execute(
            "SELECT COUNT(*) c FROM signal_log WHERE is_official=1").fetchone()["c"]
        return OK, ("provenance complete, market fresh, no missed horizons, "
                    "journal reconciles (%d official signal(s))" % n_sig)
    return worst, "; ".join(notes)


def main():
    now = dt.datetime.now(dt.timezone.utc)
    results = [("site", "Is the site current?") + check_site(now),
               ("runs", "Are the workflows healthy?") + check_runs(now),
               ("bundle", "Is the research app published?") + check_bundle(),
               ("issues", "Anything already flagged?") + check_issues(),
               ("v2", "Is the V2 record trustworthy?") + check_v2(now)]
    worst = PROBLEM if any(r[2] == PROBLEM for r in results) else (
        WARN if any(r[2] == WARN for r in results) else OK)
    icon = {OK: "✅", WARN: "⚠️", PROBLEM: "❌"}

    print("### %s %s" % (icon[worst], {
        OK: "All good",
        WARN: "Working, with something worth knowing",
        PROBLEM: "Something needs attention"}[worst]))
    print("\n| | check | result |\n|---|---|---|")
    for key, label, verdict, detail in results:
        print("| %s | %s | %s |" % (icon[verdict], label, detail))

    bad = [k for k, _, v, _ in results if v == PROBLEM]
    if any(k == "site" and "switched off" in d for k, _, v, d in results):
        bad = ["pages_off" if k == "site" else k for k in bad]
    if bad:
        print("\n**What to do**\n")
        for k in bad:
            if k in FIX:
                print("- %s" % FIX[k])
    print("\n<sub>Checked %s UTC. Runs every other night at 10 PM Eastern.</sub>"
          % now.strftime("%Y-%m-%d %H:%M"))
    return 1 if worst == PROBLEM else 0


if __name__ == "__main__":
    sys.exit(main())
