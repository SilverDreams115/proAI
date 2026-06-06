from app.models.tables import MatchResultModel
from app.repositories.result_repository import ResultRepository


class ResultService:
    def __init__(self, repository: ResultRepository) -> None:
        self.repository = repository

    def list_results_for_match(self, match_id: str):
        return self.repository.list_results_for_match(match_id)

    def list_context_results_for_match(self, match_id: str):
        return self.repository.list_context_results_for_match(match_id)

    def persist_result(self, result: MatchResultModel):
        return self.repository.save_result(result)
