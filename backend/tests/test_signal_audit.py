"""Unit tests for the base-signal audit (diagnostic-only) report.

Covers the pure warning + summary logic without a DB: the DB/feature
gathering is integration-heavy, but the diagnostic rules that decide whether
a base signal is suspicious must be locked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "signal_audit_report.py"
    spec = importlib.util.spec_from_file_location("signal_audit_report", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_usa_like_signal_is_flagged_suspicious() -> None:
    m = _load_module()
    warnings = m.compute_signal_warnings(
        raw={"L": 0.02, "E": 0.19, "V": 0.79},
        raw_argmax="V",
        raw_max=0.79,
        evidence_level="low",
        fallback_used=True,
        is_friendly=True,
        home_sample=2,
        away_sample=3,
        rating_diff=-53.8,
        is_neutral_site="unknown",
        canonicalization_risk=False,
    )
    assert "BASE_SIGNAL_SUSPICIOUS" in warnings
    assert "FALLBACK_SIGNAL" in warnings
    assert "LOW_TEAM_SAMPLE" in warnings
    assert "RAW_ARGMAX_LOW_SUPPORT" in warnings
    assert "RAW_EXTREME_WITH_LOW_EVIDENCE" in warnings
    assert "SUSPICIOUS_AWAY_BIAS" in warnings
    assert "FRIENDLY_EXTRAPOLATION" in warnings
    # Australia rated higher (rating_diff < 0) -> NOT a home-underdog case.
    assert "SUSPICIOUS_HOME_UNDERDOG" not in warnings


def test_home_underdog_when_home_rated_higher_but_away_extreme() -> None:
    m = _load_module()
    warnings = m.compute_signal_warnings(
        raw={"L": 0.10, "E": 0.10, "V": 0.80},
        raw_argmax="V",
        raw_max=0.80,
        evidence_level="medium",
        fallback_used=False,
        is_friendly=False,
        home_sample=6,
        away_sample=6,
        rating_diff=120.0,  # home rated higher, yet model picks away big
        is_neutral_site="home",
        canonicalization_risk=False,
    )
    assert "SUSPICIOUS_HOME_UNDERDOG" in warnings


def test_clean_signal_has_no_warnings() -> None:
    m = _load_module()
    warnings = m.compute_signal_warnings(
        raw={"L": 0.50, "E": 0.25, "V": 0.25},
        raw_argmax="L",
        raw_max=0.50,
        evidence_level="high",
        fallback_used=False,
        is_friendly=False,
        home_sample=6,
        away_sample=6,
        rating_diff=30.0,
        is_neutral_site="home",  # not unknown -> no UNKNOWN_NEUTRALITY
        canonicalization_risk=False,
    )
    assert warnings == []


def test_rating_diff_exaggerated_flag() -> None:
    m = _load_module()
    warnings = m.compute_signal_warnings(
        raw={"L": 0.60, "E": 0.20, "V": 0.20},
        raw_argmax="L",
        raw_max=0.60,
        evidence_level="medium",
        fallback_used=False,
        is_friendly=False,
        home_sample=6,
        away_sample=6,
        rating_diff=300.0,
        is_neutral_site="home",
        canonicalization_risk=False,
    )
    assert "RATING_DIFF_EXAGGERATED" in warnings


def test_single_structural_warning_is_not_composite_suspicious() -> None:
    m = _load_module()
    # Only LOW_TEAM_SAMPLE fires (+ UNKNOWN_NEUTRALITY which doesn't count
    # toward the composite). argmax=E avoids RAW_ARGMAX_LOW_SUPPORT.
    warnings = m.compute_signal_warnings(
        raw={"L": 0.30, "E": 0.40, "V": 0.30},
        raw_argmax="E",
        raw_max=0.40,
        evidence_level="medium",
        fallback_used=False,
        is_friendly=False,
        home_sample=6,
        away_sample=2,
        rating_diff=10.0,
        is_neutral_site="unknown",
        canonicalization_risk=False,
    )
    assert warnings == ["LOW_TEAM_SAMPLE", "UNKNOWN_NEUTRALITY"]
    assert "BASE_SIGNAL_SUSPICIOUS" not in warnings


def test_slate_summary_distributions_and_counts() -> None:
    m = _load_module()
    rows = [
        {
            "raw_argmax": "V",
            "decision_argmax": "V",
            "fallback_used": True,
            "evidence_level": "low",
            "competition_readiness": "context_only",
            "raw_probabilities": {"L": 0.02, "E": 0.19, "V": 0.79},
            "signal_warnings": ["BASE_SIGNAL_SUSPICIOUS", "SUSPICIOUS_AWAY_BIAS"],
        },
        {
            "raw_argmax": "L",
            "decision_argmax": "L",
            "fallback_used": False,
            "evidence_level": "high",
            "competition_readiness": "ready",
            "raw_probabilities": {"L": 0.55, "E": 0.25, "V": 0.20},
            "signal_warnings": [],
        },
    ]
    summary = m.build_slate_summary(rows)
    assert summary["total_matches"] == 2
    assert summary["raw_pick_distribution"] == {"L": 1, "E": 0, "V": 1}
    assert summary["decision_pick_distribution"] == {"L": 1, "E": 0, "V": 1}
    assert summary["visitor_raw_share"] == 0.5
    assert summary["fallback_count"] == 1
    assert summary["low_evidence_count"] == 1
    assert summary["friendly_count"] == 1
    assert summary["raw_extreme_count"] == 1  # only the 0.79 row
    assert summary["suspicious_signal_count"] == 1
    assert summary["matches_requiring_manual_review"] == 1
