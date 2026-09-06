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
