"""R5.7 — ticket canary dry-run service: in-memory current-vs-canary, no writes."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core import settings as settings_module
from app.models.tables import (
    CompetitionModel,
    MatchFeatureSnapshotModel,
    MatchModel,
    PredictionModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    TeamModel,
    TicketRecommendationSnapshotModel,
)
from app.repositories.team_rating_repository import TeamRatingRepository

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
DRAW = "PG-DRYRUN"


def _snap(team_id, matches, bucket, rating):
    return {
        "team_id": team_id, "namespace": "national", "rating": rating,
        "rating_delta": 0.0, "matches_count": matches, "wins": matches,
        "draws": 0, "losses": 0, "goals_for": matches, "goals_against": 0,
        "confidence_bucket": bucket, "last_result_at": None,
        "competitions_seen_json": json.dumps(["national"]),
    }


def seed_canary_slate(session, draw=DRAW) -> ProgolSlateModel:
    friendly = CompetitionModel(name="International Friendlies", country="World")
    names = ["Norway", "France", "Czech Republic", "Mexico", "Spain", "Italy"]
    teams = {n: TeamModel(name=n, country=None) for n in names}
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    session.add_all([friendly, ghost, *teams.values()])
    session.flush()

    def _match(home, away, day):
        m = MatchModel(competition_id=friendly.id, home_team_id=home.id,
                       away_team_id=away.id, kickoff_at=_BASE.replace(day=day))
        session.add(m)
        session.flush()
        return m

    m1 = _match(teams["Norway"], teams["France"], 1)
    m2 = _match(teams["Czech Republic"], teams["Mexico"], 2)
    m3 = _match(teams["Spain"], ghost, 3)  # partial rating -> never routes
    slate = ProgolSlateModel(label="dr", draw_code=draw, week_type="weekend",
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
        _snap(teams["Norway"].id, 8, "medium", 1550.0),
        _snap(teams["France"].id, 10, "strong", 1495.0),
        _snap(teams["Czech Republic"].id, 8, "medium", 1520.0),
        _snap(teams["Mexico"].id, 9, "strong", 1490.0),
        _snap(teams["Spain"].id, 8, "medium", 1500.0),
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


def enable_canary(monkeypatch, draws=(DRAW,), positions=(1, 2, 3, 5, 8, 11)):
    s = settings_module.settings
    monkeypatch.setattr(s, "team_rating_canary_enabled", True)
    monkeypatch.setattr(s, "team_rating_canary_scope", "draw_code_allowlist")
    monkeypatch.setattr(s, "team_rating_canary_draw_codes", list(draws))
    monkeypatch.setattr(s, "team_rating_canary_positions", list(positions))
    monkeypatch.setattr(s, "team_rating_canary_calibrator_id",
                        "international_friendlies_temperature_v1")
    monkeypatch.setattr(s, "team_rating_canary_routing_policy", "rating_replaces_fallback")
    monkeypatch.setattr(s, "team_rating_canary_competition_allowlist",
                        ["International Friendlies"])


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'dryrun.db'}")
    run_migrations(db_mod.engine)
    return db_mod.SessionLocal()


def _counts(session_factory):
    with session_factory() as s:
        return (
            int(s.scalar(select(func.count()).select_from(PredictionModel)) or 0),
            int(s.scalar(select(func.count()).select_from(MatchFeatureSnapshotModel)) or 0),
            int(s.scalar(select(func.count()).select_from(TicketRecommendationSnapshotModel)) or 0),
        )


def test_dry_run_structure_and_no_writes(db, monkeypatch):
    from app.db import session as db_mod
    from app.services.ticket_canary_dry_run_service import build_ticket_canary_dry_run

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    slate = db.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()

    before = _counts(db_mod.SessionLocal)
    report = build_ticket_canary_dry_run(db, slate)
    after = _counts(db_mod.SessionLocal)

    assert after == before  # no predictions/feature/ticket snapshots written
    assert report["mode"] == "ticket_canary_dry_run"
    assert report["production_active"] is False
    assert report["ticket_integration_active"] is False
    assert report["write_safety"] == {"writes_performed": False, "snapshot_created": False}
    assert "current_ticket" in report["summary"]
    assert "canary_ticket" in report["summary"]
    assert len(report["matches"]) == 3
    # canary active on the fully-rated positions only (1,2), not 3 (partial).
    assert set(report["summary"]["canary_active_positions"]) == {1, 2}
    pos3 = next(m for m in report["matches"] if m["position"] == 3)
    assert pos3["canary_active"] is False
    # Non-canary position uses identical current/effective vectors.
    assert pos3["display_probabilities"] == pos3["effective_probabilities"]


def test_canary_effective_used_only_where_active(db, monkeypatch):
    from app.services.ticket_canary_dry_run_service import build_ticket_canary_dry_run

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    slate = db.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()
    report = build_ticket_canary_dry_run(db, slate)
    for m in report["matches"]:
        if m["canary_active"]:
            assert m["effective_probabilities"] != m["display_probabilities"]
        else:
            assert m["effective_probabilities"] == m["display_probabilities"]


def test_guardrail_blocks_simple_for_risky_friendly(db, monkeypatch):
    """A low-evidence friendly (presentation_guard.simple_allowed=false) must
    never be a confident simple in either ticket."""
    from app.services.ticket_canary_dry_run_service import build_ticket_canary_dry_run

    enable_canary(monkeypatch)
    seed_canary_slate(db)
    slate = db.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()
    report = build_ticket_canary_dry_run(db, slate)
    for m in report["matches"]:
        if not m["presentation_guard"]["simple_allowed"]:
            assert m["current_pick_type"] != "simple"
            assert m["canary_pick_type"] != "simple"


def test_canary_off_makes_tickets_identical(db, monkeypatch):
    from app.services.ticket_canary_dry_run_service import build_ticket_canary_dry_run

    monkeypatch.setattr(settings_module.settings, "team_rating_canary_enabled", False)
    seed_canary_slate(db)
    slate = db.query(ProgolSlateModel).filter_by(draw_code=DRAW).one()
    report = build_ticket_canary_dry_run(db, slate)
    assert report["summary"]["canary_active_positions"] == []
    assert report["summary"]["ticket_changed"] is False
    assert report["summary"]["changed_positions"] == []
