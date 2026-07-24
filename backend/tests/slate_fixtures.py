"""Shared slate fixture for the read-only diagnostic/operational test suites.

Seeds one small International-Friendlies slate (3 positions, one of them with a
placeholder team) plus its predictions, so Money Mode, the readiness audit, the
ticket options, the results provider and the tracking tests all exercise the
same well-known shape.

Not a test module — it holds no test, only the ``db`` fixture and the seeder,
imported explicitly by the modules that need them.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.tables import (
    CompetitionModel,
    MatchModel,
    PredictionModel,
    ProgolSlateMatchModel,
    ProgolSlateModel,
    TeamModel,
)

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
DRAW = "PG-DRYRUN"


def seed_slate(session, draw=DRAW) -> ProgolSlateModel:
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
    m3 = _match(teams["Spain"], ghost, 3)  # placeholder team -> never playable
    slate = ProgolSlateModel(label="dr", draw_code=draw, week_type="weekend",
                             composition_hash="h", slate_version=1)
    session.add(slate)
    session.flush()
    for pos, m in enumerate((m1, m2, m3), start=1):
        session.add(ProgolSlateMatchModel(slate_id=slate.id, match_id=m.id, position=pos))

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


@pytest.fixture
def db(tmp_path):
    from app.db import session as db_mod
    from app.db.migrations import run_migrations
    from app.db.session import configure_session

    configure_session(f"sqlite:///{tmp_path / 'dryrun.db'}")
    run_migrations(db_mod.engine)
    return db_mod.SessionLocal()
