"""R7.0 — learning calibration audit (read-only, never trains)."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models.tables import MatchResultModel, PredictionModel
from app.services.learning_calibration_service import build_calibration_audit
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def _counts(session):
    return (
        int(session.scalar(select(func.count()).select_from(MatchResultModel)) or 0),
        int(session.scalar(select(func.count()).select_from(PredictionModel)) or 0),
    )


def test_calibration_does_not_train_and_writes_nothing(learn_db):  # noqa: F811
    """14 — calibration audit only audits: trains=False and no writes."""
    seed_official_slate(learn_db, draw="PG-CAL", n=4)
    before = _counts(learn_db)
    report = build_calibration_audit(learn_db)
    assert report["trains"] is False
    assert report["write_safety"]["writes_performed"] is False
    assert _counts(learn_db) == before


def test_calibration_reports_metrics_for_comparable_slate(learn_db):  # noqa: F811
    seed_official_slate(learn_db, draw="PG-CAL2", n=4)
    report = build_calibration_audit(learn_db)
    assert report["comparable_slate_count"] >= 1
    decision = report["vectors"]["decision_probabilities"]["overall"]
    assert decision["n"] == 4
    assert decision["brier"] is not None
    assert decision["ece"] is not None


def test_calibration_blocked_without_results(learn_db):  # noqa: F811
    seed_official_slate(learn_db, draw="PG-CALX", n=4, with_results=False)
    report = build_calibration_audit(learn_db)
    assert report["sample_count"] == 0
    assert "blocked" in report["note"]
