from app.repositories.availability_repository import AvailabilityRepository


class AvailabilityService:
    def __init__(self, repository: AvailabilityRepository) -> None:
        self.repository = repository

    def list_match_availability(self, match_id: str):
        return self.repository.list_match_availability(match_id)
