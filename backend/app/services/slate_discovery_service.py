from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.errors import ValidationError
from app.db.session import managed_transaction
from app.repositories.ingestion_repository import IngestionRepository
from app.repositories.slate_repository import SlateRepository
from app.schemas.common import CompetitionPayload
from app.schemas.common import MatchReferencePayload
from app.schemas.common import TeamPayload
from app.schemas.slate import ProgolSlateCreate
from app.schemas.slate_discovery import DiscoveredSlateMatchResponse
from app.schemas.slate_discovery import SlateDiscoveryRequest
from app.schemas.slate_discovery import SlateDiscoveryResponse


@dataclass(slots=True)
class FixtureCandidate:
    sort_key: tuple[int, int, datetime, str, str, str]
    source_position: int | None
    competition_name: str
    competition_country: str | None
    competition_season: str | None
    home_team_name: str
    away_team_name: str
    home_team_country: str | None
    away_team_country: str | None
    kickoff_at: datetime
    venue: str | None
    source_document_id: str | None
    source_name: str | None


class SlateDiscoveryService:
    DEFAULT_COUNTS = {"weekend": 14, "midweek": 9, "revancha": 7}

    def __init__(
        self,
        ingestion_repository: IngestionRepository,
        slate_repository: SlateRepository,
    ) -> None:
        self.ingestion_repository = ingestion_repository
        self.slate_repository = slate_repository

    def discover(self, payload: SlateDiscoveryRequest) -> SlateDiscoveryResponse:
        catalog_document = self._select_catalog_document(payload)
        catalog_payload = self._parse_payload(catalog_document.payload_json) if catalog_document else {}
        catalog_metadata = (
            catalog_payload.get("catalog_metadata", {}) if isinstance(catalog_payload, dict) else {}
        )
        week_type = payload.week_type or self._infer_week_type(
            catalog_metadata,
            catalog_document.title if catalog_document else None,
        )
        draw_number = self._infer_draw_number(catalog_metadata, catalog_document.title if catalog_document else None)
        match_target = self._infer_match_target(catalog_metadata, week_type)
        registration_closes_at = payload.registration_closes_at or self._infer_registration_closes_at(
            catalog_metadata
        )
        candidates = self._extract_fixture_candidates(payload)
        selected = candidates[:match_target]
        if len(selected) < match_target:
            raise ValidationError(
                f"Only {len(selected)} fixture candidates were found; {match_target} are required."
            )

        label = payload.label or self._default_label(week_type, draw_number)
        draw_code = payload.draw_code or self._default_draw_code(week_type, draw_number)
        response = SlateDiscoveryResponse(
            label=label,
            draw_code=draw_code,
            week_type=week_type,
            registration_closes_at=registration_closes_at,
            match_target=match_target,
            source_catalog_title=catalog_document.title if catalog_document else None,
            source_catalog_url=catalog_document.external_url if catalog_document else None,
            matches=[
                DiscoveredSlateMatchResponse(
                    position=index + 1,
                    competition=CompetitionPayload(
                        name=item.competition_name,
                        country=item.competition_country,
                        season=item.competition_season,
                    ),
                    home_team=TeamPayload(name=item.home_team_name, country=item.home_team_country),
                    away_team=TeamPayload(name=item.away_team_name, country=item.away_team_country),
                    kickoff_at=item.kickoff_at,
                    venue=item.venue,
                    source_document_id=item.source_document_id,
                    source_name=item.source_name,
                )
                for index, item in enumerate(selected)
            ],
        )

        if payload.create_persisted_slate:
            with managed_transaction(self.slate_repository.session):
                created = self.slate_repository.create_slate(
                    ProgolSlateCreate(
                        label=label,
                        draw_code=draw_code,
                        week_type=week_type,
                        registration_closes_at=registration_closes_at,
                        matches=[
                            MatchReferencePayload(
                                position=index + 1,
                                competition=CompetitionPayload(
                                    name=item.competition_name,
                                    country=item.competition_country,
                                    season=item.competition_season,
                                ),
                                home_team=TeamPayload(name=item.home_team_name, country=item.home_team_country),
                                away_team=TeamPayload(name=item.away_team_name, country=item.away_team_country),
                                kickoff_at=item.kickoff_at,
                                venue=item.venue,
                            )
                            for index, item in enumerate(selected)
                        ],
                    )
                )
            response.persisted_slate_id = created.id
        return response

    def _select_catalog_document(self, payload: SlateDiscoveryRequest):
        documents = self.ingestion_repository.list_documents(
            [payload.catalog_source_id] if payload.catalog_source_id else None
        )
        if payload.catalog_source_id:
            return documents[0] if documents else None
        preferred = []
        fallback = []
        for document in documents:
            parsed = self._parse_payload(document.payload_json)
            metadata = parsed.get("catalog_metadata")
            if not metadata:
                continue
            if self._catalog_matches_week_type(metadata, payload.week_type):
                preferred.append(document)
            else:
                fallback.append(document)
        if preferred:
            return preferred[0]
        if fallback:
            return fallback[0]
        return None

    def _catalog_matches_week_type(self, catalog_metadata: object, week_type: str | None) -> bool:
        if week_type is None:
            return True
        contest_type = ""
        match_count = None
        if isinstance(catalog_metadata, dict):
            contest_type = str(catalog_metadata.get("contest_type", "")).lower()
            match_count = catalog_metadata.get("match_count")
        if week_type == "revancha":
            return "revancha" in contest_type or match_count == 7
        if week_type == "midweek":
            return "media" in contest_type or match_count == 9
        return "media" not in contest_type and "revancha" not in contest_type and match_count not in {7, 9}

    def _extract_fixture_candidates(self, payload: SlateDiscoveryRequest) -> list[FixtureCandidate]:
        documents = self.ingestion_repository.list_documents(payload.fixture_source_ids or None)
        kickoff_not_before = payload.kickoff_not_before or (datetime.now(timezone.utc) - timedelta(days=3))
        if kickoff_not_before.tzinfo is None:
            kickoff_not_before = kickoff_not_before.replace(tzinfo=timezone.utc)
        kickoff_not_after = payload.kickoff_not_after
        if kickoff_not_after is not None and kickoff_not_after.tzinfo is None:
            kickoff_not_after = kickoff_not_after.replace(tzinfo=timezone.utc)

        dedup: dict[tuple[str, str, str], FixtureCandidate] = {}
        for document in documents:
            parsed = self._parse_payload(document.payload_json)
            metadata = parsed.get("catalog_metadata")
            if metadata and payload.week_type and not self._catalog_matches_week_type(metadata, payload.week_type):
                continue
            fixtures = parsed.get("fixture_candidates", [])
            if not isinstance(fixtures, list):
                continue
            for fixture in fixtures:
                if not isinstance(fixture, dict):
                    continue
                kickoff_raw = fixture.get("kickoff_at") or fixture.get("played_at")
                if not kickoff_raw:
                    continue
                kickoff_at = datetime.fromisoformat(str(kickoff_raw).replace("Z", "+00:00"))
                if kickoff_at.tzinfo is None:
                    kickoff_at = kickoff_at.replace(tzinfo=timezone.utc)
                if kickoff_at < kickoff_not_before:
                    continue
                if kickoff_not_after is not None and kickoff_at > kickoff_not_after:
                    continue
                competition_name = str(
                    fixture.get("competition") or parsed.get("competition") or "Unknown Competition"
                ).strip()
                home_team_name = str(fixture.get("home_team", "")).strip()
                away_team_name = str(fixture.get("away_team", "")).strip()
                if not competition_name or not home_team_name or not away_team_name:
                    continue
                source_position = self._optional_position(fixture.get("position"))
                key = (
                    competition_name.lower(),
                    home_team_name.lower(),
                    away_team_name.lower(),
                )
                candidate = FixtureCandidate(
                    sort_key=(
                        0 if source_position is not None else 1,
                        source_position or 999,
                        kickoff_at,
                        competition_name,
                        home_team_name,
                        away_team_name,
                    ),
                    source_position=source_position,
                    competition_name=competition_name,
                    competition_country=self._optional_text(fixture.get("country")),
                    competition_season=self._optional_text(fixture.get("season")),
                    home_team_name=home_team_name,
                    away_team_name=away_team_name,
                    home_team_country=None,
                    away_team_country=None,
                    kickoff_at=kickoff_at,
                    venue=self._optional_text(fixture.get("venue")),
                    source_document_id=document.id,
                    source_name=document.title,
                )
                if key not in dedup or candidate.sort_key < dedup[key].sort_key:
                    dedup[key] = candidate
        return sorted(dedup.values(), key=lambda item: item.sort_key)

    def _parse_payload(self, payload_json: str) -> dict[str, object]:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _infer_week_type(self, catalog_metadata: object, title: str | None) -> str:
        title_text = (title or "").lower()
        if isinstance(catalog_metadata, dict):
            match_count = catalog_metadata.get("match_count")
            if match_count == 9:
                return "midweek"
            if match_count == 7:
                return "revancha"
            if match_count == 14:
                return "weekend"
            contest_type = str(catalog_metadata.get("contest_type", ""))
            if "revancha" in contest_type:
                return "revancha"
            if "media" in contest_type:
                return "midweek"
        if "revancha" in title_text:
            return "revancha"
        if "media semana" in title_text:
            return "midweek"
        return "weekend"

    def _infer_match_target(self, catalog_metadata: object, week_type: str) -> int:
        if isinstance(catalog_metadata, dict):
            match_count = catalog_metadata.get("match_count")
            if isinstance(match_count, int) and match_count > 0:
                return min(match_count, 14)
        return self.DEFAULT_COUNTS[week_type]

    def _infer_draw_number(self, catalog_metadata: object, title: str | None) -> int | None:
        if isinstance(catalog_metadata, dict):
            raw_draw = catalog_metadata.get("draw_number")
            if isinstance(raw_draw, int):
                return raw_draw
            if isinstance(raw_draw, str) and raw_draw.isdigit():
                return int(raw_draw)
        match = re.search(r"\b(\d{3,5})\b", title or "")
        return int(match.group(1)) if match else None

    def _infer_registration_closes_at(self, catalog_metadata: object) -> datetime | None:
        if not isinstance(catalog_metadata, dict):
            return None
        for key in ("registration_closes_at", "sale_closes_at", "sales_close_at"):
            raw_value = catalog_metadata.get(key)
            if not raw_value:
                continue
            try:
                closes_at = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
            except ValueError:
                continue
            if closes_at.tzinfo is None:
                closes_at = closes_at.replace(tzinfo=timezone.utc)
            return closes_at
        return None

    def _default_label(self, week_type: str, draw_number: int | None = None) -> str:
        if draw_number is not None:
            contest_name = {
                "midweek": "Media Semana",
                "weekend": "Semanal",
                "revancha": "Revancha",
            }[week_type]
            return f"Progol {contest_name} {draw_number}"
        date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
        contest_name = {
            "midweek": "Media Semana",
            "weekend": "Semanal",
            "revancha": "Revancha",
        }[week_type]
        return f"Progol {contest_name} {date_part}"

    def _default_draw_code(self, week_type: str, draw_number: int | None = None) -> str:
        prefix = {
            "midweek": "PGM",
            "weekend": "PG",
            "revancha": "PGR",
        }[week_type]
        if draw_number is not None:
            return f"{prefix}-{draw_number}"
        date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"{prefix}-{date_part}"

    def _optional_text(self, value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    def _optional_position(self, value: object) -> int | None:
        if value is None:
            return None
        try:
            position = int(value)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            return None
        return position if position > 0 else None
