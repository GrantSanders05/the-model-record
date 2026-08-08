"""
game_ratings.py — turn EA roster overalls into grades on Grant's scales.

THE PROBLEM WITH AVERAGING OVERALLS
The obvious approach -- average every player's OVR in a position group -- is
wrong twice over.

First, depth is not talent. A team carrying seven halfbacks is not worse at
running back than a team carrying three; it just has more bodies. Roughly a
quarter of these rosters are engine-generated "filler" players, and they sit
disproportionately at the bottom of deep groups. A flat mean measures roster
construction, not the players who take the snaps. So the mean here is
DEPTH-WEIGHTED: the players who actually play carry the rating.

Second, the raw numbers are not comparable across groups. Quarterback overalls
run high and offensive line overalls run low, as an artefact of how EA rates
positions. Feeding raw averages into a formula that adds columns together would
bake that artefact in as if it were football.

THE FIX: MAP THE ORDER, NOT THE NUMBERS
The only thing taken from EA is the ORDERING of teams within each position
group. That ordering is then mapped onto the distribution Grant's own hand
grades occupy -- same centre, same spread, same declared floor and ceiling:

    QB / OL / DL / Coach-ST   1-15, realistic floor ~10
    RB / WR / LB / DB         1-10, realistic floor ~6.5

Because the shape of each column is preserved, every parameter the engine was
tuned on (scale, home-field, the win/loss point weights) remains valid. The
sheet still looks like Grant's sheet. What changes is who is ranked where --
which is the only thing a roster file actually knows.

None of this is assumed to help. `bakeoff_ratings.py` backtests it.
"""

import math

# --- EA position codes -> Grant's eight columns -------------------------------
# TE and FB are the two genuinely ambiguous ones: a tight end is a blocker and a
# receiver, a fullback is a runner and a blocker. Both are settled by backtest in
# `bakeoff_ratings.py` rather than by assertion, so both are variables here.
BASE_MAP = {
    "QB": "qb",
    "HB": "rb",
    "WR": "wr",
    "LT": "ol", "LG": "ol", "C": "ol", "RG": "ol", "RT": "ol",
    "LE": "dl", "RE": "dl", "DT": "dl",
    "LOLB": "lb", "MLB": "lb", "ROLB": "lb",
    "CB": "db", "FS": "db", "SS": "db",
}

# How many players at a group are on the field, and how much the ones behind
# them matter. A backup quarterback is nearly worthless until he plays; a
# fifth defensive lineman rotates in every series.
DEPTH_WEIGHTS = {
    "qb": [1.0, 0.18, 0.05],
    "rb": [1.0, 0.60, 0.28, 0.10],
    "wr": [1.0, 0.88, 0.72, 0.50, 0.28, 0.12],
    "ol": [1.0, 1.0, 1.0, 1.0, 1.0, 0.22, 0.10],
    "dl": [1.0, 0.92, 0.82, 0.70, 0.36, 0.16],
    "lb": [1.0, 0.86, 0.62, 0.26, 0.10],
    "db": [1.0, 0.92, 0.80, 0.62, 0.32, 0.14],
    "st": [1.0, 0.55],
}

POSITIONS = ["qb", "rb", "wr", "ol", "dl", "lb", "db", "coach_st"]
WIDE = {"qb", "ol", "dl", "coach_st"}          # 1-15 columns
NARROW = {"rb", "wr", "lb", "db"}              # 1-10 columns

# Grant's declared scales: 1-15 for the wide columns, 1-10 for the narrow ones.
#
# His "realistic floor" of ~10 / ~6.5 is a grading guideline, NOT a clamp, and
# clamping at it was a real bug. His own Coach/ST column runs down to 6.8 and his
# linebackers to 6.4 -- he does go below the guideline when a team deserves it.
# Clipping there truncated the bottom of every column and cost Coach/ST almost
# half its spread (sd 0.92 against his 1.60), which flows straight through to
# flattened margins. The distribution mapping already keeps values realistic
# because it reproduces his own mean and spread; the bound is only a backstop
# against an absurd value, so it is the declared scale and nothing tighter.
BOUNDS = {True: (1.0, 15.0), False: (1.0, 10.0)}    # keyed by is_wide


def position_map(te="wr", fb="rb"):
    m = dict(BASE_MAP)
    m["TE"] = te
    m["FB"] = fb
    return m


