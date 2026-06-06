from datetime import datetime

from pydantic import BaseModel
from pydantic import Field


class ScheduledJobCreate(BaseModel):
    source_id: str
    job_name: str
    interval_minutes: int = Field(ge=1)
    next_run_at: datetime
    is_active: bool = True


class ScheduledJobResponse(BaseModel):
    id: str
    source_id: str
    job_name: str
    interval_minutes: int
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime


class SourceHealthResponse(BaseModel):
    id: str
    source_id: str
    checked_at: datetime
    status: str
    latency_ms: int
    detail: str
