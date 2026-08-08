#!/usr/bin/env bash
# Assemble and push the PRIVATE research site.
#
# The site's code is versioned in the public repo (it is just an app), but the
# data bundle it reads contains every film grade. So deployment assembles the
# two in a scratch directory and pushes to a separate PRIVATE repo that Vercel
# watches. The grades therefore never touch the public repository.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PRIVATE_REMOTE="${1:-git@github.com:GrantSanders05/the-model-research.git}"
STAGE="$(mktemp -d)"

python3 "$HERE/src/research_export.py" --sport cfb --out "$HERE/output/research/data.json"

cp -R "$HERE/site/." "$STAGE/"
mkdir -p "$STAGE/research"
cp "$HERE/output/research/data.json" "$STAGE/research/data.json"

cd "$STAGE"
git init -q && git add -A
git -c user.name="The Model" -c user.email="grantssanders2005@gmail.com" \
    commit -q -m "research bundle $(date -u +%Y-%m-%dT%H:%M)"
git branch -M main
git remote add origin "$PRIVATE_REMOTE"
# Force-push: this repo is a build artifact, not a history worth keeping.
git push -q --force origin main
echo "pushed research site -> $PRIVATE_REMOTE"
rm -rf "$STAGE"
