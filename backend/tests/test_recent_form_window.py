"""The recent-form window must scale with each competition's actual
match cadence instead of a hand-curated keyword list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _make_session(tmp_path):
    from app.db.session import configure_session
    from app.db import session as db_session
    from app.db.migrations import run_migrations

    db_file = tmp_path / "windows.db"
    configure_session(f"sqlite:///{db_file}")
    run_migrations(db_session.engine)
    return db_session.SessionLocal()


def _seed_competition_with_cadence(
    session,
    *,
    competition_name: str,
    days_between: float,
    n_matchdays: int,
    n_teams: int = 6,
) -> str:
    """Create ``n_matchdays`` of round-robin-ish fixtures spaced
    ``days_between`` apart, each with a result, so the repository can
    compute a real median gap."""
    from app.models.tables import CompetitionModel, MatchModel, MatchResultModel, SourceModel, TeamModel

    comp = CompetitionModel(name=competition_name, country="World", season="2026")
    teams = [TeamModel(name=f"{competition_name[:3]}-Team-{i}", country=None) for i in range(n_teams)]
    source = SourceModel(name=f"src-{competition_name}", base_url="http://x", kind="thesportsdb_season", parser_profile="p", is_active=True)
    session.add_all([comp, source, *teams])
    session.flush()
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    matches: list[MatchModel] = []
    for md in range(n_matchdays):
        # Pair adjacent teams; rotate the offset each matchday so every
        # team plays in every cycle and the median gap reflects the
        # matchday cadence, not just one team's schedule.
        rotation = md % n_teams
        for i in range(n_teams // 2):
            home = teams[(i + rotation) % n_teams]
            away = teams[(i + rotation + n_teams // 2) % n_teams]
            if home.id == away.id:
                continue
            kickoff = base + timedelta(days=md * days_between)
            match = MatchModel(
                competition_id=comp.id,
                home_team_id=home.id,
                away_team_id=away.id,
                kickoff_at=kickoff,
            )
            session.add(match)
            session.flush()
            session.add(
                MatchResultModel(
                    match_id=match.id,
                    source_id=source.id,
                    played_at=kickoff,
                    home_goals=1,
                    away_goals=1,
                    result_code="X",
                )
            )
            matches.append(match)
    session.flush()
    return comp.id


def test_median_gap_returns_cadence_of_weekly_competition(tmp_path) -> None:
    from app.repositories.result_repository import ResultRepository

    session = _make_session(tmp_path)
    try:
        comp_id = _seed_competition_with_cadence(
            session,
            competition_name="Weekly League",
            days_between=7.0,
            n_matchdays=10,
        )
        repo = ResultRepository(session)
        gap = repo.median_gap_days_for_competition(comp_id)
        assert gap is not None
        # Some teams play every round, others rest a round here and there,
        # so the median sits at the matchday gap.
        assert 6.0 <= gap <= 8.0
    finally:
        session.close()


def test_median_gap_returns_long_cadence_for_infrequent_competition(tmp_path) -> None:
    from app.repositories.result_repository import ResultRepository

    session = _make_session(tmp_path)
    try:
        comp_id = _seed_competition_with_cadence(
            session,
            competition_name="National Friendlies",
            days_between=70.0,
            n_matchdays=6,
        )
        repo = ResultRepository(session)
        gap = repo.median_gap_days_for_competition(comp_id)
        assert gap is not None
        # Friendlies played every ~10 weeks should produce a gap near 70d.
        assert 60.0 <= gap <= 80.0
    finally:
        session.close()


def test_feature_service_window_widens_for_infrequent_competition(tmp_path) -> None:
    """The recent-form window for a competition with 70-day cadence
    should land much higher than for a weekly league — even though
    neither name matches the hand-curated keyword list."""
    from sqlalchemy import select

    from app.models.tables import MatchModel
    from app.repositories.feature_repository import FeatureRepository
    from app.repositories.result_repository import ResultRepository
    from app.services.feature_service import FeatureService

    session = _make_session(tmp_path)
    try:
        weekly_id = _seed_competition_with_cadence(
            session,
            competition_name="Weekly Cadence",
            days_between=7.0,
            n_matchdays=10,
        )
        rare_id = _seed_competition_with_cadence(
            session,
            competition_name="Rare Cadence",
            days_between=70.0,
            n_matchdays=6,
        )
        service = FeatureService(FeatureRepository(session), ResultRepository(session))
        service.invalidate_competition_gap_cache()

        weekly_match = session.scalar(
            select(MatchModel).where(MatchModel.competition_id == weekly_id)
        )
        rare_match = session.scalar(
            select(MatchModel).where(MatchModel.competition_id == rare_id)
        )
        weekly_window = service._max_recent_age_days(weekly_match)
        rare_window = service._max_recent_age_days(rare_match)
        # Weekly gets clamped at the minimum window (90 days) because
        # 3 * 7 = 21 < 90.
        assert weekly_window == 90.0
        # Rare cadence: 3 * ~70 = ~210 days, well above the weekly floor.
        assert rare_window >= 180.0
        assert rare_window > weekly_window
    finally:
        session.close()


def test_window_floor_reaches_past_an_inter_tournament_break() -> None:
    """A league on break leaves every team's last result 2-3 months old.

    The floor exists so the sufficiency gate does not block a whole slate over
    results that sit just past a 30-day cutoff — on PGM-806 four fixtures
    needed only 60-71 days. Lowering it back to 30 silently re-blocks them, so
    the intent is pinned here rather than left to the constant alone.
    """
    from app.services.feature_service import FeatureService

    assert FeatureService.RECENT_FORM_MIN_WINDOW_DAYS >= 90
    # Still bounded: the floor must never swallow the whole max window, or the
    # per-competition cadence sizing stops meaning anything.
    assert FeatureService.RECENT_FORM_MIN_WINDOW_DAYS < FeatureService.RECENT_FORM_MAX_WINDOW_DAYS


def test_window_floor_does_not_relax_the_sufficiency_gate() -> None:
    """Widening the window changes only how far back 'recent' reaches. The
    number of anchoring events a prediction needs is a separate rule and must
    stay put: two results per side, or three head-to-heads."""
    from app.services.prediction_service import PredictionService

    assert PredictionService.MIN_RECENT_PER_SIDE == 2
    assert PredictionService.MIN_H2H == 3
