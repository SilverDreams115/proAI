"""R5.6-A: readiness endpoint must be read-only and diagnostic only."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import CompetitionModel
from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.models.tables import TicketRecommendationSnapshotModel
from app.repositories.team_rating_repository import TeamRatingRepository

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _snap(team_id, matches, bucket, rating):
    return {
        "team_id": team_id, "namespace": "national", "rating": rating,
        "rating_delta": 0.0, "matches_count": matches, "wins": matches,
        "draws": 0, "losses": 0, "goals_for": matches, "goals_against": 0,
        "confidence_bucket": bucket, "last_result_at": None,
        "competitions_seen_json": json.dumps(["national"]),
    }


def _seed(session) -> ProgolSlateModel:
    friendly = CompetitionModel(name="International Friendlies", country="World")
    teams = {n: TeamModel(name=n, country=None) for n in "ABCD"}
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    session.add_all([friendly, ghost, *teams.values()])
    session.flush()

    def _match(home, away, day):
        m = MatchModel(
            competition_id=friendly.id, home_team_id=home.id,
            away_team_id=away.id, kickoff_at=_BASE.replace(day=day),
        )
        session.add(m)
        session.flush()
        return m

    m1 = _match(teams["A"], teams["B"], 1)
    m2 = _match(teams["C"], teams["D"], 2)
    m3 = _match(teams["A"], ghost, 3)
    slate = ProgolSlateModel(
        label="ep", draw_code="PG-RDEP", week_type="weekend",
        composition_hash="h", slate_version=1,
    )
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m1, m2, m3), start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))

    repo = TeamRatingRepository(session)
    run = repo.create_run(
        algorithm_version="elo_v1", config_json="{}", source_result_count=1,
        rated_match_count=1, excluded_match_count=0, input_checksum="in",
        output_checksum="out", status="computed",
    )
    repo.bulk_insert_snapshots(run.id, [
        _snap(teams["A"].id, 8, "medium", 1550.0),
        _snap(teams["B"].id, 10, "strong", 1495.0),
        _snap(teams["C"].id, 8, "medium", 1520.0),
        _snap(teams["D"].id, 9, "strong", 1490.0),
    ])
    repo.mark_run_active(run.id)
    session.add_all([
        PredictionModel(match_id=m1.id, generated_at=_BASE, home_probability=0.6,
                        draw_probability=0.25, away_probability=0.15,
                        recommended_outcome="1", confidence_band="high"),
        PredictionModel(match_id=m2.id, generated_at=_BASE, home_probability=0.4,
                        draw_probability=0.35, away_probability=0.25,
                        recommended_outcome="1", confidence_band="medium"),
        PredictionModel(match_id=m3.id, generated_at=_BASE, home_probability=0.4,
                        draw_probability=0.3, away_probability=0.3,
                        recommended_outcome="1", confidence_band="low"),
    ])
    session.commit()
    return slate


@pytest.mark.anyio
async def test_readiness_endpoint_200_and_read_only(client) -> None:
    from app.db import session as db_mod

    engine = db_mod.engine
    with Session(engine) as session:
        slate = _seed(session)
        slate_id = slate.id

    def counts():
        with Session(engine) as s:
            return (
                s.scalar(select(func.count()).select_from(MatchFeatureSnapshotModel)) or 0,
                s.scalar(select(func.count()).select_from(PredictionModel)) or 0,
                s.scalar(select(func.count()).select_from(TicketRecommendationSnapshotModel)) or 0,
            )

    before = counts()
    resp = await client.get(
        f"/api/predictions/slates/{slate_id}/team-rating-activation-readiness"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "activation_readiness"
    assert body["production_active"] is False
    assert body["ready_for_canary"] is False
    assert body["ready_for_full_activation"] is False
    assert body["calibrator"]["approval_status"] == "approved_inactive"
    assert body["calibrator"]["productive_available"] is False
    assert "canary_allowed_matches" in body["canary_plan"]
    assert len(body["canary_plan"]["rollback"]) >= 3

    for _ in range(4):
        again = await client.get(
            f"/api/predictions/slates/{slate_id}/team-rating-activation-readiness"
        )
        assert again.status_code == 200
    assert counts() == before


@pytest.mark.anyio
async def test_readiness_endpoint_404(client) -> None:
    resp = await client.get(
        "/api/predictions/slates/does-not-exist/team-rating-activation-readiness"
    )
    assert resp.status_code == 404
