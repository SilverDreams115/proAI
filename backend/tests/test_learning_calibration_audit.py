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


# --- Audit-payload coverage and the matched-subset comparison ---------------
# `raw`/`display` live inside sanity_audit_json; `decision` reads columns that
# were always populated. Slates predicted before 2026-06-16 carry no payload,
# so the headline numbers compare different populations.


def test_matched_subset_puts_every_vector_on_the_same_positions(learn_db):  # noqa: F811
    """A slate without the guardrail trace must not silently shrink `raw`'s
    sample while `decision` keeps the full one."""
    seed_official_slate(learn_db, draw="PG-WITH", n=4, sanity=True)
    seed_official_slate(learn_db, draw="PG-WITHOUT", n=6, sanity=False)

    report = build_calibration_audit(learn_db)
    vectors = report["vectors"]

    # Unmatched: decision sees all 10 positions, raw only the 4 traced ones.
    assert vectors["decision_probabilities"]["overall"]["n"] == 10
    assert vectors["raw_probabilities"]["overall"]["n"] == 4

    # Matched: all three restricted to the 4 positions carrying all vectors.
    matched = report["matched_subset"]
    assert matched["positions"] == 4
    assert {m["n"] for m in matched["vectors"].values()} == {4}


def test_scored_position_count_is_not_the_vector_sum(learn_db):  # noqa: F811
    """`sample_count` sums the three vectors, so it reads ~3x the number of
    matches actually scored. Both numbers are reported, distinctly named."""
    seed_official_slate(learn_db, draw="PG-COUNT", n=5, sanity=True)

    report = build_calibration_audit(learn_db)

    assert report["scored_position_count"] == 5
    assert report["sample_count"] == 15


def test_coverage_names_the_slates_that_cannot_be_measured(learn_db):  # noqa: F811
    """The gap is otherwise invisible: it shows up only as a smaller `n`,
    which reads like a smaller slate rather than an unmeasurable one."""
    seed_official_slate(learn_db, draw="PG-TRACED", n=4, sanity=True)
    seed_official_slate(learn_db, draw="PG-UNTRACED", n=4, sanity=False)

    coverage = build_calibration_audit(learn_db)["audit_payload_coverage"]

    assert "PG-UNTRACED" in coverage["slates_without_audit"]
    assert "PG-TRACED" not in coverage["slates_without_audit"]
    assert coverage["per_slate"]["PG-TRACED"]["with_audit"] == 4
    assert coverage["per_slate"]["PG-UNTRACED"]["with_audit"] == 0


def test_decision_vector_reads_the_audit_not_the_legacy_columns(learn_db):  # noqa: F811
    """PG-2337/PG-2338 were written while the columns still held the RAW
    vector. Reading columns there measures something nothing decided on, and
    made `decision` look like a worse-calibrated vector than `display` when
    every stored audit has the two identical."""
    import json

    from sqlalchemy import select

    from app.models.tables import PredictionModel
    from app.services.learning_slate_scoring_service import _decision_probs

    seed_official_slate(learn_db, draw="PG-LEGACY", n=4, sanity=True)
    pred = learn_db.scalars(select(PredictionModel)).first()

    # Simulate the legacy write: columns carry raw, audit carries the
    # adjusted vector the pick was actually made on.
    audit = json.loads(pred.sanity_audit_json)
    audit["decision_probabilities"] = {"L": 0.40, "E": 0.35, "V": 0.25}
    audit["display_probabilities"] = {"L": 0.40, "E": 0.35, "V": 0.25}
    audit["raw_probabilities"] = {"L": 0.80, "E": 0.10, "V": 0.10}
    pred.sanity_audit_json = json.dumps(audit)
    pred.home_probability, pred.draw_probability, pred.away_probability = 0.80, 0.10, 0.10
    learn_db.flush()

    assert _decision_probs(pred) == {"L": 0.40, "E": 0.35, "V": 0.25}


def test_decision_falls_back_to_columns_without_an_audit(learn_db):  # noqa: F811
    """No trace at all (pre-2026-06-16 rows) — the columns are all there is."""
    from sqlalchemy import select

    from app.models.tables import PredictionModel
    from app.services.learning_slate_scoring_service import _decision_probs

    seed_official_slate(learn_db, draw="PG-NOAUDIT", n=4, sanity=False)
    pred = learn_db.scalars(select(PredictionModel)).first()

    probs = _decision_probs(pred)
    assert probs["L"] == float(pred.home_probability)
    assert probs["E"] == float(pred.draw_probability)
