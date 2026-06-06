from __future__ import annotations

from datetime import timezone
import re
from typing import Any

from app.models.tables import MatchModel
from app.models.tables import PlayerModel
from app.models.tables import TeamModel
from app.repositories.availability_repository import AvailabilityRepository
from app.repositories.entity_repository import EntityRepository
from app.services.normalization_service import NormalizationService


class NarrativeInterpretationService:
    IMPACT_BY_STATUS = {
        "out": 0.85,
        "suspended": 0.82,
        "doubtful": 0.55,
        "rotation_risk": 0.42,
        "available": 0.1,
    }
    CATEGORY_KEYWORDS = {
        "injury": ("injury", "injured", "lesion", "lesionado", "baja"),
        "suspension": ("suspension", "suspended", "suspendido", "sanction"),
        "rotation": ("rotation", "rotacion", "rest", "lineup", "once"),
    }
    STATUS_KEYWORDS = {
        "out": ("out", "ruled out", "baja", "no juega", "descartado"),
        "suspended": ("suspended", "suspendido", "suspension", "expulsado"),
        "doubtful": ("doubtful", "questionable", "en duda"),
        "rotation_risk": ("rotation", "rotacion", "rest", "descanso"),
        "available": ("available", "returns", "regresa"),
    }

    def __init__(
        self,
        availability_repository: AvailabilityRepository,
        entity_repository: EntityRepository,
        normalization_service: NormalizationService | None = None,
    ) -> None:
        self.availability_repository = availability_repository
        self.entity_repository = entity_repository
        self.normalization_service = normalization_service or NormalizationService()

    def interpret_document_for_match(
        self,
        *,
        match: MatchModel,
        source_id: str,
        evidence_id: str | None,
        captured_at,
        payload: dict[str, Any],
    ) -> list:
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
        reports = self._extract_reports(match, payload)
        created = []
        for report in reports:
            team = match.home_team if report["team_side"] == "home" else match.away_team
            player = self._resolve_player(str(report["player_name"]), report.get("position"), team)
            created.append(
                self.availability_repository.save_availability(
                    match_id=match.id,
                    team_id=team.id,
                    player_id=player.id if player else None,
                    source_id=source_id,
                    evidence_id=evidence_id,
                    captured_at=captured_at,
                    status=str(report["status"]),
                    category=str(report["category"]),
                    player_name=str(report["player_name"]),
                    detail=str(report["detail"]),
                    confidence=float(report["confidence"]),
                    impact_score=float(report["impact_score"]),
                    payload=report,
                )
            )
        return created

    def _resolve_player(self, player_name: str, position: str | None, team: TeamModel) -> PlayerModel | None:
        normalized_name = self.normalization_service.normalize_team_name(player_name)
        if not normalized_name:
            return None
        player = self.entity_repository.find_player_by_normalized_name(normalized_name)
        if player is None:
            player = PlayerModel(
                name=player_name,
                normalized_name=normalized_name,
                primary_position=position,
            )
            self.entity_repository.session.add(player)
            self.entity_repository.session.flush()
        self.entity_repository.attach_player_to_team(
            team=team,
            player=player,
            squad_role=position,
        )
        return player

    def _extract_reports(self, match: MatchModel, payload: dict[str, Any]) -> list[dict[str, Any]]:
        structured = payload.get("availability_reports", [])
        reports: list[dict[str, Any]] = []
        source_url = str(payload.get("source_url", "")).strip()
        source_title = str(payload.get("title", "")).strip()
        if isinstance(structured, list):
            for item in structured:
                if not isinstance(item, dict):
                    continue
                team_side = self._resolve_team_side(match, str(item.get("team_name", "")))
                if team_side is None:
                    continue
                status = self._normalize_status(str(item.get("status", "doubtful")))
                category = self._normalize_category(str(item.get("category", "")), status)
                player_name = str(item.get("player_name", "")).strip() or f"{team_side}-unknown-player"
                reports.append(
                    {
                        "team_side": team_side,
                        "player_name": player_name,
                        "position": str(item.get("position", "")).strip() or None,
                        "status": status,
                        "category": category,
                        "detail": str(item.get("detail", "")),
                        "confidence": float(item.get("confidence", 0.8)),
                        "impact_score": float(item.get("impact_score", self.IMPACT_BY_STATUS.get(status, 0.5))),
                        "source_url": str(item.get("source_url") or source_url),
                        "source_title": str(item.get("source_title") or source_title),
                    }
                )
        if reports:
            return reports
        fallback = self._heuristic_reports(match, payload)
        return fallback

    def _heuristic_reports(self, match: MatchModel, payload: dict[str, Any]) -> list[dict[str, Any]]:
        title = str(payload.get("title", ""))
        summary = str(payload.get("summary", ""))
        headings = " ".join(str(item) for item in payload.get("headings", []))
        text = " ".join(part for part in [title, summary, headings] if part)
        lowered = text.lower()
        team_side = None
        if match.home_team.name.lower() in lowered:
            team_side = "home"
        elif match.away_team.name.lower() in lowered:
            team_side = "away"
        if team_side is None:
            return []
        status = self._status_from_text(lowered)
        category = self._category_from_text(lowered, status)
        if status is None or category is None:
            return []
        player_name = self._extract_player_name(text) or f"{team_side}-unknown-player"
        return [
            {
                "team_side": team_side,
                "player_name": player_name,
                "position": None,
                "status": status,
                "category": category,
                "detail": summary or title,
                "confidence": 0.55,
                "impact_score": self.IMPACT_BY_STATUS.get(status, 0.5),
                "source_url": str(payload.get("source_url", "")),
                "source_title": title,
            }
        ]

    def _extract_player_name(self, text: str) -> str | None:
        # Supports Latin accented capitals + apostrophes for LATAM/Iberian
        # names (Álvarez, Núñez, João, D'Alessandro). The ASCII-only
        # version we shipped originally silently skipped these.
        upper = "A-ZÀÁÂÃÄÅÇÉÊÍÏÑÓÔÕÖÚÛÜ"
        lower = "a-zàáâãäåçéêíïñóôõöúûü"
        # A single name token: a capital, then any mix of lowercase letters
        # or `'Capital` segments (covers D'Alessandro, O'Higgins).
        token = rf"[{upper}](?:[{lower}]+|['’][{upper}][{lower}]+)+"
        pattern = (
            rf"({token}"
            rf"(?:\s+(?:de|del|da|do|dos|das|la|las|el|los|von|van|du)\s+{token}"
            rf"|\s+{token})+)"
        )
        candidates = re.findall(pattern, text)
        return candidates[0] if candidates else None

    def _resolve_team_side(self, match: MatchModel, team_name: str) -> str | None:
        normalized = self.normalization_service.normalize_team_name(team_name)
        home = self.normalization_service.normalize_team_name(match.home_team.name)
        away = self.normalization_service.normalize_team_name(match.away_team.name)
        if normalized == home:
            return "home"
        if normalized == away:
            return "away"
        return None

    def _normalize_status(self, raw_status: str) -> str:
        lowered = raw_status.lower()
        for status, keywords in self.STATUS_KEYWORDS.items():
            if lowered == status or any(keyword in lowered for keyword in keywords):
                return status
        return "doubtful"

    def _normalize_category(self, raw_category: str, status: str) -> str:
        lowered = raw_category.lower()
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if lowered == category or any(keyword in lowered for keyword in keywords):
                return category
        if status == "suspended":
            return "suspension"
        if status == "rotation_risk":
            return "rotation"
        return "injury"

    def _status_from_text(self, lowered: str) -> str | None:
        for status, keywords in self.STATUS_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return status
        return None

    def _category_from_text(self, lowered: str, status: str | None) -> str | None:
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return category
        if status == "suspended":
            return "suspension"
        if status == "rotation_risk":
            return "rotation"
        if status is not None:
            return "injury"
        return None
