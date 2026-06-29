"""Resolve the decision/visible and raw probability vectors for a stored
prediction, robust to legacy rows.

Closed predictions persisted before the legacy probability columns were
aliased to the guardrailed (sanity-capped) *decision* vector still carry the
RAW model output in ``home_probability`` / ``draw_probability`` /
``away_probability``, while the capped decision vector lives in
``sanity_audit_json.decision_probabilities``.

The contract the product wants (see PG-2338 postmortem) is:

* raw stays in the audit and is surfaced as ``raw_probabilities`` — never
  hidden, but never the number we *score* or *display*;
* scoring (Brier, expected draws) and the UI use the calibrated/visible
  *decision* vector.

So scoring/postmortem must prefer the audit's decision vector and fall back
to the legacy columns only when no audit is present. For rows written by the
current engine, column == decision, so this is a no-op; for pre-aliasing rows
it corrects the read path WITHOUT regenerating the closed prediction.
"""
from __future__ import annotations

import json
from typing import Any

# sanity_audit_json stores vectors keyed by L/E/V (Progol sign), while the
# legacy columns are home/draw/away. This maps the two.
_LEV = ("L", "E", "V")


def _vector_from_audit(pred: Any, key: str) -> tuple[float, float, float] | None:
    audit = getattr(pred, "sanity_audit_json", None)
    if not audit:
        return None
    try:
        data = json.loads(audit)
    except (ValueError, TypeError):
        return None
    vec = data.get(key)
    if not isinstance(vec, dict):
        return None
    try:
        values = tuple(float(vec[k]) for k in _LEV)
    except (KeyError, TypeError, ValueError):
        return None
    return values  # type: ignore[return-value]


def visible_probabilities(pred: Any) -> tuple[float, float, float]:
    """(home, draw, away) decision/visible vector — what the UI and scoring use.

    Prefers ``decision_probabilities`` (falls back to ``display_probabilities``)
    from the audit; falls back to the legacy columns when no audit is present.
    """
    for key in ("decision_probabilities", "display_probabilities"):
        vec = _vector_from_audit(pred, key)
        if vec is not None:
            return vec
    return (
        float(pred.home_probability),
        float(pred.draw_probability),
        float(pred.away_probability),
    )


def raw_probabilities(pred: Any) -> tuple[float, float, float] | None:
    """(home, draw, away) raw model vector from the audit, or None if absent.

    Returned for transparency in the postmortem; never used to score/display.
    """
    return _vector_from_audit(pred, "raw_probabilities")
