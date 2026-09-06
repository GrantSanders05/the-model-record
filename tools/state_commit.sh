#!/usr/bin/env bash
# state_commit.sh — publish the REDACTED journal to the data-state branch.
#
# THIS SCRIPT NEVER PUBLISHES state/. It publishes the redacted copy that
# state_events.write_publishable produces, and it refuses to push at all if the
# audit finds a film grade in it. This repository is public; a grade snapshot
# payload carries the eight position grades for a named team, and those are the
# whole moat.
#
#   tools/state_commit.sh                # build, audit, commit, push
#   DRY_RUN=1 tools/state_commit.sh      # build and audit only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BRANCH="${STATE_BRANCH:-data-state}"
SRC="${STATE_DIR:-state}"
PUB="${PUBLISH_DIR:-.state-publish}"

echo "── exporting the live database into the journal ──"
python3 src/replay_state.py --export --state-dir "$SRC"

echo "── building the publishable copy ──"
python3 - "$SRC" "$PUB" <<'PY'
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
import state_events as se
c = se.write_publishable(sys.argv[1], sys.argv[2])
print("  %(events)d event(s): %(written)d written, %(redacted)d redacted, "
      "%(dropped)d dropped" % c)
problems = se.audit_publishable(sys.argv[2])
if problems:
    print("\n  REFUSING TO PUBLISH — the redacted journal still contains:")
    for p in problems[:10]:
        print("    %s" % p)
    raise SystemExit(1)
print("  audit clean: no film grades in the publishable journal")
PY

if [ "${DRY_RUN:-0}" = "1" ]; then
  echo "DRY RUN — built and audited, nothing pushed."
  exit 0
fi

# A worktree, so the working checkout is never switched underneath a running job.
WT="$(mktemp -d)"
git fetch origin "$BRANCH" --quiet 2>/dev/null || true
if git show-ref --quiet "refs/remotes/origin/$BRANCH"; then
  git worktree add --quiet "$WT" "origin/$BRANCH" --detach
else
  git worktree add --quiet --detach "$WT"
  git -C "$WT" checkout --quiet --orphan "$BRANCH"
  git -C "$WT" rm -rq --cached . 2>/dev/null || true
  find "$WT" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
fi

rm -rf "${WT:?}/state"
cp -R "$PUB" "$WT/state"
cat > "$WT/README.md" <<'MD'
# data-state

Append-only journal of the facts this project cannot re-fetch: market quote
observations, forecasts, strategy decisions, official signals, results, missed
horizons and voids.

**Film grades are redacted from this branch.** A grade event here carries its
snapshot id, team, timestamps and the hash of the vector — enough to prove which
grade state was in force for a forecast and when it changed — and none of the
numbers. The grades are the one input this project does not publish.

Rebuild a database from it:

    python3 src/replay_state.py --state-dir state --db /tmp/rebuilt.db
    python3 src/replay_state.py --verify-against data/model.db
MD

git -C "$WT" add -A state README.md
if git -C "$WT" diff --cached --quiet; then
  echo "  nothing new to record."
else
  git -C "$WT" -c user.name="the-model-bot" \
      -c user.email="bot@users.noreply.github.com" \
      commit --quiet -m "state: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git -C "$WT" push --quiet origin "HEAD:$BRANCH"
  echo "  pushed to $BRANCH"
fi
git worktree remove --force "$WT"
