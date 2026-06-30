"""Conservative draw (X) calibration layer — pure, no DB, no I/O.

Motivation (PG-2338 postmortem): on a few low-evidence / high-uncertainty
matches the model squashes the draw probability far below any reasonable
football base rate, so X never enters ticket coverage even when the match is
effectively a coin-flip. This layer nudges the *visible/decision* draw
probability back toward a conservative prior — only when the match is
genuinely uncertain AND its p_draw sits below the prior.

Hard guarantees (see the audit: aggregate draws are NOT under-predicted, only
a minority of uncertain matches are — so we must not inflate the whole slate):

  * the lift is bounded (``MAX_DRAW_LIFT``) and a fraction of the gap, never
    aggressive, never tuned to a single slate;
  * X is NEVER turned into the argmax / fixed pick — the calibrated p_draw is
    capped strictly below the top outcome;
  * solid high-evidence favourites are left untouched;
  * if p_draw already meets the prior, nothing changes;
  * raw probabilities are never touched — only the decision vector is, and the
    pre-calibration vector is preserved for the postmortem.

The output is consumed by the ticket optimizer (as decision probabilities) and
surfaced in the UI; raw stays in the audit.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

# Documented conservative fallback. Football/Progol draw base rates sit around
# 0.26-0.30; we anchor at the low end so the prior never over-promotes X when
# we lack a trustworthy sample. Used whenever the observed sample is too small.
FALLBACK_DRAW_PRIOR = 0.27

# Sample thresholds for prior confidence (in matches).
MIN_SAMPLE_MEDIUM = 60
MIN_SAMPLE_HIGH = 150

# --- calibration gating / magnitude (no magic numbers downstream) ----------
LOW_EVIDENCE_BANDS = frozenset({"low", "blocked"})
REVIEW_STATUSES = frozenset({"REVISAR", "BLOQUEADO"})
# top1-top2 at/below this is a "low spread" (near coin-flip top two).
SPREAD_LOW = 0.15
# normalized entropy at/above this is "high uncertainty".
ENTROPY_HIGH = 0.90
# Only fire when p_draw is MEANINGFULLY below the prior. The aggregate audit
# shows draws are not globally under-predicted, only squashed on a minority of
# matches — so we skip the many matches already near the prior and target the
# genuinely suppressed tail (e.g. p(X)=0.02 on a coin-flip).
MIN_DRAW_GAP = 0.06
# fraction of the (prior - p_draw) gap we close — deliberately gentle.
LIFT_FRACTION = 0.35
# absolute ceiling on how much p_draw may rise on a single match.
MAX_DRAW_LIFT = 0.08
# calibrated p_draw must stay at least this far BELOW the top outcome so X is
# never promoted to the argmax (never a fixed/automatic draw pick).
MARGIN_BELOW_TOP = 0.03


@dataclass(frozen=True)
class DrawPrior:
    value: float
    source: str  # "observed" | "fallback" | "blended"
    sample_size: int
    confidence: str  # "high" | "medium" | "low"
    low_confidence_prior: bool


@dataclass(frozen=True)
class DrawCalibrationResult:
    probabilities: dict[str, float]  # calibrated decision vector (L/E/V)
    applied: bool
    reason: str
    pre_probabilities: dict[str, float]  # decision vector BEFORE calibration


def _confidence_for_sample(n: int) -> str:
    if n >= MIN_SAMPLE_HIGH:
        return "high"
    if n >= MIN_SAMPLE_MEDIUM:
        return "medium"
    return "low"


def _prior_from_rate(rate: float, n: int, *, label: str) -> DrawPrior:
    """Blend the observed draw rate with the fallback by sample size.

    With a small sample we lean almost entirely on the documented fallback so
    a freak high/low-draw stretch can't move the prior. The blend weight grows
    with n and caps at MIN_SAMPLE_HIGH.
    """
    if n <= 0:
        return DrawPrior(FALLBACK_DRAW_PRIOR, "fallback", 0, "low", True)
    weight = min(1.0, n / MIN_SAMPLE_HIGH)
    value = weight * rate + (1.0 - weight) * FALLBACK_DRAW_PRIOR
    confidence = _confidence_for_sample(n)
    low_conf = n < MIN_SAMPLE_MEDIUM
    source = "observed" if weight >= 1.0 else "blended"
    return DrawPrior(round(value, 4), source, n, confidence, low_conf)


def compute_draw_priors(
    observations: Iterable[tuple[str, bool]],
) -> dict[str, DrawPrior]:
    """Compute global / weekend / midweek / fallback draw priors.

    ``observations`` is an iterable of ``(week_type, is_draw)`` over official,
    comparable, FINAL matches only (the caller is responsible for that filter;
    demos / unverified must never reach here). Returns a dict keyed by
    ``"global"``, ``"weekend"``, ``"midweek"``, ``"fallback"``.
    """
    total = 0
    draws = 0
    by_type: dict[str, list[int]] = {}
    for week_type, is_draw in observations:
        total += 1
        d = 1 if is_draw else 0
        draws += d
        bucket = by_type.setdefault(week_type, [0, 0])
        bucket[0] += 1
        bucket[1] += d

    priors: dict[str, DrawPrior] = {
        "fallback": DrawPrior(FALLBACK_DRAW_PRIOR, "fallback", 0, "low", True),
        "global": _prior_from_rate(draws / total if total else 0.0, total, label="global"),
    }
    for wt in ("weekend", "midweek"):
        n, d = by_type.get(wt, [0, 0])
        priors[wt] = _prior_from_rate(d / n if n else 0.0, n, label=wt)
    return priors


def _normalized_entropy(probs: dict[str, float]) -> float:
    values = [max(probs.get(k, 0.0), 1e-9) for k in ("L", "E", "V")]
    ent = -sum(p * math.log(p) for p in values)
    return ent / math.log(3)


def calibrate_draw(
    probabilities: dict[str, float],
    *,
    prior: DrawPrior,
    confidence_band: str | None,
    evidence_level: str | None,
    final_status: str | None,
    quality_ok: bool,
    is_knockout: bool = False,
) -> DrawCalibrationResult:
    """Conservatively nudge the decision-vector draw probability toward the
    prior on uncertain, low-evidence matches. Pure; never mutates the input.
    """
    pre = {k: float(probabilities.get(k, 0.0)) for k in ("L", "E", "V")}
    home, draw, away = pre["L"], pre["E"], pre["V"]

    # Knockouts have no X on the boleta — never calibrate a draw in.
    if is_knockout:
        return DrawCalibrationResult(pre, False, "knockout_no_draw", pre)

    top = max(home, draw, away)
    top_two = sorted((home, draw, away), reverse=True)
    top_gap = top_two[0] - top_two[1]
    entropy = _normalized_entropy(pre)

    band = (confidence_band or "").lower()
    status = (final_status or "").upper()
    uncertain = (
        band in LOW_EVIDENCE_BANDS
        or (evidence_level or "").lower() == "low"
        or status in REVIEW_STATUSES
        or top_gap <= SPREAD_LOW
        or entropy >= ENTROPY_HIGH
        or not quality_ok
    )
    if not uncertain:
        # Solid, high-evidence favourite — leave it alone.
        return DrawCalibrationResult(pre, False, "high_confidence_unchanged", pre)
    if prior.value - draw < MIN_DRAW_GAP:
        # p_draw is already at/near the prior — nothing to rescue.
        return DrawCalibrationResult(pre, False, "draw_already_sufficient", pre)

    target = draw + LIFT_FRACTION * (prior.value - draw)
    target = min(target, draw + MAX_DRAW_LIFT)
    # Never let X become the pick: keep it strictly below the top outcome.
    target = min(target, top - MARGIN_BELOW_TOP)
    if target <= draw + 1e-6:
        return DrawCalibrationResult(pre, False, "lift_below_threshold", pre)

    delta = target - draw
    nondraw = home + away
    if nondraw <= 0:
        return DrawCalibrationResult(pre, False, "degenerate_vector", pre)
    home2 = home - delta * (home / nondraw)
    away2 = away - delta * (away / nondraw)
    calibrated = {"L": home2, "E": target, "V": away2}
    s = sum(calibrated.values())
    calibrated = {k: round(v / s, 6) for k, v in calibrated.items()}

    reason = (
        f"baja evidencia/alta incertidumbre (banda={band or 'n/a'}, "
        f"gap={top_gap:.2f}, entropía={entropy:.2f}); p(X) {draw:.2f}→{calibrated['E']:.2f} "
        f"hacia prior {prior.value:.2f} [{prior.source}, conf={prior.confidence}]"
    )
    return DrawCalibrationResult(calibrated, True, reason, pre)
