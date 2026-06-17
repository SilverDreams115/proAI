"""Prediction sanity / guardrail layer.

This module is a *pure* (no DB, no I/O) post-processing stage that runs
between the model scoring pipeline and the user-facing prediction. Its
job is NOT to hide model bugs — every adjustment it makes is recorded as
an explicit flag and the raw probabilities are always preserved in the
output — but to stop the product from presenting irresponsible picks:

  * an 80% favourite on a match with no real evidence,
  * an international friendly treated like an audited league fixture,
  * a `FIJO` / `LISTO` status on a low-evidence or fallback prediction.

It cleanly separates the four concepts the rest of the codebase used to
conflate:

  * ``probability``     -- the statistical chance of each L/E/V outcome.
  * ``evidence_level``  -- how much real data anchors that probability.
  * ``confidence``      -- how much the *system* trusts the number.
  * ``risk_level``      -- how risky the pick is for the quiniela.

and turns them into a single, auditable ``final_status``.

All thresholds are named constants — no magic numbers — so the policy is
reviewable in one place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Version of the guardrail policy (thresholds, caps, status rules) encoded
# in this module. Persisted with every prediction audit so a row can be
# re-interpreted later against the exact rules that produced it. Bump this
# whenever a threshold/cap/rule below changes in a way that alters outputs.
SANITY_POLICY_VERSION = "sanity-v1"


class EvidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FinalStatus(str, Enum):
    """User-facing status. Maps to the UI Fijo / Listo / Revisar tabs.

    ``BLOQUEADO`` is the hard-stop bucket (no usable prediction)."""

    FIJO = "FIJO"
    LISTO = "LISTO"
    REVISAR = "REVISAR"
    BLOQUEADO = "BLOQUEADO"


class SanityFlag(str, Enum):
    LOW_EVIDENCE = "LOW_EVIDENCE"
    INTERNATIONAL_FRIENDLY = "INTERNATIONAL_FRIENDLY"
    FRIENDLY_UNCERTAINTY_PENALTY = "FRIENDLY_UNCERTAINTY_PENALTY"
    EXTREME_PROBABILITY_WITHOUT_EVIDENCE = "EXTREME_PROBABILITY_WITHOUT_EVIDENCE"
    EXTREME_PROBABILITY_CAPPED = "EXTREME_PROBABILITY_CAPPED"
    SUSPICIOUS_CLASS_PROBABILITY = "SUSPICIOUS_CLASS_PROBABILITY"
    FALLBACK_USED = "FALLBACK_USED"
    BLOCKED_INSUFFICIENT_DATA = "BLOCKED_INSUFFICIENT_DATA"


# --- Policy thresholds (no magic numbers downstream) -----------------------

# Above this raw probability a single outcome is "extreme". An extreme
# favourite is only credible when the evidence is HIGH; otherwise it is
# flagged and degraded.
EXTREME_PROBABILITY_THRESHOLD = 0.75

# Hard caps on the *displayed* top probability. The most restrictive
# applicable cap wins. When a cap bites we shrink the whole vector toward
# the uniform prior (preserving the argmax and the ordering) until the
# top outcome sits at the cap — and record EXTREME_PROBABILITY_CAPPED.
LOW_EVIDENCE_PROBABILITY_CAP = 0.60
FRIENDLY_PROBABILITY_CAP = 0.65
# When evidence is HIGH we still don't want runaway numbers from a thin
# friendly sample, but we allow more headroom.
HIGH_EVIDENCE_PROBABILITY_CAP = 0.90

# Any class probability at or below this floor is "suspicious" — it is
# the shape an inverted-class mapping would produce (one outcome near 0).
SUSPICIOUS_CLASS_FLOOR = 0.05

# Confidence is a 0-1 system-trust score, deliberately distinct from the
# probability. It starts at the (capped) top probability and is scaled by
# how much we trust the inputs.
_EVIDENCE_CONFIDENCE_MULTIPLIER = {
    EvidenceLevel.HIGH: 1.0,
    EvidenceLevel.MEDIUM: 0.8,
    EvidenceLevel.LOW: 0.55,
}
_FRIENDLY_CONFIDENCE_PENALTY = 0.15
_FALLBACK_CONFIDENCE_PENALTY = 0.10

# Slate-level distribution alarms (Fase 1.4 / Test 6). A healthy slate
# should not pile everyone onto the visitor or produce many near-zero
# class probabilities.
SLATE_MAX_AWAY_SHARE = 0.55
SLATE_MAX_SUSPICIOUS_SHARE = 0.30
SLATE_MIN_MATCHES_FOR_ALARM = 6


@dataclass
class SanityResult:
    """The full, auditable output of the sanity layer for one match."""

    raw_probabilities: dict[str, float]
    adjusted_probabilities: dict[str, float]
    final_probabilities: dict[str, float]
    evidence_level: EvidenceLevel
    confidence: float
    risk_level: RiskLevel
    status_before_sanity: FinalStatus
    status_after_sanity: FinalStatus
    recommendation_before_sanity: str
    recommendation_after_sanity: str
    flags: list[SanityFlag] = field(default_factory=list)

    @property
    def final_status(self) -> FinalStatus:
        return self.status_after_sanity

    @property
    def recommendation(self) -> str:
        return self.recommendation_after_sanity

    def flag_values(self) -> list[str]:
        return [flag.value for flag in self.flags]


def _argmax_label(probabilities: dict[str, float]) -> str:
    return max(probabilities.items(), key=lambda item: item[1])[0]


def _status_from_band(band: str) -> FinalStatus:
    """Map the model's confidence band to a *pre-sanity* status."""
    normalized = (band or "").strip().lower()
    if normalized == "blocked":
        return FinalStatus.BLOQUEADO
    if normalized == "high":
        return FinalStatus.FIJO
    if normalized == "medium":
        return FinalStatus.LISTO
    return FinalStatus.REVISAR


