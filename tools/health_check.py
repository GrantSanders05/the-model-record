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
BUNDLE = SITE + "research/data.enc.json"
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
        seen.setdefault(r["name"], []).append(r)
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
                             "being published (RESEARCH_PASS secret missing?)")
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
    "site": "GitHub throttled the rebuild cron. Force one now: **Actions → "
            "“refresh grades & republish” → Run workflow.** If that fixes it, "
            "nothing is broken — install the sheet trigger "
            "(`tools/SHEET-TRIGGER-SETUP.md`) so edits publish in ~1 min instead.",
    "runs": "Open the failed run and read the error. If it says Google refused / "
            "the sheet is not shared, the Google Sheet's sharing was changed — set "
            "it back to **Anyone with the link → Viewer**; the pipeline reads it "
            "through a link-shared export and has no other way in.",
    "bundle": "Re-set the `RESEARCH_PASS` repo secret, then rebuild — the bundle is "
              "encrypted at build time, so the new passphrase does nothing until "
              "the site republishes.",
}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    results = [("site", "Is the site current?") + check_site(now),
               ("runs", "Are the workflows healthy?") + check_runs(now),
               ("bundle", "Is the research app published?") + check_bundle(),
               ("issues", "Anything already flagged?") + check_issues()]
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
