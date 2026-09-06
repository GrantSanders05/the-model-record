"""
public_export.py — what may leave, decided by a list of what may leave.

THE RULE IS ALLOW-LIST, NOT DENY-LIST. A bundle built by dumping an internal
structure and then deleting a few keys is safe only until somebody adds a key.
A bundle assembled field by field is safe until somebody adds a field TO THE
ALLOW LIST, which is a decision rather than an accident.

WHAT IS ALREADY PUBLIC, DELIBERATELY
------------------------------------
The film grades. That was Grant's call on 1 September 2026, made with the
contents in front of him: the research bundle is served in the clear, the
passphrase gate was removed, and `teams[].grades` carries the eight position
numbers for all 138 teams. This module does not second-guess that.

What that decision DID leave behind is stated in its own commit message: "the
transport no longer protects anything, so anything genuinely private has to be
kept OUT of the bundle by research_export.py. The live wire to watch is
`mybets` -- it is empty today, but it reads the sheet's My Bets tab, so bets
logged there would publish with stakes, books and ROI."

This is that guard. Personal wagering is a different category from a model's
opinion: how much money a person put on a game, at which book, and what it did
to their bankroll is nobody's business, and unlike a grade it cannot be
un-published once it has been fetched.
"""

import json

# ── what may never be published, and what depends on context ─────────────────
#
# The distinction matters, and getting it wrong in either direction is fatal to
# the gate. A first version forbade `stake` outright and immediately fired on
# every row of the model's own board, where `stake` is the RECOMMENDED Kelly
# fraction — the model's output, published on purpose. A gate that fires on the
# thing it is protecting gets switched off, and then it protects nothing.

# Never legitimate in a public bundle, under any parent, at any depth. There is
# no reading of this project under which a bankroll, an account or a token is
# part of the model's output.
ALWAYS_FORBIDDEN = {
    "bankroll", "dollars_won", "dollars_risked", "dollars", "unit_size",
    "account", "account_id", "accounts", "sportsbook",
    "email", "user_id", "username", "phone", "full_name",
    "token", "access_token", "refresh_token", "api_key", "apikey",
    "secret", "client_secret", "private_key", "password", "passphrase",
    "credentials", "service_account",
}

# Legitimate as a model recommendation, forbidden as a record of a placed wager.
# `stake` on the board is "what Kelly says"; `stake` beside a book name is "what
# somebody actually put down".
WAGER_AMOUNT_KEYS = {"stake", "stakes", "stake_units", "units_risked",
                     "units_won", "amount_risked", "wager", "payout", "risked"}
# The fields that turn an amount into somebody's bet.
PLACEMENT_KEYS = {"book", "books", "sportsbook", "account", "account_id",
                  "placed_at", "bet_id", "ticket"}

# `mybets` may appear as a STATUS object and must never carry rows.
BETS_CONTAINER_KEYS = {"mybets", "my_bets", "bet_log", "user_bets"}
BETS_ROW_KEYS = {"bets", "rows", "wagers"}


def audit(bundle, *, path="bundle"):
    """
    Recursively scan a bundle for anything that must not be published. -> [str]

    Three rules, in order of how a leak actually arrives:

      1. an ALWAYS_FORBIDDEN key anywhere;
      2. an object carrying a wager AMOUNT beside a PLACEMENT field, which is
         the shape of a bet somebody made rather than a bet a model suggested;
      3. a wager container holding rows at all.

    RECURSIVE, and into JSON stored as a string, because a serialized blob is
    exactly as published as a nested object and nobody adds a stake to the root.
    """
    problems = []

    def walk(node, p, inside_wager_container):
        if isinstance(node, dict):
            keys = {str(k).lower() for k in node}
            for k in node:
                if str(k).lower() in ALWAYS_FORBIDDEN:
                    problems.append("%s.%s is never publishable" % (p, k))

            amounts = sorted(keys & WAGER_AMOUNT_KEYS)
            if amounts and (keys & PLACEMENT_KEYS or inside_wager_container):
                problems.append(
                    "%s carries a wager amount (%s) beside placement details — "
                    "that is a bet somebody made, not one a model suggested"
                    % (p, ", ".join(amounts)))

            for k, v in node.items():
                kl = str(k).lower()
                if kl in BETS_CONTAINER_KEYS and isinstance(v, dict):
                    for rk in BETS_ROW_KEYS:
                        rows = v.get(rk)
                        if isinstance(rows, list) and rows:
                            problems.append(
                                "%s.%s.%s carries %d wager row(s); publish status "
                                "only, never rows" % (p, k, rk, len(rows)))
                walk(v, "%s.%s" % (p, k),
                     inside_wager_container or kl in BETS_CONTAINER_KEYS)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, "%s[%d]" % (p, i), inside_wager_container)
        elif isinstance(node, str) and node.strip().startswith(("{", "[")):
            try:
                walk(json.loads(node), p + "(json)", inside_wager_container)
            except ValueError:
                pass

    walk(bundle, path, False)
    return problems


def safe_my_bets(mybets):
    """
    A status-only view of the bet log. -> dict

    Whether the sheet was readable, and when it was read. No rows, no stakes, no
    books, no ROI — and NOT a filtered copy of the rows either, because a filter
    is a deny-list and inherits every problem of one. The keys are written out.
    """
    mb = mybets or {}
    totals = mb.get("totals") or {}
    return {
        "state": mb.get("state"),
        "problems": mb.get("problems") or [],
        "fetched_utc": mb.get("fetched_utc"),
        # Counts only. How many bets exist is operational; what they were is not.
        "n": totals.get("n"),
        "settled": totals.get("settled"),
        "open": totals.get("open"),
    }


def sanitize(bundle):
    """
    Return a bundle safe to publish, and the list of what was removed.
    -> (bundle, [notes])

    Only `mybets` is rewritten today. Everything else in the bundle is the
    model's own output, which this project publishes on purpose.
    """
    out = dict(bundle)
    notes = []
    for key in BETS_CONTAINER_KEYS:
        if key in out and isinstance(out[key], dict):
            rows = out[key].get("bets") or []
            out[key] = safe_my_bets(out[key])
            notes.append("%s reduced to status only%s"
                         % (key, " (%d row(s) withheld)" % len(rows) if rows else ""))
    return out, notes