def _shrink_toward_prior(
    probabilities: dict[str, float],
    cap: float,
    *,
    is_knockout: bool,
) -> dict[str, float]:
    """Blend the vector toward the uniform prior until the top outcome
    sits at ``cap``.

    Uses ``p' = lambda * p + (1 - lambda) * prior`` with a single lambda
    chosen so ``max(p')`` == ``cap``. This preserves the argmax and the
    relative ordering of all outcomes — it degrades confidence without
    inventing a different pick. For knockouts the draw is held at 0 and
    the prior is split 50/50 across L and V.
    """
    top_label = _argmax_label(probabilities)
    top_value = probabilities[top_label]
    if top_value <= cap:
        return dict(probabilities)

    if is_knockout:
        prior = {"home": 0.5, "draw": 0.0, "away": 0.5}
    else:
        prior = {"home": 1.0 / 3.0, "draw": 1.0 / 3.0, "away": 1.0 / 3.0}
    prior_top = prior[top_label]

    denominator = top_value - prior_top
    if denominator <= 1e-9:
        lam = 0.0
    else:
        lam = (cap - prior_top) / denominator
    lam = max(0.0, min(1.0, lam))

    blended = {
        label: lam * probabilities[label] + (1.0 - lam) * prior[label]
        for label in ("home", "draw", "away")
    }
    total = sum(blended.values())
    if total <= 0:
        return dict(probabilities)
    return {label: value / total for label, value in blended.items()}


def _round_vector(probabilities: dict[str, float]) -> dict[str, float]:
    return {label: round(float(value), 3) for label, value in probabilities.items()}


