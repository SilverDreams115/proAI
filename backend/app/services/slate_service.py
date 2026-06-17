from datetime import datetime, timezone

from app.db.session import managed_transaction
from app.models.tables import ProgolSlateModel
from app.repositories.slate_repository import SlateRepository
from app.schemas.slate import ProgolSlateCreate


class SlateService:
    def __init__(self, repository: SlateRepository) -> None:
        self.repository = repository

    def list_slates(self, include_closed: bool = False) -> list[ProgolSlateModel]:
        now = datetime.now(timezone.utc)
        slates = self.repository.list_slates()
        if not include_closed:
            slates = [slate for slate in slates if not self.is_closed(slate, now)]
        return sorted(slates, key=lambda slate: self._active_slate_sort_key(slate, now))

    def get_slate(self, slate_id: str) -> ProgolSlateModel | None:
        return self.repository.get_slate(slate_id)

    def create_slate(self, payload: ProgolSlateCreate) -> ProgolSlateModel:
        with managed_transaction(self.repository.session):
            return self.repository.upsert_slate(payload)

    def get_active_slate(self, now: datetime | None = None) -> ProgolSlateModel | None:
        # Returns the single most-urgent open slate across all week_types.
        # Use get_active_slate_by_week_type when you need week_type isolation.
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        slates = [slate for slate in self.repository.list_slates() if not self.is_closed(slate, now)]
        if not slates:
            return None
        slates.sort(key=lambda slate: self._active_slate_sort_key(slate, now))
        return slates[0]

    def get_active_slate_by_week_type(
        self, week_type: str, now: datetime | None = None
    ) -> ProgolSlateModel | None:
        """Return the most-urgent open slate for a specific week_type.

        Used by auto-promote to check whether the SAME week_type concurso
        is approaching cierre before promoting the next one of that type.
        Weekend and midweek/MS slates are independent and must not block
        each other's promotion.
        """
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        slates = [
            s for s in self.repository.list_slates()
            if not self.is_closed(s, now) and s.week_type == week_type
        ]
        if not slates:
            return None
        slates.sort(key=lambda slate: self._active_slate_sort_key(slate, now))
        return slates[0]

    def archive_due_slates(self, now: datetime | None = None) -> list[str]:
        # Idempotent: returns the draw_codes archived on THIS call. A second
        # call within the same minute returns []. The worker uses the
        # returned list to emit a metric and log a structured event.
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        due = self.repository.list_due_for_archive(now)
        if not due:
            return []
        archived: list[str] = []
        with managed_transaction(self.repository.session):
            for slate in due:
                self.repository.mark_archived(slate)
                archived.append(slate.draw_code)
        return archived

    def is_closed(self, slate: ProgolSlateModel, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if slate.is_archived:
            return True
        if slate.registration_closes_at is not None:
            closes_at = slate.registration_closes_at
            if closes_at.tzinfo is None:
                closes_at = closes_at.replace(tzinfo=timezone.utc)
            return closes_at <= now
        kickoffs = self._normalized_kickoffs(slate)
        return bool(kickoffs and min(kickoffs) <= now)

    def _active_slate_sort_key(self, slate: ProgolSlateModel, now: datetime) -> tuple[int, float, int, float]:
        week_priority = {"weekend": 0, "midweek": 1, "revancha": 2}.get(slate.week_type, 3)
        if self.is_closed(slate, now):
            closed_at = slate.registration_closes_at or self._last_kickoff(slate) or slate.created_at
            if closed_at.tzinfo is None:
                closed_at = closed_at.replace(tzinfo=timezone.utc)
            return 2, (now - closed_at).total_seconds(), week_priority, -slate.created_at.timestamp()
        if slate.registration_closes_at is not None:
            closes_at = slate.registration_closes_at
            if closes_at.tzinfo is None:
                closes_at = closes_at.replace(tzinfo=timezone.utc)
            return 0, (closes_at - now).total_seconds(), week_priority, -slate.created_at.timestamp()
        first_kickoff = self._first_kickoff(slate)
        if first_kickoff is not None:
            return 1, (first_kickoff - now).total_seconds(), week_priority, -slate.created_at.timestamp()
        return 3, 0.0, week_priority, -slate.created_at.timestamp()

    def _normalized_kickoffs(self, slate: ProgolSlateModel) -> list[datetime]:
        kickoffs = [link.match.kickoff_at for link in slate.matches if link.match is not None]
        return [
            kickoff.replace(tzinfo=timezone.utc) if kickoff.tzinfo is None else kickoff.astimezone(timezone.utc)
            for kickoff in kickoffs
        ]

    def _first_kickoff(self, slate: ProgolSlateModel) -> datetime | None:
        normalized_kickoffs = self._normalized_kickoffs(slate)
        if not normalized_kickoffs:
            return None
        return min(normalized_kickoffs)

    def _last_kickoff(self, slate: ProgolSlateModel) -> datetime | None:
        normalized_kickoffs = self._normalized_kickoffs(slate)
        if not normalized_kickoffs:
            return None
        return max(normalized_kickoffs)
