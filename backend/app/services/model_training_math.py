"""Pure mathematical helpers extracted from ModelTrainingArtifactsMixin.

Everything in this module is side-effect free, has no `self` dependency, and
does not touch the database, logging, or settings. That property is what lets
the test suite exercise them as plain functions without spinning up a full
training service. The mixin keeps thin delegating methods so external callers
that still reach for `self._poisson_pmf` etc. continue to work, but new code
should import these functions directly.
"""

from __future__ import annotations

import math
from datetime import datetime

DIXON_COLES_GRID_MAX = 8


def poisson_pmf(lam: float, k: int) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def dixon_coles_rho_from_draw_rate(league_draw_rate: float) -> float:
    """Heuristic mapping from observed draw rate to the DC rho parameter.

    rho > 0 inflates low-score draws (0-0, 1-1). The original DC paper keeps
    rho in roughly [-0.2, 0.2]. We scale it from the gap between the empirical
    draw rate and a neutral 0.26 baseline."""
    clamped = max(min(league_draw_rate, 0.45), 0.18)
    return max(min((clamped - 0.26) * 1.4, 0.2), -0.2)


def dixon_coles_tau(
    home_goals: int,
    away_goals: int,
    home_lambda: float,
    away_lambda: float,
    rho: float,
) -> float:
    """Dixon-Coles correction factor for the low-score cells.

    Outside (0,0), (0,1), (1,0), (1,1) tau is 1 (independent Poisson)."""
    if home_goals == 0 and away_goals == 0:
        return 1.0 - home_lambda * away_lambda * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + home_lambda * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + away_lambda * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def dixon_coles_outcome(
    home_lambda: float,
    away_lambda: float,
    rho: float,
    max_goals: int = DIXON_COLES_GRID_MAX,
) -> tuple[float, float, float]:
    """P(home win), P(draw), P(away win) from a DC-corrected score grid."""
    home_pmf = [poisson_pmf(home_lambda, k) for k in range(max_goals + 1)]
    away_pmf = [poisson_pmf(away_lambda, k) for k in range(max_goals + 1)]
    p_home = 0.0
    p_draw = 0.0
    p_away = 0.0
    for i, p_i in enumerate(home_pmf):
        for j, p_j in enumerate(away_pmf):
            cell = p_i * p_j * dixon_coles_tau(i, j, home_lambda, away_lambda, rho)
            if i > j:
                p_home += cell
            elif i == j:
                p_draw += cell
            else:
                p_away += cell
    total = p_home + p_draw + p_away
    if total <= 0:
        return (1.0 / 3, 1.0 / 3, 1.0 / 3)
    return (p_home / total, p_draw / total, p_away / total)


def brier_score(probabilities: list[float], actual_index: int) -> float:
    return sum(
        (probability - (1.0 if index == actual_index else 0.0)) ** 2
        for index, probability in enumerate(probabilities)
    )


def log_loss(probabilities: list[float], actual_index: int) -> float:
    clipped = min(max(probabilities[actual_index], 1e-6), 1 - 1e-6)
    return -math.log(clipped)


def time_decay_weights(
    played_at: list[datetime],
    *,
    half_life_days: float = 365.0,
) -> list[float]:
    """Exponential decay so matches from prior seasons contribute less.

    Anchored on the most recent match in the training set so a backfill run
    today produces the same weights regardless of wall clock. A half-life of
    one season means a match from one season ago carries half the weight of
    a freshly-played one — enough decay to downweight stale tactical eras
    without throwing them away.
    """
    if not played_at:
        return []
    anchor_ts = max(dt.timestamp() for dt in played_at)
    weights: list[float] = []
    for dt in played_at:
        age_days = (anchor_ts - dt.timestamp()) / 86400.0
        weights.append(math.exp(-age_days / half_life_days))
    return weights


def safe_rate(numerator: float | int | None, denominator: float | int | None) -> float:
    denominator_value = float(denominator or 0.0)
    if denominator_value <= 0:
        return 0.0
    return float(numerator or 0.0) / denominator_value
