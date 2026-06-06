from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    uptime_seconds: float
    database_ok: bool
    schema_version: int
    schema_up_to_date: bool
    # Operational signals (P8). All optional so older deploys that don't
    # surface them still pass schema validation. Operators eyeball the
    # ages to spot stalled cron jobs without grepping logs.
    last_ingest_at: str | None = None
    last_ingest_age_seconds: float | None = None
    last_ingest_status: str | None = None
    backtest_verdict_generated_at: str | None = None
    backtest_verdict_age_seconds: float | None = None
    worker_last_executed_at: str | None = None
    worker_last_polled_at: str | None = None
    unregistered_parser_sources: int = 0


class ReadyResponse(BaseModel):
    status: str
    ready: bool
    database_ok: bool
    schema_up_to_date: bool
