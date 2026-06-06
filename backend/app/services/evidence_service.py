import json

from app.models.tables import MatchModel
from app.models.tables import SourceDocumentModel
from app.repositories.evidence_repository import EvidenceRepository
from app.services.normalization_service import NormalizationService


class EvidenceService:
    MATCH_THRESHOLD = 0.55

    def __init__(
        self,
        repository: EvidenceRepository,
        normalization_service: NormalizationService | None = None,
    ) -> None:
        self.repository = repository
        self.normalization_service = normalization_service or NormalizationService()

    def list_match_evidence(self, match_id: str):
        return self.repository.list_match_evidence(match_id)

    def auto_link_unmatched_documents(self, matches: list[MatchModel]) -> list[tuple[SourceDocumentModel, str]]:
        linked: list[tuple[SourceDocumentModel, str]] = []
        documents = self.repository.list_unlinked_documents()
        for document in documents:
            payload = json.loads(document.payload_json)
            haystack = " ".join(
                [
                    document.title,
                    document.summary,
                    " ".join(str(item) for item in payload.get("headings", [])),
                    str(payload.get("competition", "")),
                    " ".join(str(item) for item in payload.get("teams", [])),
                    str(payload.get("context_summary", "")),
                ]
            )
            normalized_haystack = self.normalization_service.normalize_competition_name(haystack)
            best_match: MatchModel | None = None
            best_score = 0.0
            for match in matches:
                score = self._score_document_match(match, normalized_haystack)
                if score > best_score:
                    best_score = score
                    best_match = match
            if best_match is not None and best_score >= self.MATCH_THRESHOLD:
                summary = f"Linked source document '{document.title}' to match context."
                evidence = self.repository.create_evidence_for_document(
                    document=document,
                    match_id=best_match.id,
                    summary=summary,
                    confidence=round(best_score, 2),
                    payload=payload,
                )
                linked.append((document, evidence.id))
        return linked

    def _score_document_match(self, match: MatchModel, normalized_haystack: str) -> float:
        haystack_tokens = {token for token in normalized_haystack.split("-") if token}
        home_tokens = {
            token
            for token in self.normalization_service.normalize_team_name(match.home_team.name).split("-")
            if token
        }
        away_tokens = {
            token
            for token in self.normalization_service.normalize_team_name(match.away_team.name).split("-")
            if token
        }
        competition_tokens = {
            token
            for token in self.normalization_service.normalize_competition_name(match.competition.name).split("-")
            if token
        }
        home_matches = home_tokens.intersection(haystack_tokens)
        away_matches = away_tokens.intersection(haystack_tokens)
        if not home_matches or not away_matches:
            return 0.0
        scored = 0
        total = len(home_tokens) + len(away_tokens) + len(competition_tokens)
        if total == 0:
            return 0.0
        scored += len(home_matches)
        scored += len(away_matches)
        scored += len(competition_tokens.intersection(haystack_tokens))
        return scored / total
