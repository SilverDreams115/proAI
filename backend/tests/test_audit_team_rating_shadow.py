"""R5.1: read-only shadow auditor for the team-rating gate."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.tables import CompetitionModel
from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.models.tables import TicketRecommendationSnapshotModel
from app.repositories.team_rating_repository import TeamRatingRepository
from scripts import audit_team_rating_shadow as shadow_audit


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'shadow.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _snap(team_id: str, ns: str, matches: int, bucket: str, rating: float = 1500.0):
    return {
        "team_id": team_id,
        "namespace": ns,
        "rating": rating,
        "rating_delta": 0.0,
        "matches_count": matches,
        "wins": matches,
        "draws": 0,
        "losses": 0,
        "goals_for": matches,
        "goals_against": 0,
        "confidence_bucket": bucket,
        "last_result_at": None,
        "competitions_seen_json": json.dumps([ns]),
    }


def _seed(session):
    friendly = CompetitionModel(name="International Friendlies", country="World")
    libert = CompetitionModel(name="Copa Libertadores", country="SA")
    brasil = CompetitionModel(name="Brasileirao", country="BR")
    teams = {
        name: TeamModel(name=name, country=None)
        for name in ("A", "B", "C", "D", "E", "F", "G", "H")
    }
    ghost = TeamModel(name="Ghost", country=None, is_placeholder=True)
    session.add_all([friendly, libert, brasil, ghost, *teams.values()])
    session.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _match(comp, home, away, day):
        match = MatchModel(
            competition_id=comp.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=base.replace(day=day),
        )
        session.add(match)
        session.flush()
        return match

    m_ok = _match(friendly, teams["A"], teams["B"], 1)
    m_partial = _match(friendly, teams["C"], ghost, 2)
    m_sanity = _match(friendly, teams["D"], teams["E"], 3)
    m_libert = _match(libert, teams["F"], teams["G"], 4)
    m_brasil = _match(brasil, teams["G"], teams["H"], 5)
    slate = ProgolSlateModel(
        label="shadow",
        draw_code="PG-SHADOW",
        week_type="weekend",
        composition_hash="hash",
        slate_version=1,
    )
    session.add(slate)
    session.flush()
    for pos, match in enumerate((m_ok, m_partial, m_sanity, m_libert, m_brasil), start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=match.id, position=pos))

    repo = TeamRatingRepository(session)
    run = repo.create_run(
        algorithm_version="elo_v1",
        config_json="{}",
        source_result_count=1,
        rated_match_count=1,
        excluded_match_count=0,
        input_checksum="in",
        output_checksum="out",
        status="computed",
    )
    repo.bulk_insert_snapshots(
        run.id,
        [
            _snap(teams["A"].id, "national", 8, "medium", 1550.0),
            _snap(teams["B"].id, "national", 10, "strong", 1495.0),
            _snap(teams["C"].id, "national", 6, "medium", 1510.0),
            _snap(teams["D"].id, "national", 7, "medium", 1520.0),
            _snap(teams["E"].id, "national", 8, "strong", 1480.0),
            _snap(teams["F"].id, "club", 12, "strong", 1600.0),
            _snap(teams["G"].id, "club", 9, "medium", 1500.0),
            _snap(teams["H"].id, "club", 9, "medium", 1515.0),
        ],
    )
    repo.mark_run_active(run.id)
    session.add(
        PredictionModel(
            match_id=m_sanity.id,
            generated_at=base,
            home_probability=0.4,
            draw_probability=0.3,
            away_probability=0.3,
            recommended_outcome="1",
            confidence_band="medium",
            sanity_audit_json=json.dumps(
                {"sanity_flags": ["FALLBACK_USED"], "fallback_used": True}
            ),
        )
    )
    session.commit()
    return slate


def test_shadow_audit_current_off_and_assumed_enabled_breakdown(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)
    links = sorted(slate.matches, key=lambda link: link.position)

    current = shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=False,
        assume_calibrator_available=False,
    )
    assert current["summary"]["eligible_current"] == 0
    assert current["summary"]["eligible_if_enabled"] == 0
    assert current["summary"]["would_use_rating_model_current"] == 0
    assert current["summary"]["blocked_by_flag"] == 5

    assumed = shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=True,
        routing_policy="strict",
    )
    summary = assumed["summary"]
    assert summary["total_matches"] == 5
    assert summary["eligible_current"] == 0
    assert summary["eligible_if_enabled"] == 2
    assert summary["would_use_rating_model_if_enabled"] == 1
    assert summary["blocked_by_rating"] == 1
    assert summary["blocked_by_competition"] == 2
    assert summary["blocked_by_sanity"] == 1
    assert summary["blocked_by_soft_sanity"] == 1
    assert summary["positions_would_route"] == [1]
    assert summary["positions_eligible_if_enabled"] == [1, 3]
    assert summary["positions_blocked"] == [2, 3, 4, 5]
    session.close()


def test_shadow_audit_rating_replaces_fallback_policy(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)
    links = sorted(slate.matches, key=lambda link: link.position)

    report = shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=True,
        routing_policy="rating-replaces-fallback",
    )
    summary = report["summary"]
    assert summary["eligible_if_enabled"] == 2
    assert summary["would_use_rating_model_if_enabled"] == 2
    assert summary["blocked_by_soft_sanity"] == 0
    assert summary["warnings"] == 1
    assert summary["positions_would_route"] == [1, 3]
    assert summary["positions_blocked"] == [2, 4, 5]
    session.close()


def test_shadow_audit_accepts_calibrator_candidate(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)
    links = sorted(slate.matches, key=lambda link: link.position)

    report = shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=False,
        routing_policy="rating-replaces-fallback",
        calibrator_candidate_id="international_friendlies_temperature_v1",
        assume_calibrator_candidate_available=True,
    )
    gate_config = report["gate_config"]
    assert gate_config["calibrator_candidate_id"] == "international_friendlies_temperature_v1"
    assert gate_config["calibrator_compatible"] is False  # mixed competition slate
    assert "mixed_competitions" in gate_config["calibrator_compatibility_blockers"]
    assert gate_config["calibrator_candidate_available"] is False
    assert report["calibrator_candidate"]["productive_available"] is False
    session.close()


def test_shadow_audit_calibrator_candidate_compatible_for_friendly_scope(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    links = shadow_audit._links_for_scope(
        session, draw_code=None, competition="International Friendlies"
    )
    report = shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=False,
        routing_policy="rating-replaces-fallback",
        calibrator_candidate_id="international_friendlies_temperature_v1",
        assume_calibrator_candidate_available=True,
    )
    gate_config = report["gate_config"]
    assert gate_config["calibrator_compatible"] is True
    assert gate_config["calibrator_compatibility_blockers"] == []
    assert gate_config["calibrator_candidate_available"] is True
    assert report["summary"]["eligible_if_enabled"] == 2
    assert report["summary"]["would_use_rating_model_if_enabled"] == 2
    # The sanity-flagged friendly has a current prediction, so the auditor can
    # show a transient calibrated vector without persisting it.
    row = next(r for r in report["rows"] if r["position"] == 3)
    assert row["calibrated_probability_vector"] is not None
    assert sum(row["calibrated_probability_vector"].values()) == pytest.approx(1.0)
    session.close()


def test_competition_scope_and_calibrator_blocker(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    links = shadow_audit._links_for_scope(
        session, draw_code=None, competition="International Friendlies"
    )
    report = shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=False,
        routing_policy="rating_replaces_fallback",
    )
    assert report["summary"]["total_matches"] == 3
    assert report["summary"]["blocked_by_calibrator"] == 2
    assert report["summary"]["eligible_if_enabled"] == 0
    session.close()


def test_script_no_writes_db(tmp_path):
    session = _make_session(tmp_path)
    slate = _seed(session)
    links = list(slate.matches)
    before = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }
    shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=True,
        routing_policy="review_allowed_shadow",
        calibrator_candidate_id="international_friendlies_temperature_v1",
        assume_calibrator_candidate_available=True,
    )
    after = {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }
    assert before == after
    assert not session.new and not session.dirty
    session.rollback()
    session.close()


def test_live_auditor_enforces_postgres_read_only_transaction():
    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Session:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def get_bind(self):
            return _Bind()

        def execute(self, statement):
            self.statements.append(str(statement))

    session = _Session()

    shadow_audit._enforce_read_only_transaction(session)  # type: ignore[arg-type]

    assert session.statements == ["SET TRANSACTION READ ONLY"]


def test_no_active_service_integration_and_defaults_off():
    root = Path(__file__).resolve().parents[1]
    prediction = (root / "app/services/prediction_service.py").read_text()
    ticket = (root / "app/services/ticket_recommendation_service.py").read_text()
    feature = (root / "app/services/feature_service.py").read_text()

    assert "team_rating_shadow_service" not in prediction
    assert "team_rating_gate_service" not in prediction
    assert "team_rating_routing_policy" not in prediction
    assert "team_rating_calibrator" not in prediction
    assert "team_rating_shadow_service" not in ticket
    assert "team_rating_gate_service" not in ticket
    assert "team_rating_routing_policy" not in ticket
    assert "team_rating_calibrator" not in ticket
    assert "team_rating_shadow_service" not in feature
    assert "team_rating_gate_service" not in feature
    assert "team_rating_routing_policy" not in feature
    assert "team_rating_calibrator" not in feature

    from app.core.settings import load_settings
    import os

    for key in (
        "PROAI_TEAM_RATING_FEATURE_ENABLED",
        "PROAI_TEAM_RATING_GATE_ENABLED",
    ):
        os.environ.pop(key, None)
    load_settings.cache_clear()
    settings = load_settings()
    assert settings.team_rating_feature_enabled is False
    assert settings.team_rating_gate_enabled is False


def test_shadow_audit_candidate_applies_per_position_on_mixed_slate(tmp_path):
    """Candidate availability is per position: friendlies rows route even when
    the slate as a whole is mixed; non-friendly rows stay blocked by the
    competition gate. The gate_config keeps the slate-scope verdict."""
    session = _make_session(tmp_path)
    slate = _seed(session)
    links = sorted(slate.matches, key=lambda link: link.position)

    report = shadow_audit.audit_shadow(
        session,
        links,
        assume_gate_enabled=True,
        assume_calibrator_available=False,
        routing_policy="rating-replaces-fallback",
        calibrator_candidate_id="international_friendlies_temperature_v1",
        assume_calibrator_candidate_available=True,
    )

    gate_config = report["gate_config"]
    assert gate_config["calibrator_compatible"] is False  # slate scope: mixed
    assert "mixed_competitions" in gate_config["calibrator_compatibility_blockers"]

    summary = report["summary"]
    assert summary["positions_would_route"] == [1, 3]
    rows = {r["position"]: r for r in report["rows"]}
    assert rows[1]["would_use_rating_model"] is True
    assert "calibrator_unavailable" not in rows[1]["blockers"]
    assert rows[4]["would_use_rating_model"] is False
    assert "competition_not_allowed" in rows[4]["blockers"]
    session.close()
