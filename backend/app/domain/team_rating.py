"""Pure domain logic for the internal team Elo rating (R1).

This module is intentionally self-contained: it imports nothing from the DB,
repositories, services, model-training or feature layers. It computes a
deterministic Elo rating from a list of input matches and produces auditable,
checksummed snapshots. It NEVER touches the database, predictions, the model
or the approval gate — wiring those in is a later phase (R2+).

Design reference: ``docs/team_rating_design.md`` (commit 2e91929).

Determinism guarantees:
  * matches are processed in ``(played_at, match_id)`` ascending order, so the
    caller's input ordering never affects the result;
  * ineligible matches (no score, conflict, sign-only) are skipped per config;
  * the Elo update is zero-sum per match;
  * checksums are pure functions of (config / sorted-input / sorted-output) and
    never depend on wall-clock time.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Iterable

ALGORITHM_VERSION = "elo_v1"

# Namespace used internally when ``namespaces_separated`` is False (one pool).
_GLOBAL_NAMESPACE = "global"
# Namespace assigned to universe teams that never appear in a rated match.
_UNKNOWN_NAMESPACE = "unknown"

# Float precision used everywhere a float is hashed or compared, so checksums
# are stable across processes/platforms.
_FLOAT_PRECISION = 6


class ConfidenceBucket(str, Enum):
    NO_RATING = "no_rating"   # 0 matches
    WEAK = "weak"             # 1-3
    MEDIUM = "medium"         # 4-9
    STRONG = "strong"         # 10+


def confidence_bucket(matches_count: int) -> ConfidenceBucket:
    if matches_count <= 0:
        return ConfidenceBucket.NO_RATING
    if matches_count <= 3:
        return ConfidenceBucket.WEAK
    if matches_count <= 9:
        return ConfidenceBucket.MEDIUM
    return ConfidenceBucket.STRONG


@dataclass(frozen=True)
class TeamRatingConfig:
    """Frozen, reproducible configuration for one rating run (elo_v1)."""

    algorithm_version: str = ALGORITHM_VERSION
    initial_rating: float = 1500.0
    k_base: float = 32.0
    home_advantage: float = 0.0
    goal_diff_enabled: bool = False
    goal_diff_cap: float = 1.75
    recency_decay_enabled: bool = False
    minimum_matches_for_confident_rating: int = 5
    exclude_conflicts: bool = True
    score_required: bool = True
    namespaces_separated: bool = True
    include_sign_only: bool = False


@dataclass(frozen=True)
class TeamRatingInputMatch:
    """One completed match fed to the calculator. Pure value object."""

    match_id: str
    played_at: datetime
    home_team_id: str
    away_team_id: str
    home_score: int | None
    away_score: int | None
    competition: str
    namespace: str
    is_conflict: bool = False
    is_sign_only: bool = False


@dataclass
class TeamRatingSnapshot:
    team_id: str
    namespace: str
    rating: float
    rating_delta: float
    matches_count: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    confidence_bucket: ConfidenceBucket
    last_result_at: datetime | None
    competitions_seen: list[str] = field(default_factory=list)


@dataclass
class TeamRatingRunSummary:
    algorithm_version: str
    source_match_count: int
    rated_match_count: int
    excluded_match_count: int
    excluded_reasons: dict[str, int]
    team_count: int
    config_checksum: str
    input_checksum: str
    output_checksum: str


# --- checksums --------------------------------------------------------------


def _nf(value: float) -> float:
    return round(float(value), _FLOAT_PRECISION)


def _sha(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def config_checksum(config: TeamRatingConfig) -> str:
    return _sha({
        "algorithm_version": config.algorithm_version,
        "initial_rating": _nf(config.initial_rating),
        "k_base": _nf(config.k_base),
        "home_advantage": _nf(config.home_advantage),
        "goal_diff_enabled": config.goal_diff_enabled,
        "goal_diff_cap": _nf(config.goal_diff_cap),
        "recency_decay_enabled": config.recency_decay_enabled,
        "minimum_matches_for_confident_rating": config.minimum_matches_for_confident_rating,
        "exclude_conflicts": config.exclude_conflicts,
        "score_required": config.score_required,
        "namespaces_separated": config.namespaces_separated,
        "include_sign_only": config.include_sign_only,
    })


def input_checksum(matches: Iterable[TeamRatingInputMatch]) -> str:
    """Order-independent checksum of the input match set."""
    rows = sorted(
        (
            [
                m.match_id,
                m.played_at.isoformat(),
                m.home_team_id,
                m.away_team_id,
                m.home_score,
                m.away_score,
                m.competition,
                m.namespace,
                bool(m.is_conflict),
                bool(m.is_sign_only),
            ]
            for m in matches
        ),
        key=lambda row: (row[1], row[0]),  # played_at, match_id
    )
    return _sha(rows)


def output_checksum(snapshots: Iterable[TeamRatingSnapshot]) -> str:
    rows = sorted(
        (
            [
                s.team_id,
                s.namespace,
                _nf(s.rating),
                s.matches_count,
                s.wins,
                s.draws,
                s.losses,
                s.goals_for,
                s.goals_against,
                s.confidence_bucket.value,
            ]
            for s in snapshots
        ),
        key=lambda row: (row[1], row[0]),  # namespace, team_id
    )
    return _sha(rows)


# --- calculator -------------------------------------------------------------


class TeamRatingCalculator:
    """Deterministic, pure Elo calculator. No I/O, no DB, no model."""

    def __init__(self, config: TeamRatingConfig | None = None) -> None:
        self.config = config or TeamRatingConfig()

    # -- eligibility --------------------------------------------------------

    def exclusion_reason(self, match: TeamRatingInputMatch) -> str | None:
        """Return why a match is excluded, or None if it is eligible."""
        cfg = self.config
        if cfg.score_required and (match.home_score is None or match.away_score is None):
            return "missing_score"
        if cfg.exclude_conflicts and match.is_conflict:
            return "conflict"
        if (not cfg.include_sign_only) and match.is_sign_only:
            return "sign_only"
        return None

    # -- elo math -----------------------------------------------------------

    def _goal_diff_multiplier(self, home_score: int, away_score: int) -> float:
        if not self.config.goal_diff_enabled:
            return 1.0
        gd = abs(home_score - away_score)
        return min(1.0 + math.log1p(gd), self.config.goal_diff_cap)

    def _namespace_for(self, match: TeamRatingInputMatch) -> str:
        return match.namespace if self.config.namespaces_separated else _GLOBAL_NAMESPACE

    # -- public API ---------------------------------------------------------

    def compute(
        self,
        matches: Iterable[TeamRatingInputMatch],
        *,
        team_universe: set[str] | None = None,
    ) -> tuple[dict[tuple[str, str], TeamRatingSnapshot], TeamRatingRunSummary]:
        """Compute ratings deterministically.

        Returns ``(snapshots, summary)`` where ``snapshots`` is keyed by
        ``(team_id, namespace)``.

        ``team_universe`` (optional): when given, every team_id in the set
        that produced NO rated match is emitted as a ``no_rating`` snapshot in
        the ``unknown`` namespace. When omitted, only teams that actually
        played appear in the result (the pure calculator does not invent the
        universe). This is the documented answer to "team with no matches".
        """
        cfg = self.config
        all_matches = list(matches)
        excluded: Counter[str] = Counter()
        eligible: list[TeamRatingInputMatch] = []
        for m in all_matches:
            reason = self.exclusion_reason(m)
            if reason is None:
                eligible.append(m)
            else:
                excluded[reason] += 1

        eligible.sort(key=lambda m: (m.played_at, m.match_id))

        ratings: dict[tuple[str, str], float] = {}
        snapshots: dict[tuple[str, str], TeamRatingSnapshot] = {}

        def _ensure(key: tuple[str, str], namespace: str) -> None:
            if key not in ratings:
                ratings[key] = cfg.initial_rating
                team_id, _ = key
                snapshots[key] = TeamRatingSnapshot(
                    team_id=team_id, namespace=namespace, rating=cfg.initial_rating,
                    rating_delta=0.0, matches_count=0, wins=0, draws=0, losses=0,
                    goals_for=0, goals_against=0,
                    confidence_bucket=ConfidenceBucket.NO_RATING,
                    last_result_at=None, competitions_seen=[],
                )

        for m in eligible:
            ns = self._namespace_for(m)
            hk, ak = (m.home_team_id, ns), (m.away_team_id, ns)
            _ensure(hk, ns)
            _ensure(ak, ns)
            rh, ra = ratings[hk], ratings[ak]
            exp_home = 1.0 / (1.0 + 10 ** ((ra - rh - cfg.home_advantage) / 400.0))
            hs, as_ = int(m.home_score or 0), int(m.away_score or 0)
            if hs > as_:
                score_home = 1.0
            elif hs == as_:
                score_home = 0.5
            else:
                score_home = 0.0
            k_eff = cfg.k_base * self._goal_diff_multiplier(hs, as_)
            delta = k_eff * (score_home - exp_home)
            ratings[hk] = rh + delta
            ratings[ak] = ra - delta  # zero-sum

            for key, gf, ga, is_home in ((hk, hs, as_, True), (ak, as_, hs, False)):
                s = snapshots[key]
                s.matches_count += 1
                s.goals_for += gf
                s.goals_against += ga
                if gf > ga:
                    s.wins += 1
                elif gf == ga:
                    s.draws += 1
                else:
                    s.losses += 1
                s.rating = ratings[key]
                s.rating_delta = round(delta if is_home else -delta, _FLOAT_PRECISION)
                s.last_result_at = m.played_at
                if m.competition not in s.competitions_seen:
                    s.competitions_seen.append(m.competition)

        for key, s in snapshots.items():
            s.rating = round(ratings[key], _FLOAT_PRECISION)
            s.confidence_bucket = confidence_bucket(s.matches_count)

        if team_universe is not None:
            played = {team_id for team_id, _ in snapshots}
            for team_id in sorted(team_universe):
                if team_id in played:
                    continue
                key = (team_id, _UNKNOWN_NAMESPACE)
                if key in snapshots:
                    continue
                snapshots[key] = TeamRatingSnapshot(
                    team_id=team_id, namespace=_UNKNOWN_NAMESPACE,
                    rating=round(cfg.initial_rating, _FLOAT_PRECISION), rating_delta=0.0,
                    matches_count=0, wins=0, draws=0, losses=0, goals_for=0, goals_against=0,
                    confidence_bucket=ConfidenceBucket.NO_RATING,
                    last_result_at=None, competitions_seen=[],
                )

        summary = TeamRatingRunSummary(
            algorithm_version=cfg.algorithm_version,
            source_match_count=len(all_matches),
            rated_match_count=len(eligible),
            excluded_match_count=len(all_matches) - len(eligible),
            excluded_reasons=dict(excluded),
            team_count=len(snapshots),
            config_checksum=config_checksum(cfg),
            input_checksum=input_checksum(all_matches),
            output_checksum=output_checksum(snapshots.values()),
        )
        return snapshots, summary


def default_config() -> TeamRatingConfig:
    """The proposed productive elo_v1 defaults (see design doc)."""
    return replace(TeamRatingConfig())
