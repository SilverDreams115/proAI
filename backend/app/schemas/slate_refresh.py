from pydantic import BaseModel
from pydantic import Field

from app.schemas.slate_discovery import SlateDiscoveryRequest
from app.schemas.slate_discovery import SlateDiscoveryResponse


class SlateAutoRefreshRequest(BaseModel):
    catalog_source_id: str | None = None
    fixture_source_ids: list[str] = Field(default_factory=list)
    availability_source_ids: list[str] = Field(default_factory=list)
    discovery: SlateDiscoveryRequest


class SlateAutoRefreshResponse(BaseModel):
    ingested_source_ids: list[str]
    discovery: SlateDiscoveryResponse


class CurrentProgolRefreshRequest(BaseModel):
    source_name: str = "Progol Current Local Context"
    local_path: str | None = None


class CurrentProgolRefreshResponse(BaseModel):
    slate_id: str
    draw_code: str
    label: str
    match_count: int
    prediction_count: int = 0
    archived_slate_ids: list[str]
    ingestion_run_id: str | None = None
    ingestion_status: str | None = None
    step_durations_ms: dict[str, float] = Field(default_factory=dict)
