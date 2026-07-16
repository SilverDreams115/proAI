"""Read-only readiness report for active Progol slates.

This report explains why a match is not "listo" without changing prediction,
ticket, result or training state. It is intentionally stricter than Money Mode:
it never proposes relabeling a REVISAR match when a hard guardrail is present.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateModel
from app.services.slate_service import SlateService
from app.services.team_name_quality_service import suspicious_team_names
from app.services.team_name_quality_service import team_name_issue_flags

_HARD_REVIEW_FLAGS = frozenset(
    {
        "LOW_EVIDENCE",
        "FALLBACK_USED",
        "EXTREME_PROBABILITY_WITHOUT_EVIDENCE",
        "SUSPICIOUS_CLASS_PROBABILITY",
        "BLOCKED_INSUFFICIENT_DATA",
        "PLACEHOLDER_TEAM",
        "SUSPICIOUS_TEAM_NAME",
    }
)

_FALLBACK_ONLY_FLAG = "FALLBACK_ONLY_NO_RECENT_CONTEXT"


def _json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_prediction(session: Session, slate_id: str, match_id: str) -> PredictionModel | None:
    return session.scalar(
        select(PredictionModel)
        .where(PredictionModel.slate_id == slate_id, PredictionModel.match_id == match_id)
        .order_by(PredictionModel.generated_at.desc())
        .limit(1)
    )


def _latest_features(session: Session, match_id: str) -> MatchFeatureSnapshotModel | None:
    return session.scalar(
        select(MatchFeatureSnapshotModel)
        .where(MatchFeatureSnapshotModel.match_id == match_id)
        .order_by(MatchFeatureSnapshotModel.generated_at.desc())
        .limit(1)
    )


def _top_pick(probabilities: dict[str, Any]) -> dict[str, Any]:
    ordered = [
        ("L", float(probabilities.get("L") or 0.0)),
        ("E", float(probabilities.get("E") or 0.0)),
        ("V", float(probabilities.get("V") or 0.0)),
    ]
    ordered.sort(key=lambda item: item[1], reverse=True)
    return {
        "pick": ordered[0][0],
        "top_probability": round(ordered[0][1], 3),
        "top2_gap": round(ordered[0][1] - ordered[1][1], 3),
    }


def _safe_revisar_to_listo_candidate(status: str, evidence: str, flags: list[str]) -> bool:
    return (
        status == "REVISAR"
        and evidence in {"medium", "high"}
        and not (set(flags) & _HARD_REVIEW_FLAGS)
    )


def _actionable_blockers(
    *,
    status: str,
    flags: list[str],
    suspicious: list[str],
) -> list[str]:
    blockers: list[str] = []
    flag_set = set(flags)
    if suspicious or flag_set & {"PLACEHOLDER_TEAM", "SUSPICIOUS_TEAM_NAME"}:
        blockers.append("team_resolution")
    if flag_set & {"LOW_EVIDENCE", "BLOCKED_INSUFFICIENT_DATA"}:
        blockers.append("evidence_coverage")
    if "FALLBACK_USED" in flag_set:
        blockers.append("model_fallback")
    if status == "REVISAR" and not blockers:
        blockers.append("pick_review")
    return blockers


def _apply_fallback_only_guardrail(
    *,
    status: str,
    flags: list[str],
    recent_results_count: int,
    head_to_head_results_count: int,
) -> tuple[str, list[str]]:
    """Do not allow LISTO when the pick is fallback-only with no context.

    This is a diagnostic/presentation guardrail. It does not change persisted
    predictions or probabilities; it only makes readiness honest for publishing
    and Money Mode gates.
    """
    if (
        status == "LISTO"
        and "FALLBACK_USED" in set(flags)
        and recent_results_count == 0
        and head_to_head_results_count == 0
    ):
        out = list(flags)
        if _FALLBACK_ONLY_FLAG not in out:
            out.append(_FALLBACK_ONLY_FLAG)
        return "REVISAR", out
    return status, flags


def build_slate_readiness_report(
    session: Session,
    *,
    include_archived: bool = False,
    draw_codes: set[str] | None = None,
    slate_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return a read-only report for active/open slates.

    The report is diagnostic only. ``safe_revisar_to_listo_candidates`` is a
    conservative list of positions where the guardrail does not object; most
    slates should legitimately return an empty list.
    """
    slate_service = SlateService(None)  # type: ignore[arg-type]
    statement = select(ProgolSlateModel).order_by(ProgolSlateModel.week_type, ProgolSlateModel.draw_code)
    if not include_archived:
        statement = statement.where(ProgolSlateModel.is_archived.is_(False))
    if draw_codes:
        statement = statement.where(ProgolSlateModel.draw_code.in_(draw_codes))
    if slate_ids:
        statement = statement.where(ProgolSlateModel.id.in_(slate_ids))
    slates = list(session.scalars(statement).unique())

    report_slates: list[dict[str, Any]] = []
    for slate in slates:
        status_counts: Counter[str] = Counter()
        evidence_counts: Counter[str] = Counter()
        flag_counts: Counter[str] = Counter()
        matches: list[dict[str, Any]] = []
        safe_candidates: list[int] = []
        suspicious_positions: list[int] = []

        for link in sorted(slate.matches, key=lambda item: item.position):
            match = link.match
            prediction = _latest_prediction(session, slate.id, match.id)
            features = _latest_features(session, match.id)
            audit = _json(prediction.sanity_audit_json if prediction else None)
            feature_payload = _json(features.payload_json if features else None)
            probabilities = audit.get("decision_probabilities") or {}
            pick = _top_pick(probabilities)
            status = str(audit.get("final_status") or ("NO_PRED" if prediction is None else prediction.confidence_band.upper()))
            evidence = str(audit.get("evidence_level") or "unknown")
            flags = [str(item) for item in audit.get("sanity_flags") or []]
            data_flags = sorted(
                set(
                    team_name_issue_flags(match.home_team.name, is_placeholder=bool(match.home_team.is_placeholder))
                    + team_name_issue_flags(match.away_team.name, is_placeholder=bool(match.away_team.is_placeholder))
                )
            )
            report_flags = flags + [flag for flag in data_flags if flag not in flags]
            suspicious = suspicious_team_names(match.home_team.name, match.away_team.name)
            recent_results_count = int(feature_payload.get("recent_results_count") or 0)
            head_to_head_results_count = int(float(feature_payload.get("head_to_head_results_count") or 0))
            status, report_flags = _apply_fallback_only_guardrail(
                status=status,
                flags=report_flags,
                recent_results_count=recent_results_count,
                head_to_head_results_count=head_to_head_results_count,
            )
            candidate = _safe_revisar_to_listo_candidate(status, evidence, report_flags)
            actionable_blockers = _actionable_blockers(
                status=status,
                flags=report_flags,
                suspicious=suspicious,
            )

            status_counts[status] += 1
            evidence_counts[evidence] += 1
            for flag in report_flags:
                flag_counts[flag] += 1
            if candidate:
                safe_candidates.append(link.position)
            if suspicious:
                suspicious_positions.append(link.position)

            matches.append(
                {
                    "position": link.position,
                    "match_id": match.id,
                    "match": f"{match.home_team.name} vs {match.away_team.name}",
                    "competition": match.competition.name,
                    "status": status,
                    "evidence_level": evidence,
                    "flags": report_flags,
                    "sanity_flags": flags,
                    "data_flags": data_flags,
                    **pick,
                    "recent_results_count": recent_results_count,
                    "head_to_head_results_count": head_to_head_results_count,
                    "suspicious_team_names": suspicious,
                    "actionable_blockers": actionable_blockers,
                    "safe_revisar_to_listo_candidate": candidate,
                }
            )

        is_closed = slate_service.is_closed(slate) if slate.registration_closes_at else False
        report_slates.append(
            {
                "draw_code": slate.draw_code,
                "slate_id": slate.id,
                "week_type": slate.week_type,
                "match_count": len(matches),
                "is_closed": is_closed,
                "status_counts": dict(status_counts),
                "evidence_counts": dict(evidence_counts),
                "flag_counts": dict(flag_counts),
                "safe_revisar_to_listo_candidates": safe_candidates,
                "suspicious_team_name_positions": suspicious_positions,
                "matches": matches,
            }
        )

    return {"mode": "slate_readiness_report", "slates": report_slates}
