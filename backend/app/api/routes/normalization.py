from fastapi import APIRouter

from app.schemas.normalization import NormalizationPreviewRequest
from app.schemas.normalization import NormalizationPreviewResponse
from app.services.normalization_service import NormalizationService

router = APIRouter(prefix="/normalization", tags=["normalization"])


@router.post("/preview", response_model=list[NormalizationPreviewResponse])
async def preview_normalization(
    payload: NormalizationPreviewRequest,
) -> list[NormalizationPreviewResponse]:
    service = NormalizationService()
    responses: list[NormalizationPreviewResponse] = []
    if payload.team_name:
        responses.append(
            NormalizationPreviewResponse(
                input_value=payload.team_name,
                normalized_value=service.normalize_team_name(payload.team_name),
                entity_type="team",
            )
        )
    if payload.competition_name:
        responses.append(
            NormalizationPreviewResponse(
                input_value=payload.competition_name,
                normalized_value=service.normalize_competition_name(payload.competition_name),
                entity_type="competition",
            )
        )
    return responses
