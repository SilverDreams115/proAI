"""R2: compute_team_ratings CLI — mapping, dry-run safety, apply guard.

Covers: canonical results → TeamRatingInputMatch mapping (placeholder and
conflict exclusion), use of the REAL R1 calculator, dry-run writes nothing
and needs no team_rating_* tables, and --apply refusing without the token.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect

from app.models.tables import CompetitionModel
from app.models.tables import MatchModel
from app.models.tables import MatchResultModel
from app.models.tables import SourceModel
from app.models.tables import TeamModel
from scripts import compute_team_ratings as cli


def _make_session(tmp_path):
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'cli.db'}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed(session):
    comp = CompetitionModel(name="Liga MX", country="Mexico", season="2026")
    friendly = CompetitionModel(name="International Friendlies", country="World")
    teams = {
        name: TeamModel(name=name, country=None)
        for name in ("Alpha", "Beta", "Gamma", "Delta")
    }
    placeholder = TeamModel(name="GhostFC", country=None, is_placeholder=True)
    src_a = SourceModel(
        name="src-a", base_url="http://a", kind="thesportsdb_season",
        parser_profile="p", is_active=True, result_source_priority=10,
    )
    src_b = SourceModel(
        name="src-b", base_url="http://b", kind="thesportsdb_season",
        parser_profile="p", is_active=True, result_source_priority=50,
    )
    session.add_all([comp, friendly, placeholder, src_a, src_b, *teams.values()])
    session.flush()

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _match(home, away, competition, day):
        m = MatchModel(
            competition_id=competition.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_at=base.replace(day=day),
        )
        session.add(m)
        session.flush()
        return m

    def _result(match, source, hg, ag, day):
        code = "L" if hg > ag else ("E" if hg == ag else "V")
        session.add(MatchResultModel(
            match_id=match.id, source_id=source.id,
            played_at=base.replace(day=day), home_goals=hg, away_goals=ag,
            result_code=code,
        ))

    # Clean rated matches (both sources agree → canonical).
    m1 = _match(teams["Alpha"], teams["Beta"], comp, 1)
    _result(m1, src_a, 2, 0, 1)
    _result(m1, src_b, 2, 0, 1)
    m2 = _match(teams["Beta"], teams["Gamma"], comp, 2)
    _result(m2, src_a, 1, 1, 2)
    m3 = _match(teams["Gamma"], teams["Delta"], friendly, 3)
    _result(m3, src_a, 0, 3, 3)

    # Conflict: sources disagree → excluded by the calculator.
    m4 = _match(teams["Alpha"], teams["Gamma"], comp, 4)
    _result(m4, src_a, 1, 0, 4)
    _result(m4, src_b, 0, 1, 4)

    # Placeholder team → pre-filtered out (never rated).
    m5 = _match(teams["Delta"], placeholder, comp, 5)
    _result(m5, src_a, 4, 0, 5)

    session.commit()


def test_build_input_matches_excludes_placeholder_and_maps_namespace(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)

    matches, teams_by_id, prefilter, considered = cli.build_input_matches(session)

    # 5 matches have results; the placeholder match is pre-filtered.
    assert considered == 5
    assert prefilter.get("placeholder_team") == 1
    assert len(matches) == 4  # m1..m4 (m4 conflict still mapped, flagged)

    # Friendly match maps to the national namespace.
    friendly_match = next(m for m in matches if m.competition == "International Friendlies")
    assert friendly_match.namespace == "national"
    league_match = next(m for m in matches if m.competition == "Liga MX")
    assert league_match.namespace == "club"
    # The conflict is mapped with is_conflict=True (calculator excludes it).
    assert any(m.is_conflict for m in matches)
    assert all(not m.is_sign_only for m in matches)
    session.close()


def test_dry_run_uses_real_calculator_and_writes_nothing(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)

    before = session.query(MatchResultModel).count()
    report, config_json, snapshot_rows = cli.build_report(session, draw_code=None)

    # Conflict excluded → rated < considered; reasons include conflict.
    assert report["run_summary"]["source_result_count"] == 5
    assert report["run_summary"]["excluded_reasons"].get("conflict") == 1
    assert report["run_summary"]["excluded_reasons"].get("placeholder_team") == 1
    assert report["checksums"]["input_checksum"]
    assert snapshot_rows  # produced rows for the rated teams
    # Nothing was added to the session by a read-only compute.
    assert not session.new
    assert not session.dirty
    assert session.query(MatchResultModel).count() == before
    session.rollback()
    session.close()


def test_dry_run_does_not_require_team_rating_tables(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    from app.db import session as db_session

    # Drop the tables if the bootstrap created them: dry-run must still work.
    from app.models.team_rating import TeamRatingRunModel, TeamRatingSnapshotModel

    insp = inspect(db_session.engine)
    if insp.has_table("team_rating_snapshots"):
        TeamRatingSnapshotModel.__table__.drop(db_session.engine)
    if insp.has_table("team_rating_runs"):
        TeamRatingRunModel.__table__.drop(db_session.engine)

    report, _cfg, _rows = cli.build_report(session, draw_code=None)
    assert report["run_summary"]["rated_match_count"] >= 1
    session.rollback()
    session.close()


def test_apply_requires_exact_token(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)

    import pytest

    with pytest.raises(SystemExit):
        cli.run_apply(session, confirm="")
    with pytest.raises(SystemExit):
        cli.run_apply(session, confirm="wrong-token")
    session.close()


def test_apply_aborts_when_tables_missing_even_with_token(tmp_path):
    session = _make_session(tmp_path)
    _seed(session)
    from app.db import session as db_session
    from app.models.team_rating import TeamRatingRunModel, TeamRatingSnapshotModel

    insp = inspect(db_session.engine)
    if insp.has_table("team_rating_snapshots"):
        TeamRatingSnapshotModel.__table__.drop(db_session.engine)
    if insp.has_table("team_rating_runs"):
        TeamRatingRunModel.__table__.drop(db_session.engine)

    import pytest

    with pytest.raises(SystemExit):
        cli.run_apply(session, confirm=cli.CONFIRM_TOKEN)
    session.close()
