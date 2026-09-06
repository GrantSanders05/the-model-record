"""
provenance.py — what a forecast can prove about itself.

A prediction is only evidence if it can be reproduced. `cfb_grades@2026-09-05`
cannot reproduce anything: the code, the defaults, the aliases, the provider
choice and the grade sheet can all move on the same day and the label does not
change. This module supplies the pieces that pin a forecast down — the exact
code, the exact effective configuration, the exact inputs — and hashes them the
same way every time.

THE FULLY MERGED CONFIG IS WHAT GETS HASHED, not the file on disk. A sparse
config file inherits engine defaults, so hashing the file makes a forecast
irreproducible the moment a default moves: same file, same hash, different
model. The optimizer in this repository already saves merged configs for that
reason; this extends the same rule to production model identity.

CANONICAL JSON OR NOTHING. Python dict ordering, float repr and unicode escaping
all vary. Hashing `repr(d)` or `json.dumps(d)` without sort_keys produces a value
that depends on insertion order, so the same configuration hashes two ways and
"has the config changed?" becomes unanswerable. Everything here goes through
`canonical_json`.
"""

import hashlib
import json
import os
import subprocess

# ID prefixes, so a bare hash in a log or a foreign key says what it identifies.
PREFIXES = {
    "market_quote": "mq",
    "market_snapshot": "ms",
    "grade_snapshot": "gs",
    "feature_snapshot": "fs",
    "forecast": "fc",
    "strategy_evaluation": "ev",
    "signal": "sg",
    "event": "evt",
    "artifact": "art",
    "availability_event": "av",
    "weather_snapshot": "wx",
}

# How much of a record's provenance genuinely exists.
#
#   complete  every input hash was captured at decision time
#   partial   some inputs known, others reconstructed from surviving columns
#   legacy    migrated from a record written before provenance existed
#
# A backfilled row says `legacy` and stops there. Manufacturing a hash for a
# source state nobody preserved would make an unreproducible forecast look
# reproducible, which is worse than admitting the gap.
COMPLETE, PARTIAL, LEGACY = "complete", "partial", "legacy"


def canonical_json(value):
    """Deterministic JSON: sorted keys, no incidental whitespace, real unicode."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=_fallback)


def _fallback(obj):
    """Serialize the few non-JSON types that legitimately appear in a payload."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError("cannot canonicalize %r of type %s" % (obj, type(obj).__name__))


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_hash(value):
    """SHA-256 of the canonical form of any JSON-able value."""
    return sha256_text(canonical_json(value))


def stable_id(kind, value):
    """
    A content-addressed ID: same contents, same ID, on any machine and any run.

    Content addressing is what makes appending idempotent. A workflow that retries
    re-derives the identical ID, the insert collides on the primary key, and the
    duplicate is ignored rather than becoming a second copy of the same fact.
    """
    if kind not in PREFIXES:
        raise ValueError("unknown id kind %r; add it to PREFIXES" % (kind,))
    return "%s_%s" % (PREFIXES[kind], payload_hash(value)[:24])


def merged_config(config, defaults=None):
    """
    The effective config, defaults filled in. This is what identity is computed on.

    Defaults come from engine.DEFAULT_CONFIG unless supplied, and nested dicts are
    merged one level deep, matching engine.merge_config so the hash describes the
    configuration the engine will actually run.
    """
    if defaults is None:
        import engine
        defaults = engine.DEFAULT_CONFIG
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in defaults.items()}
    for k, v in (config or {}).items():
        if k.startswith("_"):
            continue                       # injected runtime payloads, not config
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def config_hash(config, defaults=None):
    """SHA-256 of the fully merged effective config."""
    return payload_hash(merged_config(config, defaults))


def git_sha(root=None):
    """
    (sha, dirty) for the code that is running.

    Prefers GITHUB_SHA, which is exactly the commit a workflow checked out. Falls
    back to the local HEAD. `dirty` is True when the working tree has uncommitted
    changes — a local experiment, not something an official signal may carry.
    """
    env = os.environ.get("GITHUB_SHA")
    if env:
        return env, False
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return None, None
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                capture_output=True, text=True, timeout=10)
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return sha.stdout.strip(), dirty
    except (OSError, subprocess.SubprocessError):
        return None, None


def describe_code(root=None):
    """Provenance block for the running code."""
    sha, dirty = git_sha(root)
    return {"git_sha": sha, "git_dirty": dirty}


def official_ready(sha, dirty, *, allow_dirty=False):
    """
    May code in this state produce an OFFICIAL signal? -> (bool, reason)

    A missing SHA means the run cannot say what code produced it. A dirty tree
    means the SHA does not describe what actually ran. Both are fine for local
    research and neither belongs on a record anybody is asked to trust.
    """
    if not sha:
        return False, "no git sha available; the run cannot identify its own code"
    if dirty and not allow_dirty:
        return False, "working tree is dirty; the recorded sha does not describe what ran"
    return True, None
