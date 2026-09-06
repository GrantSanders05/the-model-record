"""
api_budget.py — spend the free tier on the things that cannot be re-fetched.

The existing ledger counts calls and refuses at the cap. That is the right shape
and it stops one call too late: by the time a hard cap fires, the requests it
blocks are whichever ones happened to come last, and on a Saturday afternoon
those are the market observations that decide what gets bet.

So the policy has three bands, and criticality is declared by the CALLER:

    below the soft limit   everything runs
    soft limit reached     research and backfill calls are refused; results,
                           lines and games still run
    hard reserve reached   only market, results and recovery calls run

WHY MARKET DATA WINS EVERY TIE. A line observed at 4:58pm cannot be recovered:
re-fetching later returns a retrospective number, not the one that was on the
screen. A season's advanced statistics can be pulled again next week. The cheap
thing to lose is the thing that is still there tomorrow.

A DASHBOARD MUST NEVER SPEND THE SATURDAY BUDGET. That is the whole reason this
module has a `purpose` argument rather than a single counter.
"""

import datetime as dt
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_FILE = os.path.join(ROOT, "data", "api_budget.json")

# CFBD's free tier. The soft limit leaves room for a bad week; the reserve leaves
# room for a Saturday.
MONTHLY_CAP = 1000
SOFT_LIMIT = 800
HARD_RESERVE = 100          # calls held back for CRITICAL only

# What a call is for. Ordered by what survives being skipped.
CRITICAL = "critical"       # market quotes, results, recovery — unrepeatable
STANDARD = "standard"       # games, rankings, grades — repeatable but wanted
RESEARCH = "research"       # backfills, charts, exploration — always repeatable

PRIORITY = {CRITICAL: 0, STANDARD: 1, RESEARCH: 2}


def _month_key(now=None):
    return (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m")


def read(path=None):
    path = path or BUDGET_FILE
    if os.path.exists(path):
        try:
            with open(path) as fh:
                return json.load(fh)
        except ValueError:
            # A corrupt ledger must not be read as "nothing spent" -- that is the
            # one interpretation that turns a bad file into an overspend.
            return {"_corrupt": True}
    return {}


def _entry(data, mk):
    e = data.get(mk)
    if isinstance(e, int):                          # the original flat format
        return {"used": e, "by_purpose": {}, "by_endpoint": {}}
    if isinstance(e, dict):
        return e
    return {"used": 0, "by_purpose": {}, "by_endpoint": {}}


def status(now=None, path=None):
    """Where the month stands. -> dict"""
    data = read(path)
    mk = _month_key(now)
    e = _entry(data, mk)
    used = e.get("used", 0)
    now = now or dt.datetime.now(dt.timezone.utc)
    # Days elapsed in the month, for a straight-line projection. Crude and
    # honest: it says "at this rate", not "we predict".
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elapsed = max((now - start).total_seconds() / 86400.0, 0.5)
    nxt = (start.replace(year=start.year + 1, month=1) if start.month == 12
           else start.replace(month=start.month + 1))
    days_in_month = (nxt - start).days
    return {
        "month": mk,
        "used": used,
        "cap": MONTHLY_CAP,
        "soft_limit": SOFT_LIMIT,
        "hard_reserve": HARD_RESERVE,
        "remaining": MONTHLY_CAP - used,
        "remaining_before_soft": max(SOFT_LIMIT - used, 0),
        "projected_month_end": int(round(used / elapsed * days_in_month)),
        "by_purpose": e.get("by_purpose", {}),
        "by_endpoint": e.get("by_endpoint", {}),
        "band": band(used),
        "corrupt": bool(data.get("_corrupt")),
    }


def band(used):
    if used >= MONTHLY_CAP - HARD_RESERVE:
        return "reserve"
    if used >= SOFT_LIMIT:
        return "soft"
    return "normal"


def allowed(purpose, *, now=None, path=None, n=1):
    """
    May a call of this `purpose` be made? -> (bool, reason)

    Refusing here is not an error: the caller falls back to cache and says so.
    The alternative -- letting a chart exhaust the budget and then failing a
    market fetch at 4:58pm -- is the failure this exists to prevent.
    """
    st = status(now, path)
    if st["corrupt"]:
        # Unknown spend. Only unrepeatable calls proceed.
        return (purpose == CRITICAL,
                "the budget ledger is unreadable; only critical calls proceed")
    used = st["used"]
    if used + n > MONTHLY_CAP:
        return False, ("the monthly cap of %d is exhausted (%d used); cached data "
                       "still works" % (MONTHLY_CAP, used))
    b = band(used)
    if b == "reserve" and purpose != CRITICAL:
        return False, ("only %d call(s) remain before the cap and they are reserved "
                       "for market and result data" % st["remaining"])
    if b == "soft" and purpose == RESEARCH:
        return False, ("past the soft limit of %d; research calls are paused so the "
                       "rest of the month can still record results" % SOFT_LIMIT)
    return True, None


def spend(n=1, *, purpose=STANDARD, endpoint=None, now=None, path=None):
    """Record a call. Returns the new monthly total. Raises if not allowed."""
    okay, why = allowed(purpose, now=now, path=path, n=n)
    if not okay:
        raise RuntimeError(why)
    path = path or BUDGET_FILE
    data = read(path)
    data.pop("_corrupt", None)
    mk = _month_key(now)
    e = _entry(data, mk)
    e["used"] = e.get("used", 0) + n
    e.setdefault("by_purpose", {})[purpose] = e["by_purpose"].get(purpose, 0) + n
    if endpoint:
        e.setdefault("by_endpoint", {})[endpoint] = \
            e["by_endpoint"].get(endpoint, 0) + n
    e["last_updated"] = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    data[mk] = e
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return e["used"]


def describe(now=None, path=None):
    st = status(now, path)
    lines = ["  CFBD budget %s: %d/%d used (%s band)"
             % (st["month"], st["used"], st["cap"], st["band"])]
    if st["projected_month_end"] > st["cap"]:
        lines.append("  at this rate the month ends at %d, over the %d cap — "
                     "research calls will be paused first"
                     % (st["projected_month_end"], st["cap"]))
    elif st["band"] != "normal":
        lines.append("  %d call(s) remain; %d are reserved for market and results"
                     % (st["remaining"], st["hard_reserve"]))
    if st["by_purpose"]:
        lines.append("  by purpose: %s" % ", ".join(
            "%s %d" % (k, v) for k, v in sorted(st["by_purpose"].items())))
    return "\n".join(lines)
