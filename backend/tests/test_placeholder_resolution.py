"""Regression coverage for the Tampico/Tampico-Madero placeholder bug.

When a slate promotion creates a fallback team row (because the PDF
fixture couldn't be resolved against an existing team), that row used
to win subsequent `find_team_by_alias` lookups by name match — even
after the real team landed via ingestion. The `is_placeholder` flag
plus an ORDER BY in the repository fixes that: real rows always come
first; placeholders only surface when no real row exists.
"""

from __future__ import annotations


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "placeholder.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def test_find_team_by_alias_prefers_real_row_over_placeholder(tmp_path) -> None:
    from app.models.tables import TeamModel, TeamAliasModel
    from app.repositories.entity_repository import EntityRepository

    session = _make_session(tmp_path)
    try:
        placeholder = TeamModel(name="Tampico", country=None, is_placeholder=True)
        real = TeamModel(name="Tampico Madero", country=None, is_placeholder=False)
        session.add_all([placeholder, real])
        session.flush()
        session.add(TeamAliasModel(team=placeholder, alias="Tampico", normalized_alias="tampico"))
        session.add(
            TeamAliasModel(team=real, alias="Tampico Madero", normalized_alias="tampico-madero")
        )
        session.flush()

        repo = EntityRepository(session)
        # Slug "tampico-madero" maps via NormalizationService to the
        # real row's alias — but the placeholder also matches by raw
        # name. The repository must prefer the real row.
        result = repo.find_team_by_alias("Tampico Madero", "tampico-madero")
        assert result is not None
        assert result.id == real.id
        assert result.is_placeholder is False
    finally:
        session.close()


def test_find_team_by_alias_prefers_canonical_alias_over_short_exact_row(tmp_path) -> None:
    from app.models.tables import TeamModel, TeamAliasModel
    from app.repositories.entity_repository import EntityRepository

    session = _make_session(tmp_path)
    try:
        short = TeamModel(name="Vancouver", country=None, is_placeholder=False)
        canonical = TeamModel(name="Vancouver Whitecaps", country=None, is_placeholder=False)
        session.add_all([short, canonical])
        session.flush()
        session.add(TeamAliasModel(team=short, alias="Vancouver", normalized_alias="vancouver"))
        session.add(
            TeamAliasModel(
                team=canonical,
                alias="Vancouver Whitecaps",
                normalized_alias="vancouver-whitecaps",
            )
        )
        session.flush()

        repo = EntityRepository(session)
        result = repo.find_team_by_alias("Vancouver", "vancouver-whitecaps")

        assert result is not None
        assert result.id == canonical.id
    finally:
        session.close()


def test_resolve_team_upgrades_placeholder_when_real_ingest_arrives(tmp_path) -> None:
    from app.models.tables import TeamModel, TeamAliasModel
    from app.repositories.entity_repository import EntityRepository
    from app.services.entity_resolution_service import EntityResolutionService

    session = _make_session(tmp_path)
    try:
        # Slate promotion ran first and created a placeholder row for
        # an unresolvable PDF fixture.
        placeholder = TeamModel(name="Brommapojkarna", country=None, is_placeholder=True)
        session.add(placeholder)
        session.flush()
        session.add(
            TeamAliasModel(
                team=placeholder,
                alias="Brommapojkarna",
                normalized_alias="brommapojkarna",
            )
        )
        session.flush()

        # Now an Allsvenskan ingest lands the same team — but as a real
        # row. The resolver should upgrade the existing row instead of
        # creating a duplicate.
        resolver = EntityResolutionService(EntityRepository(session))
        team = resolver.resolve_team("Brommapojkarna", country="Sweden")
        session.flush()

        assert team.id == placeholder.id
        assert team.is_placeholder is False, "Real ingest must clear the placeholder flag"
        rows = session.query(TeamModel).filter(TeamModel.name == "Brommapojkarna").all()
        assert len(rows) == 1, "No duplicate row should be created"
    finally:
        session.close()


def test_resolve_team_marks_single_letter_names_as_placeholder(tmp_path) -> None:
    from app.models.tables import TeamModel
    from app.repositories.entity_repository import EntityRepository
    from app.services.entity_resolution_service import EntityResolutionService

    session = _make_session(tmp_path)
    try:
        resolver = EntityResolutionService(EntityRepository(session))
        team = resolver.resolve_team("G", country=None)
        session.flush()

        assert team.is_placeholder is True
        rows = session.query(TeamModel).filter(TeamModel.name == "G").all()
        assert len(rows) == 1
        assert rows[0].is_placeholder is True
    finally:
        session.close()


def test_promote_creates_placeholder_competition_for_unresolved_fixture(tmp_path) -> None:
    from app.models.tables import CompetitionModel
    from app.repositories.entity_repository import EntityRepository
    from app.services.entity_resolution_service import EntityResolutionService

    session = _make_session(tmp_path)
    try:
        resolver = EntityResolutionService(EntityRepository(session))
        comp = resolver.resolve_competition(
            "Progol Concurso 2335",
            country=None,
            season=None,
            is_placeholder=True,
        )
        session.flush()
        assert comp.is_placeholder is True

        # Later, the same comp name is resolved by a real ingest.
        comp_real = resolver.resolve_competition(
            "Progol Concurso 2335",
            country=None,
            season="2025-2026",
            is_placeholder=False,
        )
        session.flush()
        assert comp_real.id == comp.id
        assert comp_real.is_placeholder is False
        rows = session.query(CompetitionModel).filter(
            CompetitionModel.name == "Progol Concurso 2335"
        ).all()
        assert len(rows) == 1
    finally:
        session.close()
