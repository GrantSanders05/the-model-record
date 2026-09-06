# The Model — V2 Research Program

**Created:** 2026-09-05  
**Status:** Research only — no production behavior changed by these documents.  
**Purpose:** Preserve the audit, decisions, experiments, and implementation order for improving The Model without destroying the integrity of its live record.

## Documents

1. [`AUDIT-2026-09-05.md`](./AUDIT-2026-09-05.md) — current-state audit: what is trustworthy, what is not yet proven, and the highest-risk defects/gaps.
2. [`ROADMAP.md`](./ROADMAP.md) — target V2 model architecture, validation framework, feature plan, champion/challenger design, and prioritized implementation phases.
3. [`AUTOMATION-DATA-PLAN.md`](./AUTOMATION-DATA-PLAN.md) — exact data, snapshot, scheduling, persistence, availability, weather, and market-line automation plan.
4. [`EXPERIMENT-REGISTER.md`](./EXPERIMENT-REGISTER.md) — pre-registration log for every model change. This is the anti-overfitting memory of the project.
5. [`CLAUDE-OPUS-HANDOFF.md`](./CLAUDE-OPUS-HANDOFF.md) — repository-side coding-agent handoff. Use it together with the full generated `Model_Record_V2_Master_Implementation_Build_Doc.md` when implementation begins. The handoff records the expected SHA-256 of that generated master file.

## The operating rule from this point forward

**Do not optimize the current production model on 2026 results and then present the same 2026 results as proof that the change works.**

The 2025 grade season has already been inspected repeatedly, used for ablations, scale fitting, edge shrinkage, quality-rule decisions, and other development decisions. It is still useful development data, but it is no longer an untouched test set at the *human research process* level.

Therefore:

- The current production configuration becomes the **Champion**.
- New ideas run as **Shadow Challengers**.
- Every forecast, feature snapshot, model version, and market quote is timestamped.
- 2026 prospective results are never rewritten when a model changes.
- Model changes are evaluated on predictions that were generated before the games and before their closing lines were known.
- A bug fix may ship when necessary, but it creates a new model/version identifier and never rewrites old forecasts.
- A performance improvement is promoted only after a pre-registered prospective comparison or a genuinely untouched historical evaluation.

## North-star question

The project should answer one question honestly:

> **Does information available to us before a wager is made improve prediction or price discovery beyond the market price available at that same moment?**

Everything else — win rate, a good Saturday, a large raw model edge, a backtest found after many experiments, or a visually convincing dashboard — is secondary.

## Definitions that must stay separate

- **Forecast:** what a model predicts for a game, whether or not it is actionable.
- **Signal:** a forecast that passes the strategy's eligibility/edge/uncertainty rules.
- **Bet:** an actual wager, with exact book, line, price, stake, and timestamp.
- **Locked-line result:** whether the published signal won at the exact line/price available when it was locked.
- **Closing-line result:** whether the same side would have covered the final closing number. Useful diagnostically, but not the realized result of the published wager.
- **CLV:** movement from the exact locked market price to the close, in the direction of the signal. Raw model-vs-market disagreement is **not** CLV.
- **Model edge:** a model's difference from the market. It is a hypothesis until calibrated out of sample.
- **Economic edge:** expected value after converting the model to a calibrated probability and comparing that probability with the actual offered price after vig.

## Current recommendation in one sentence

Keep the hand-grade model as the unique-information core, but rebuild the research system around **time-stamped market residual prediction, matchup interactions, probability calibration, exact line/price snapshots, official availability data, durable event storage, and prospective champion/challenger validation**.
