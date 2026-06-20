"""Read-only team-rating feature adapter (R3) — DISABLED BY DEFAULT.

This module prepares the FUTURE rating features without touching any
productive path:

  * it does NOT import or modify ``FeatureService`` or ``PredictionService``;
  * :func:`rating_features_enabled` reads ``settings.team_rating_feature_enabled``
    (env ``PROAI_TEAM_RATING_FEATURE_ENABLED``), default ``False``;
  * :func:`load_rating_features` returns ``None`` when the flag is off, so a
    future caller that asks for features still gets "no signal" and the model
    is unaffected.

Feature contract (see ``docs/team_rating_design.md`` §4):
  home_rating, away_rating, rating_diff, home_rating_confidence,
  away_rating_confidence, both_rating_medium_plus, rating_namespace,
  rating_match_count_diff, rating_present.

Missing-rating behaviour:
  * a side with no snapshot / 0 matches → neutral 1500 for arithmetic but
    NOT counted as present;
  * ``rating_present`` is True only when BOTH sides have a real rating, so a
    no_rating side can never unblock a match by itself;
  * ``weak`` (1–3 matches) counts as present but ``both_rating_medium_plus``
    stays False, so it cannot promote a pick on its own.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.domain.team_rating import ALGORITHM_VERSION
from app.domain.team_rating import TeamRatingConfig
from app.models.team_rating import TeamRatingSnapshotModel
from app.repositories.team_rating_repository import TeamRatingRepository

# bucket → ordinal so the model can treat confidence as a numeric feature.
_CONFIDENCE_ORDINAL = {"no_rating": 0, "weak": 1, "medium": 2, "strong": 3}

_NEUTRAL_RATING = TeamRatingConfig().initial_rating
_MEDIUM_PLUS_MIN_MATCHES = 4


@dataclass(frozen=True)
class RatingFeatures:
    home_rating: float
    away_rating: float
    rating_diff: float
    home_rating_confidence: int
    away_rating_confidence: int
    both_rating_medium_plus: bool
    rating_namespace: str
    rating_match_count_diff: int
    rating_present: bool

    def as_dict(self) -> dict:
        return asdict(self)


def rating_features_enabled() -> bool:
    """Master switch. False by default → the adapter is inert."""
    return bool(settings.team_rating_feature_enabled)


def _side(snapshot: TeamRatingSnapshotModel | None) -> tuple[float, int, bool, int]:
    """(rating, confidence_ordinal, present, matches_count) for one team."""
    if snapshot is None or snapshot.matches_count <= 0:
        return _NEUTRAL_RATING, 0, False, 0
    confidence = _CONFIDENCE_ORDINAL.get(snapshot.confidence_bucket, 0)
    return float(snapshot.rating), confidence, True, snapshot.matches_count


def build_rating_features(
    home_snapshot: TeamRatingSnapshotModel | None,
    away_snapshot: TeamRatingSnapshotModel | None,
    *,
    namespace: str,
) -> RatingFeatures:
    """Pure feature builder from two snapshots. No DB, no flag check —
    deterministic and unit-testable in isolation."""
    home_rating, home_conf, home_present, home_n = _side(home_snapshot)
    away_rating, away_conf, away_present, away_n = _side(away_snapshot)
    both_present = home_present and away_present
    both_medium_plus = home_n >= _MEDIUM_PLUS_MIN_MATCHES and away_n >= _MEDIUM_PLUS_MIN_MATCHES
    return RatingFeatures(
        home_rating=round(home_rating, 6),
        away_rating=round(away_rating, 6),
        # rating_diff is meaningful only when both sides are present; keep it
        # neutral (0.0) otherwise so a missing side never fabricates a gap.
        rating_diff=round(home_rating - away_rating, 6) if both_present else 0.0,
        home_rating_confidence=home_conf,
        away_rating_confidence=away_conf,
        both_rating_medium_plus=both_medium_plus,
        rating_namespace=namespace,
        rating_match_count_diff=home_n - away_n,
        rating_present=both_present,
    )


def load_rating_features(
    session: Session,
    home_team_id: str,
    away_team_id: str,
    *,
    namespace: str,
    algorithm_version: str = ALGORITHM_VERSION,
) -> RatingFeatures | None:
    """Load both snapshots from the latest active run and build features.

    Returns ``None`` when the feature flag is off (the default), so this can
    be called from a future feature layer without changing behaviour until
    the flag is explicitly enabled AND the caller is wired in.
    """
    if not rating_features_enabled():
        return None
    repo = TeamRatingRepository(session)
    home = repo.get_team_snapshot(
        home_team_id, namespace, algorithm_version=algorithm_version
    )
    away = repo.get_team_snapshot(
        away_team_id, namespace, algorithm_version=algorithm_version
    )
    return build_rating_features(home, away, namespace=namespace)
