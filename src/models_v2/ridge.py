"""
ridge.py — regularized least squares, in the standard library.

No numpy, no scikit-learn. The design constraint is deliberate: this repository's
model, backtester and optimizer are pure stdlib, which is why they run anywhere
and why a workflow needs no dependency step for them. A ridge fit is a symmetric
system of a dozen equations, and the Gaussian elimination for it already exists
in calibrate.py.

    beta = (X'X + lambda*I)^-1 X'y

WHAT THE REGULARIZATION IS FOR. With a dozen correlated grade differences and a
few hundred games, unpenalized least squares will happily fit the noise and
report a beautiful in-sample R². Ridge shrinks the coefficients toward zero by an
amount chosen on data the fit did not see.

TWO THINGS THAT ARE EASY TO GET WRONG AND ARE DONE HERE:

  * THE INTERCEPT IS NOT PENALIZED. Shrinking it pulls every prediction toward
    zero, which for a margin model means toward a pick'em, and that is not a
    prior anybody holds.
  * STANDARDIZATION USES TRAINING STATISTICS ONLY, and they are stored with the
    artifact. Standardizing with the full sample's mean leaks the test set into
    the fit, quietly and by a small amount, which is the hardest kind to notice.
"""

import math


def _solve(A, b):
    """Gaussian elimination with partial pivoting on a small dense system."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        if abs(M[c][c]) < 1e-12:
            raise ValueError("the design matrix is singular at column %d" % c)
        for r in range(n):
            if r == c:
                continue
            f = M[r][c] / M[c][c]
            for k in range(c, n + 1):
                M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def standardize(rows, features):
    """
    (means, sds) over the training rows only. -> (dict, dict)

    A feature with zero variance gets sd 1.0 and is recorded as such: dividing by
    zero would produce NaN and silently poison every coefficient, and dropping it
    without saying so would make the artifact's feature list a lie.
    """
    means, sds = {}, {}
    for f in features:
        vals = [r[f] for r in rows if r.get(f) is not None]
        m = sum(vals) / len(vals) if vals else 0.0
        var = (sum((v - m) ** 2 for v in vals) / len(vals)) if vals else 0.0
        means[f] = m
        sds[f] = math.sqrt(var) if var > 1e-12 else 1.0
    return means, sds


def design(rows, features, means, sds):
    return [[((r.get(f) if r.get(f) is not None else means[f]) - means[f]) / sds[f]
             for f in features] for r in rows]


def fit_ridge(rows, features, target, lam):
    """
    Fit with an UNPENALIZED intercept. -> dict artifact

    `rows` are dicts, `features` an ordered list of keys, `target` a key.
    """
    train = [r for r in rows if r.get(target) is not None]
    if len(train) <= len(features) + 1:
        raise ValueError("only %d usable row(s) for %d feature(s)"
                         % (len(train), len(features)))
    means, sds = standardize(train, features)
    X = design(train, features, means, sds)
    y = [r[target] for r in train]

    # The intercept is fitted as the target's mean on standardized predictors
    # (which are centred), so it never enters the penalized system at all.
    y_mean = sum(y) / len(y)
    yc = [v - y_mean for v in y]

    k = len(features)
    A = [[sum(X[i][a] * X[i][b] for i in range(len(X))) for b in range(k)]
         for a in range(k)]
    for i in range(k):
        A[i][i] += lam
    b = [sum(X[i][a] * yc[i] for i in range(len(X))) for a in range(k)]
    beta = _solve(A, b)

    return {
        "features": list(features), "lambda": lam,
        "means": means, "sds": sds,
        "zero_variance": sorted(f for f in features if sds[f] == 1.0
                                and all(abs((r.get(f) or means[f]) - means[f]) < 1e-12
                                        for r in train)),
        "coefficients": dict(zip(features, beta)),
        "intercept": y_mean, "n_train": len(train), "target": target,
    }


def predict_ridge(artifact, row):
    """Apply a fitted artifact to one row. -> float"""
    total = artifact["intercept"]
    for f in artifact["features"]:
        v = row.get(f)
        if v is None:
            v = artifact["means"][f]           # the training mean, i.e. no signal
        z = (v - artifact["means"][f]) / artifact["sds"][f]
        total += artifact["coefficients"][f] * z
    return total


def choose_lambda(train, valid, features, target, candidates):
    """
    Pick lambda on data the fit did not see. -> (lam, [(lam, rmse)])

    Chosen on a VALIDATION split, never on the training rows and never on the
    prospective season. A lambda selected by the score it produces on the data it
    was fitted to is not regularization, it is a longer way of writing zero.
    """
    scored = []
    for lam in candidates:
        try:
            art = fit_ridge(train, features, target, lam)
        except ValueError:
            continue
        errs = [(predict_ridge(art, r) - r[target]) ** 2
                for r in valid if r.get(target) is not None]
        if errs:
            scored.append((lam, math.sqrt(sum(errs) / len(errs))))
    if not scored:
        raise ValueError("no lambda could be scored; check the validation split")
    best = min(scored, key=lambda t: t[1])[0]
    return best, scored


class Ridge:
    """A fitted artifact with a predict()."""

    def __init__(self, artifact):
        self.artifact = artifact

    def predict(self, row):
        return predict_ridge(self.artifact, row)
