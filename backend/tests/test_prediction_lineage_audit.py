"""R7.6 — prediction lineage audit (read-only, detects blind rows)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models.tables import PredictionModel
from scripts.audit_prediction_lineage import _audit
from backend.tests._learning_seed import learn_db, seed_official_slate  # noqa: F401


def _count(session):
    return int(session.scalar(select(func.count()).select_from(PredictionModel)) or 0)


def test_audit_detects_blind_rows_without_writing(learn_db):  # noqa: F811
    """8 — the audit reports existing blind rows and writes nothing."""
    slate = seed_official_slate(learn_db, draw="PG-AUDIT", n=2)  # 2 complete predictions
    # Add one blind prediction (no slate_id, no sanity_audit) on an existing match.
    blind_match_id = slate.matches[0].match_id
    learn_db.add(
        PredictionModel(
            match_id=blind_match_id,
            slate_id=None,
            composition_hash=None,
            slate_version=None,
            generated_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            home_probability=0.5,
            draw_probability=0.3,
            away_probability=0.2,
            recommended_outcome="1",
            confidence_band="low",
            sanity_audit_json=None,
        )
    )
    learn_db.commit()

    before = _count(learn_db)
    report = _audit(learn_db)
    after = _count(learn_db)

    assert after == before  # read-only, no writes
    assert report["writes_performed"] is False
    assert report["backfill_performed"] is False
    assert report["without_slate_id"] >= 1
    assert report["without_sanity_audit"] >= 1
    assert report["blind_under_future_policy"] >= 1
    # The 2 seeded predictions are complete and persistable.
    assert report["persistable_under_future_policy"] >= 2
