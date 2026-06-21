"""R5.6-A: readiness CLI must print a coherent report and write nothing."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.models.tables import CompetitionModel
from app.models.tables import MatchFeatureSnapshotModel
from app.models.tables import MatchModel
from app.models.tables import PredictionModel
from app.models.tables import ProgolSlateMatchModel
from app.models.tables import ProgolSlateModel
from app.models.tables import TeamModel
from app.models.tables import TicketRecommendationSnapshotModel
from app.repositories.team_rating_repository import TeamRatingRepository
from scripts import audit_team_rating_activation_readiness as cli

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_session(tmp_path, name="rcli.db"):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / name}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _open(tmp_path, name="rcli.db"):
    from app.db import session as db_session
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / name}")
    return db_session.SessionLocal()


def _snap(team_id, matches, bucket, rating):
    return {
        "team_id": team_id, "namespace": "national", "rating": rating,
        "rating_delta": 0.0, "matches_count": matches, "wins": matches,
        "draws": 0, "losses": 0, "goals_for": matches, "goals_against": 0,
        "confidence_bucket": bucket, "last_result_at": None,
        "competitions_seen_json": json.dumps(["national"]),
    }


def _seed(session):
    friendly = CompetitionModel(name="International Friendlies", country="World")
    teams = {n: TeamModel(name=n, country=None) for n in "ABCD"}
    session.add_all([friendly, *teams.values()])
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
    slate = ProgolSlateModel(
        label="cli", draw_code="PG-RCLI", week_type="weekend",
        composition_hash="h", slate_version=1,
    )
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m1, m2), start=1):
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
    ])
    session.commit()
    return slate


def _counts(session):
    return {
        "predictions": session.query(PredictionModel).count(),
        "feature_snapshots": session.query(MatchFeatureSnapshotModel).count(),
        "ticket_snapshots": session.query(TicketRecommendationSnapshotModel).count(),
    }


def test_cli_human_output(tmp_path, capsys):
    session = _make_session(tmp_path)
    _seed(session)
    before = _counts(session)
    session.close()

    rc = cli.main(["--draw-code", "PG-RCLI"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "mode: activation_readiness" in out
    assert "ready_for_canary: False" in out
    assert "approval_status=approved_inactive" in out
    assert "canary_allowed_positions:" in out
    assert "rollback" in out
    assert "team_rating_gate_enabled=false" in out

    after = _counts(_open(tmp_path))
    assert before == after


def test_cli_json_output_and_no_writes(tmp_path, capsys):
    session = _make_session(tmp_path)
    slate = _seed(session)
    slate_id = slate.id
    before = _counts(session)
    session.close()

    rc = cli.main(["--slate-id", slate_id, "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert rc == 0
    assert payload["mode"] == "activation_readiness"
    assert payload["ready_for_canary"] is False
    assert payload["calibrator"]["approval_status"] == "approved_inactive"
    assert payload["dry_run_summary"]["total_matches"] == 2
    assert len(payload["canary_plan"]["rollback"]) >= 3

    after = _counts(_open(tmp_path))
    assert before == after
