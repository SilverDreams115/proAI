from pydantic import BaseModel


class NormalizationPreviewRequest(BaseModel):
    team_name: str | None = None
    competition_name: str | None = None


class NormalizationPreviewResponse(BaseModel):
    input_value: str
    normalized_value: str
    entity_type: str
