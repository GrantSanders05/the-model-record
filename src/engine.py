"""
engine.py — the prediction model itself.

Everything that could be a judgement call is a CONFIG NUMBER, because the whole
point of this rebuild is to stop guessing constants and start fitting them.
The `x2` multiplier, home field advantage, and every win/loss quality point
value are parameters the optimizer can search.

Three rating sources, sharing one interface:

  GradeRater   Grant's film-based position group grades, read from the `grades`
               table. This is the real model and the brand's differentiator.

  EloRater     Self-updating power rating derived only from results. Needs no
               grades at all, which is what lets the whole pipeline be built
               and validated before the sheets are exported.

  BlendRater   Weighted blend of the two. This is the interesting one: it
               measures how much the film grades add ON TOP OF what results
               alone already tell you. If blending at 100% Elo scores as well
               as 100% grades, the grading work isn't paying for itself. If
               the blend beats both, the grades carry real independent signal.

WALK-FORWARD CONTRACT
Every rater exposes predict() and observe(). The backtester always calls
predict() for a game before observe() on that same game, and never calls
observe() out of chronological order. A rater must never read a score during
predict(). This is the single guard against the look-ahead bias that inflates
backtests.
"""

import math
from collections import defaultdict

# Grant's position groups, as they actually appear in the workbook.
# Coach and Special Teams are ONE combined column ("Coach/ST Score 15").
GROUPS_WIDE = ["qb", "ol", "dl", "coach_st"]        # graded 1-15 (floor ~10)
GROUPS_NARROW = ["rb", "wr", "lb", "db"]            # graded 1-10 (floor ~6.5)
ALL_GROUPS = GROUPS_WIDE + GROUPS_NARROW
# The seven the sheet double-counts; Coach/ST is deliberately excluded.
SEVEN_PLAYER_GROUPS = ["qb", "rb", "wr", "ol", "dl", "lb", "db"]


DEFAULT_CONFIG = {
    "rater": "elo",                # 'elo' | 'grades' | 'blend'
    "blend_weight": 0.5,           # weight on grades when rater='blend'

    # ── spread ──
    "scale": 1.0,                  # multiplier on rating difference -> points
    "hfa": 2.4,                    # home field advantage, points
    "neutral_hfa": 0.0,            # HFA applied at neutral sites. Measured: the
                                   # designated home team wins neutral games by
                                   # +0.92 +/- 1.38 and the market prices +0.09,
                                   # so zero is the honest value. Leave it.

    # ── Elo rater ──
    "elo_k": 22.0,
    "elo_start": 1500.0,
    "elo_hfa": 60.0,               # in Elo points
    "elo_revert": 0.25,            # season-to-season regression toward mean
    "elo_per_point": 25.0,         # Elo points per 1 point of spread
    "elo_mov": True,               # margin-of-victory multiplier
    "elo_fcs_rating": 1200.0,      # rating assigned to non-FBS opponents

    # ── grade rater ──
    "grade_formula": "sheet",      # 'sheet' = his exact arithmetic | 'fixed'/'tuned' = corrected
    "sheet_coach_weight": 1.0,     # 1.0 = Coach/ST counted half; 2.0 = equal to other groups
    "sheet_loss_sign": -1.0,       # -1.0 = as-is (a bad loss ADDS); +1.0 = a bad loss hurts
    "sheet_raw_wl": 1.0,           # 1.0 = raw W-L included; 0.0 = removed
    "grade_scale": 2.0,            # Grant's "doubled" -- the prime suspect
    "grade_weights": {g: 1.0 for g in ALL_GROUPS},

    # ── win/loss quality points (added to a team's running total) ──
    "wq_top5": 4.0,
    "wq_top10": 3.0,
    "wq_top25": 2.0,
    "wq_other": 0.0,
    "lq_ranked": 0.0,
    "lq_unranked_fbs": -2.0,
    "lq_fcs": -4.0,
    "quality_scale": 1.0,          # global dial on how much quality points matter

    # ── efficiency rater (SP+ family) ──
    "ppa_points_per_ppa": 55.0,    # net PPA/play -> points of margin
    "ppa_prior_games": 4.0,
    "ppa_carryover": 0.35,

    # ── market anchoring ──
    # THE professional technique: shrink the model toward the closing line and
    # only act on what survives. 0 = back your own number every week, 1 = you
    # are the market. Tuned, not assumed.
    "market_anchor": 0.0,

    # ── totals (pace x efficiency; independent of the spread ratings) ──
    "totals_enabled": True,
    "totals_prior_games": 3.0,     # shrink early-season rates toward league mean
    "totals_carryover": 0.0,       # weight on LAST season's team rate in the prior (0-1)
    "totals_scale": 1.0,
}