def group_values(players, pos_map, mode="weighted"):
    """
    Raw EA strength per group, before any rescaling.

    mode: "weighted"  depth-weighted mean (default)
          "starters"  flat mean of the on-field players only
          "flat"      flat mean of everyone (kept to prove it is worse)
    """
    buckets = {}
    for p in players:
        g = pos_map.get(p.get("POS"))
        if g and p.get("OVR") is not None:
            buckets.setdefault(g, []).append(float(p["OVR"]))

    out = {}
    for g, vals in buckets.items():
        vals.sort(reverse=True)
        w = DEPTH_WEIGHTS.get(g, [1.0])
        if mode == "flat":
            out[g] = sum(vals) / len(vals)
        elif mode == "starters":
            n = sum(1 for x in w if x >= 0.6) or 1
            top = vals[:n]
            out[g] = sum(top) / len(top)
        else:
            top = vals[:len(w)]
            ws = w[:len(top)]
            tot = sum(ws)
            out[g] = sum(v * x for v, x in zip(top, ws)) / tot if tot else 0.0
    return out


def special_teams(players, meta, pos_map, kicker_weight=0.5):
    """
    Coach/ST is one column in Grant's sheet, so it needs both halves.

    Special teams comes from the kicker and punter; coaching comes from the
    roster file's own prestige fields. They are combined on the z-scale later --
    here we just return the two raw pieces.
    """
    st = [float(p["OVR"]) for p in players
          if p.get("POS") in ("K", "P") and p.get("OVR") is not None]
    st_val = sum(sorted(st, reverse=True)[:2]) / max(1, len(st[:2])) if st else None

    td = (meta.get("teamData") or [{}])[0]
    coach = []
    for k in ("coachPrestige", "coachStability", "prestige"):
        v = td.get(k)
        if isinstance(v, (int, float)):
            coach.append(float(v))
    # coachStability runs 1-15ish while prestige runs 1-5; normalising each to a
    # 0-1 share of its own observed range happens in the z-step, so just carry
    # the pieces through separately.
    return st_val, (td.get("coachPrestige"), td.get("coachStability"),
                    td.get("prestige"), td.get("specialTeamsOVR"))


def _mean_sd(v):
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
    return m, sd


def _rank_z(values_by_team):
    """
    Rank -> normal score. Robust to outliers and to EA's odd scale gaps.

    Using the rank rather than the raw value means one absurd rating cannot drag
    a whole column, and the output shape is controlled entirely by the target
    distribution rather than by EA's.
    """
    n = len(values_by_team)
    order = sorted(values_by_team.items(), key=lambda kv: kv[1])
    out = {}
    for i, (team, _) in enumerate(order):
        p = (i + 0.5) / n                       # mid-rank plotting position
        out[team] = _probit(p)
    return out


