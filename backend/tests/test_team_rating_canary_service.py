"""R5.6-B: controlled canary service. Changes served effective probabilities
for scoped positions only; never writes the DB or touches the ticket layer."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.core import settings as settings_module
from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.repositories.team_rating_repository import TeamRatingRepository
from app.schemas.prediction import MatchPredictionResponse
from app.services.team_rating_canary_service import apply_canary_to_predictions
from app.services.team_rating_canary_service import build_canary_status

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DRAW = "PG-CANARY"


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'canary.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _snap(team_id, matches, bucket, rating):
    return {
        "team_id": team_id, "namespace": "national", "rating": rating,
        "rating_delta": 0.0, "matches_count": matches, "wins": matches,
        "draws": 0, "losses": 0, "goals_for": matches, "goals_against": 0,
        "confidence_bucket": bucket, "last_result_at": None,
        "competitions_seen_json": json.dumps(["national"]),
    }


def _pred(match_id, probs, *, sanity=None):
    return PredictionModel(
        match_id=match_id, generated_at=_BASE,
        home_probability=probs[0], draw_probability=probs[1], away_probability=probs[2],
        recommended_outcome="1", confidence_band="medium",
        sanity_audit_json=json.dumps(sanity) if sanity is not None else None,
    )


def _seed(session) -> ProgolSlateModel:
    friendly = CompetitionModel(name="International Friendlies", country="World")
    teams = {n: TeamModel(name=n, country=None) for n in "ABCDEFGH"}
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    session.add_all([friendly, ghost, *teams.values()])
    session.flush()

    def _match(home, away, day):
        m = MatchModel(competition_id=friendly.id, home_team_id=home.id,
                       away_team_id=away.id, kickoff_at=_BASE.replace(day=day))
        session.add(m)
        session.flush()
        return m

    rows = [
        _match(teams["A"], teams["B"], 1),  # 1 route
        _match(teams["C"], teams["D"], 2),  # 2 soft -> route
        _match(teams["E"], teams["F"], 3),  # 3 review -> block
        _match(teams["G"], teams["H"], 4),  # 4 hard -> block
        _match(teams["A"], ghost, 5),       # 5 rating -> block
    ]
    slate = ProgolSlateModel(label="cn", draw_code=_DRAW, week_type="weekend",
                             composition_hash="h", slate_version=1)
    session.add(slate)
    session.flush()
    for pos, m in enumerate(rows, start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))

    repo = TeamRatingRepository(session)
    run = repo.create_run(algorithm_version="elo_v1", config_json="{}", source_result_count=1,
                          rated_match_count=1, excluded_match_count=0, input_checksum="in",
                          output_checksum="out", status="computed")
    repo.bulk_insert_snapshots(run.id, [
        _snap(teams["A"].id, 8, "medium", 1560.0), _snap(teams["B"].id, 10, "strong", 1490.0),
        _snap(teams["C"].id, 8, "medium", 1520.0), _snap(teams["D"].id, 9, "strong", 1495.0),
        _snap(teams["E"].id, 8, "medium", 1530.0), _snap(teams["F"].id, 9, "strong", 1480.0),
        _snap(teams["G"].id, 8, "medium", 1515.0), _snap(teams["H"].id, 9, "strong", 1500.0),
    ])
    repo.mark_run_active(run.id)
    session.add_all([
        _pred(rows[0].id, (0.6, 0.25, 0.15)),
        _pred(rows[1].id, (0.45, 0.30, 0.25), sanity={"fallback_used": True}),
        _pred(rows[2].id, (0.5, 0.3, 0.2), sanity={"final_status": "REVISAR"}),
        _pred(rows[3].id, (0.5, 0.3, 0.2), sanity={"final_status": "BLOCKED"}),
        _pred(rows[4].id, (0.4, 0.35, 0.25)),
    ])
    session.commit()
    return slate


def _enable_canary(monkeypatch, *, positions=(1, 2, 3, 5, 8, 11), draw_codes=(_DRAW,)):
    s = settings_module.settings
    monkeypatch.setattr(s, "team_rating_canary_enabled", True)
    monkeypatch.setattr(s, "team_rating_canary_draw_codes", list(draw_codes))
    monkeypatch.setattr(s, "team_rating_canary_positions", list(positions))
    monkeypatch.setattr(s, "team_rating_canary_calibrator_id",
                        "international_friendlies_temperature_v1")
    monkeypatch.setattr(s, "team_rating_canary_routing_policy", "rating_replaces_fallback")
    monkeypatch.setattr(s, "team_rating_canary_competition_allowlist",
                        ["International Friendlies"])


def _responses(slate):
    out = []
    for link in sorted(slate.matches, key=lambda x: x.position):
        out.append(MatchPredictionResponse(
            slate_id=slate.id, position=link.position, match_id=link.match.id,
            competition_name="International Friendlies",
            home_team_name="H", away_team_name="A", generated_at=_BASE,
            home_probability=0.5, draw_probability=0.3, away_probability=0.2,
            recommended_outcome="1", competition_readiness="ready",
            live_pick_allowed=True, policy_reason="", confidence_band="medium",
            rationale=[],
            display_probabilities={"L": 0.5, "E": 0.3, "V": 0.2},
            decision_probabilities={"L": 0.5, "E": 0.3, "V": 0.2},
            probabilities={"L": 0.5, "E": 0.3, "V": 0.2},
        ))
    return out


def test_canary_off_is_identity(tmp_path, monkeypatch):
    session = _make_session(tmp_path)
    slate = _seed(session)
    monkeypatch.setattr(settings_module.settings, "team_rating_canary_enabled", False)
    preds = _responses(slate)

    plan = apply_canary_to_predictions(session, slate, preds)

    assert plan.active_positions == []
    for p in preds:
        assert p.canary.active is False
        assert p.effective_probabilities == p.display_probabilities
    session.close()


def test_canary_on_only_routes_allowed_routing_positions(tmp_path, monkeypatch):
    session = _make_session(tmp_path)
    slate = _seed(session)
    _enable_canary(monkeypatch, positions=(1, 2, 3))  # 3 is review-blocked
    preds = _responses(slate)

    plan = apply_canary_to_predictions(session, slate, preds)

    # Only positions that the gate would route AND are in the allowlist.
    assert plan.active_positions == [1, 2]
    assert 3 not in plan.active_positions  # review-blocked
    assert plan.blocked_positions == [3, 4, 5]
    by_pos = {p.position: p for p in preds}
    assert by_pos[1].canary.active is True
    assert by_pos[1].canary.engine == "team_rating_canary_temperature_v1"
    assert by_pos[3].canary.active is False
    assert by_pos[4].canary.active is False
    assert by_pos[5].canary.active is False
    session.close()


def test_canary_effective_probabilities_valid_and_pick_stable(tmp_path, monkeypatch):
    session = _make_session(tmp_path)
    slate = _seed(session)
    _enable_canary(monkeypatch)
    preds = _responses(slate)

    apply_canary_to_predictions(session, slate, preds)
    p1 = next(p for p in preds if p.position == 1)

    assert abs(sum(p1.effective_probabilities.values()) - 1.0) < 1e-3
    assert p1.effective_probabilities != p1.display_probabilities
    assert sum(p1.canary.probability_delta.values()) == pytest.approx(0.0, abs=1e-6)
    assert p1.canary.max_abs_delta > 0.0
    # Temperature scaling is monotonic -> the top pick does not flip.
    assert p1.canary.top_pick_changed is False
    assert "ticket_not_using_canary" in p1.canary.warnings
    # Non-active positions mirror the original.
    p5 = next(p for p in preds if p.position == 5)
    assert p5.effective_probabilities == p5.display_probabilities
    session.close()


def test_canary_wrong_draw_code_not_in_scope(tmp_path, monkeypatch):
    session = _make_session(tmp_path)
    slate = _seed(session)
    _enable_canary(monkeypatch, draw_codes=("PG-OTHER",))
    preds = _responses(slate)

    plan = apply_canary_to_predictions(session, slate, preds)
    assert plan.in_scope is False
    assert plan.active_positions == []
    assert all(p.canary.active is False for p in preds)
    session.close()


def test_canary_status_payload(tmp_path, monkeypatch):
    session = _make_session(tmp_path)
    slate = _seed(session)
    _enable_canary(monkeypatch, positions=(1, 2, 3))
    status = build_canary_status(session, slate)

    assert status["canary_enabled"] is True
    assert status["scope"] == _DRAW
    assert status["active_positions"] == [1, 2]
    assert status["full_activation"] is False
    assert status["ticket_integration"] is False
    assert status["rollback_available"] is True
    session.close()


def test_canary_service_does_not_write(tmp_path, monkeypatch):
    from app.models.tables import MatchFeatureSnapshotModel
    from app.models.tables import TicketRecommendationSnapshotModel

    session = _make_session(tmp_path)
    slate = _seed(session)
    _enable_canary(monkeypatch)
    before = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }
    apply_canary_to_predictions(session, slate, _responses(slate))
    after = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }
    assert before == after
    assert not session.new and not session.dirty
    session.rollback()
    session.close()


def test_canary_service_has_no_ticket_integration():
    from pathlib import Path

    src = Path("backend/app/services/team_rating_canary_service.py").read_text()
    code = "\n".join(
        line for line in src.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "*"))
    )
    assert "ticket_recommendation_service" not in code
    assert "TicketRecommendationService" not in code
    assert "save_snapshot" not in code
    assert "session.add" not in code
    assert ".commit(" not in code


def test_canary_stays_active_for_friendlies_on_mixed_slate(tmp_path, monkeypatch):
    """A mixed-competition slate (the normal case outside pure-Mundial weeks)
    must not disable the candidate for the positions inside its competition:
    before the per-position compatibility fix, one non-friendly position made
    calibrator_unavailable block ALL positions and the canary went silently
    dead while the status header still said enabled."""
    session = _make_session(tmp_path)
    slate = _seed(session)
    other = CompetitionModel(name="Liga MX", country="MX")
    t1 = TeamModel(name="X1", country=None)
    t2 = TeamModel(name="X2", country=None)
    session.add_all([other, t1, t2])
    session.flush()
    extra = MatchModel(
        competition_id=other.id, home_team_id=t1.id, away_team_id=t2.id,
        kickoff_at=_BASE.replace(day=6),
    )
    session.add(extra)
    session.flush()
    session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=extra.id, position=6))
    session.commit()
    _enable_canary(monkeypatch, positions=(1, 2, 3))

    status = build_canary_status(session, slate)

    assert status["active_positions"] == [1, 2]
    assert 6 in status["blocked_positions"]
    # The slate-scope incompatibility stays visible instead of silently
    # zeroing the canary.
    assert "mixed_competitions" in status["calibrator_compatibility_blockers"]
