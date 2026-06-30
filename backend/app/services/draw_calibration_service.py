"""DB-aware wrapper that builds conservative draw priors from official,
comparable, FINAL results only — never demos / unverified slates.

Pure math lives in ``draw_calibration``; this module just sources the
observations (real canonical results for slates with official LN lineage) and
hands them to ``compute_draw_priors``. Read-only.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.repositories.canonical_result_repository import CanonicalResultRepository
from app.repositories.slate_repository import SlateRepository
from app.services.draw_calibration import DrawPrior, compute_draw_priors
from app.services.slate_classification_service import classify_slate


def build_draw_priors(session: Session) -> dict[str, DrawPrior]:
    """Compute global / weekend / midweek / fallback draw priors from official
    comparable slates' FINAL canonical results. Demos / unverified excluded."""
    observations: list[tuple[str, bool]] = []
    for slate in SlateRepository(session).list_slates():
        if not classify_slate(session, slate).comparable_with_results:
            continue
        match_ids = [sm.match_id for sm in slate.matches]
        canonical = CanonicalResultRepository(session).get_canonical_for_matches(match_ids)
        for result in canonical.values():
            observations.append((slate.week_type, result.result_code == "X"))
    return compute_draw_priors(observations)


def prior_for_week_type(priors: dict[str, DrawPrior], week_type: str) -> DrawPrior:
    """Pick the week_type-specific prior, falling back to global then fallback."""
    return priors.get(week_type) or priors.get("global") or priors["fallback"]
