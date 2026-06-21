from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.repositories.feature_repository import FeatureRepository
from app.repositories.entity_repository import EntityRepository
from app.repositories.result_repository import ResultRepository
from app.repositories.slate_repository import SlateRepository
from app.repositories.ticket_repository import TicketRecommendationRepository
from app.repositories.training_repository import TrainingRepository
from app.schemas.prediction import MatchPredictionResponse
from app.schemas.prediction import TicketRecommendationResponse
from app.schemas.feature import MatchFeatureResponse
from app.schemas.feature import MatchDataQualityResponse
from app.schemas.team_rating_shadow import TeamRatingShadowResponse
from app.services.feature_service import FeatureService
from app.services.ingestion_service import IngestionService
from app.services.model_training_service import ModelTrainingService
from app.services.prediction_service import PredictionService
from app.services.slate_service import SlateService
from app.services.ticket_recommendation_service import TicketRecommendationService

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/slates/{slate_id}", response_model=list[MatchPredictionResponse])
async def get_slate_predictions(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> list[MatchPredictionResponse]:
    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")

    training_service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    prediction_service = PredictionService(training_service)
    return prediction_service.build_slate_predictions(slate, persist_audit=False)


@router.post("/slates/{slate_id}/refresh", response_model=list[MatchPredictionResponse], status_code=200)
async def refresh_slate_predictions(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> list[MatchPredictionResponse]:
    """Force a re-score for the slate by clearing every layer of cache
    that sits between fresh ingest data and the prediction response.

    Without this, operators have to restart the container to surface
    new team-merge / ingestion data — the prediction TTL, feature
    snapshots, XGBoost verdict, and competition-gap caches all
    short-circuit reads from the latest DB state. The route is
    idempotent: each call invalidates and rebuilds; safe to retry."""
    from app.services.feature_service import FeatureService
    from app.services.prediction_service import invalidate_slate_prediction_cache

    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")

    training_service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    feature_service = FeatureService(FeatureRepository(session), ResultRepository(session))

    invalidate_slate_prediction_cache(slate_id)
    feature_service.invalidate_competition_gap_cache()
    training_service.reset_xgboost_verdict_cache()
    IngestionService._competition_tolerance_cache.clear()  # type: ignore[attr-defined]

    prediction_service = PredictionService(training_service)
    return prediction_service.build_slate_predictions(slate)


@router.get("/slates/{slate_id}/features", response_model=list[MatchFeatureResponse])
async def get_slate_feature_snapshots(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> list[MatchFeatureResponse]:
    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")

    feature_service = FeatureService(FeatureRepository(session), ResultRepository(session))
    responses: list[MatchFeatureResponse] = []
    for slate_match in sorted(slate.matches, key=lambda item: item.position):
        match, payload, generated_at = feature_service.build_match_features(
            slate_match.match.id, persist=False
        )
        responses.append(
            MatchFeatureResponse(
                match_id=match.id,
                generated_at=generated_at,
                feature_set_version=feature_service.FEATURE_SET_VERSION,
                home_team_name=match.home_team.name,
                away_team_name=match.away_team.name,
                competition_name=match.competition.name,
                payload=payload,
            )
        )
    return responses


@router.get(
    "/slates/{slate_id}/team-rating-shadow",
    response_model=TeamRatingShadowResponse,
)
async def get_slate_team_rating_shadow(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> TeamRatingShadowResponse:
    """Read-only Team Rating Shadow diagnostic for the slate.

    Shadow-only: reports what the inactive team-rating gate *would* do if it
    were enabled (eligibility, routing, blockers per match) without touching
    predictions, picks, tickets or probabilities, and without writing any row.
    """
    from app.services.team_rating_shadow_report import build_slate_shadow_report

    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    return build_slate_shadow_report(session, slate)


@router.get("/slates/{slate_id}/quality", response_model=list[MatchDataQualityResponse])
async def get_slate_data_quality(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> list[MatchDataQualityResponse]:
    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")

    training_service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    prediction_service = PredictionService(training_service)
    policies = {
        prediction.match_id: prediction
        for prediction in prediction_service.build_slate_predictions(slate, persist_audit=False)
    }
    feature_repository = FeatureRepository(session)
    feature_service = FeatureService(feature_repository, ResultRepository(session))
    responses: list[MatchDataQualityResponse] = []
    for slate_match in sorted(slate.matches, key=lambda item: item.position):
        match, payload, _generated_at = feature_service.build_match_features(
            slate_match.match.id, persist=False
        )
        prediction = policies[match.id]
        evidence_count = max(
            int(payload.get("evidence_items", 0) or 0),
            len(feature_repository.list_match_evidence(match.id)),
            len(payload.get("evidence_summaries", [])) if isinstance(payload.get("evidence_summaries"), list) else 0,
        )
        recent_results_count = int(payload.get("recent_results_count", 0) or 0)
        head_to_head_results_count = int(float(payload.get("head_to_head_results_count", 0) or 0))
        availability_count = len(feature_repository.list_match_availability(match.id))
        score = 0
        missing: list[str] = []
        notes: list[str] = []
        if evidence_count:
            score += 30
            notes.append(f"{evidence_count} evidencia(s) contextual(es) enlazada(s).")
        else:
            missing.append("evidencia contextual")
        if recent_results_count >= 2:
            score += 25
            notes.append(f"{recent_results_count} resultado(s) recientes usados.")
        elif recent_results_count:
            score += 12
            notes.append("Solo hay una muestra reciente minima.")
        else:
            missing.append("forma reciente")
        if head_to_head_results_count:
            score += 20
            notes.append(f"{head_to_head_results_count} antecedente(s) directo(s).")
        else:
            missing.append("historial directo")
        if prediction.competition_readiness in {"ready", "covered"}:
            score += 15
            notes.append(f"Benchmark de competencia: {prediction.competition_readiness}.")
        elif prediction.competition_readiness == "context_only":
            score += 8
            missing.append("benchmark historico")
            notes.append("Competencia con contexto local, sin backtest auditado.")
        else:
            missing.append("benchmark confiable")
        if availability_count:
            score += 5
            notes.append(f"{availability_count} reporte(s) de disponibilidad.")
        else:
            notes.append("Sin bajas o alineaciones confirmadas por fuente enlazada.")
        if payload.get("venue_known"):
            score += 5

        quality_level = "good" if score >= 70 else "partial" if score >= 40 else "thin"
        responses.append(
            MatchDataQualityResponse(
                match_id=match.id,
                position=slate_match.position,
                home_team_name=match.home_team.name,
                away_team_name=match.away_team.name,
                competition_name=match.competition.name,
                quality_score=min(score, 100),
                quality_level=quality_level,
                evidence_count=evidence_count,
                recent_results_count=recent_results_count,
                head_to_head_results_count=head_to_head_results_count,
                availability_count=availability_count,
                competition_readiness=prediction.competition_readiness,
                live_pick_allowed=prediction.live_pick_allowed,
                missing=missing,
                notes=notes,
            )
        )
    return responses


@router.get("/slates/{slate_id}/ticket/coverage-target")
async def get_coverage_target_for_slate(
    slate_id: str,
    min_correct: int | None = None,
    target_probability: float = 0.90,
    session: Session = Depends(get_db_session),
) -> dict:
    """Return the smallest (doubles, triples) budget needed to reach
    `P(>= min_correct correct) >= target_probability` on this slate.

    `min_correct` defaults to 90% of the slate (rounded up); operators
    can override it. The response always includes the achieved
    probability for the recommended budget, plus a `feasible` flag so the
    UI can warn when no budget within the safe cap reaches the target.
    """
    import math as _math

    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")
    training_service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    prediction_service = PredictionService(training_service)
    predictions = prediction_service.build_slate_predictions(slate, persist_audit=False)
    if not predictions:
        return {
            "slate_id": slate_id,
            "min_correct": 0,
            "target_probability": target_probability,
            "doubles_needed": 0,
            "triples_needed": 0,
            "achieved_probability": 0.0,
            "target_reached": False,
            "expected_correct": 0.0,
        }
    from app.services.ticket_optimizer import TicketOption, min_budget_for_target

    options = [
        # DECISION probabilities (guardrailed), not raw model output, so the
        # budget projection matches what the ticket optimizer actually bets.
        TicketOption(
            match_id=p.match_id,
            top1=max(p.decision_vector()),
            top2=sorted(p.decision_vector(), reverse=True)[1],
            top3=min(p.decision_vector()),
        )
        for p in predictions
    ]
    n = len(predictions)
    floor = min_correct if min_correct is not None else _math.ceil(n * 0.9)
    floor = max(1, min(floor, n))
    report = min_budget_for_target(
        options,
        target_probability=max(0.0, min(target_probability, 1.0)),
        min_correct=floor,
        max_doubles_cap=n,
        max_triples_cap=n,
    )
    return {
        "slate_id": slate_id,
        "matches": n,
        "min_correct": floor,
        "target_probability": target_probability,
        "doubles_needed": report.doubles_needed,
        "triples_needed": report.triples_needed,
        "achieved_probability": round(report.plan.probability_target_met, 6),
        "target_reached": report.target_reached,
        "expected_correct": round(report.plan.expected_correct, 4),
        "decisions": report.plan.decisions,
    }


@router.get("/slates/{slate_id}/ticket", response_model=TicketRecommendationResponse)
async def get_slate_ticket_recommendations(
    slate_id: str,
    session: Session = Depends(get_db_session),
) -> TicketRecommendationResponse:
    slate_service = SlateService(SlateRepository(session))
    slate = slate_service.get_slate(slate_id)
    if slate is None:
        raise HTTPException(status_code=404, detail="Slate not found.")

    training_service = ModelTrainingService(
        TrainingRepository(session),
        EntityRepository(session),
        ResultRepository(session),
    )
    prediction_service = PredictionService(training_service)
    predictions = prediction_service.build_slate_predictions(slate, persist_audit=False)
    feature_service = FeatureService(FeatureRepository(session), ResultRepository(session))
    ticket_service = TicketRecommendationService(TicketRecommendationRepository(session))
    snapshot = ticket_service.repository.latest_for_slate(
        slate.id,
        composition_hash=getattr(slate, "composition_hash", None),
        model_version=ticket_service.MODEL_VERSION,
    )
    if snapshot is not None:
        return ticket_service.response_from_snapshot(snapshot)

    feature_payloads_by_match: dict[str, dict[str, object]] = {}
    for slate_match in sorted(slate.matches, key=lambda item: item.position):
        _match, payload, _generated_at = feature_service.build_match_features(
            slate_match.match.id, persist=False
        )
        feature_payloads_by_match[slate_match.match.id] = payload
    return ticket_service.build_read_only(
        slate=slate,
        predictions=predictions,
        feature_payloads_by_match=feature_payloads_by_match,
    )
