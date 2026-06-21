"""R5.6-B: canary status endpoint (read-only)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core import settings as settings_module
from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.repositories.team_rating_repository import TeamRatingRepository

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DRAW = "PG-CANST"


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
    session.add_all([friendly, *teams.values()])
    session.flush()

    def _match(home, away, day):
        m = MatchModel(competition_id=friendly.id, home_team_id=home.id,
                       away_team_id=away.id, kickoff_at=_BASE.replace(day=day))
        session.add(m)
        session.flush()
        return m

    m1 = _match(teams["A"], teams["B"], 1)
    m2 = _match(teams["C"], teams["D"], 2)
    slate = ProgolSlateModel(label="st", draw_code=_DRAW, week_type="weekend",
                             composition_hash="h", slate_version=1)
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m1, m2), start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))

    repo = TeamRatingRepository(session)
    run = repo.create_run(algorithm_version="elo_v1", config_json="{}", source_result_count=1,
                          rated_match_count=1, excluded_match_count=0, input_checksum="in",
                          output_checksum="out", status="computed")
    repo.bulk_insert_snapshots(run.id, [
        _snap(teams["A"].id, 8, "medium", 1550.0), _snap(teams["B"].id, 10, "strong", 1495.0),
        _snap(teams["C"].id, 8, "medium", 1520.0), _snap(teams["D"].id, 9, "strong", 1490.0),
    ])
    repo.mark_run_active(run.id)
    session.add_all([
        PredictionModel(match_id=m1.id, generated_at=_BASE, home_probability=0.6,
                        draw_probability=0.25, away_probability=0.15,
                        recommended_outcome="1", confidence_band="high"),
        PredictionModel(match_id=m2.id, generated_at=_BASE, home_probability=0.5,
                        draw_probability=0.3, away_probability=0.2,
                        recommended_outcome="1", confidence_band="medium"),
    ])
    session.commit()
    return slate


@pytest.mark.anyio
async def test_status_endpoint_canary_on(client, monkeypatch) -> None:
    from app.db import session as db_mod

    with Session(db_mod.engine) as session:
        slate = _seed(session)
        slate_id = slate.id

    s = settings_module.settings
    monkeypatch.setattr(s, "team_rating_canary_enabled", True)
    monkeypatch.setattr(s, "team_rating_canary_draw_codes", [_DRAW])
    monkeypatch.setattr(s, "team_rating_canary_positions", [1, 2, 3, 5, 8, 11])
    monkeypatch.setattr(s, "team_rating_canary_competition_allowlist", ["International Friendlies"])

    resp = await client.get(
        f"/api/predictions/slates/{slate_id}/team-rating-canary-status"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["canary_enabled"] is True
    assert body["scope"] == _DRAW
    assert body["active_positions"] == [1, 2]
    assert body["full_activation"] is False
    assert body["ticket_integration"] is False
    assert body["rollback_available"] is True


@pytest.mark.anyio
async def test_status_endpoint_canary_off(client, monkeypatch) -> None:
    from app.db import session as db_mod

    with Session(db_mod.engine) as session:
        slate = _seed(session)
        slate_id = slate.id

    monkeypatch.setattr(settings_module.settings, "team_rating_canary_enabled", False)
    resp = await client.get(
        f"/api/predictions/slates/{slate_id}/team-rating-canary-status"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["canary_enabled"] is False
    assert body["active_positions"] == []


@pytest.mark.anyio
async def test_status_endpoint_404(client) -> None:
    resp = await client.get(
        "/api/predictions/slates/nope/team-rating-canary-status"
    )
    assert resp.status_code == 404
