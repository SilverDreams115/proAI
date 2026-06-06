from datetime import datetime

from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.tables import ScheduledIngestionJobModel
from app.models.tables import SourceHealthCheckModel
from app.schemas.scheduler import ScheduledJobCreate


class SchedulerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_job(self, payload: ScheduledJobCreate) -> ScheduledIngestionJobModel:
        job = ScheduledIngestionJobModel(
            source_id=payload.source_id,
            job_name=payload.job_name,
            interval_minutes=payload.interval_minutes,
            next_run_at=payload.next_run_at,
            is_active=payload.is_active,
        )
        self.session.add(job)
        self.session.flush()
        self.session.refresh(job)
        return job

    def get_job_by_name(self, job_name: str) -> ScheduledIngestionJobModel | None:
        statement = select(ScheduledIngestionJobModel).where(ScheduledIngestionJobModel.job_name == job_name)
        return self.session.scalar(statement)

    def list_jobs(self) -> list[ScheduledIngestionJobModel]:
        statement = select(ScheduledIngestionJobModel).order_by(ScheduledIngestionJobModel.job_name.asc())
        return list(self.session.scalars(statement))

    def list_due_jobs(self, now: datetime) -> list[ScheduledIngestionJobModel]:
        statement = (
            select(ScheduledIngestionJobModel)
            .where(
                ScheduledIngestionJobModel.is_active.is_(True),
                ScheduledIngestionJobModel.next_run_at <= now,
            )
            .order_by(ScheduledIngestionJobModel.next_run_at.asc())
        )
        return list(self.session.scalars(statement))

    def claim_job(
        self,
        job_id: str,
        expected_next_run_at: datetime,
        lease_until: datetime,
    ) -> bool:
        statement = (
            update(ScheduledIngestionJobModel)
            .where(
                ScheduledIngestionJobModel.id == job_id,
                ScheduledIngestionJobModel.is_active.is_(True),
                ScheduledIngestionJobModel.next_run_at == expected_next_run_at,
            )
            .values(next_run_at=lease_until)
        )
        result = self.session.execute(statement)
        self.session.flush()
        return int(getattr(result, "rowcount", 0) or 0) == 1

    def save_job(self, job: ScheduledIngestionJobModel) -> ScheduledIngestionJobModel:
        self.session.add(job)
        self.session.flush()
        self.session.refresh(job)
        return job

    def add_health_check(
        self,
        source_id: str,
        status: str,
        latency_ms: int,
        detail: str,
    ) -> SourceHealthCheckModel:
        check = SourceHealthCheckModel(
            source_id=source_id,
            status=status,
            latency_ms=latency_ms,
            detail=detail,
        )
        self.session.add(check)
        self.session.flush()
        self.session.refresh(check)
        return check

    def list_health_checks(self) -> list[SourceHealthCheckModel]:
        statement = select(SourceHealthCheckModel).order_by(SourceHealthCheckModel.checked_at.desc())
        return list(self.session.scalars(statement))