def apply_sanity_layer(
    *,
    probabilities: dict[str, float],
    confidence_band: str,
    evidence_level: EvidenceLevel,
    is_international_friendly: bool = False,
    fallback_used: bool = False,
    is_knockout: bool = False,
    recommended_outcome: str,
) -> SanityResult:
    """Run the guardrail policy over one match's probabilities.

    Parameters
    ----------
    probabilities
        ``{"home": pL, "draw": pE, "away": pV}`` AFTER the model's own
        post-processing (calibration, context, knockout redistribution).
        This is the input the sanity layer treats as "raw" for *its*
        purposes — it is preserved untouched in ``raw_probabilities``.
    confidence_band
        The model band: ``blocked`` / ``low`` / ``medium`` / ``high``.
    evidence_level
        How much real data anchors the prediction.
    recommended_outcome
        The label the pipeline picked (``home``/``draw``/``away`` or the
        Outcome code ``1``/``X``/``2``). Used to phrase the recommendation.
    """
    raw = {
        "home": float(probabilities.get("home", 0.0)),
        "draw": float(probabilities.get("draw", 0.0)),
        "away": float(probabilities.get("away", 0.0)),
    }
    flags: list[SanityFlag] = []

    status_before = _status_from_band(confidence_band)
    top_label = _argmax_label(raw)
    top_value = raw[top_label]
    recommendation_before = _describe_recommendation(top_label, status_before, top_value)

    # --- Flag collection (no mutation yet) ---------------------------------
    if evidence_level == EvidenceLevel.LOW:
        flags.append(SanityFlag.LOW_EVIDENCE)
    if is_international_friendly:
        flags.append(SanityFlag.INTERNATIONAL_FRIENDLY)
    if fallback_used:
        flags.append(SanityFlag.FALLBACK_USED)
    if status_before is FinalStatus.BLOQUEADO:
        flags.append(SanityFlag.BLOCKED_INSUFFICIENT_DATA)
    if min(raw["home"], raw["away"]) <= SUSPICIOUS_CLASS_FLOOR:
        # A near-zero L or V is the fingerprint of an inverted-class
        # mapping or a runaway extrapolation. Surface it for review.
        flags.append(SanityFlag.SUSPICIOUS_CLASS_PROBABILITY)

    extreme = top_value >= EXTREME_PROBABILITY_THRESHOLD
    if extreme and evidence_level != EvidenceLevel.HIGH:
        flags.append(SanityFlag.EXTREME_PROBABILITY_WITHOUT_EVIDENCE)

    # --- Determine the most restrictive applicable cap ---------------------
    cap = HIGH_EVIDENCE_PROBABILITY_CAP
    if evidence_level == EvidenceLevel.LOW:
        cap = min(cap, LOW_EVIDENCE_PROBABILITY_CAP)
    if is_international_friendly and evidence_level != EvidenceLevel.HIGH:
        cap = min(cap, FRIENDLY_PROBABILITY_CAP)
    if extreme and evidence_level != EvidenceLevel.HIGH:
        # An extreme favourite with anything less than HIGH evidence is
        # degraded to the friendly cap at most — it must not be shown raw.
        cap = min(cap, FRIENDLY_PROBABILITY_CAP)

    adjusted = _shrink_toward_prior(raw, cap, is_knockout=is_knockout)
    if _argmax_label(adjusted) != top_label or adjusted[top_label] < raw[top_label] - 1e-6:
        # Cap actually bit. Record the right flag(s).
        if SanityFlag.EXTREME_PROBABILITY_CAPPED not in flags:
            flags.append(SanityFlag.EXTREME_PROBABILITY_CAPPED)
        if is_international_friendly and SanityFlag.FRIENDLY_UNCERTAINTY_PENALTY not in flags:
            flags.append(SanityFlag.FRIENDLY_UNCERTAINTY_PENALTY)

    final = _round_vector(adjusted)
    # Renormalize after rounding so the displayed vector still sums to 1.
    total = sum(final.values())
    if total > 0:
        final = {label: round(value / total, 3) for label, value in final.items()}

    # --- Status degradation -------------------------------------------------
    status_after = _degrade_status(
        status_before,
        evidence_level=evidence_level,
        is_international_friendly=is_international_friendly,
        fallback_used=fallback_used,
        extreme_without_evidence=SanityFlag.EXTREME_PROBABILITY_WITHOUT_EVIDENCE in flags,
    )

    # --- Confidence & risk --------------------------------------------------
    confidence = _confidence_score(
        final[_argmax_label(final)],
        evidence_level=evidence_level,
        is_international_friendly=is_international_friendly,
        fallback_used=fallback_used,
    )
    risk_level = _risk_level(status_after, evidence_level, flags)

    final_top_label = _argmax_label(final)
    recommendation_after = _describe_recommendation(
        final_top_label, status_after, final[final_top_label], flags=flags
    )

    return SanityResult(
        raw_probabilities=_round_vector(raw),
        adjusted_probabilities=final,
        final_probabilities=final,
        evidence_level=evidence_level,
        confidence=round(confidence, 3),
        risk_level=risk_level,
        status_before_sanity=status_before,
        status_after_sanity=status_after,
        recommendation_before_sanity=recommendation_before,
        recommendation_after_sanity=recommendation_after,
        flags=flags,
    )


def _degrade_status(
    status_before: FinalStatus,
    *,
    evidence_level: EvidenceLevel,
    is_international_friendly: bool,
    fallback_used: bool,
    extreme_without_evidence: bool,
) -> FinalStatus:
    """Apply the hard guardrail rules. A status can only ever move
    *down* (toward REVISAR / BLOQUEADO) here, never up."""
    if status_before is FinalStatus.BLOQUEADO:
        return FinalStatus.BLOQUEADO

    status = status_before

    # Rule: low evidence can never be FIJO and defaults to REVISAR.
    if evidence_level == EvidenceLevel.LOW:
        return FinalStatus.REVISAR

    # Rule: an extreme favourite without HIGH evidence must be reviewed.
    if extreme_without_evidence:
        return FinalStatus.REVISAR

    # Rule: fallback heuristic never auto-promotes to FIJO.
    if fallback_used and status is FinalStatus.FIJO:
        status = FinalStatus.LISTO

    # Rule: international friendlies never auto-promote to FIJO (roster
    # volatility); the best they can be is LISTO.
    if is_international_friendly and status is FinalStatus.FIJO:
        status = FinalStatus.LISTO

    return status


