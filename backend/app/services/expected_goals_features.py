"""Pure feature builders for the XGBoost-based Expected Goals model.

This module sits one layer below `expected_goals_service` so the
features themselves can be unit-tested with plain Python dicts — no
SQLAlchemy session, no repository, no XGBoost. The service is then a
thin orchestrator that reads matches from the repository, hands the
relevant context dicts to these builders, and feeds the resulting rows
into a Booster.

We model goals at the **per-side** granularity: every historical match
contributes two training rows (home-perspective + away-perspective)
sharing the same kickoff and the same `home_indicator` flag flipped. At
prediction time we call the model twice per match (once with the home
features, once with the away features) to recover ``(home_lambda,
away_lambda)`` for Dixon-Coles.

Why per-side instead of jointly modelling (home_goals, away_goals): the
single-output regressor doubles the training sample size, lets us reuse
the same XGBoost reg:squarederror tree without the multi-output gluing
xgboost.dask requires, and lines up naturally with the way the existing
Poisson lambda priors are computed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Ordering matters — the trainer pins this list into the artifact so
# inference does not rely on dict ordering. Add new features at the end
# and bump `FEATURE_VERSION` rather than mutating an existing slot.
FEATURE_NAMES: tuple[str, ...] = (
    "rolling_goals_for_5",
    "rolling_goals_against_5",
    "rolling_goals_for_10",
    "rolling_goals_against_10",
    "points_per_match_10",
    "home_indicator",
    "competition_baseline_home_goals",
    "competition_baseline_away_goals",
    "days_rest",
)
FEATURE_VERSION: str = "xg_v1"

# Long-run averages used when a team has zero prior matches in the
# rolling window. Anchored on the same defaults as the heuristic
# `_competition_lambda_priors` so a brand-new team scores like the
# league average rather than a zero blackhole that the booster would
# overfit to.
DEFAULT_COMPETITION_HOME_GOALS: float = 1.45
DEFAULT_COMPETITION_AWAY_GOALS: float = 1.15
# Days-rest is clamped so a multi-year gap (preseason returns, lost
# imports) does not blow up the tree splits. The 30-day ceiling matches
# the longest mid-season gap most leagues see in practice.
MAX_DAYS_REST: float = 30.0


@dataclass(frozen=True)
class TeamRecentHistory:
    """A team's recent results from the perspective of *that* team.

    `goals_for` and `goals_against` are aligned with `kickoffs` and
    `points` index-by-index; the most-recent match sits at the end of
    each list. Empty lists are valid — the feature builder substitutes
    league baselines in that case."""

    goals_for: list[int]
    goals_against: list[int]
    points: list[int]
    kickoffs: list[datetime]


@dataclass(frozen=True)
class CompetitionBaseline:
    """Long-run mean goals per league. Cached and re-used across all
    matches in the same competition so the trainer does not recompute
    them per row."""

    home_goals: float = DEFAULT_COMPETITION_HOME_GOALS
    away_goals: float = DEFAULT_COMPETITION_AWAY_GOALS


def _mean(values: list[int], default: float = 0.0) -> float:
    if not values:
        return default
    return sum(values) / len(values)


def _rolling_window(history: TeamRecentHistory, window: int) -> tuple[float, float]:
    """Return (goals_for_avg, goals_against_avg) over the last `window`."""
    gf = history.goals_for[-window:]
    ga = history.goals_against[-window:]
    return _mean(gf), _mean(ga)


def _days_rest(history: TeamRecentHistory, kickoff: datetime) -> float:
    """Days between the team's previous match and this one, clamped to
    `MAX_DAYS_REST`. Falls back to the ceiling when there is no prior
    match (model treats brand-new teams as fully rested)."""
    if not history.kickoffs:
        return MAX_DAYS_REST
    last = history.kickoffs[-1]
    delta_days = (kickoff - last).total_seconds() / 86400.0
    if delta_days <= 0:
        return 0.0
    return min(delta_days, MAX_DAYS_REST)


def build_feature_row(
    *,
    history: TeamRecentHistory,
    is_home: bool,
    kickoff: datetime,
    competition_baseline: CompetitionBaseline,
) -> dict[str, float]:
    """Return one feature row aligned with FEATURE_NAMES.

    The caller is responsible for slicing `history` to events that
    actually precede `kickoff` (no leakage). This function does not
    re-validate that invariant — it would have to scan kickoffs again,
    which doubles the cost in the inner loop of training."""
    gf5, ga5 = _rolling_window(history, 5)
    gf10, ga10 = _rolling_window(history, 10)
    last_points = history.points[-10:]
    return {
        "rolling_goals_for_5": gf5,
        "rolling_goals_against_5": ga5,
        "rolling_goals_for_10": gf10,
        "rolling_goals_against_10": ga10,
        "points_per_match_10": _mean(last_points),
        "home_indicator": 1.0 if is_home else 0.0,
        "competition_baseline_home_goals": competition_baseline.home_goals,
        "competition_baseline_away_goals": competition_baseline.away_goals,
        "days_rest": _days_rest(history, kickoff),
    }


def row_to_vector(row: dict[str, float]) -> list[float]:
    """Materialize the feature row into the FEATURE_NAMES-ordered vector
    XGBoost expects. Missing keys fall back to 0.0 so an extended row
    from a future schema still trains; the trainer pins the FEATURE_NAMES
    list into the artifact so inference can spot a real mismatch."""
    return [float(row.get(name, 0.0)) for name in FEATURE_NAMES]


def slice_history_before(
    *,
    full_history: TeamRecentHistory,
    cutoff: datetime,
) -> TeamRecentHistory:
    """Trim a team's history to events strictly before `cutoff`.

    Used at training time to prevent look-ahead leakage: when we score
    match M, the team's history must only contain matches kicked off
    before M's kickoff. Returns a fresh TeamRecentHistory; the original
    is not mutated."""
    kept_gf: list[int] = []
    kept_ga: list[int] = []
    kept_p: list[int] = []
    kept_k: list[datetime] = []
    for gf, ga, p, k in zip(
        full_history.goals_for,
        full_history.goals_against,
        full_history.points,
        full_history.kickoffs,
        strict=True,
    ):
        if k < cutoff:
            kept_gf.append(gf)
            kept_ga.append(ga)
            kept_p.append(p)
            kept_k.append(k)
    return TeamRecentHistory(
        goals_for=kept_gf,
        goals_against=kept_ga,
        points=kept_p,
        kickoffs=kept_k,
    )


def points_for_result(side_goals: int, opponent_goals: int) -> int:
    """W=3, D=1, L=0 from one side's perspective."""
    if side_goals > opponent_goals:
        return 3
    if side_goals == opponent_goals:
        return 1
    return 0


def append_match_to_history(
    history: TeamRecentHistory,
    *,
    goals_for: int,
    goals_against: int,
    kickoff: datetime,
) -> TeamRecentHistory:
    """Return a new history with this match appended at the end.

    Caller is responsible for ordering: pass matches in chronological
    order, oldest first, so the rolling window slices off the most
    recent N events. This function does not sort defensively."""
    return TeamRecentHistory(
        goals_for=history.goals_for + [goals_for],
        goals_against=history.goals_against + [goals_against],
        points=history.points + [points_for_result(goals_for, goals_against)],
        kickoffs=history.kickoffs + [kickoff],
    )


def empty_history() -> TeamRecentHistory:
    return TeamRecentHistory(goals_for=[], goals_against=[], points=[], kickoffs=[])
