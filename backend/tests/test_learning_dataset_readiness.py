"""R7.0 — learning dataset readiness (read-only, never trains)."""
from __future__ import annotations

import app.services.learning_dataset_readiness_service as readiness_mod
from app.services.learning_dataset_readiness_service import build_dataset_readiness
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def test_readiness_false_without_results(learn_db):  # noqa: F811
    """12 — no comparable matches (no results) -> training_ready=false."""
    seed_official_slate(learn_db, draw="PG-2337", n=14, with_results=False)
    report = build_dataset_readiness(learn_db)
    assert report["training_ready"] is False
    assert report["comparable_match_count"] == 0
    assert report["trains"] is False


def test_readiness_true_with_sufficient_fixture(learn_db, monkeypatch):  # noqa: F811
    """13 — with enough clean comparable evidence (thresholds lowered for the
    fixture), training_ready can become true. It is still a gated, manual call."""
    monkeypatch.setattr(readiness_mod, "MIN_COMPARABLE_SLATES", 1)
    monkeypatch.setattr(readiness_mod, "MIN_COMPARABLE_MATCHES", 1)
    seed_official_slate(learn_db, draw="PG-READY", n=4, with_results=True)
    report = build_dataset_readiness(learn_db)
    assert report["comparable_match_count"] == 4
    assert report["comparable_slate_count"] == 1
    assert report["training_ready"] is True


def test_readiness_excludes_conflicts(learn_db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(readiness_mod, "MIN_COMPARABLE_SLATES", 1)
    monkeypatch.setattr(readiness_mod, "MIN_COMPARABLE_MATCHES", 1)
    seed_official_slate(learn_db, draw="PG-CONF", n=4, with_results=True, conflict_pos=2)
    report = build_dataset_readiness(learn_db)
    # A conflicting result means the slate is not fully covered -> excluded.
    assert "PG-CONF" in report["excluded"]
    assert report["training_ready"] is False
