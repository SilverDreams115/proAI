"""Read-only matching of slate fixtures to scored sports results.

AUDIT ONLY. Given one Progol slate match (draw_code/position/home/away/
date/competition) and a pool of normalized sports fixtures (e.g. from the
API-Football connector), this layer scores each candidate and emits a
conservative decision — ``safe`` / ``needs_review`` / ``no_match`` — that
a *future* apply step could gate on. It never writes the DB and never
fabricates a result.

The scoring is intentionally biased toward caution for international
selections: a fixture is only ``safe`` when the team match is
unambiguous, the date is in range, the orientation is not inverted, the
match is finished and a scoreline is present. Any doubt downgrades to
``needs_review`` (or ``no_match`` below the confidence floor).

Part 4 (LN vs sports cross-check) lives here too: :func:`evaluate_ln_sign_check`
compares the official LN sign-only outcome already stored in
``match_live_results`` against the scored ``result_code`` and *blocks*
learning on any conflict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime
from difflib import SequenceMatcher

from app.connectors.api_football import ApiFootballFixture
from app.services.normalization_service import NormalizationService

# Confidence bands (spec).
SAFE_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.70

# Date tolerance: ±2 days around the slate fixture date.
MAX_DATE_OFFSET_DAYS = 2

# A team match below this clarity floor is "ambiguous" and can never be
# safe even if date/competition push the weighted score up.
TEAM_CLARITY_FLOOR = 0.80
# A second candidate this close to the best one means two strong
# candidates → never safe.
SECOND_CANDIDATE_MARGIN = 0.10

DECISION_SAFE = "safe"
DECISION_REVIEW = "needs_review"
DECISION_NO_MATCH = "no_match"
DECISION_BLOCKED = "blocked"

LN_CHECK_NOT_AVAILABLE = "not_available"
LN_CHECK_MATCHES = "matches"
LN_CHECK_CONFLICT = "conflict"
LN_CONFLICT_REASON = "ln_sign_sports_score_conflict"

_normalizer = NormalizationService()


@dataclass(frozen=True)
class SlateMatchInput:
    """The slate side of a match — what we are trying to find a result for."""

    slate_id: str
    draw_code: str | None
    position: int
    home: str
    away: str
    date: date_cls | None
    competition: str | None = None


@dataclass(frozen=True)
class CandidateScore:
    fixture: ApiFootballFixture
    team_match_score: float
    date_score: float
    competition_score: float
    home_away_orientation_score: float
    overall_confidence: float
    inverted: bool


@dataclass(frozen=True)
class MatchDecision:
    decision: str
    candidate: CandidateScore | None
    confidence: float
    mapping_warnings: list[str] = field(default_factory=list)
    safe_blockers: list[str] = field(default_factory=list)


def _similarity(a: str | None, b: str | None) -> float:
    """Normalized-name similarity in [0, 1] (national-team aware)."""
    if not a or not b:
        return 0.0
    na = _normalizer.normalize_team_name(a)
    nb = _normalizer.normalize_team_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _competition_similarity(a: str | None, b: str | None) -> float:
    # Competition is corroborating evidence, not disqualifying evidence:
    # providers mislabel the same fixture (API-Football files national
    # friendlies under "World Cup"). So the score is FLOORED at 0.5 — an
    # exact match adds the full weight, a mismatch contributes a neutral
    # 0.5, but a different label can never drag a strong team/date/
    # orientation match below the safe band on its own.
    if not a or not b:
        return 0.6
    na = _normalizer.normalize_competition_name(a)
    nb = _normalizer.normalize_competition_name(b)
    if not na or not nb:
        return 0.6
    if na == nb:
        return 1.0
    return 0.5 + 0.5 * SequenceMatcher(None, na, nb).ratio()


def _competition_mismatch(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return _normalizer.normalize_competition_name(a) != _normalizer.normalize_competition_name(b)


# Timezone offsets of ±1 day are expected (kickoff UTC vs slate local
# date) and must not heavily penalize an otherwise-strong match: 0d→1.00,
# ±1d→0.90, ±2d→0.75, beyond → 0.0 (a hard out-of-range blocker).
_DATE_SCORE_BY_OFFSET = {0: 1.00, 1: 0.90, 2: 0.75}


def _date_score(slate_date: date_cls | None, fixture_date: str | None) -> tuple[float, int | None]:
    """Return (score, offset_days). Out of ±2 range → score 0.0."""
    if slate_date is None or not fixture_date:
        return 0.5, None
    try:
        fx = datetime.fromisoformat(fixture_date).date()
    except ValueError:
        return 0.5, None
    offset = abs((fx - slate_date).days)
    if offset > MAX_DATE_OFFSET_DAYS:
        return 0.0, offset
    return _DATE_SCORE_BY_OFFSET[offset], offset


def score_candidate(slate: SlateMatchInput, fixture: ApiFootballFixture) -> CandidateScore:
    direct = (_similarity(slate.home, fixture.home) + _similarity(slate.away, fixture.away)) / 2
    swapped = (_similarity(slate.home, fixture.away) + _similarity(slate.away, fixture.home)) / 2
    team_match_score = max(direct, swapped)
    inverted = swapped > direct
    orientation = 0.0 if inverted else 1.0

    date_score, _offset = _date_score(slate.date, fixture.date)
    competition_score = _competition_similarity(slate.competition, fixture.competition)

    # Competition CONTRIBUTES but must never block: providers label the
    # same friendly inconsistently (e.g. API-Football files these national
    # selections under "World Cup", not "Friendlies"). Keeping its weight
    # low means a competition-label mismatch can't, on its own, knock an
    # otherwise-unanimous team/date/orientation match below the safe band.
    overall = (
        0.60 * team_match_score
        + 0.20 * date_score
        + 0.10 * competition_score
        + 0.10 * orientation
    )
    return CandidateScore(
        fixture=fixture,
        team_match_score=round(team_match_score, 4),
        date_score=round(date_score, 4),
        competition_score=round(competition_score, 4),
        home_away_orientation_score=orientation,
        overall_confidence=round(overall, 4),
        inverted=inverted,
    )


def _warnings_for(slate: SlateMatchInput, cand: CandidateScore) -> list[str]:
    warnings: list[str] = []
    fx = cand.fixture
    if cand.inverted:
        warnings.append("home_away_inverted")
    if fx.status != "finished":
        warnings.append(f"status_not_finished:{fx.status}")
    if not fx.has_score:
        warnings.append("missing_score")
    if cand.date_score == 0.0:
        warnings.append("date_out_of_range")
    _, offset = _date_score(slate.date, fx.date)
    if offset:
        warnings.append(f"date_offset_{offset}d")
    if _competition_mismatch(slate.competition, fx.competition):
        warnings.append("competition_mismatch")
    if cand.team_match_score < TEAM_CLARITY_FLOOR:
        warnings.append("ambiguous_team_match")
    return warnings


def match_slate_fixture(
    slate: SlateMatchInput,
    fixtures: list[ApiFootballFixture],
) -> MatchDecision:
    """Score every fixture against the slate match and decide.

    Decision rules (never ``safe`` when any blocker is present):

    * ``overall_confidence < 0.70`` → ``no_match``
    * ``0.70 <= overall < 0.90`` → ``needs_review``
    * ``overall >= 0.90`` → ``safe`` unless a blocker downgrades it.

    Blockers (force at most ``needs_review``): inverted orientation,
    ambiguous team match, date out of range, missing score, match not
    finished, or two strong candidates.
    """
    if not fixtures:
        return MatchDecision(
            decision=DECISION_NO_MATCH,
            candidate=None,
            confidence=0.0,
            mapping_warnings=["no_candidates"],
        )

    scored = sorted(
        (score_candidate(slate, fx) for fx in fixtures),
        key=lambda c: c.overall_confidence,
        reverse=True,
    )
    best = scored[0]
    confidence = best.overall_confidence
    warnings = _warnings_for(slate, best)

    safe_blockers: list[str] = []
    if best.inverted:
        safe_blockers.append("home_away_inverted")
    if best.team_match_score < TEAM_CLARITY_FLOOR:
        safe_blockers.append("ambiguous_team_match")
    if best.date_score == 0.0:
        safe_blockers.append("date_out_of_range")
    if not best.fixture.has_score:
        safe_blockers.append("missing_score")
    if best.fixture.status != "finished":
        safe_blockers.append("match_not_finished")
    if len(scored) > 1:
        second = scored[1]
        if (
            second.overall_confidence >= REVIEW_THRESHOLD
            and best.overall_confidence - second.overall_confidence < SECOND_CANDIDATE_MARGIN
        ):
            safe_blockers.append("two_strong_candidates")

    if confidence < REVIEW_THRESHOLD:
        decision = DECISION_NO_MATCH
    elif confidence < SAFE_THRESHOLD:
        decision = DECISION_REVIEW
    else:
        decision = DECISION_REVIEW if safe_blockers else DECISION_SAFE

    return MatchDecision(
        decision=decision,
        candidate=best,
        confidence=confidence,
        mapping_warnings=warnings,
        safe_blockers=safe_blockers,
    )


# ---- Part 4: LN sign-only vs scored sports result -----------------------


@dataclass(frozen=True)
class LnSignCheck:
    ln_sign_check: str
    decision: str | None = None
    usable_for_learning: bool = True
    exclusion_reason: str | None = None


def evaluate_ln_sign_check(
    ln_sign_result_code: str | None,
    sports_result_code: str | None,
) -> LnSignCheck:
    """Cross-check the LN sign-only outcome against the scored result.

    * No LN sign-only result → ``not_available`` (nothing to contradict).
    * LN sign present but no scored result yet → ``not_available``.
    * LN sign == scored result_code → ``matches`` (consistent).
    * LN sign != scored result_code → ``conflict``: the official sign
      contradicts the scoreline, so the row is *blocked* and excluded
      from learning. Mandatory for PG-2336, which already has an official
      LN sign-only marcador.
    """
    if not ln_sign_result_code or not sports_result_code:
        return LnSignCheck(ln_sign_check=LN_CHECK_NOT_AVAILABLE)
    if ln_sign_result_code == sports_result_code:
        return LnSignCheck(ln_sign_check=LN_CHECK_MATCHES)
    return LnSignCheck(
        ln_sign_check=LN_CHECK_CONFLICT,
        decision=DECISION_BLOCKED,
        usable_for_learning=False,
        exclusion_reason=LN_CONFLICT_REASON,
    )
