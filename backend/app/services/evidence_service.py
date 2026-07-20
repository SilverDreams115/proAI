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
        # Per-instance cache of a match's normalized token sets. The auto-link
        # / stats-link passes score every unmatched document against the full
        # match table (tens of thousands of rows); without this, each match's
        # team/competition names were re-normalized once per document — the
        # O(documents x matches) normalization that pinned the worker at 100%
        # CPU. Cached by match id, computed once per instance.
        self._match_token_cache: dict[str, tuple[set[str], set[str], set[str]]] = {}

    def list_match_evidence(self, match_id: str):
        return self.repository.list_match_evidence(match_id)

    def _match_tokens(self, match: MatchModel) -> tuple[set[str], set[str], set[str]]:
        cached = self._match_token_cache.get(match.id)
        if cached is not None:
            return cached
        ns = self.normalization_service
        home = {t for t in ns.normalize_team_name(match.home_team.name).split("-") if t}
        away = {t for t in ns.normalize_team_name(match.away_team.name).split("-") if t}
        comp = {t for t in ns.normalize_competition_name(match.competition.name).split("-") if t}
        tokens = (home, away, comp)
        self._match_token_cache[match.id] = tokens
        return tokens

    @staticmethod
    def _score_from_tokens(
        home_tokens: set[str],
        away_tokens: set[str],
        competition_tokens: set[str],
        haystack_tokens: set[str],
    ) -> float:
        # Requires at least one home AND one away token in the haystack, then
        # scores the fraction of home+away+competition tokens present. Identical
        # scoring to the pre-optimization per-pair computation.
        home_matches = home_tokens & haystack_tokens
        if not home_matches:
            return 0.0
        away_matches = away_tokens & haystack_tokens
        if not away_matches:
            return 0.0
        total = len(home_tokens) + len(away_tokens) + len(competition_tokens)
        if total == 0:
            return 0.0
        scored = len(home_matches) + len(away_matches) + len(competition_tokens & haystack_tokens)
        return scored / total

    def auto_link_unmatched_documents(self, matches: list[MatchModel]) -> list[tuple[SourceDocumentModel, str]]:
        linked: list[tuple[SourceDocumentModel, str]] = []
        documents = self.repository.list_unlinked_documents()
        if not documents:
            return linked
        # Precompute each match's normalized token sets once (via the cache),
        # dropping matches that can never score (missing home/away tokens), so
        # the per-document loop is pure set intersection instead of re-running
        # normalization len(documents) times over the whole match table.
        scored_matches: list[tuple[MatchModel, set[str], set[str], set[str]]] = []
        for match in matches:
            home, away, comp = self._match_tokens(match)
            if home and away:
                scored_matches.append((match, home, away, comp))
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
            # Compute the haystack token set once per document (not once per
            # (document, match) pair as the old _score_document_match did).
            haystack_tokens = {token for token in normalized_haystack.split("-") if token}
            best_match: MatchModel | None = None
            best_score = 0.0
            for match, home, away, comp in scored_matches:
                score = self._score_from_tokens(home, away, comp, haystack_tokens)
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
        home_tokens, away_tokens, competition_tokens = self._match_tokens(match)
        return self._score_from_tokens(
            home_tokens, away_tokens, competition_tokens, haystack_tokens
        )
