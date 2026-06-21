"""Read-only Team Rating Shadow report for a single slate (R5.4).

This builds the diagnostic payload the UI renders. It is strictly read-only and
shadow-only: it never writes a row, never regenerates predictions, never loads
a model artifact, and never changes probabilities, picks or tickets.

All gate / routing / calibrator rules are reused verbatim from the already
audited ``scripts.audit_team_rating_shadow`` module — we do not re-derive any
rule here. That module is importable from the API process because uvicorn runs
with ``--app-dir /app/backend`` (and pytest puts ``backend/`` on ``sys.path``).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.models.tables import ProgolSlateModel
from app.schemas.team_rating_shadow import ShadowActiveRun
from app.schemas.team_rating_shadow import ShadowCalibratorCandidate
from app.schemas.team_rating_shadow import ShadowMatch
from app.schemas.team_rating_shadow import ShadowSummary
from app.schemas.team_rating_shadow import TeamRatingShadowResponse
from scripts.audit_team_rating_shadow import audit_shadow
from scripts.audit_team_rating_shadow import _enforce_read_only_transaction

# Default what-if scenario surfaced to the UI. The *current* gate state is
# always reported truthfully (eligible_current uses the real OFF flag); these
# only drive the "if enabled" shadow projection.
DEFAULT_CALIBRATOR_CANDIDATE_ID = "international_friendlies_temperature_v1"
DEFAULT_ROUTING_POLICY = "rating_replaces_fallback"


def build_slate_shadow_report(
    session: Session,
    slate: ProgolSlateModel,
    *,
    assume_gate_enabled: bool = True,
    routing_policy: str = DEFAULT_ROUTING_POLICY,
    calibrator_candidate_id: str | None = DEFAULT_CALIBRATOR_CANDIDATE_ID,
    assume_calibrator_candidate_available: bool = True,
) -> TeamRatingShadowResponse:
    # Belt-and-suspenders: force the live PostgreSQL session read-only so an
    # accidental write inside the reused audit path fails fast.
    _enforce_read_only_transaction(session)

    links = sorted(slate.matches, key=lambda link: link.position)
    audit = audit_shadow(
        session,
        links,
        assume_gate_enabled=assume_gate_enabled,
        assume_calibrator_available=False,
        routing_policy=routing_policy,
        calibrator_candidate_id=calibrator_candidate_id,
        assume_calibrator_candidate_available=assume_calibrator_candidate_available,
    )
    return _shape_report(slate, audit)


def _shape_report(
    slate: ProgolSlateModel, audit: dict[str, Any]
) -> TeamRatingShadowResponse:
    gate_config = audit["gate_config"]
    run = audit["active_run"]
    candidate_raw = audit.get("calibrator_candidate")

    calibrator: ShadowCalibratorCandidate | None = None
    if candidate_raw is not None:
        calibrator = ShadowCalibratorCandidate(
            id=candidate_raw["candidate_id"],
            competition=candidate_raw["competition"],
            temperature=candidate_raw["temperature"],
            routing_policy=candidate_raw["routing_policy"],
            productive_available=candidate_raw["productive_available"],
            compatible=bool(gate_config["calibrator_compatible"]),
            compatibility_blockers=list(
                gate_config["calibrator_compatibility_blockers"]
            ),
        )

    summary_raw = audit["summary"]
    summary = ShadowSummary(
        total_matches=summary_raw["total_matches"],
        eligible_current=summary_raw["eligible_current"],
        eligible_if_enabled=summary_raw["eligible_if_enabled"],
        would_use_rating_model_current=summary_raw["would_use_rating_model_current"],
        would_use_rating_model_if_enabled=summary_raw[
            "would_use_rating_model_if_enabled"
        ],
        would_remain_fallback=summary_raw["would_remain_fallback"],
        blocked_by_flag=summary_raw["blocked_by_flag"],
        blocked_by_competition=summary_raw["blocked_by_competition"],
        blocked_by_rating=summary_raw["blocked_by_rating"],
        blocked_by_calibrator=summary_raw["blocked_by_calibrator"],
        blocked_by_sanity=summary_raw["blocked_by_sanity"],
        warnings=summary_raw["warnings"],
        positions_eligible_if_enabled=summary_raw["positions_eligible_if_enabled"],
        positions_would_route=summary_raw["positions_would_route"],
        positions_blocked=summary_raw["positions_blocked"],
    )

    matches = [
        ShadowMatch(
            position=row["position"],
            match_id=row["match_id"],
            home_team=row["home_team"],
            away_team=row["away_team"],
            competition=row["competition"],
            rating_status=row["rating_status"],
            rating_diff=row["rating_diff"],
            both_medium_plus=row["both_medium_plus"],
            eligible_current=row["eligible_current"],
            eligible_if_enabled=row["eligible_if_enabled"],
            would_use_rating_model_if_enabled=row["would_use_rating_model"],
            blockers=list(row["blockers"]),
            warnings=list(row["warnings"]),
        )
        for row in audit["rows"]
    ]

    return TeamRatingShadowResponse(
        slate_id=slate.id,
        draw_code=getattr(slate, "draw_code", None),
        mode="shadow_only",
        production_active=False,
        feature_flag_enabled=settings.team_rating_gate_enabled,
        gate_flag_enabled=bool(gate_config["team_rating_gate_enabled"]),
        routing_policy=gate_config["routing_policy"],
        active_rating_run=ShadowActiveRun(
            run_id=run["run_id"],
            algorithm_version=run["algorithm_version"],
            status=run["status"],
            snapshot_count=run["snapshot_count"],
        ),
        calibrator_candidate=calibrator,
        summary=summary,
        matches=matches,
    )
