"""Tests for the pure feature builder powering the xG regressor.

These are the contract for the Booster: if a feature changes shape or
ordering, the model that was trained against the old shape will quietly
misalign without throwing — pinning the behaviour here gives us a real
gate before anything reaches inference.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.expected_goals_features import (
    DEFAULT_COMPETITION_AWAY_GOALS,
    DEFAULT_COMPETITION_HOME_GOALS,
    FEATURE_NAMES,
    FEATURE_VERSION,
    MAX_DAYS_REST,
    CompetitionBaseline,
    TeamRecentHistory,
    append_match_to_history,
    build_feature_row,
    empty_history,
    points_for_result,
    row_to_vector,
    slice_history_before,
)


def _h(kickoff: datetime) -> TeamRecentHistory:
    """Build a deterministic 3-game history terminating just before `kickoff`."""
    base = kickoff - timedelta(days=7)
    return TeamRecentHistory(
        goals_for=[2, 1, 3],
        goals_against=[1, 1, 0],
        points=[3, 1, 3],
        kickoffs=[base - timedelta(days=14), base - timedelta(days=7), base],
    )


class TestPointsForResult:
    def test_win_draw_loss(self) -> None:
        assert points_for_result(2, 1) == 3
        assert points_for_result(1, 1) == 1
        assert points_for_result(0, 2) == 0


class TestBuildFeatureRow:
    def test_returns_all_named_features(self) -> None:
        kickoff = datetime(2026, 5, 29, tzinfo=timezone.utc)
        row = build_feature_row(
            history=_h(kickoff),
            is_home=True,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(home_goals=1.6, away_goals=1.1),
        )
        assert set(row.keys()) == set(FEATURE_NAMES)

    def test_home_indicator_flips_with_side(self) -> None:
        kickoff = datetime(2026, 5, 29, tzinfo=timezone.utc)
        home = build_feature_row(
            history=_h(kickoff),
            is_home=True,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(),
        )
        away = build_feature_row(
            history=_h(kickoff),
            is_home=False,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(),
        )
        assert home["home_indicator"] == 1.0
        assert away["home_indicator"] == 0.0

    def test_rolling_windows_match_recent_slice(self) -> None:
        kickoff = datetime(2026, 5, 29, tzinfo=timezone.utc)
        # 12 games, last 5 score [1,2,3,4,5]; mean = 3.
        history = TeamRecentHistory(
            goals_for=list(range(1, 13)),
            goals_against=[0] * 12,
            points=[3] * 12,
            kickoffs=[
                kickoff - timedelta(days=14 + i) for i in range(12, 0, -1)
            ],
        )
        row = build_feature_row(
            history=history,
            is_home=True,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(),
        )
        assert row["rolling_goals_for_5"] == pytest.approx(10.0, abs=1e-9)  # (8+9+10+11+12)/5
        # Last 10 = 3..12, mean 7.5
        assert row["rolling_goals_for_10"] == pytest.approx(7.5, abs=1e-9)

    def test_empty_history_falls_back_to_zero_rolling(self) -> None:
        kickoff = datetime(2026, 5, 29, tzinfo=timezone.utc)
        row = build_feature_row(
            history=empty_history(),
            is_home=True,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(),
        )
        for key in (
            "rolling_goals_for_5",
            "rolling_goals_against_5",
            "rolling_goals_for_10",
            "rolling_goals_against_10",
            "points_per_match_10",
        ):
            assert row[key] == 0.0

    def test_days_rest_clamped_to_max(self) -> None:
        kickoff = datetime(2026, 5, 29, tzinfo=timezone.utc)
        history = TeamRecentHistory(
            goals_for=[0],
            goals_against=[0],
            points=[1],
            kickoffs=[kickoff - timedelta(days=400)],
        )
        row = build_feature_row(
            history=history,
            is_home=True,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(),
        )
        assert row["days_rest"] == MAX_DAYS_REST

    def test_days_rest_floor_at_zero_for_simultaneous_kickoff(self) -> None:
        kickoff = datetime(2026, 5, 29, tzinfo=timezone.utc)
        history = TeamRecentHistory(
            goals_for=[0],
            goals_against=[0],
            points=[1],
            kickoffs=[kickoff],
        )
        row = build_feature_row(
            history=history,
            is_home=True,
            kickoff=kickoff,
            competition_baseline=CompetitionBaseline(),
        )
        assert row["days_rest"] == 0.0

    def test_baseline_defaults_match_legacy_priors(self) -> None:
        # The fallback must exactly match the heuristic _competition_lambda_priors
        # so a team with no history scores the same lambda as the old model.
        assert DEFAULT_COMPETITION_HOME_GOALS == 1.45
        assert DEFAULT_COMPETITION_AWAY_GOALS == 1.15


class TestRowToVector:
    def test_returns_feature_names_order(self) -> None:
        row = {name: float(idx) for idx, name in enumerate(FEATURE_NAMES)}
        vec = row_to_vector(row)
        assert vec == [float(i) for i in range(len(FEATURE_NAMES))]

    def test_missing_keys_become_zero(self) -> None:
        row: dict[str, float] = {}
        vec = row_to_vector(row)
        assert vec == [0.0] * len(FEATURE_NAMES)


class TestSliceHistoryBefore:
    def test_drops_events_after_cutoff(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = TeamRecentHistory(
            goals_for=[1, 2, 3],
            goals_against=[0, 1, 2],
            points=[3, 3, 1],
            kickoffs=[
                cutoff - timedelta(days=10),
                cutoff - timedelta(days=1),
                cutoff + timedelta(days=1),
            ],
        )
        kept = slice_history_before(full_history=history, cutoff=cutoff)
        assert kept.goals_for == [1, 2]
        assert kept.points == [3, 3]
        assert len(kept.kickoffs) == 2

    def test_returns_empty_when_all_events_are_after_cutoff(self) -> None:
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        history = TeamRecentHistory(
            goals_for=[1],
            goals_against=[0],
            points=[3],
            kickoffs=[cutoff + timedelta(days=1)],
        )
        kept = slice_history_before(full_history=history, cutoff=cutoff)
        assert kept.goals_for == []


class TestAppendMatchToHistory:
    def test_appends_at_the_end_and_does_not_mutate_input(self) -> None:
        history = empty_history()
        kickoff = datetime(2026, 5, 29, tzinfo=timezone.utc)
        new = append_match_to_history(
            history,
            goals_for=2,
            goals_against=1,
            kickoff=kickoff,
        )
        assert new.goals_for == [2]
        assert new.points == [3]
        # Original is untouched.
        assert history.goals_for == []

    def test_preserves_ordering_for_chained_appends(self) -> None:
        history = empty_history()
        kickoff_a = datetime(2026, 4, 1, tzinfo=timezone.utc)
        kickoff_b = datetime(2026, 4, 15, tzinfo=timezone.utc)
        history = append_match_to_history(history, goals_for=1, goals_against=0, kickoff=kickoff_a)
        history = append_match_to_history(history, goals_for=0, goals_against=2, kickoff=kickoff_b)
        assert history.kickoffs == [kickoff_a, kickoff_b]
        assert history.points == [3, 0]


class TestFeatureVersionPin:
    def test_version_is_pinned(self) -> None:
        # The trainer reads FEATURE_VERSION from the module and pins it
        # in the artifact. Bumping the version is a deliberate schema
        # break; if you change it, you must also retrain every artifact.
        assert FEATURE_VERSION == "xg_v1"

    def test_feature_count_is_stable(self) -> None:
        # If this assertion fires you added a feature without bumping the
        # version. Update FEATURE_VERSION first, then the model artifact
        # schema, then this count.
        assert len(FEATURE_NAMES) == 9