def _confidence_score(
    top_probability: float,
    *,
    evidence_level: EvidenceLevel,
    is_international_friendly: bool,
    fallback_used: bool,
) -> float:
    score = top_probability * _EVIDENCE_CONFIDENCE_MULTIPLIER[evidence_level]
    if is_international_friendly:
        score -= _FRIENDLY_CONFIDENCE_PENALTY
    if fallback_used:
        score -= _FALLBACK_CONFIDENCE_PENALTY
    return max(0.0, min(1.0, score))


def _risk_level(
    status_after: FinalStatus,
    evidence_level: EvidenceLevel,
    flags: list[SanityFlag],
) -> RiskLevel:
    high_risk_flags = {
        SanityFlag.EXTREME_PROBABILITY_WITHOUT_EVIDENCE,
        SanityFlag.SUSPICIOUS_CLASS_PROBABILITY,
        SanityFlag.BLOCKED_INSUFFICIENT_DATA,
    }
    if status_after in (FinalStatus.REVISAR, FinalStatus.BLOQUEADO):
        return RiskLevel.HIGH
    if any(flag in high_risk_flags for flag in flags) or evidence_level == EvidenceLevel.LOW:
        return RiskLevel.HIGH
    if status_after is FinalStatus.LISTO or evidence_level == EvidenceLevel.MEDIUM:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


_LABEL_TO_OUTCOME = {"home": "L", "draw": "E", "away": "V"}
_LABEL_TO_NAME = {"home": "local", "draw": "empate", "away": "visitante"}


def _describe_recommendation(
    label: str,
    status: FinalStatus,
    probability: float,
    *,
    flags: list[SanityFlag] | None = None,
) -> str:
    outcome = _LABEL_TO_OUTCOME.get(label, "?")
    name = _LABEL_TO_NAME.get(label, label)
    if status is FinalStatus.BLOQUEADO:
        return "evitar / sin datos suficientes"
    if status is FinalStatus.REVISAR:
        # Suggest a double covering the draw when the draw is the natural
        # hedge, otherwise a plain review.
        return f"revisar / posible {outcome}-E, no fijo"
    if status is FinalStatus.LISTO:
        return f"{outcome} ({name}) — usable, cubrir si hay presupuesto"
    return f"{outcome} ({name}) — fijo defendible"


def decision_leaks_raw_probabilities(
    raw: dict[str, float],
    display: dict[str, float],
    decision: dict[str, float],
    *,
    tolerance: float = 1e-6,
) -> bool:
    """Detect an optimizer silently consuming RAW probabilities.

    Returns True when the display vector degraded the raw model output
    (they differ) yet the decision vector still tracks raw instead of the
    degraded display — exactly the contradiction this second pass exists
    to eliminate. Keyed on L/E/V; returns False if any vector is missing
    a key."""
    keys = ("L", "E", "V")
    if not all(k in raw and k in display and k in decision for k in keys):
        return False
    display_degraded_raw = any(abs(float(raw[k]) - float(display[k])) > tolerance for k in keys)
    decision_tracks_raw = all(abs(float(decision[k]) - float(raw[k])) <= tolerance for k in keys)
    return display_degraded_raw and decision_tracks_raw


# --- Slate-level distribution diagnostics ----------------------------------


@dataclass
class SlateDistributionReport:
    total_matches: int
    count_L: int
    count_E: int
    count_V: int
    avg_p_L: float
    avg_p_E: float
    avg_p_V: float
    max_probability: float
    matches_with_p_gt_75: int
    matches_low_evidence_p_gt_60: int
    matches_friendly_p_gt_65: int
    matches_home_under_5: int
    matches_status_ready_but_low_evidence: int
    matches_status_fixed_but_low_evidence: int
    away_share: float
    suspicious_share: float
    alarms: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "total_matches": self.total_matches,
            "count_L": self.count_L,
            "count_E": self.count_E,
            "count_V": self.count_V,
            "avg_p_L": round(self.avg_p_L, 3),
            "avg_p_E": round(self.avg_p_E, 3),
            "avg_p_V": round(self.avg_p_V, 3),
            "max_probability": round(self.max_probability, 3),
            "matches_with_p_gt_75": self.matches_with_p_gt_75,
            "matches_low_evidence_p_gt_60": self.matches_low_evidence_p_gt_60,
            "matches_friendly_p_gt_65": self.matches_friendly_p_gt_65,
            "matches_home_under_5": self.matches_home_under_5,
            "matches_status_ready_but_low_evidence": self.matches_status_ready_but_low_evidence,
            "matches_status_fixed_but_low_evidence": self.matches_status_fixed_but_low_evidence,
            "away_share": round(self.away_share, 3),
            "suspicious_share": round(self.suspicious_share, 3),
            "alarms": self.alarms,
        }


