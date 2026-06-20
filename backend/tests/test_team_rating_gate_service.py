"""R5.0: pure team-rating gate predicate. Default OFF; conservative rules."""

from __future__ import annotations

from app.services.team_rating_gate_service import evaluate_team_rating_gate


def _eligible_kwargs(**over):
    base = dict(
        competition_name="International Friendlies",
        rating_present=True,
        both_rating_medium_plus=True,
        home_rating_confidence="strong",
        away_rating_confidence="medium",
        calibrator_available=True,
        sanity_flags=[],
        feature_flag_enabled=True,
        gate_competitions=["International Friendlies"],
        require_both_medium_plus=True,
        require_calibrator=True,
    )
    base.update(over)
    return base


def test_flag_off_always_blocks_even_if_everything_else_passes():
    d = evaluate_team_rating_gate(**_eligible_kwargs(feature_flag_enabled=False))
    assert d.eligible is False
    assert d.reason == "flag_disabled"
    assert d.blockers == ["flag_disabled"]


def test_default_flag_off_via_settings():
    # Without feature_flag_enabled override it reads settings (default False).
    d = evaluate_team_rating_gate(
        competition_name="International Friendlies", rating_present=True,
        both_rating_medium_plus=True, home_rating_confidence="strong",
        away_rating_confidence="strong", calibrator_available=True,
    )
    assert d.eligible is False and d.reason == "flag_disabled"


def test_all_conditions_met_is_eligible():
    d = evaluate_team_rating_gate(**_eligible_kwargs())
    assert d.eligible is True and d.reason == "eligible" and d.blockers == []


def test_only_configured_competition_passes():
    for comp in ("Copa Libertadores", "Brasileirao", "Premier League"):
        d = evaluate_team_rating_gate(**_eligible_kwargs(competition_name=comp))
        assert d.eligible is False
        assert "competition_not_allowed" in d.blockers


def test_requires_both_medium_plus():
    d = evaluate_team_rating_gate(**_eligible_kwargs(both_rating_medium_plus=False))
    assert d.eligible is False and "not_both_medium_plus" in d.blockers


def test_missing_rating_blocks():
    d = evaluate_team_rating_gate(**_eligible_kwargs(rating_present=False))
    assert d.eligible is False and "rating_not_present" in d.blockers


def test_weak_confidence_blocks():
    d = evaluate_team_rating_gate(**_eligible_kwargs(home_rating_confidence="weak"))
    assert d.eligible is False and "home_confidence_too_low" in d.blockers
    d2 = evaluate_team_rating_gate(**_eligible_kwargs(away_rating_confidence="no_rating"))
    assert d2.eligible is False and "away_confidence_too_low" in d2.blockers


def test_missing_calibrator_blocks():
    d = evaluate_team_rating_gate(**_eligible_kwargs(calibrator_available=False))
    assert d.eligible is False and "calibrator_unavailable" in d.blockers


def test_sanity_blocker_blocks():
    for flag in ("LOW_EVIDENCE", "FALLBACK_USED", "BLOCKED", "REVISAR",
                 "EXTREME_PROBABILITY_WITHOUT_EVIDENCE"):
        d = evaluate_team_rating_gate(**_eligible_kwargs(sanity_flags=[flag]))
        assert d.eligible is False and "sanity_blocked" in d.blockers
    # a non-critical flag does NOT block
    ok = evaluate_team_rating_gate(**_eligible_kwargs(sanity_flags=["INTERNATIONAL_FRIENDLY"]))
    assert ok.eligible is True


def test_prediction_service_does_not_import_gate():
    import app.services.prediction_service as ps
    import app.services.feature_service as fs

    for module in (ps, fs):
        with open(module.__file__) as fh:
            text = fh.read()
        assert "team_rating_gate_service" not in text
        assert "evaluate_team_rating_gate" not in text


def test_gate_flags_default_off():
    from app.core.settings import load_settings
    import os

    for k in (
        "PROAI_TEAM_RATING_GATE_ENABLED",
        "PROAI_TEAM_RATING_GATE_REQUIRE_BOTH_MEDIUM_PLUS",
        "PROAI_TEAM_RATING_GATE_REQUIRE_CALIBRATOR",
    ):
        os.environ.pop(k, None)
    load_settings.cache_clear()
    s = load_settings()
    assert s.team_rating_gate_enabled is False
    assert s.team_rating_gate_competitions == ["International Friendlies"]
    assert s.team_rating_gate_require_both_medium_plus is True
    assert s.team_rating_gate_require_calibrator is True
    assert s.team_rating_gate_min_test_rows == 150
