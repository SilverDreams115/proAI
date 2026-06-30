"""Conservative draw (X) calibration — priors + per-match calibration.

Pins the guarantees from the PG-2338 draw-prior phase:
  * priors carry sample_size + confidence and flag low_confidence on a small
    sample (and never overfit to the observed rate then);
  * calibration keeps the vector summing to 1, never touches raw, only nudges
    p_draw UP toward the prior on genuinely squashed + uncertain matches, never
    promotes X to the argmax, and leaves solid high-evidence favourites alone.
"""
from __future__ import annotations

from app.services.draw_calibration import (
    FALLBACK_DRAW_PRIOR,
    MIN_SAMPLE_MEDIUM,
    DrawPrior,
    calibrate_draw,
    compute_draw_priors,
)


def _prior(value=0.27, *, conf="low", n=0):
    return DrawPrior(value, "fallback", n, conf, n < MIN_SAMPLE_MEDIUM)


# --- priors ----------------------------------------------------------------

def test_prior_sample_size_and_low_confidence_on_small_sample():
    obs = [("weekend", i % 4 == 0) for i in range(10)]  # 10 matches, ~25% draws
    priors = compute_draw_priors(obs)
    assert priors["global"].sample_size == 10
    assert priors["global"].low_confidence_prior is True
    assert priors["global"].confidence == "low"
    # Small sample => blended toward the fallback, not the raw observed rate.
    assert priors["global"].source in {"blended", "fallback"}


def test_prior_blends_toward_fallback_on_tiny_sample():
    # 4 matches, all draws (rate=1.0) must NOT yield a 1.0 prior.
    priors = compute_draw_priors([("weekend", True)] * 4)
    assert priors["weekend"].value < 0.5
    assert priors["weekend"].low_confidence_prior is True


def test_prior_empty_is_fallback():
    priors = compute_draw_priors([])
    assert priors["global"].value == FALLBACK_DRAW_PRIOR
    assert priors["fallback"].value == FALLBACK_DRAW_PRIOR
    assert priors["global"].low_confidence_prior is True


def test_large_sample_raises_confidence():
    obs = [("weekend", i % 4 == 0) for i in range(160)]
    priors = compute_draw_priors(obs)
    assert priors["global"].confidence == "high"
    assert priors["global"].low_confidence_prior is False
    assert priors["global"].source == "observed"


# --- calibration -----------------------------------------------------------

def test_calibration_preserves_sum_to_one():
    probs = {"L": 0.62, "E": 0.18, "V": 0.20}
    res = calibrate_draw(
        probs, prior=_prior(0.27), confidence_band="low", evidence_level="low",
        final_status="REVISAR", quality_ok=False,
    )
    assert res.applied is True
    assert abs(sum(res.probabilities.values()) - 1.0) < 1e-6


def test_calibration_raises_draw_moderately_on_low_evidence():
    probs = {"L": 0.66, "E": 0.14, "V": 0.20}
    res = calibrate_draw(
        probs, prior=_prior(0.27), confidence_band="low", evidence_level="low",
        final_status="REVISAR", quality_ok=False,
    )
    assert res.applied is True
    assert res.probabilities["E"] > probs["E"]
    # Moderate: never more than MAX_DRAW_LIFT above the original.
    assert res.probabilities["E"] - probs["E"] <= 0.08 + 1e-9
    # pre is preserved.
    assert res.pre_probabilities == probs


def test_calibration_never_makes_draw_the_pick():
    # Even if we wanted to lift a lot, X must stay below the top outcome.
    probs = {"L": 0.40, "E": 0.30, "V": 0.30}
    res = calibrate_draw(
        probs, prior=_prior(0.45), confidence_band="low", evidence_level="low",
        final_status="REVISAR", quality_ok=False,
    )
    top = max(res.probabilities["L"], res.probabilities["V"])
    assert res.probabilities["E"] < top


def test_high_confidence_favourite_unchanged():
    probs = {"L": 0.72, "E": 0.12, "V": 0.16}
    res = calibrate_draw(
        probs, prior=_prior(0.27), confidence_band="high", evidence_level="high",
        final_status="LISTO", quality_ok=True,
    )
    assert res.applied is False
    assert res.probabilities == probs


def test_draw_already_near_prior_unchanged():
    probs = {"L": 0.45, "E": 0.25, "V": 0.30}  # E already close to 0.27
    res = calibrate_draw(
        probs, prior=_prior(0.27), confidence_band="low", evidence_level="low",
        final_status="REVISAR", quality_ok=False,
    )
    assert res.applied is False
    assert res.reason == "draw_already_sufficient"


def test_knockout_never_calibrated():
    probs = {"L": 0.70, "E": 0.05, "V": 0.25}
    res = calibrate_draw(
        probs, prior=_prior(0.27), confidence_band="low", evidence_level="low",
        final_status="REVISAR", quality_ok=False, is_knockout=True,
    )
    assert res.applied is False
    assert res.reason == "knockout_no_draw"


def test_pg2338_like_squashed_draw_gets_coverage_lift():
    # Japan-Sweden shape after sanity: 0.60/0.20/0.20, low evidence, real draw.
    probs = {"L": 0.60, "E": 0.20, "V": 0.20}
    res = calibrate_draw(
        probs, prior=_prior(0.27), confidence_band="low", evidence_level="low",
        final_status="REVISAR", quality_ok=False,
    )
    assert res.applied is True
    # X rises but stays below the top (never the fixed pick).
    assert probs["E"] < res.probabilities["E"] < res.probabilities["L"]
