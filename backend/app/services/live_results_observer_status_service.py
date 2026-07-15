"""Read-only validation for the live-results observer.

This service intentionally does not fetch, ingest, create sources, or mutate
results. It answers one operational question: are the current active slates
receiving live/final marcador data from the already-configured sources?
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.settings import Settings, load_settings
from app.models.tables import (
    MatchLiveResultModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    SourceModel,
)
from app.repositories.slate_repository import SlateRepository
from app.services.live_results_service import LiveResultsService
from app.services.results_ingestion_service import (
    RESULTS_SOURCE_BASE_URL,
    RESULTS_SOURCE_KIND,
    RESULTS_SOURCE_NAME,
)
from app.services.slate_classification_service import classify_slate
from app.services.slate_service import SlateService


class LiveResultsObserverStatusService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or load_settings()
        self.slate_service = SlateService(SlateRepository(session))
        self.live_service = LiveResultsService(session)

    def build_status(self) -> dict[str, Any]:
        source_url = self.settings.live_results_source_url
        existing_sources = self._existing_results_sources(source_url)
        active_existing_sources = [src for src in existing_sources if src.is_active]
        slates = self._active_result_slates()
        slate_statuses = [self._slate_status(slate) for slate in slates]
        warnings = self._warnings(source_url, active_existing_sources, slate_statuses)

        return {
            "status": "ok" if not warnings else "attention_required",
            "observer_enabled": self.settings.live_results_observe_enabled,
            "fetch_enabled": self.settings.live_results_fetch_enabled,
            "observe_interval_minutes": self.settings.live_results_observe_interval_minutes,
            "configured_source_url": source_url,
            "expected_source_name": RESULTS_SOURCE_NAME,
            "expected_source_kind": RESULTS_SOURCE_KIND,
            "expected_source_url": RESULTS_SOURCE_BASE_URL,
            "uses_existing_sources_only": True,
            "existing_source_count": len(active_existing_sources),
            "pull_ready": (
                self.settings.live_results_observe_enabled
                and self.settings.live_results_fetch_enabled
                and bool(source_url)
                and bool(active_existing_sources)
            ),
            "warnings": warnings,
            "sources": [self._source_payload(src) for src in existing_sources],
            "latest_ingestion": self._latest_ingestion_payload(active_existing_sources),
            "active_slates": slate_statuses,
        }

    def _existing_results_sources(self, source_url: str | None) -> list[SourceModel]:
        predicates = [
            SourceModel.name == RESULTS_SOURCE_NAME,
            SourceModel.kind == RESULTS_SOURCE_KIND,
            SourceModel.base_url == RESULTS_SOURCE_BASE_URL,
        ]
        if source_url:
            predicates.append(SourceModel.base_url == source_url)
        return list(
            self.session.scalars(
                select(SourceModel)
                .where(or_(*predicates))
                .order_by(SourceModel.result_source_priority.asc(), SourceModel.name.asc())
            )
        )

    def _active_result_slates(self) -> list[ProgolSlateModel]:
        # Non-archived slates are the operational scope: open slates can receive
        # live observations, and recently closed slates can receive/finalize
        # official final results.
        return [
            slate
            for slate in self.slate_service.list_slates(include_closed=True)
            if not slate.is_archived and slate.composition_hash
        ]

    def _slate_status(self, slate: ProgolSlateModel) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        results = self.live_service.build_live_results(slate)
        matches = results["matches"]
        reality = classify_slate(self.session, slate)
        sources = sorted({m["source"] for m in matches if m.get("source")})
        completed_or_live = results["completed_count"] + results["live_count"]
        results_with_source = sum(1 for m in matches if m.get("source"))
        has_scorelines = any(
            m.get("home_goals") is not None and m.get("away_goals") is not None
            for m in matches
        )
        sign_only_final_count = sum(
            1
            for m in matches
            if m.get("is_final")
            and m.get("result_code")
            and m.get("home_goals") is None
            and m.get("away_goals") is None
        )
        if results["is_complete"]:
            pull_state = "complete"
        elif results["live_count"] > 0:
            pull_state = "receiving_live"
        elif completed_or_live > 0:
            pull_state = "receiving_results"
        else:
            pull_state = "waiting_results"

        return {
            "slate_id": slate.id,
            "draw_code": slate.draw_code,
            "week_type": slate.week_type,
            "is_archived": slate.is_archived,
            "is_closed": self.slate_service.is_closed(slate, now),
            "classification": reality.classification.value,
            "comparable": reality.comparable_with_results,
            "match_count": results["match_count"],
            "completed_count": results["completed_count"],
            "live_count": results["live_count"],
            "pending_count": results["pending_count"],
            "is_complete": results["is_complete"],
            "has_any_result": completed_or_live > 0,
            "has_scorelines": has_scorelines,
            "sign_only_final_count": sign_only_final_count,
            "results_with_source_count": results_with_source,
            "sources": sources,
            "last_updated_at": results["last_updated_at"],
            "pull_state": pull_state,
        }

    @staticmethod
    def _source_payload(source: SourceModel) -> dict[str, Any]:
        return {
            "id": source.id,
            "name": source.name,
            "kind": source.kind,
            "base_url": source.base_url,
            "is_active": source.is_active,
            "priority": source.result_source_priority,
        }

    def _latest_ingestion_payload(self, sources: list[SourceModel]) -> dict[str, Any] | None:
        if not sources:
            return None
        source_ids = [source.id for source in sources]
        rows = self.session.execute(
            select(
                ProgolSlateModel.id,
                ProgolSlateModel.draw_code,
                ProgolSlateModel.week_type,
                func.count(MatchLiveResultModel.id),
                func.sum(case((MatchLiveResultModel.is_final.is_(True), 1), else_=0)),
                func.max(MatchLiveResultModel.updated_at),
            )
            .join(
                ProgolSlateMatchModel,
                ProgolSlateMatchModel.slate_id == ProgolSlateModel.id,
            )
            .join(
                MatchLiveResultModel,
                MatchLiveResultModel.match_id == ProgolSlateMatchModel.match_id,
            )
            .where(MatchLiveResultModel.source_id.in_(source_ids))
            .group_by(
                ProgolSlateModel.id,
                ProgolSlateModel.draw_code,
                ProgolSlateModel.week_type,
            )
            .order_by(func.max(MatchLiveResultModel.updated_at).desc())
            .limit(5)
        ).all()
        if not rows:
            return None
        latest_at = max(row[5] for row in rows if row[5] is not None)
        total_rows = sum(int(row[3] or 0) for row in rows)
        return {
            "last_success_at": latest_at,
            "slate_count": len(rows),
            "result_rows": total_rows,
            "draws": [
                {
                    "slate_id": row[0],
                    "draw_code": row[1],
                    "week_type": row[2],
                    "result_rows": int(row[3] or 0),
                    "final_rows": int(row[4] or 0),
                    "last_updated_at": row[5],
                }
                for row in rows
            ],
        }

    def _warnings(
        self,
        source_url: str | None,
        active_existing_sources: list[SourceModel],
        slate_statuses: list[dict[str, Any]],
    ) -> list[str]:
        warnings: list[str] = []
        if not self.settings.live_results_observe_enabled:
            warnings.append("live_results_observer_disabled")
        if not self.settings.live_results_fetch_enabled:
            warnings.append("live_results_fetch_disabled")
        if not source_url:
            warnings.append("live_results_source_url_missing")
        if not active_existing_sources:
            warnings.append("existing_results_source_missing")
        if not slate_statuses:
            warnings.append("no_active_slates_to_observe")
        elif not any(s["has_any_result"] for s in slate_statuses):
            warnings.append("no_active_slate_results_seen_yet")
        if any(s["has_any_result"] and not s["sources"] for s in slate_statuses):
            warnings.append("some_results_missing_source_marker")
        return warnings
