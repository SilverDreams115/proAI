from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.connectors.local_context_json import LocalContextJsonConnector
from app.connectors.registry import connector_registry
from app.db.session import managed_transaction
from app.models.tables import EvidenceItemModel
from app.models.tables import SourceModel
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.source_repository import SourceRepository
from app.services.evidence_service import EvidenceService
from app.schemas.common import CompetitionPayload
from app.schemas.common import MatchReferencePayload
from app.schemas.common import TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.schemas.slate_refresh import CurrentProgolRefreshResponse
from app.services.ingestion_service import IngestionService
from app.services.normalization_service import NormalizationService
from app.services.slate_service import SlateService


class CurrentProgolService:
    DEFAULT_SOURCE_NAME = "Progol Current Local Context"
    SUPPORTED_CONTEST_TYPES = ("progol", "progol_media_semana", "progol_revancha")

    def __init__(
        self,
        source_repository: SourceRepository,
        ingestion_repository: IngestionRepository,
        slate_repository: SlateRepository,
    ) -> None:
        self.source_repository = source_repository
        self.ingestion_repository = ingestion_repository
        self.slate_repository = slate_repository

    def refresh_current(self, source_name: str | None = None, local_path: str | None = None) -> CurrentProgolRefreshResponse:
        source_name = source_name or self.DEFAULT_SOURCE_NAME
        resolved_path = self._resolve_context_path(local_path)
        source = self.ensure_context_source(source_name, resolved_path)
        slate_payload = self._build_current_slate_payload(resolved_path)
        slate = SlateService(self.slate_repository).create_slate(slate_payload)
        run = IngestionService(self.ingestion_repository).run_for_source(source.id)
        self._upsert_local_context_evidence(slate.id, source.id, resolved_path)
        self._link_current_context_to_slate(slate.id)
        archived_ids = self._archive_non_current_slates(slate.id)
        return CurrentProgolRefreshResponse(
            slate_id=slate.id,
            draw_code=slate.draw_code,
            label=slate.label,
            match_count=len(slate.matches),
            archived_slate_ids=archived_ids,
            ingestion_run_id=run.id,
            ingestion_status=run.status,
        )

    def _resolve_context_path(self, local_path: str | None) -> Path:
        selected_path = local_path or os.getenv("PROAI_LOCAL_CONTEXT_PATH")
        if selected_path is None:
            selected_path = "current.json" if os.getenv("PROAI_LOCAL_CONTEXT_ROOT") else "/data/progol_context/current.json"
        return LocalContextJsonConnector.resolve_allowed_path(selected_path)

    def ensure_default_context_source(self) -> SourceModel:
        return self.ensure_context_source(self.DEFAULT_SOURCE_NAME, self._resolve_context_path(None))

    def ensure_context_source(self, source_name: str, resolved_path: Path) -> SourceModel:
        existing = self.source_repository.get_by_name(source_name)
        base_url = LocalContextJsonConnector.to_base_url(str(resolved_path))
        if existing is not None:
            existing.base_url = base_url
            existing.kind = "local_context_json"
            existing.parser_profile = "generic"
            existing.is_active = True
            with managed_transaction(self.source_repository.session):
                self.source_repository.session.add(existing)
            connector_registry.register(LocalContextJsonConnector(name=existing.name, file_path=existing.base_url))
            return existing

        with managed_transaction(self.source_repository.session):
            source = SourceModel(
                name=source_name,
                base_url=base_url,
                kind="local_context_json",
                parser_profile="generic",
                is_active=True,
            )
            self.source_repository.session.add(source)
            self.source_repository.session.flush()
            self.source_repository.session.refresh(source)
        connector_registry.register(LocalContextJsonConnector(name=source.name, file_path=source.base_url))
        return source

    def _build_current_slate_payload(self, path: Path) -> ProgolSlateCreate:
        with path.open(encoding="utf-8") as handle:
            raw_payload = json.load(handle)
        items = raw_payload if isinstance(raw_payload, list) else raw_payload.get("items", [])
        if not isinstance(items, list):
            raise ValueError("Current Progol context must contain an items list.")

        current = self._select_current_progol_item(items)
        metadata = current.get("catalog_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        draw_number = int(metadata.get("draw_number") or 0)
        fixtures = current.get("fixture_candidates") or current.get("fixtures") or []
        if not draw_number or not isinstance(fixtures, list) or not fixtures:
            raise ValueError("Current Progol context does not include a valid draw number and fixtures.")

        registration_closes_at = self._parse_datetime(metadata.get("registration_closes_at"))
        contest_type = str(metadata.get("contest_type") or "progol")
        if contest_type == "progol_media_semana":
            week_type = "midweek"
            label = f"Progol Media Semana {draw_number}"
            draw_code = f"PGM-{draw_number}"
        elif contest_type == "progol_revancha":
            week_type = "revancha"
            label = f"Progol Revancha {draw_number}"
            draw_code = f"PGR-{draw_number}"
        else:
            week_type = "weekend" if len(fixtures) >= 14 else "midweek" if len(fixtures) >= 8 else "revancha"
            label = f"Progol Fin de Semana {draw_number}" if week_type == "weekend" else f"Progol {draw_number}"
            draw_code = f"PG-{draw_number}"
        return ProgolSlateCreate(
            label=label,
            draw_code=draw_code,
            week_type=week_type,
            registration_closes_at=registration_closes_at,
            matches=[self._fixture_to_match(index, fixture) for index, fixture in enumerate(fixtures, start=1)],
        )

    def _select_current_progol_item(self, items: list[object]) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("catalog_metadata", {})
            if not isinstance(metadata, dict):
                continue
            if metadata.get("contest_type") not in self.SUPPORTED_CONTEST_TYPES:
                continue
            if not item.get("fixture_candidates") and not item.get("fixtures"):
                continue
            candidates.append(item)
        if not candidates:
            raise ValueError("No current Progol item found in local context.")
        # Prefer the slate whose earliest fixture is closest to now in the
        # future; fall back to draw_number for ties. This keeps the active
        # contest selectable across `progol`, `progol_media_semana`, and
        # `progol_revancha` types regardless of historical draw numbers.
        now = datetime.now(timezone.utc)
        return max(candidates, key=lambda item: self._candidate_sort_key(item, now))

    def _candidate_sort_key(self, item: dict[str, Any], now: datetime) -> tuple[int, float, int]:
        metadata = item.get("catalog_metadata", {})
        draw_number = int(metadata.get("draw_number") or 0) if isinstance(metadata, dict) else 0
        fixtures = item.get("fixture_candidates") or item.get("fixtures") or []
        future_kickoffs: list[datetime] = []
        for fixture in fixtures:
            if not isinstance(fixture, dict):
                continue
            parsed = self._parse_datetime(fixture.get("kickoff_at"))
            if parsed is not None and parsed >= now:
                future_kickoffs.append(parsed)
        if future_kickoffs:
            earliest = min(future_kickoffs)
            # Higher key wins: future slates beat past ones; among future,
            # the closest kickoff wins (negative delta keeps "soonest" max).
            return (1, -(earliest - now).total_seconds(), draw_number)
        return (0, 0.0, draw_number)

    def _fixture_to_match(self, index: int, fixture: object) -> MatchReferencePayload:
        if not isinstance(fixture, dict):
            raise ValueError("Fixture entries must be objects.")
        kickoff_at = self._parse_datetime(fixture.get("kickoff_at"))
        if kickoff_at is None:
            raise ValueError("Fixture is missing kickoff_at.")
        return MatchReferencePayload(
            position=int(fixture.get("position") or index),
            competition=CompetitionPayload(
                name=str(fixture.get("competition") or "Progol"),
                country=str(fixture.get("country") or "") or None,
                season=str(fixture.get("season") or "") or None,
            ),
            home_team=TeamPayload(
                name=str(fixture.get("home_team") or ""),
                country=str(fixture.get("home_country") or fixture.get("country") or "") or None,
            ),
            away_team=TeamPayload(
                name=str(fixture.get("away_team") or ""),
                country=str(fixture.get("away_country") or fixture.get("country") or "") or None,
            ),
            kickoff_at=kickoff_at,
            venue=str(fixture.get("venue") or "") or None,
        )

    def _parse_datetime(self, value: object) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _archive_non_current_slates(self, current_slate_id: str) -> list[str]:
        # Only archive slates that have actually closed. The old behavior
        # of archiving every non-"current" open slate breaks the Fase 3
        # auto-promote pipeline: when the worker promotes the next
        # concurso before the active one closes both must remain open
        # until each individual cierre passes. archive_due_slates is the
        # authoritative cierre-based janitor; this method now just
        # collects which closed slates landed in the same cycle so the
        # response reports them.
        service = SlateService(self.slate_repository)
        archived_ids: list[str] = []
        now = datetime.now(timezone.utc)
        with managed_transaction(self.slate_repository.session):
            for slate in self.slate_repository.list_slates():
                if slate.id == current_slate_id or slate.is_archived:
                    continue
                if not service.is_closed(slate, now):
                    continue
                slate.is_archived = True
                self.slate_repository.session.add(slate)
                archived_ids.append(slate.id)
        return archived_ids

    def _link_current_context_to_slate(self, slate_id: str) -> None:
        slate = self.slate_repository.get_slate(slate_id)
        if slate is None:
            return
        matches = [link.match for link in slate.matches]
        EvidenceService(EvidenceRepository(self.slate_repository.session)).auto_link_unmatched_documents(matches)

    def _upsert_local_context_evidence(self, slate_id: str, source_id: str, path: Path) -> None:
        slate = self.slate_repository.get_slate(slate_id)
        if slate is None:
            return
        with path.open(encoding="utf-8") as handle:
            raw_payload = json.load(handle)
        items = raw_payload if isinstance(raw_payload, list) else raw_payload.get("items", [])
        if not isinstance(items, list):
            return
        normalizer = NormalizationService()
        context_by_key = {
            self._context_item_key(item, normalizer): item
            for item in items
            if isinstance(item, dict) and self._context_item_key(item, normalizer) is not None
        }
        captured_at = datetime.now(timezone.utc)
        with managed_transaction(self.slate_repository.session):
            for link in slate.matches:
                match = link.match
                key = (
                    normalizer.normalize_competition_name(match.competition.name),
                    normalizer.normalize_team_name(match.home_team.name),
                    normalizer.normalize_team_name(match.away_team.name),
                )
                item = context_by_key.get(key)
                if item is None:
                    item = {
                        "title": f"{match.home_team.name} vs {match.away_team.name} - contexto local",
                        "source_url": str(path),
                        "summary": f"Contexto local minimo para {match.home_team.name} vs {match.away_team.name}.",
                        "context_summary": "Partido incluido en la papeleta local vigente; requiere fuentes externas adicionales.",
                    }
                summary = str(item.get("context_summary") or item.get("summary") or item.get("title") or "")
                payload = {
                    "source_title": str(item.get("title") or ""),
                    "source_url": str(item.get("source_url") or path),
                    "context_summary": summary,
                    "article_prediction": item.get("article_prediction"),
                    "availability_reports": item.get("availability_reports", []),
                    "historical_results": item.get("historical_results", []),
                    "local_context_kind": "current_progol_fixture",
                }
                existing = self.slate_repository.session.scalar(
                    select(EvidenceItemModel).where(
                        EvidenceItemModel.match_id == match.id,
                        EvidenceItemModel.source_id == source_id,
                        EvidenceItemModel.kind == "local_context",
                    )
                )
                if existing is None:
                    existing = EvidenceItemModel(
                        match_id=match.id,
                        source_id=source_id,
                        kind="local_context",
                        captured_at=captured_at,
                        confidence=0.8 if item is not None else 0.45,
                        summary=summary,
                        payload_json=json.dumps(payload, sort_keys=True),
                    )
                else:
                    existing.captured_at = captured_at
                    existing.confidence = 0.8
                    existing.summary = summary
                    existing.payload_json = json.dumps(payload, sort_keys=True)
                self.slate_repository.session.add(existing)

    def _context_item_key(
        self,
        item: dict[str, object],
        normalizer: NormalizationService,
    ) -> tuple[str, str, str] | None:
        teams = item.get("teams")
        if not isinstance(teams, list) or len(teams) < 2:
            return None
        competition = str(item.get("competition") or "")
        home = str(teams[0] or "")
        away = str(teams[1] or "")
        if not competition or not home or not away:
            return None
        return (
            normalizer.normalize_competition_name(competition),
            normalizer.normalize_team_name(home),
            normalizer.normalize_team_name(away),
        )
