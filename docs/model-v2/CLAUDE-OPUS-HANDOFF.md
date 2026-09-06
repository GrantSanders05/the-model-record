# The Model V2 — Claude Opus Handoff

This file is the repository-side handoff for implementing the V2 research plan. The full implementation specification is the separately generated `Model_Record_V2_Master_Implementation_Build_Doc.md` (approximately 12,800 words / 3,700 lines). Give that master document to the coding agent together with this repository; this repo-side file is intentionally a concise durable index rather than a second 90KB duplicate.

**Generated master document SHA-256:** `b3c5b033df65fd83bfa342e755744aa84a21db5837898cf561239548131114dc`

## Read first

Before editing code, read:

- `docs/model-v2/AUDIT-2026-09-05.md`
- `docs/model-v2/ROADMAP.md`
- `docs/model-v2/AUTOMATION-DATA-PLAN.md`
- `docs/model-v2/EXPERIMENT-REGISTER.md`
- the full `Model_Record_V2_Master_Implementation_Build_Doc.md`

Then inspect the current `main` branch. The repository may have changed since the audit; preserve the document's invariants rather than stale line numbers.

## Non-negotiable implementation rules

1. Preserve the current production hand-grade model as the frozen Champion. Behavior-changing changes create a new version.
2. Do not rewrite historical forecasts or silently change the meaning of old results.
3. Official W/L must be graded at the exact line locked before kickoff. Closing-line performance is a separate diagnostic.
4. The original selected side is immutable. Never recompute a historical side from `model_margin - closing_margin`.
5. Never invent spread/total prices. Unknown juice means official ROI is unavailable.
6. Separate forecasts, strategy evaluations, official signals, and actual user bets.
7. Every new official forecast/signal needs model version, Git SHA, fully merged config hash, feature schema, exact as-of source snapshots, generated timestamp, and horizon.
8. No post-kickoff information may enter a pregame record.
9. GitHub Actions cache must not remain the sole source of truth for non-regenerable decision-time state.
10. Challengers run shadow first. Do not promote them automatically.
11. Treat 2025 as development evidence. Preserve 2026 as prospective evidence for model versions that did not train on those outcomes.
12. Keep the core lightweight/stdlib-oriented during the integrity work.
13. Do not delete legacy tables during V2 migration; use compatibility bridges and reconciliation reports.
14. Public export must be allow-list based and must not expose personal wager/account data.
15. Every critical gate needs a synthetic failure/control proving it can actually fail.

## Required build order

### Phase 0A — grading semantics

Add one canonical grading module and tests for:

- same pick winning at its locked line but losing at close;
- same pick losing at lock but winning at close;
- original side remaining unchanged even if the closing line crosses the model number;
- unknown price producing no real ROI.

Preserve legacy `ats_result`/`ou_result` values. Add explicit locked-line and close-result fields.

### Phase 0B — V2 schema and provenance

Add V2 tables for at least:

- provider-level market quotes;
- market snapshots;
- timestamped grade snapshots;
- feature snapshots;
- model registry;
- forecast log;
- strategy evaluations;
- signal log;
- results/close;
- missed standardized horizons.

Add canonical JSON/hash helpers and a non-destructive `migrate_v2.py` with dry-run/apply/report modes.

### Phase 0C — market quotes

Modify CFB ingest so V2 stores every provider observation. Keep the legacy preferred `lines` table as a materialized compatibility view during transition. Do not choose the research truth at ingest time.

Create versioned consensus and close policies. A close must use only pre-kickoff observations.

### Phase 0D — exact grade snapshots

Extend sheet sync to append changed full-team grade vectors with exact `observed_at`/`effective_at`. New forecast lookup must be as-of the forecast timestamp.

### Phase 1A — forecast / strategy / signal separation

Build a Champion adapter around the current engine rather than reimplementing Champion arithmetic. Add standardized horizons (T72/T24/T6/T2, optional T30), reason-coded strategy evaluations, and shadow V2 signal generation.

Start T2 as the official horizon policy, but do not cut over the official writer until side-by-side validation passes.

### Phase 1B — durable state

Implement append-only JSONL event state on a dedicated `data-state` branch and a replay/reconciliation path. SQLite remains the fast materialized database; cache becomes acceleration rather than the only durable history.

All state-writing workflows should share one repository concurrency group so automated commits cannot race.

### Phase 1C — official cutover

Only after shadow validation:

- V2 becomes the official Champion signal writer;
- legacy `picks_log` becomes a compatibility mirror/read-only historical layer;
- public record leads with locked-line signal results;
- closing-line diagnostics remain separate;
- historical methodology transition is documented.

### Phase 2 — research challengers

Begin with E003: a regularized ridge model predicting `actual margin - market margin at the decision snapshot`, using the private grade information. Keep it shadow-only. Then E004 matchup interactions.

Do not refit the same version using newly observed 2026 outcomes.

## Current CI gates that must remain

The audited production workflow currently runs:

```bash
cd tools/qa && npm install --no-fund --no-audit --silent
cd "$GITHUB_WORKSPACE"
python3 tools/test_tracking.py
python3 tools/test_model.py
node tools/qa/public.mjs
if [ -f output/research/data.json ]; then node tools/qa/research.mjs; fi
python3 tools/make_fixture.py
QA_BUNDLE=output/research/fixture.json node tools/qa/research.mjs
QA_PAGE=output/site/fixture.html node tools/qa/public.mjs
rm -f output/research/fixture.json output/site/fixture.html
```

Add V2 integrity/migration/replay gates; do not remove these old gates.

## Engineering preference order

When multiple implementations are valid, prefer:

1. least destructive migration;
2. clearer auditability;
3. fewer hidden fallbacks;
4. deterministic behavior;
5. fewer dependencies;
6. easier synthetic testing;
7. easier rollback.

## Final deliverable expected from the coding agent

Provide a branch/PR plus:

- architecture summary;
- changed/new files;
- schema and migration report;
- exact test commands/results;
- Champion before/after comparison;
- state replay/reconciliation result;
- public record-definition changes;
- known data limitations;
- current shadow challengers;
- rollback instructions;
- remaining roadmap items.

The objective is not to make historical percentages look better. The objective is to make every future number auditable enough to trust.