def _probit(p):
    """Inverse normal CDF (Acklam's rational approximation). Stdlib only."""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > ph:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _empirical_quantile(sorted_vals, p):
    """Value at proportion `p` of a sorted sample, linearly interpolated."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    x = p * (n - 1)
    i = int(math.floor(x))
    if i >= n - 1:
        return sorted_vals[-1]
    frac = x - i
    return sorted_vals[i] * (1 - frac) + sorted_vals[i + 1] * frac


def to_grades(raw_by_team, target, spread=1.0, shape="empirical"):
    """
    Map each column's ORDERING onto the target distribution.

    `target` is {position: sorted list of Grant's grades in that column}.

    shape="empirical" (default) is a QUANTILE MAP: the team ranked k-th of n by
    EA gets the value at the same proportion through Grant's own sorted column.
    The output column is then his column, re-ordered -- same centre, same
    spread, and critically the same TAILS.

    That last part is not cosmetic. Fitting a normal curve instead (shape="normal")
    reproduced his mean and sd correctly but capped the extremes at +/-2.7 sd,
    which is all a normal has room for at n=138. His real grades have a much
    longer bottom tail -- his worst team sat 20 points below the field, not 12 --
    and that truncation shrank the model's TOTAL range from his 35 points to 28.
    Week 1 is disproportionately great-team-versus-terrible-team, so the missing
    tail landed squarely on the games being priced.

    `spread` widens or tightens around the centre; 1.0 leaves his shape alone.
    """
    teams = sorted(raw_by_team)
    out = {t: {} for t in teams}
    for pos in POSITIONS:
        col = {t: raw_by_team[t][pos] for t in teams
               if raw_by_team[t].get(pos) is not None}
        if len(col) < 5:
            continue
        vals = target.get(pos)
        if not vals:
            continue
        lo, hi = BOUNDS[pos in WIDE]
        mean, sd = _mean_sd(vals)

        if shape == "normal":
            for t, zz in _rank_z(col).items():
                v = mean + zz * sd * spread
                out[t][pos] = round(min(hi, max(lo, v)), 1)
            continue

        n = len(col)
        order = sorted(col.items(), key=lambda kv: kv[1])
        for i, (t, _) in enumerate(order):
            p = i / (n - 1) if n > 1 else 0.5
            v = _empirical_quantile(vals, p)
            if spread != 1.0:
                v = mean + (v - mean) * spread
            out[t][pos] = round(min(hi, max(lo, v)), 1)
    return out


def winsorize(vals, frac=0.01):
    """
    Pull the extreme ends of a column in to its 1st/99th percentile.

    The quantile map reproduces the target column faithfully, which is the whole
    point -- and also means a single bad cell becomes the floor of the new
    season's scale. Grant's 2025 `dl` column contains one value of 7.3 while the
    next lowest is 10.0: a linebacker-scale number typed into a 1-15 column,
    present in all nine weekly snapshots. Reproduced literally, it handed a
    2.7-point cliff to whichever 2026 team happened to rank last in DL, and made
    that team's best lineman look like the most valuable player in the country.

    Clipping ~1.4 values at each end of a 136-row column kills a lone outlier
    without flattening the genuine tail -- which matters, because truncating the
    tails too hard is what shrank the model's range once already.
    """
    if len(vals) < 20:
        return list(vals)
    v = sorted(vals)
    k = max(1, int(round(frac * len(v))))
    lo, hi = v[k], v[-1 - k]
    return [min(hi, max(lo, x)) for x in v]


def target_from_grades(conn, sport="cfb", season=2025, winsor=0.01):
    """Grant's own column values, sorted and de-spiked — the shape to reproduce."""
    rows = {}
    q = ("SELECT position, grade FROM grades WHERE sport=? AND season=? AND "
         "week=(SELECT MAX(week) FROM grades WHERE sport=? AND season=?)")
    for r in conn.execute(q, (sport, season, sport, season)):
        rows.setdefault(r["position"], []).append(r["grade"])
    return {p: winsorize(sorted(v), winsor)
            for p, v in rows.items() if len(v) > 5}


def suspect_grades(conn, sport="cfb", season=2025, gap=0.8):
    """
    Column values that sit far outside their own column — likely entry errors.

    Reports rather than silently corrects: the sheet is Grant's, and a value
    that looks wrong here may be a deliberate judgement there.
    """
    rows = {}
    q = ("SELECT position, team, grade FROM grades WHERE sport=? AND season=? AND "
         "week=(SELECT MAX(week) FROM grades WHERE sport=? AND season=?)")
    for r in conn.execute(q, (sport, season, sport, season)):
        rows.setdefault(r["position"], []).append((r["grade"], r["team"]))
    out = []
    for pos, vals in rows.items():
        if pos not in POSITIONS or len(vals) < 20:
            continue
        v = sorted(vals)
        if v[1][0] - v[0][0] >= gap:
            out.append({"position": pos, "team": v[0][1], "grade": v[0][0],
                        "next": v[1][0], "end": "low"})
        if v[-1][0] - v[-2][0] >= gap:
            out.append({"position": pos, "team": v[-1][1], "grade": v[-1][0],
                        "next": v[-2][0], "end": "high"})
    return out


def build(rosters, target, te="wr", fb="rb", mode="weighted", spread=1.0,
          kicker_weight=0.5):
    """
    rosters: {team_name: {"meta":..., "players":[...]}}
    returns: {team_name: {position: grade}}
    """
    pos_map = position_map(te=te, fb=fb)
    raw = {}
    for team, blob in rosters.items():
        players = blob.get("players") or []
        if not players:
            continue
        vals = group_values(players, pos_map, mode=mode)
        st_val, coach_bits = special_teams(players, blob.get("meta") or {}, pos_map)
        cp, cs, pres, st_ovr = coach_bits

        # Coach/ST: blend the kicking game with the programme's coaching marks.
        # Each piece is standardised across teams in the rank step below, so the
        # raw units here only need to be internally consistent.
        pieces, weights = [], []
        if st_val is not None:
            pieces.append(st_val / 99.0); weights.append(kicker_weight)
        if st_ovr is not None:
            pieces.append(float(st_ovr) / 99.0); weights.append(kicker_weight)
        for v, scale in ((cp, 10.0), (cs, 15.0), (pres, 5.0)):
            if isinstance(v, (int, float)):
                pieces.append(float(v) / scale)
                weights.append((1.0 - kicker_weight) / 3.0)
        vals["coach_st"] = (sum(p * w for p, w in zip(pieces, weights)) / sum(weights)
                            if pieces else None)
        raw[team] = vals
    return to_grades(raw, target, spread=spread)