def merge_config(overrides=None):
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    return cfg


# ── rating sources ─────────────────────────────────────────────────────────────

class EloRater:
    """Standard Elo with margin-of-victory damping and season reversion."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.r = defaultdict(lambda: cfg["elo_start"])
        self._season = None

    def _rating(self, team, div):
        # Non-FBS opponents get a fixed low rating rather than polluting the pool.
        if div and div not in ("fbs", "nfl"):
            return self.cfg["elo_fcs_rating"]
        return self.r[team]

    def new_season(self, season):
        if self._season is not None:
            k = self.cfg["elo_revert"]
            base = self.cfg["elo_start"]
            for t in list(self.r):
                self.r[t] = base + (self.r[t] - base) * (1 - k)
        self._season = season

    def strength(self, game):
        """Return (home_pts, away_pts) contribution in POINTS, before HFA."""
        h = self._rating(game["home_team"], game["home_div"])
        a = self._rating(game["away_team"], game["away_div"])
        per = self.cfg["elo_per_point"] or 25.0
        return (h - a) / per

    def observe(self, game):
        hs, as_ = game["home_score"], game["away_score"]
        if hs is None or as_ is None:
            return
        cfg = self.cfg
        h_is_fbs = game["home_div"] in ("fbs", "nfl", None)
        a_is_fbs = game["away_div"] in ("fbs", "nfl", None)

        rh = self._rating(game["home_team"], game["home_div"])
        ra = self._rating(game["away_team"], game["away_div"])
        hfa = 0.0 if game["neutral_site"] else cfg["elo_hfa"]

        exp_h = 1.0 / (1.0 + 10 ** (-((rh + hfa) - ra) / 400.0))
        margin = hs - as_
        score_h = 1.0 if margin > 0 else (0.0 if margin < 0 else 0.5)

        k = cfg["elo_k"]
        if cfg["elo_mov"]:
            # Damps blowouts and corrects the favorite-bias in MOV updates.
            elo_diff = (rh + hfa) - ra
            mult = math.log(abs(margin) + 1.0) * (2.2 / (abs(elo_diff) * 0.001 + 2.2))
            k = k * mult

        delta = k * (score_h - exp_h)
        if h_is_fbs:
            self.r[game["home_team"]] = rh + delta
        if a_is_fbs:
            self.r[game["away_team"]] = ra - delta


# ── the win/loss quality rule ──────────────────────────────────────────────────
#
# Grant's own scoring, written in a note beside the columns of every weekly tab
# of the spreadsheet:
#
#     beat a top-5  +5      lose to a ranked team    0
#     beat a top-10 +4      lose to an unranked FBS  -4
#     beat a top-25 +3      lose to an FCS side      -4
#     beat anyone else 0
#
# Module level, and taking cfg rather than reading self, so that the exporter
# can grant the same points the rater grants. When this lived inside observe()
# the research site had no way to reach it and computed the team page's TOTAL
# from four spreadsheet columns instead -- columns that are empty for 2026, so
# the page showed bare position grades while the model bet on these. The page
# then told the reader the two were "the same number".

def win_points(cfg, opp_rank):
    """Points for beating a team ranked `opp_rank` (None = unranked)."""
    if opp_rank is None:
        return cfg["wq_other"]
    if opp_rank <= 5:
        return cfg["wq_top5"]
    if opp_rank <= 10:
        return cfg["wq_top10"]
    if opp_rank <= 25:
        return cfg["wq_top25"]
    return cfg["wq_other"]


def loss_points(cfg, opp_rank, opp_div):
    """Points for losing to that team. Negative, or zero for a ranked opponent."""
    if opp_div and opp_div not in ("fbs", "nfl"):
        return cfg["lq_fcs"]
    if opp_rank is not None and opp_rank <= 25:
        return cfg["lq_ranked"]
    return cfg["lq_unranked_fbs"]


class GradeRater:
    """
    Grant's film grades. Reproduces his arithmetic exactly:

        TEAM TOTAL = (sum of position grades) x grade_scale
                     + cumulative win/loss quality points

    `grade_scale` defaults to 2.0 (his "doubled") and is a search parameter --
    the measured calibration slope of 0.62 says the correct value is nearer 1.24.

    Grades are looked up as of the most recent week <= the game's week, so a
    grade published in week 7 is never used to predict a week 5 game.
    """

    def __init__(self, cfg, grades_by_team):
        self.cfg = cfg
        self.grades = grades_by_team      # {(season, team): [(week, {pos: grade})]}
        self.quality = defaultdict(float)
        # The same accrual, kept in its parts. `quality` is what the rating uses
        # and is unchanged; these exist so the team page can show a coach WHY a
        # rating moved -- "2-1, +5 for beating a top-5, -4 for the FCS loss" --
        # instead of one opaque number. Derived here rather than recomputed
        # anywhere else, because two implementations of a scoring rule is how
        # the page and the picks came to disagree in the first place.
        self.wins = defaultdict(int)
        self.losses = defaultdict(int)
        self.win_quality = defaultdict(float)
        self.loss_quality = defaultdict(float)
        self._season = None

    def new_season(self, season):
        self._season = season
        self.quality.clear()              # quality points are per-season
        self.wins.clear()
        self.losses.clear()
        self.win_quality.clear()
        self.loss_quality.clear()

    def record(self, team):
        """Everything accrued for one team this season, for display."""
        return {
            "wins": self.wins.get(team, 0),
            "losses": self.losses.get(team, 0),
            "win_points": round(self.win_quality.get(team, 0.0), 1),
            "loss_points": round(self.loss_quality.get(team, 0.0), 1),
            "quality": round(self.quality.get(team, 0.0), 1),
        }

    def _snapshot(self, season, team, week):
        """Most recent grade snapshot effective at or before `week`."""
        entries = self.grades.get((season, team))
        if not entries:
            return None
        best = None
        for w, gmap in entries:
            if w <= week and (best is None or w > best[0]):
                best = (w, gmap)
        return best[1] if best else None

    def _grade_total(self, season, team, week):
        g = self._snapshot(season, team, week)
        if g is None:
            return None
        formula = self.cfg.get("grade_formula", "sheet")

        if formula == "sheet":
            # Grant's spreadsheet, reproduced exactly (verified against its own
            # TOTAL column on 1,224 team-weeks with zero mismatches):
            #   2 x (7 player groups) + Coach/ST + WinPts - LossPts + W - L
            #
            # Each of its three oddities is a separate dial, so they can be
            # ablated ONE AT A TIME instead of bundled. Bundling them was a
            # mistake on the first pass: the combined "fix" scored worse and
            # there was no way to see which change caused it.
            #
            #   sheet_coach_weight  1.0 = as-is (Coach/ST counted once while the
            #                       other seven are counted twice); 2.0 = equal.
            #   sheet_loss_sign    -1.0 = as-is. LossPts is STORED negative and
            #                       SUBTRACTED, so a bad loss ADDS points.
            #                       +1.0 makes a bad loss actually hurt.
            #   sheet_raw_wl        1.0 = as-is (raw W-L added on top of the
            #                       quality points); 0.0 = removed.
            seven = sum(g.get(p, 0.0) for p in SEVEN_PLAYER_GROUPS)
            return (2.0 * seven
                    + self.cfg.get("sheet_coach_weight", 1.0) * g.get("coach_st", 0.0)
                    + g.get("_win_points", 0.0)
                    + self.cfg.get("sheet_loss_sign", -1.0) * g.get("_loss_points", 0.0)
                    + self.cfg.get("sheet_raw_wl", 1.0)
                    * (g.get("_wins", 0.0) - g.get("_losses", 0.0)))

        if formula == "computed":
            # THE SAME FORMULA AS 'sheet', WITH ONE THING CHANGED: the win/loss
            # quality points come from the games actually played instead of from
            # four spreadsheet columns. Everything else -- the doubled seven, the
            # Coach/ST weight -- is byte-identical, deliberately, because bundling
            # several changes at once is how the first pass produced a "fix" that
            # scored worse with no way to tell which part caused it.
            #
            # WHY THIS EXISTS. Those four columns (Wins, Losses, Win Points, Loss
            # Points) were filled in BY HAND. For 2026 they are empty on all 138
            # teams, so the whole quality term contributes exactly zero and the
            # ratings are bare position grades. Meanwhile `observe()` below has
            # been accruing the same quantity from real results on every run and
            # NOTHING HAS EVER READ IT -- a writer with no reader, the mirror of
            # the usual defect and just as dead.
            #
            # Safe because both callers are strictly predict-then-observe:
            # backtest.run() predicts at line ~129 and observes at ~146, and
            # predict.generate() observes each game only after any pick on it is
            # recorded. So self.quality holds prior games only, never this one.
            seven = sum(g.get(p, 0.0) for p in SEVEN_PLAYER_GROUPS)
            return (2.0 * seven
                    + self.cfg.get("sheet_coach_weight", 1.0) * g.get("coach_st", 0.0)
                    + self.cfg.get("quality_scale", 1.0) * self.quality.get(team, 0.0))

        # 'fixed' and 'tuned' share a corrected structure:
        #   * every position group weighted the same way (Coach/ST no longer half)
        #   * loss points ADDED with their stored sign, so a bad loss hurts
        #   * raw W-L dropped: it double-counts the quality points
        weights = self.cfg["grade_weights"]
        base = sum(g.get(p, 0.0) * weights.get(p, 1.0) for p in ALL_GROUPS)
        quality = g.get("_win_points", 0.0) + g.get("_loss_points", 0.0)
        return base * self.cfg["grade_scale"] + quality * self.cfg["quality_scale"]

    def _grade_parts(self, season, team, week):
        """(position-grade points, raw quality points) — the rating, unmixed.

        Only defined for `computed`, the only formula where the two halves are
        separable: under `sheet` the quality points live inside the snapshot and
        cannot be told apart from the position grades.
        """
        if self.cfg.get("grade_formula") != "computed":
            return None
        g = self._snapshot(season, team, week)
        if g is None:
            return None
        base = (2.0 * sum(g.get(p, 0.0) for p in SEVEN_PLAYER_GROUPS)
                + self.cfg.get("sheet_coach_weight", 1.0) * g.get("coach_st", 0.0))
        return base, self.quality.get(team, 0.0)

    def parts(self, game):
        """The two halves of `strength`, for calibration. See calibrate.py.

        WHY THIS IS EXPOSED. `scale` multiplies the whole rating, but the rating
        is two quantities with different units: position grades, which are fixed
        all season, and quality points, which start at zero and accumulate. One
        multiplier cannot be right for both, and measurably was not -- the
        calibration slope ran 1.09 over weeks 1-4 and 0.80 over weeks 11+, light
        on every early-season game and heavy on every late one. Splitting them
        needs the two differences separately, and deriving them here rather than
        in the fitter is what stops the fit from being run against arithmetic
        nobody ships.
        """
        season, week = game["season"], game["week"] or 1
        ph = self._grade_parts(season, game["home_team"], week)
        pa = self._grade_parts(season, game["away_team"], week)
        if ph is None or pa is None:
            return None
        return ph[0] - pa[0], ph[1] - pa[1]

    def strength(self, game):
        season, week = game["season"], game["week"] or 1
        gh = self._grade_total(season, game["home_team"], week)
        ga = self._grade_total(season, game["away_team"], week)
        if gh is None or ga is None:
            return None                    # caller falls back to Elo
        # _grade_total already returns a COMPLETE team rating -- scaling and
        # quality points are applied inside it. In 'sheet' mode the win/loss
        # points come from the spreadsheet; in 'computed' mode _grade_total reads
        # self.quality itself. Re-applying grade_scale or adding self.quality
        # here would double-count both.
        return gh - ga

    def observe(self, game):
        """Accrue win/loss quality points from a completed game."""
        hs, as_ = game["home_score"], game["away_score"]
        if hs is None or as_ is None:
            return
        cfg = self.cfg
        h_rank = game.get("home_rank")
        a_rank = game.get("away_rank")

        if hs > as_:
            winner, loser = game["home_team"], game["away_team"]
            wp = win_points(cfg, a_rank)
            lp = loss_points(cfg, h_rank, game["home_div"])
        elif as_ > hs:
            winner, loser = game["away_team"], game["home_team"]
            wp = win_points(cfg, h_rank)
            lp = loss_points(cfg, a_rank, game["away_div"])
        else:
            return                        # a tie earns neither side anything

        self.wins[winner] += 1
        self.losses[loser] += 1
        self.win_quality[winner] += wp
        self.loss_quality[loser] += lp
        self.quality[winner] += wp
        self.quality[loser] += lp


class BlendRater:
    """Weighted blend. Measures what the film grades add over results alone."""

    def __init__(self, cfg, grades_by_team):
        self.elo = EloRater(cfg)
        self.grades = GradeRater(cfg, grades_by_team)
        self.w = cfg["blend_weight"]

    def new_season(self, season):
        self.elo.new_season(season)
        self.grades.new_season(season)

    def strength(self, game):
        e = self.elo.strength(game)
        g = self.grades.strength(game)
        if g is None:
            return e
        return (1 - self.w) * e + self.w * g

    def observe(self, game):
        self.elo.observe(game)
        self.grades.observe(game)


# ── totals ─────────────────────────────────────────────────────────────────────

class TotalsModel:
    """
    Predicts combined score from opponent-adjusted scoring rates.

    This is deliberately NOT built from the position grades. A total is a
    function of tempo and efficiency; the grades encode relative strength.
    Two evenly matched teams have a spread of 0 and a total that could be 38
    or 58 -- no amount of grade arithmetic can distinguish those.

    Per team it tracks points scored and allowed per game, each adjusted for
    the quality of opposition faced, and shrunk toward the league mean early
    in the season when sample size is thin.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.off = defaultdict(list)      # team -> [points scored] this season
        self.dfn = defaultdict(list)      # team -> [points allowed] this season
        self.league = []                  # all team-game point totals this season
        # Carried across seasons so week 1 is not a cold start. Without this,
        # every season opens predicting 2x the league default for every game.
        self.prior_off = {}
        self.prior_def = {}
        self.prior_league = None

    def new_season(self, season):
        # Roll this season's rates forward as next season's prior. Teams change
        # a lot year to year, so this is a weak prior (worth `totals_prior_games`
        # games) -- but it is far better than no information at all.
        if self.league:
            self.prior_league = sum(self.league) / len(self.league)
            for t, v in self.off.items():
                if v:
                    self.prior_off[t] = sum(v) / len(v)
            for t, v in self.dfn.items():
                if v:
                    self.prior_def[t] = sum(v) / len(v)
        self.off.clear()
        self.dfn.clear()
        self.league = []

    def _lg_mean(self):
        if self.league:
            return sum(self.league) / len(self.league)
        if self.prior_league is not None:
            return self.prior_league
        return 24.0

    def _rate(self, series, prior_mean, prior_n):
        if not series:
            return prior_mean
        n = len(series)
        return (sum(series) + prior_mean * prior_n) / (n + prior_n)

    def predict(self, game):
        lg = self._lg_mean()
        pn = self.cfg["totals_prior_games"]
        ht, at = game["home_team"], game["away_team"]
        # How much last season's rate should inform this season's prior is an
        # empirical question, not a judgement call: `totals_carryover` blends
        # the team's prior-season rate with the league mean and the optimizer
        # picks the weight. Measured on CFB it prefers a LOW weight, because
        # roster turnover makes last year's scoring rate a poor guide.
        c = self.cfg.get("totals_carryover", 0.0)

        def prior(table, team):
            pv = table.get(team)
            return lg if pv is None else c * pv + (1 - c) * lg

        h_off = self._rate(self.off[ht], prior(self.prior_off, ht), pn)
        h_def = self._rate(self.dfn[ht], prior(self.prior_def, ht), pn)
        a_off = self._rate(self.off[at], prior(self.prior_off, at), pn)
        a_def = self._rate(self.dfn[at], prior(self.prior_def, at), pn)
        # Each side's expected points = own offense + opponent defense, re-centred
        # on the league mean so the two adjustments don't double-count.
        exp_home = h_off + a_def - lg
        exp_away = a_off + h_def - lg
        return (exp_home + exp_away) * self.cfg["totals_scale"]

    def observe(self, game):
        hs, as_ = game["home_score"], game["away_score"]
        if hs is None or as_ is None:
            return
        self.off[game["home_team"]].append(hs)
        self.dfn[game["home_team"]].append(as_)
        self.off[game["away_team"]].append(as_)
        self.dfn[game["away_team"]].append(hs)
        self.league.extend([hs, as_])


