from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.connectors.base import SourceDocument
from app.models.tables import SourceModel
from app.repositories.scheduler_repository import SchedulerRepository
from app.schemas.scheduler import ScheduledJobCreate
from app.services.ingestion_service import IngestionService
from app.services.scheduler_service import SchedulerService


class RetryConnector:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self) -> list[SourceDocument]:
        self.calls += 1
        if self.calls < 3:
            raise TimeoutError("temporary source timeout")
        return [
            SourceDocument(
                source_name="Retry Source",
                source_url="https://example.com/retry",
                captured_at=datetime.now(timezone.utc),
                payload={"title": "Recovered", "summary": "Recovered"},
            )
        ]


class StubRun:
    def __init__(self, status: str) -> None:
        self.status = status


class StubIngestionService:
    def __init__(self, status: str = "completed") -> None:
        self.status = status
        self.calls: list[str] = []

    def run_for_source(self, source_id: str) -> StubRun:
        self.calls.append(source_id)
        return StubRun(self.status)


class StubSession:
    def __init__(self) -> None:
        self.info: dict[str, int] = {}
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_ingestion_retries_transient_fetch_errors() -> None:
    service = IngestionService(SimpleNamespace(session=None))
    connector = RetryConnector()

    documents = service._fetch_documents(connector)

    assert len(documents) == 1
    assert connector.calls == 3


def test_scheduler_claim_job_is_single_winner(tmp_path) -> None:
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_session.configure_session(f"sqlite:///{tmp_path / 'scheduler_claim.db'}")
    run_migrations(db_session.engine)

    session_one = db_session.SessionLocal()
    session_two = db_session.SessionLocal()
    try:
        repository_one = SchedulerRepository(session_one)
        repository_two = SchedulerRepository(session_two)
        source = SourceModel(
            name="Claim Once Source",
            base_url="https://example.com/claim",
            kind="html_page",
            parser_profile="generic",
            is_active=True,
        )
        session_one.add(source)
        session_one.flush()
        payload = ScheduledJobCreate(
            source_id=source.id,
            job_name="claim-once-job",
            interval_minutes=5,
            next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            is_active=True,
        )
        repository_one.create_job(payload)
        session_one.commit()

        job_view_one = repository_one.list_due_jobs(datetime.now(timezone.utc))[0]
        job_view_two = repository_two.list_due_jobs(datetime.now(timezone.utc))[0]
        lease_until = datetime.now(timezone.utc) + timedelta(minutes=5)

        claimed_one = repository_one.claim_job(job_view_one.id, job_view_one.next_run_at, lease_until)
        session_one.commit()
        claimed_two = repository_two.claim_job(job_view_two.id, job_view_two.next_run_at, lease_until)

        assert claimed_one is True
        assert claimed_two is False
    finally:
        session_one.close()
        session_two.close()


def test_scheduler_skips_already_claimed_jobs(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id="job-1",
        source_id="source-1",
        interval_minutes=5,
        next_run_at=now - timedelta(minutes=1),
        last_run_at=None,
    )

    class StubRepository:
        def __init__(self) -> None:
            self.session = StubSession()
            self.saved_jobs = 0

        def list_due_jobs(self, current_now):
            return [job]

        def claim_job(self, job_id, expected_next_run_at, lease_until):
            return False

        def save_job(self, current_job):
            self.saved_jobs += 1
            return current_job

    repository = StubRepository()
    ingestion_repository = SimpleNamespace()
    scheduler = SchedulerService(repository, ingestion_repository)
    stub_ingestion = StubIngestionService()

    monkeypatch.setattr("app.services.scheduler_service.IngestionService", lambda _: stub_ingestion)

    runs = scheduler.run_due_jobs()

    assert runs == []
    assert stub_ingestion.calls == []
    assert repository.saved_jobs == 0


def test_scheduler_upserts_current_progol_refresh_job(tmp_path, monkeypatch) -> None:
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    context_root = tmp_path / "progol_context"
    context_root.mkdir()
    (context_root / "current.json").write_text('{"items": []}', encoding="utf-8")
    monkeypatch.setenv("PROAI_LOCAL_CONTEXT_ROOT", str(context_root))

    db_session.configure_session(f"sqlite:///{tmp_path / 'current_job.db'}")
    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        from app.repositories.ingestion_repository import IngestionRepository
        from app.repositories.slate_repository import SlateRepository
        from app.repositories.source_repository import SourceRepository
        from app.services.current_progol_service import CurrentProgolService

        ingestion_repository = IngestionRepository(session)
        source = CurrentProgolService(
            SourceRepository(session),
            ingestion_repository,
            SlateRepository(session),
        ).ensure_default_context_source()
        scheduler = SchedulerService(SchedulerRepository(session), ingestion_repository)

        created = scheduler.ensure_current_progol_refresh_job(
            source_id=source.id,
            interval_minutes=60,
        )
        updated = scheduler.ensure_current_progol_refresh_job(
            source_id=source.id,
            interval_minutes=30,
        )
        jobs = SchedulerRepository(session).list_jobs()

        assert created.id == updated.id
        assert len(jobs) == 1
        assert jobs[0].job_name == "current-progol-refresh"
        assert jobs[0].interval_minutes == 30
    finally:
        session.close()


def test_worker_run_loop_records_failed_iterations(monkeypatch) -> None:
    from app.workers.scheduler_worker import SchedulerWorker

    worker = SchedulerWorker()
    calls = {"count": 0}

    def fail_once_then_succeed() -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return 2

    monkeypatch.setattr(worker, "run_once", fail_once_then_succeed)

    summary = worker.run_loop(poll_interval_seconds=0, max_iterations=2)

    assert summary.iterations == 2
    assert summary.executed_runs == 2
    assert summary.failed_iterations == 1
