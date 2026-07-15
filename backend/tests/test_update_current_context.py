from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.update_current_context import _select_current_progol


def _catalog(draw: int, *, closes_delta_days: int, kickoff_delta_days: int, contest_type: str = "progol") -> dict:
    now = datetime.now(timezone.utc)
    return {
        "catalog_metadata": {
            "contest_type": contest_type,
            "draw_number": draw,
            "registration_closes_at": (now + timedelta(days=closes_delta_days)).isoformat(),
        },
        "fixture_candidates": [
            {
                "position": 1,
                "competition": "Liga MX",
                "home_team": "A",
                "away_team": "B",
                "kickoff_at": (now + timedelta(days=kickoff_delta_days)).isoformat(),
            }
        ],
    }


def test_select_current_progol_rejects_stale_catalogs() -> None:
    with pytest.raises(ValueError, match="No active or future"):
        _select_current_progol([_catalog(797, closes_delta_days=-5, kickoff_delta_days=-4)])


def test_select_current_progol_supports_midweek_active_catalog() -> None:
    selected = _select_current_progol(
        [
            _catalog(797, closes_delta_days=-5, kickoff_delta_days=-4, contest_type="progol_media_semana"),
            _catalog(804, closes_delta_days=2, kickoff_delta_days=3, contest_type="progol_media_semana"),
        ]
    )

    assert selected["catalog_metadata"]["draw_number"] == 804