@dataclass
class SlateMatchObservation:
    """The minimal per-match view the distribution report needs."""

    probabilities: dict[str, float]
    recommended_label: str  # "home" | "draw" | "away"
    evidence_level: EvidenceLevel
    is_international_friendly: bool
    final_status: FinalStatus


def build_slate_distribution_report(
    observations: list[SlateMatchObservation],
) -> SlateDistributionReport:
    """Aggregate per-match observations into a slate-level diagnostic.

    Detects the "everyone goes to the visitor" / "many near-zero class
    probabilities" failure modes (Test 6) and raises named alarms."""
    total = len(observations)
    count_l = sum(1 for o in observations if o.recommended_label == "home")
    count_e = sum(1 for o in observations if o.recommended_label == "draw")
    count_v = sum(1 for o in observations if o.recommended_label == "away")

    def _avg(label: str) -> float:
        if not observations:
            return 0.0
        return sum(float(o.probabilities.get(label, 0.0)) for o in observations) / total

    max_prob = max(
        (max(o.probabilities.values()) for o in observations if o.probabilities),
        default=0.0,
    )
    p_gt_75 = sum(
        1 for o in observations if o.probabilities and max(o.probabilities.values()) >= EXTREME_PROBABILITY_THRESHOLD
    )
    low_ev_gt_60 = sum(
        1
        for o in observations
        if o.evidence_level == EvidenceLevel.LOW
        and o.probabilities
        and max(o.probabilities.values()) > LOW_EVIDENCE_PROBABILITY_CAP
    )
    friendly_gt_65 = sum(
        1
        for o in observations
        if o.is_international_friendly
        and o.probabilities
        and max(o.probabilities.values()) > FRIENDLY_PROBABILITY_CAP
    )
    home_under_5 = sum(
        1 for o in observations if float(o.probabilities.get("home", 1.0)) <= SUSPICIOUS_CLASS_FLOOR
    )
    away_under_5 = sum(
        1 for o in observations if float(o.probabilities.get("away", 1.0)) <= SUSPICIOUS_CLASS_FLOOR
    )
    ready_but_low = sum(
        1
        for o in observations
        if o.final_status in (FinalStatus.FIJO, FinalStatus.LISTO)
        and o.evidence_level == EvidenceLevel.LOW
    )
    fixed_but_low = sum(
        1
        for o in observations
        if o.final_status is FinalStatus.FIJO and o.evidence_level == EvidenceLevel.LOW
    )

    away_share = (count_v / total) if total else 0.0
    suspicious_share = ((home_under_5 + away_under_5) / total) if total else 0.0

    alarms: list[str] = []
    if total >= SLATE_MIN_MATCHES_FOR_ALARM:
        if away_share > SLATE_MAX_AWAY_SHARE:
            alarms.append(
                f"AWAY_BIAS: {count_v}/{total} picks al visitante "
                f"({away_share:.0%} > {SLATE_MAX_AWAY_SHARE:.0%}) — revisar inversion home/away o sesgo del modelo."
            )
        if suspicious_share > SLATE_MAX_SUSPICIOUS_SHARE:
            alarms.append(
                f"SUSPICIOUS_CLASS_DISTRIBUTION: {home_under_5 + away_under_5}/{total} partidos con "
                f"L o V <= {SUSPICIOUS_CLASS_FLOOR:.0%} — posible mapeo de clases invertido."
            )
    if fixed_but_low:
        alarms.append(
            f"STATUS_INTEGRITY: {fixed_but_low} partido(s) marcados FIJO con evidencia baja — no deberia ocurrir."
        )

    return SlateDistributionReport(
        total_matches=total,
        count_L=count_l,
        count_E=count_e,
        count_V=count_v,
        avg_p_L=_avg("home"),
        avg_p_E=_avg("draw"),
        avg_p_V=_avg("away"),
        max_probability=max_prob,
        matches_with_p_gt_75=p_gt_75,
        matches_low_evidence_p_gt_60=low_ev_gt_60,
        matches_friendly_p_gt_65=friendly_gt_65,
        matches_home_under_5=home_under_5,
        matches_status_ready_but_low_evidence=ready_but_low,
        matches_status_fixed_but_low_evidence=fixed_but_low,
        away_share=away_share,
        suspicious_share=suspicious_share,
        alarms=alarms,
    )
