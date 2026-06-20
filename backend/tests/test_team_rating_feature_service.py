"""R3: read-only rating feature helper. Pure builder + flag default OFF.

Confirms: rating_diff math, safe no_rating flags, weak/medium/strong
ordinals, the master flag defaults OFF (load returns None), and the module
never imports PredictionService / FeatureService.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services import team_rating_feature_service as svc


@dataclass
class _Snap:
    rating: float
    matches_count: int
    confidence_bucket: str


def test_build_features_rating_diff_both_present():
    home = _Snap(1600.0, 8, "medium")
    away = _Snap(1500.0, 6, "medium")
    feats = svc.build_rating_features(home, away, namespace="club")
    assert feats.rating_present is True
    assert feats.rating_diff == 100.0
    assert feats.both_rating_medium_plus is True
    assert feats.rating_namespace == "club"
    assert feats.rating_match_count_diff == 2
    assert feats.home_rating_confidence == 2
    assert feats.away_rating_confidence == 2


def test_no_rating_is_safe():
    feats = svc.build_rating_features(None, None, namespace="national")
    assert feats.rating_present is False
    assert feats.both_rating_medium_plus is False
    assert feats.rating_diff == 0.0  # never fabricate a gap
    assert feats.home_rating_confidence == 0
    assert feats.away_rating_confidence == 0


def test_one_side_missing_does_not_unblock():
    home = _Snap(1700.0, 12, "strong")
    feats = svc.build_rating_features(home, None, namespace="club")
    assert feats.rating_present is False  # both must be present
    assert feats.rating_diff == 0.0
    assert feats.both_rating_medium_plus is False
    assert feats.home_rating_confidence == 3


def test_weak_present_but_not_medium_plus():
    home = _Snap(1550.0, 2, "weak")  # weak: 1-3 matches
    away = _Snap(1490.0, 9, "medium")
    feats = svc.build_rating_features(home, away, namespace="club")
    assert feats.rating_present is True  # weak still counts as present
    assert feats.both_rating_medium_plus is False  # weak side blocks medium+
    assert feats.home_rating_confidence == 1


def test_flag_defaults_off_so_load_returns_none():
    # Default settings: PROAI_TEAM_RATING_FEATURE_ENABLED unset → False.
    assert svc.rating_features_enabled() is False
    # load_rating_features short-circuits to None before any DB access, so a
    # bogus session object is never touched.
    sentinel = object()
    assert svc.load_rating_features(
        sentinel, "home", "away", namespace="club"  # type: ignore[arg-type]
    ) is None


def test_flag_on_enables_load(monkeypatch):
    monkeypatch.setattr(svc.settings, "team_rating_feature_enabled", True)
    assert svc.rating_features_enabled() is True
    monkeypatch.setattr(svc.settings, "team_rating_feature_enabled", False)


def test_helper_does_not_import_prediction_or_feature_service():
    # Importing the helper must NOT pull the prediction/feature services into
    # the module's own dependency surface (no productive coupling).
    import app.services.team_rating_feature_service as module  # noqa: F401

    src = module.__file__
    with open(src) as fh:
        text = fh.read()
    assert "prediction_service" not in text
    assert "feature_service" not in text
    assert "import" in text  # sanity: file actually read
