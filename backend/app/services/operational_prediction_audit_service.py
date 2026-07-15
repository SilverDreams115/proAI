"""Operational prediction audit for active and completed Progol slates.

Read-only service that joins the existing tracking, readiness and live-result
observer reports into one operator payload. It never writes predictions,
results, tickets, snapshots or training rows.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamAliasModel
from app.models.tables import TeamModel
from app.repositories.slate_repository import SlateRepository
from app.services.live_results_observer_status_service import LiveResultsObserverStatusService
from app.services.normalization_service import NormalizationService
from app.services.slate_readiness_report_service import build_slate_readiness_report
from app.services.slate_service import SlateService
from app.services.tracking_service import TrackingService


_HARD_GATE_FLAGS = {
    "PLACEHOLDER_TEAM",
    "SUSPICIOUS_TEAM_NAME",
    "BLOCKED_INSUFFICIENT_DATA",
}


def _json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class OperationalPredictionAuditService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.slate_service = SlateService(SlateRepository(session))
        self.normalizer = NormalizationService()

    def build(self, *, slate_id: str | None = None) -> dict[str, Any]:
        slates = self._slates(slate_id)
        readiness_slate_ids = self._readiness_slate_ids(slate_id)
        readiness = build_slate_readiness_report(
            self.session,
            include_archived=True,
            slate_ids=readiness_slate_ids,
        )
        observer = LiveResultsObserverStatusService(self.session).build_status()
        publish_gate = self._publish_gate(readiness)

        return {
            "mode": "operational_prediction_audit",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "selected_slate_id": slate_id,
            "uses_existing_sources_only": True,
            "prediction_audit": self._prediction_audit(slates),
            "placeholder_queue": self._placeholder_queue(readiness),
            "confidence_explainer": self._confidence_explainer(readiness),
            "publish_gate": publish_gate,
            "freshness_monitor": self._freshness_monitor(observer),
        }

    def _slates(self, slate_id: str | None) -> list[ProgolSlateModel]:
        if slate_id:
            slate = SlateRepository(self.session).get_slate(slate_id)
            return [slate] if slate is not None else []
        return [
            slate
            for slate in self.slate_service.list_slates(include_closed=True)
            if not slate.is_archived and slate.composition_hash
        ]

    def _readiness_slate_ids(self, slate_id: str | None) -> set[str]:
        if slate_id:
            return {slate_id}
        now = datetime.now(timezone.utc)
        return {
            slate.id
            for slate in self.slate_service.list_slates(include_closed=True)
            if not slate.is_archived
            and slate.composition_hash
            and not self.slate_service.is_closed(slate, now)
        }

    def _prediction_audit(self, slates: list[ProgolSlateModel]) -> dict[str, Any]:
        slate_rows: list[dict[str, Any]] = []
        segments: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
        total_scored = total_hits = 0

        for slate in slates:
            tracking = TrackingService(self.session).build_tracking(slate)
            if tracking["scored_matches"] == 0:
                continue
            slate_hits = int(tracking["hits"])
            slate_scored = int(tracking["scored_matches"])
            total_scored += slate_scored
            total_hits += slate_hits

            for match in tracking["matches"]:
                if match["prediction_status"] not in {"hit", "miss"}:
                    continue
                pred = self._latest_prediction(slate.id, match["match_id"])
                audit = _json(pred.sanity_audit_json if pred else None)
                hit = match["prediction_status"] == "hit"
                self._segment(segments, "confidence", audit.get("final_status") or (pred.confidence_band if pred else "unknown"), hit)
                self._segment(segments, "evidence", audit.get("evidence_level") or "unknown", hit)
                self._segment(segments, "ticket_strategy", audit.get("ticket_strategy") or match.get("ticket_strategy") or "unknown", hit)
                self._segment(segments, "league", match.get("competition") or "unknown", hit)
                self._segment(segments, "probability_source", match.get("probability_source") or "unknown", hit)
                self._segment(segments, "model", audit.get("model_artifact_id") or "heuristic_or_unstamped", hit)

            slate_rows.append(
                {
                    "slate_id": slate.id,
                    "draw_code": slate.draw_code,
                    "week_type": slate.week_type,
                    "status": tracking["status"],
                    "scored_matches": slate_scored,
                    "hits": slate_hits,
                    "misses": int(tracking["misses"]),
                    "accuracy": tracking["accuracy"],
                    "last_result_update": tracking["last_result_update"],
                }
            )

        return {
            "summary": {
                "slate_count": len(slate_rows),
                "scored_matches": total_scored,
                "hits": total_hits,
                "misses": max(0, total_scored - total_hits),
                "accuracy": round(total_hits / total_scored, 4) if total_scored else None,
            },
            "slates": slate_rows,
            "segments": {
                bucket: [self._segment_payload(label, counts) for label, counts in sorted(labels.items())]
                for bucket, labels in sorted(segments.items())
            },
        }

    @staticmethod
    def _segment(
        segments: dict[str, dict[str, Counter[str]]], bucket: str, label: Any, hit: bool
    ) -> None:
        key = str(label or "unknown")
        segments[bucket][key]["total"] += 1
        if hit:
            segments[bucket][key]["hits"] += 1

    @staticmethod
    def _segment_payload(raw_label: str, counter: Counter[str]) -> dict[str, Any]:
        total = int(counter["total"])
        hits = int(counter["hits"])
        return {
            "label": raw_label,
            "total": total,
            "hits": hits,
            "misses": max(0, total - hits),
            "accuracy": round(hits / total, 4) if total else None,
        }

    def _latest_prediction(self, slate_id: str, match_id: str) -> PredictionModel | None:
        return self.session.scalar(
            select(PredictionModel)
            .where(PredictionModel.slate_id == slate_id, PredictionModel.match_id == match_id)
            .order_by(PredictionModel.generated_at.desc())
            .limit(1)
        )

    def _placeholder_queue(self, readiness: dict[str, Any]) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for slate in readiness.get("slates") or []:
            for match in slate.get("matches") or []:
                if "team_resolution" not in (match.get("actionable_blockers") or []):
                    continue
                teams = self._split_match(str(match.get("match") or ""))
                suggestions = {
                    side: self._candidate_teams(name)
                    for side, name in teams.items()
                    if name
                }
                items.append(
                    {
                        "slate_id": slate.get("slate_id"),
                        "draw_code": slate.get("draw_code"),
                        "position": match.get("position"),
                        "match": match.get("match"),
                        "flags": match.get("data_flags") or match.get("flags") or [],
                        "suspicious_team_names": match.get("suspicious_team_names") or [],
                        "suggestions": suggestions,
                        "recommended_action": "resolver alias/equipo interno antes de publicar",
                    }
                )
        return {
            "count": len(items),
            "items": items,
        }

    @staticmethod
    def _split_match(label: str) -> dict[str, str]:
        if " vs " not in label:
            return {}
        home, away = label.split(" vs ", 1)
        return {"home": home.strip(), "away": away.strip()}

    def _candidate_teams(self, name: str) -> list[dict[str, Any]]:
        normalized = self.normalizer.normalize_team_name(name)
        if not normalized or len(normalized) < 2:
            return []
        stmt = (
            select(TeamModel)
            .outerjoin(TeamAliasModel, TeamAliasModel.team_id == TeamModel.id)
            .where(
                or_(
                    TeamAliasModel.normalized_alias == normalized,
                    TeamModel.name.ilike(f"%{name.strip()}%"),
                )
            )
            .where(TeamModel.is_placeholder.is_(False))
            .order_by(TeamModel.name.asc())
            .limit(3)
        )
        teams = list(self.session.scalars(stmt).unique())
        return [
            {
                "team_id": team.id,
                "name": team.name,
                "country": team.country,
                "normalized_alias": normalized,
            }
            for team in teams
        ]

    def _confidence_explainer(self, readiness: dict[str, Any]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for slate in readiness.get("slates") or []:
            for match in slate.get("matches") or []:
                flags = set(match.get("flags") or [])
                evidence = str(match.get("evidence_level") or "unknown")
                gap = float(match.get("top2_gap") or 0.0)
                recent = int(match.get("recent_results_count") or 0)
                h2h = int(match.get("head_to_head_results_count") or 0)
                rows.append(
                    {
                        "draw_code": slate.get("draw_code"),
                        "position": match.get("position"),
                        "match": match.get("match"),
                        "status": match.get("status"),
                        "pick": match.get("pick"),
                        "top_probability": match.get("top_probability"),
                        "top2_gap": gap,
                        "components": {
                            "probability_strength": self._component(
                                "strong" if gap >= 0.12 else "thin" if gap < 0.05 else "medium",
                                f"gap top-2 {gap:.3f}",
                            ),
                            "evidence_coverage": self._component(
                                "strong" if evidence == "high" else "medium" if evidence == "medium" else "thin",
                                f"{evidence}; forma {recent}, H2H {h2h}",
                            ),
                            "data_quality": self._component(
                                "blocked" if flags & {"PLACEHOLDER_TEAM", "SUSPICIOUS_TEAM_NAME"} else "ok",
                                "placeholder/sospechoso" if flags & {"PLACEHOLDER_TEAM", "SUSPICIOUS_TEAM_NAME"} else "nombres resueltos",
                            ),
                            "model_provenance": self._component(
                                "fallback" if "FALLBACK_USED" in flags else "ok",
                                "fallback usado" if "FALLBACK_USED" in flags else "modelo/auditoría disponible",
                            ),
                        },
                    }
                )
        return {"matches": rows}

    @staticmethod
    def _component(level: str, reason: str) -> dict[str, str]:
        return {"level": level, "reason": reason}

    def _publish_gate(self, readiness: dict[str, Any]) -> dict[str, Any]:
        blocked: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        for slate in readiness.get("slates") or []:
            for match in slate.get("matches") or []:
                flags = set(match.get("flags") or [])
                status = str(match.get("status") or "")
                evidence = str(match.get("evidence_level") or "unknown")
                reasons: list[str] = []
                if flags & _HARD_GATE_FLAGS:
                    reasons.append("equipo placeholder/sospechoso o datos insuficientes")
                if status == "BLOQUEADO":
                    reasons.append("predicción bloqueada")
                if "FALLBACK_USED" in flags and evidence in {"low", "unknown"}:
                    reasons.append("fallback débil con evidencia baja")
                if reasons:
                    blocked.append(
                        {
                            "draw_code": slate.get("draw_code"),
                            "position": match.get("position"),
                            "match": match.get("match"),
                            "reasons": reasons,
                        }
                    )
                elif status == "REVISAR":
                    warnings.append(
                        {
                            "draw_code": slate.get("draw_code"),
                            "position": match.get("position"),
                            "match": match.get("match"),
                            "reason": "requiere revisión antes de marcar listo",
                        }
                    )
        return {
            "allowed": not blocked,
            "blocked_count": len(blocked),
            "warning_count": len(warnings),
            "blocked_positions": blocked,
            "warnings": warnings,
            "whatsapp_allowed": not blocked,
            "mark_ready_allowed": not blocked,
        }

    def _freshness_monitor(self, observer: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        slates: list[dict[str, Any]] = []
        attention = 0
        for slate in observer.get("active_slates") or []:
            updated_at = _aware(slate.get("last_updated_at"))
            age_minutes = round((now - updated_at).total_seconds() / 60, 1) if updated_at else None
            is_closed = bool(slate.get("is_closed"))
            is_complete = bool(slate.get("is_complete"))
            has_any = bool(slate.get("has_any_result"))
            stale = (is_closed and not is_complete and not has_any) or (
                bool(age_minutes is not None and age_minutes > 120 and not is_complete)
            )
            if stale:
                attention += 1
            slates.append(
                {
                    "slate_id": slate.get("slate_id"),
                    "draw_code": slate.get("draw_code"),
                    "pull_state": slate.get("pull_state"),
                    "completed_count": slate.get("completed_count"),
                    "live_count": slate.get("live_count"),
                    "match_count": slate.get("match_count"),
                    "sources": slate.get("sources") or [],
                    "last_updated_at": slate.get("last_updated_at"),
                    "age_minutes": age_minutes,
                    "needs_attention": stale,
                }
            )
        return {
            "status": "attention_required" if attention else "ok",
            "attention_count": attention,
            "observer_status": observer.get("status"),
            "pull_ready": observer.get("pull_ready"),
            "slates": slates,
        }
