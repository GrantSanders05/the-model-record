#!/usr/bin/env bash
# notify_failure.sh — turn a red workflow into something Grant actually sees.
#
# WHY THIS IS SHARED RATHER THAN COPIED
# update.yml had a notifier. refresh.yml did not. On 2026-09-02 GitHub Pages was
# switched off on the repo, every deploy started 404ing, and the first failure
# (05:12) went unreported for TEN HOURS -- until the nightly update happened to
# fail too at 15:01 and filed the issue. Four failed runs in between told nobody
# anything, while the site was down the whole time.
#
# One issue per title, commented rather than duplicated, so a repeated failure
# does not bury the inbox. health.yml closes these when things recover.
#
# The body is built in a QUOTED heredoc written to a file, then passed with
# --body-file. The first version nested `$(cat <<EOF ...)` inside a command
# substitution; an apostrophe in the prose ("the workbook's sharing") broke the
# parse and the script would not run at all. Quoted heredoc + body file has no
# expansion and no nesting, so prose cannot break it.
#
# Usage: notify_failure.sh "<issue title>" "<what happened>"
set -euo pipefail

TITLE="${1:?usage: notify_failure.sh <title> <what happened>}"
WHAT="${2:?usage: notify_failure.sh <title> <what happened>}"
RUN="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
SETTINGS="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/settings/pages"
NOW="$(date -u +'%Y-%m-%d %H:%M')"
BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

{
  printf '%s\n\n' "$WHAT"
  printf 'Failed at %s UTC — [view the run](%s)\n\n' "$NOW" "$RUN"
  cat <<'EOF'
**Nothing on the site is refreshing while this is red.** The other jobs may keep
going green and republishing the last good build, so the pages will look fine and
will not be current.

<details><summary>Usual causes, most likely first</summary>

- **`Deploy to GitHub Pages` fails with 404, "Ensure GitHub Pages has been
  enabled"** — Pages got switched off for the repo. This took the whole site
  down on 2026-09-02. Fix it at Settings → Pages (link below); Source must be
  **GitHub Actions**.
- **`Re-read the grade sheet` fails with "Google refused / the sheet is not
  shared"** — the workbook sharing changed. Set it back to **Anyone with the
  link → Viewer**; the pipeline reads it through a link-shared export and has no
  other way in.
- **A test in `Exercise the site in a real DOM` fails** — a real regression. Read
  the failing assertion; it names what broke.
- **A one-off network timeout** — transient. The next scheduled run recovers and
  this issue closes itself.

</details>
EOF
  printf '\nSettings → Pages: %s\n' "$SETTINGS"
} > "$BODY_FILE"

N="$(gh issue list --state open --search "$TITLE in:title" --json number -q '.[0].number' || true)"
if [ -n "$N" ]; then
  gh issue comment "$N" --body-file "$BODY_FILE"
  echo "commented on #$N"
else
  gh issue create --title "$TITLE" --body-file "$BODY_FILE"
fi