# ── the model ──────────────────────────────────────────────────────────────────

class Model:
    def __init__(self, config=None, grades_by_team=None, stats_by_game=None):
        self.cfg = merge_config(config)
        self._stats = stats_by_game
        g = grades_by_team or {}
        kind = self.cfg["rater"]
        if kind == "ppa":
            import pro_models
            self.rater = pro_models.PPARater(self.cfg, self._stats or {})
        elif kind == "elo":
            self.rater = EloRater(self.cfg)
        elif kind == "grades":
            self.rater = GradeRater(self.cfg, g)
        elif kind == "blend":
            self.rater = BlendRater(self.cfg, g)
        else:
            raise ValueError("unknown rater %r" % kind)
        self.totals = TotalsModel(self.cfg) if self.cfg["totals_enabled"] else None
        self._fallback = EloRater(self.cfg) if kind == "grades" else None
        self.predictions = 0
        self.fallbacks = 0
        # A grade rater with NO grades is not a grade rater. Every prediction would
        # fall through to Elo and the run would report itself as the grade model —
        # which is how a bakeoff came back showing "grades", "elo" and three blend
        # weights producing byte-identical results, and how any conclusion drawn
        # from it would have been about Elo.
        if kind in ("grades", "blend") and not g:
            raise ValueError(
                "rater %r needs film grades and none were supplied. Pass them as "
                "config['_grades'] (backtest.load_grades) — without them every "
                "prediction silently becomes an Elo prediction." % kind)

    def fallback_share(self):
        """What fraction of predictions the fallback rater answered, not this one."""
        return self.fallbacks / self.predictions if self.predictions else 0.0

    # ── home-field advantage ───────────────────────────────────────────────
    #
    # A constant, and deliberately so. Measured on 8,364 FBS-vs-FBS non-neutral
    # games since 2014: the home team wins by 4.26 (se 0.23) and the market
    # charges 4.49. The model shipped with 3.0, which is outside that interval --
    # about a point light on every home game, in the same direction every time.
    #
    # `hfa` is not rater-independent. It absorbs whatever mean offset a rater's
    # strength() carries, which is why one value cannot serve both: the grade
    # model's residual bias zeroes at ~3.9 and Elo's at ~2.5. Each config carries
    # its own, fitted to its own rater.
    #
    # AN ADAPTIVE VERSION WAS BUILT AND MEASURED AND IT LOST. Learning the
    # residual from finished games (leak-free, accumulated in observe()) is
    # obvious enough that someone will propose it again, so: over 2016-2025 it was
    # worse than a correct constant in seven seasons of ten, and worst of all in
    # 2020 and 2021 -- the empty-stadium regime break it exists to handle. It
    # enters a broken regime carrying the old one's evidence and spends the season
    # catching up. An estimator that lags a step change does not protect you from
    # step changes. The drift is real (2.13 in 2020, 5.40 in 2024) but it is not
    # tradeable in-season, and the market prices home field within a quarter point
    # anyway, so the whole available win is having the constant right.

    def hfa_for(self, game):
        """The home-field term for one game, in points."""
        return self.cfg["neutral_hfa"] if game["neutral_site"] else self.cfg["hfa"]

    def new_season(self, season):
        self.rater.new_season(season)
        if self._fallback:
            self._fallback.new_season(season)
        if self.totals:
            self.totals.new_season(season)

    def parts(self, game):
        """The rater's two rating halves, when it has them. See GradeRater.parts."""
        fn = getattr(self.rater, "parts", None)
        return fn(game) if fn else None

    def predict(self, game):
        """Predicted HOME margin (+ = home favored) and, optionally, total."""
        s = self.rater.strength(game)
        self.predictions += 1
        borrowed = False
        if s is None and self._fallback:
            # Counted, because a fallback is a DIFFERENT MODEL answering. One team
            # missing a grade is a fine reason to borrow Elo for that game; every
            # team missing one means the run is Elo wearing the grade model's name,
            # and it reports itself as the grade model all the way to the website.
            self.fallbacks += 1
            borrowed = True
            s = self._fallback.strength(game)
        if s is None:
            s = 0.0
        # AND IT NEEDS ITS OWN SCALE, for the reason `hfa` needs one: a scale is
        # fitted to a particular rater's units and carries that rater's mean
        # offset. Sharing it meant re-fitting the grade model silently re-tuned
        # Elo -- measured, when `scale` moved 0.997 -> 1.311 on the strength of
        # 808 graded games, the 126 borrowed ones went 65-61 to 62-64 against the
        # spread without anybody having fitted anything about them.
        # Defaults to `scale`, so a config that has not fitted one behaves exactly
        # as it did before this existed.
        scale = self.cfg.get("fallback_scale") if borrowed else None
        margin = s * (self.cfg["scale"] if scale is None else scale) + self.hfa_for(game)
        # Market anchoring happens LAST, after the model has had its say. The
        # closing line is public before kickoff, so using it is information, not
        # look-ahead.
        anchor = self.cfg.get("market_anchor", 0.0)
        if anchor > 0:
            import pro_models
            margin = pro_models.market_anchor(margin, game.get("market_margin"), anchor)
        out = {"pred_margin": margin, "borrowed": borrowed}
        if self.totals:
            out["pred_total"] = self.totals.predict(game)
        return out

    def observe(self, game):
        self.rater.observe(game)
        if self._fallback:
            self._fallback.observe(game)
        if self.totals:
            self.totals.observe(game)
