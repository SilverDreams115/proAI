"""R7.6 — prediction lineage contract (pure, no DB)."""
from __future__ import annotations

import pytest

from app.domain.prediction_lineage import (
    PredictionLineageError,
    assert_prediction_lineage_complete,
    check_prediction_lineage,
)


def _complete_audit(**over):
    audit = {
        "raw_probabilities": {"L": 0.6, "E": 0.25, "V": 0.15},
        "display_probabilities": {"L": 0.6, "E": 0.25, "V": 0.15},
        "decision_probabilities": {"L": 0.6, "E": 0.25, "V": 0.15},
        "final_status": "LISTO",
        "evidence_level": "high",
        "sanity_policy_version": "v1",
        "model_artifact_id": "artifact-123",
        "fallback_used": False,
    }
    audit.update(over)
    return audit


def _complete(**over):
    payload = {
        "match_id": "m1",
        "slate_id": "s1",
        "composition_hash": "hash1",
        "slate_version": 1,
        "recommended_outcome": "1",
        "sanity_audit": _complete_audit(),
    }
    payload.update(over)
    return payload


def test_complete_lineage_can_persist():
    """1 — a fully-traced prediction passes the contract (no raise)."""
    assert_prediction_lineage_complete(**_complete())
    assert check_prediction_lineage(**_complete()).complete is True


def test_missing_slate_id_fails():
    """2 — slate_id NULL blocks persistence."""
    with pytest.raises(PredictionLineageError) as exc:
        assert_prediction_lineage_complete(**_complete(slate_id=None))
    assert "slate_id" in str(exc.value)


def test_missing_sanity_audit_fails():
    """3 — no sanity_audit_json blocks persistence."""
    with pytest.raises(PredictionLineageError) as exc:
        assert_prediction_lineage_complete(**_complete(sanity_audit=None))
    assert "sanity_audit_json" in str(exc.value)


def test_missing_composition_hash_fails():
    """4 — composition_hash NULL blocks persistence."""
    with pytest.raises(PredictionLineageError) as exc:
        assert_prediction_lineage_complete(**_complete(composition_hash=None))
    assert "composition_hash" in str(exc.value)


def test_readonly_incomplete_marks_not_complete_without_raising():
    """5 — read-only check is non-raising and reports the missing fields."""
    chk = check_prediction_lineage(
        match_id="m1", slate_id=None, composition_hash=None,
        slate_version=None, recommended_outcome="1", sanity_audit=None,
    )
    assert chk.complete is False
    assert "slate_id" in chk.missing
    assert "composition_hash" in chk.missing
    assert "sanity_audit_json" in chk.missing


def test_fallback_heuristic_audit_is_valid():
    """6 — a fallback prediction (no artifact id but fallback_used) is valid."""
    audit = _complete_audit(model_artifact_id=None, fallback_used=True)
    assert_prediction_lineage_complete(**_complete(sanity_audit=audit))


def test_missing_decision_vector_fails():
    """7 — raw/display/decision must be present explicitly."""
    audit = _complete_audit()
    del audit["decision_probabilities"]
    with pytest.raises(PredictionLineageError) as exc:
        assert_prediction_lineage_complete(**_complete(sanity_audit=audit))
    assert "decision_probabilities" in str(exc.value)


def test_missing_model_and_fallback_fails():
    audit = _complete_audit(model_artifact_id=None, fallback_used=False)
    with pytest.raises(PredictionLineageError) as exc:
        assert_prediction_lineage_complete(**_complete(sanity_audit=audit))
    assert "model_artifact_id" in str(exc.value)
