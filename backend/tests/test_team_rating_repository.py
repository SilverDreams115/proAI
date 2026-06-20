"""R2: TeamRatingRepository on an ephemeral SQLite DB.

Covers run creation, bulk snapshot insert, the
``unique(run_id, team_id, namespace)`` constraint, latest-active lookup,
supersede-previous-active, and latest-active team snapshot reads.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.tables import TeamModel
from app.models.team_rating import create_team_rating_tables
from app.models.team_rating import team_rating_tables_exist
from app.repositories.team_rating_repository import TeamRatingRepository


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'rating.db'}")
    run_migrations(db_session.engine)
    create_team_rating_tables(db_session.engine)  # idempotent
    return db_session.SessionLocal()


def _seed_team(session, name: str) -> str:
    team = TeamModel(name=name, country=None)
    session.add(team)
    session.flush()
    return team.id


def _snap(team_id: str, namespace: str = "club", rating: float = 1500.0, matches: int = 5):
    return {
        "team_id": team_id,
        "namespace": namespace,
        "rating": rating,
        "rating_delta": 1.0,
        "matches_count": matches,
        "wins": matches,
        "draws": 0,
        "losses": 0,
        "goals_for": matches,
        "goals_against": 0,
        "confidence_bucket": "medium",
        "last_result_at": None,
        "competitions_seen_json": json.dumps(["Liga"]),
    }


def _new_run(repo: TeamRatingRepository, *, checksum: str, status: str = "computed"):
    return repo.create_run(
        algorithm_version="elo_v1",
        config_json="{}",
        source_result_count=10,
        rated_match_count=8,
        excluded_match_count=2,
        input_checksum=checksum,
        output_checksum="out-" + checksum,
        status=status,
    )


def test_tables_exist_after_setup(tmp_path):
    session = _make_session(tmp_path)
    from app.db import session as db_session

    assert team_rating_tables_exist(db_session.engine)
    session.close()


def test_create_run_and_bulk_insert_snapshots(tmp_path):
    session = _make_session(tmp_path)
    repo = TeamRatingRepository(session)
    t1 = _seed_team(session, "Alpha")
    t2 = _seed_team(session, "Beta")
    run = _new_run(repo, checksum="abc")

    count = repo.bulk_insert_snapshots(run.id, [_snap(t1), _snap(t2, rating=1480.0)])
    session.commit()

    assert count == 2
    rows = repo.get_snapshots_for_run(run.id)
    assert {r.team_id for r in rows} == {t1, t2}
    session.close()


def test_unique_run_team_namespace(tmp_path):
    session = _make_session(tmp_path)
    repo = TeamRatingRepository(session)
    t1 = _seed_team(session, "Alpha")
    run = _new_run(repo, checksum="dup")

    repo.bulk_insert_snapshots(run.id, [_snap(t1, namespace="club")])
    session.commit()

    # Same (run, team, namespace) → must violate the unique constraint.
    with pytest.raises(IntegrityError):
        repo.bulk_insert_snapshots(run.id, [_snap(t1, namespace="club")])
        session.flush()
    session.rollback()

    # But the SAME team in a DIFFERENT namespace is allowed (separate pools).
    repo.bulk_insert_snapshots(run.id, [_snap(t1, namespace="national")])
    session.commit()
    assert len(repo.get_snapshots_for_run(run.id)) == 2
    session.close()


def test_latest_active_and_supersede(tmp_path):
    session = _make_session(tmp_path)
    repo = TeamRatingRepository(session)

    run_a = _new_run(repo, checksum="a")
    repo.mark_run_active(run_a.id)
    session.commit()
    assert repo.get_latest_active_run("elo_v1").id == run_a.id

    # New run supersedes the previous active, then becomes active itself.
    superseded = repo.supersede_previous_active("elo_v1")
    assert superseded == 1
    run_b = _new_run(repo, checksum="b")
    repo.mark_run_active(run_b.id)
    session.commit()

    latest = repo.get_latest_active_run("elo_v1")
    assert latest.id == run_b.id
    assert repo.get_run(run_a.id).status == "superseded"
    session.close()


def test_get_team_snapshot_latest_active(tmp_path):
    session = _make_session(tmp_path)
    repo = TeamRatingRepository(session)
    t1 = _seed_team(session, "Alpha")

    run = _new_run(repo, checksum="x")
    repo.bulk_insert_snapshots(run.id, [_snap(t1, namespace="club", rating=1600.0)])
    repo.mark_run_active(run.id)
    session.commit()

    snap = repo.get_team_snapshot(t1, "club", algorithm_version="elo_v1")
    assert snap is not None
    assert snap.rating == pytest.approx(1600.0)
    # Unknown namespace for this team → None.
    assert repo.get_team_snapshot(t1, "national", algorithm_version="elo_v1") is None
    session.close()


def test_active_run_with_checksum_guard(tmp_path):
    session = _make_session(tmp_path)
    repo = TeamRatingRepository(session)
    run = _new_run(repo, checksum="same")
    repo.mark_run_active(run.id)
    session.commit()

    assert repo.active_run_with_checksum("elo_v1", "same") is not None
    assert repo.active_run_with_checksum("elo_v1", "different") is None
    session.close()
