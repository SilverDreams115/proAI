"""R5.6-B: the predictions API applies the canary additively for scoped
positions only, and writes nothing."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import settings as settings_module
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
_DRAW = "PG-CANINT"


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
        m = MatchModel(competition_id=friendly.id, home_team_id=home.id,
                       away_team_id=away.id, kickoff_at=_BASE.replace(day=day))
        session.add(m)
        session.flush()
        return m

    m1 = _match(teams["A"], teams["B"], 1)
    m2 = _match(teams["C"], teams["D"], 2)
    m3 = _match(teams["A"], ghost, 3)  # partial rating -> never routes
    slate = ProgolSlateModel(label="ci", draw_code=_DRAW, week_type="weekend",
                             composition_hash="h", slate_version=1)
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m1, m2, m3), start=1):
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
        PredictionModel(match_id=m3.id, generated_at=_BASE, home_probability=0.4,
                        draw_probability=0.3, away_probability=0.3,
                        recommended_outcome="1", confidence_band="low"),
    ])
    session.commit()
    return slate


def _enable(monkeypatch, **over):
    s = settings_module.settings
    monkeypatch.setattr(s, "team_rating_canary_enabled", over.get("enabled", True))
    monkeypatch.setattr(s, "team_rating_canary_draw_codes", over.get("draws", [_DRAW]))
    monkeypatch.setattr(s, "team_rating_canary_positions", over.get("positions", [1, 2, 3, 5, 8, 11]))
    monkeypatch.setattr(s, "team_rating_canary_calibrator_id",
                        "international_friendlies_temperature_v1")
    monkeypatch.setattr(s, "team_rating_canary_routing_policy", "rating_replaces_fallback")
    monkeypatch.setattr(s, "team_rating_canary_competition_allowlist",
                        ["International Friendlies"])


@pytest.mark.anyio
async def test_predictions_apply_canary_for_scoped_positions(client, monkeypatch) -> None:
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

    _enable(monkeypatch)
    before = counts()
    resp = await client.get(f"/api/predictions/slates/{slate_id}")
    assert resp.status_code == 200
    body = resp.json()
    by_pos = {m["position"]: m for m in body}

    # Positions 1 and 2 route -> canary active; position 3 (partial rating) does not.
    assert by_pos[1]["canary"]["active"] is True
    assert by_pos[1]["canary"]["engine"] == "team_rating_canary_temperature_v1"
    assert by_pos[2]["canary"]["active"] is True
    assert by_pos[3]["canary"]["active"] is False

    # Effective probabilities differ only for active positions and stay valid.
    assert by_pos[1]["effective_probabilities"] != by_pos[1]["display_probabilities"]
    assert abs(sum(by_pos[1]["effective_probabilities"].values()) - 1.0) < 1e-3
    assert by_pos[3]["effective_probabilities"] == by_pos[3]["display_probabilities"]
    # Top pick does not flip (monotonic temperature scaling).
    assert by_pos[1]["canary"]["top_pick_changed"] is False
    # Persisted/legacy fields are untouched (display == decision as built).
    assert by_pos[1]["display_probabilities"] == by_pos[1]["decision_probabilities"]

    for _ in range(4):
        again = await client.get(f"/api/predictions/slates/{slate_id}")
        assert again.status_code == 200
    assert counts() == before


@pytest.mark.anyio
async def test_predictions_canary_off_is_identity(client, monkeypatch) -> None:
    from app.db import session as db_mod

    engine = db_mod.engine
    with Session(engine) as session:
        slate = _seed(session)
        slate_id = slate.id

    monkeypatch.setattr(settings_module.settings, "team_rating_canary_enabled", False)
    resp = await client.get(f"/api/predictions/slates/{slate_id}")
    assert resp.status_code == 200
    for m in resp.json():
        assert m["canary"]["active"] is False
        assert m["effective_probabilities"] == m["display_probabilities"]
