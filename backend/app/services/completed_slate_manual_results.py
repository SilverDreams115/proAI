"""R7.0 — Manual official results: load, validate and (guarded) apply.

The learning loop accepts results from three sources: (1) results already in
``match_results``, (2) the read-only provider dry-run, and (3) a manually
curated *official* file an operator supplies from a trusted source
(TuLotero / Pronósticos / Lotería Nacional). This module owns source (3).

Everything here that touches the database is GUARDED: ``evaluate_manual_apply``
is read-only and decides whether the file is safe to apply; ``apply_manual_results``
performs the write and is only ever reached behind an explicit, typed CLI
confirmation. Nothing is auto-applied and no results are invented.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import MatchResultModel, ProgolSlateModel, SourceModel
from app.repositories.canonical_result_repository import CanonicalResultRepository

_VALID_SIGNS = {"L", "E", "V"}
_SIGN_FROM_CODE = {"1": "L", "X": "E", "2": "V"}
_CODE_FROM_SIGN = {"L": "1", "E": "X", "V": "2"}
# Operator-curated official results outrank scrapers (lower = higher priority).
MANUAL_OFFICIAL_SOURCE_NAME = "Manual Official Progol Results"
MANUAL_OFFICIAL_SOURCE_PRIORITY = 30
_HIGH_CONFIDENCE_SOURCES = {"manual_official"}


class ManualResultsError(ValueError):
    """Raised when a manual results file is structurally invalid."""


@dataclass(frozen=True)
class ManualResult:
    position: int
    sign: str
    score: str | None
    source_note: str | None

    @property
    def goals(self) -> tuple[int, int] | None:
        if not self.score or "-" not in self.score:
            return None
        try:
            home, away = (int(p) for p in self.score.split("-", 1))
        except ValueError:
            return None
        return home, away

    @property
    def score_sign(self) -> str | None:
        goals = self.goals
        if goals is None:
            return None
        home, away = goals
        if home > away:
            return "L"
        if home < away:
            return "V"
        return "E"


@dataclass(frozen=True)
class ManualResults:
    draw_code: str
    source: str
    results: tuple[ManualResult, ...]
    checksum: str

    @property
    def source_confidence(self) -> str:
        return "high" if self.source in _HIGH_CONFIDENCE_SOURCES else "low"


def _checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_manual_results(payload: dict[str, Any]) -> ManualResults:
    """Parse + structurally validate a manual results payload."""
    if not isinstance(payload, dict):
        raise ManualResultsError("payload must be a JSON object")
    draw_code = payload.get("draw_code")
    if not isinstance(draw_code, str) or not draw_code.strip():
        raise ManualResultsError("draw_code is required")
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ManualResultsError("source is required")
    raw_results = payload.get("results")
    if not isinstance(raw_results, list) or not raw_results:
        raise ManualResultsError("results must be a non-empty list")

    results: list[ManualResult] = []
    for entry in raw_results:
        if not isinstance(entry, dict):
            raise ManualResultsError("each result must be an object")
        position = entry.get("position")
        if not isinstance(position, int):
            raise ManualResultsError("each result needs an integer position")
        sign_raw = entry.get("sign")
        sign = str(sign_raw).upper() if sign_raw is not None else None
        if sign is not None and sign not in _VALID_SIGNS:
            raise ManualResultsError(f"invalid sign {sign_raw!r} (expected L/E/V)")
        score = entry.get("score")
        if score is not None and not isinstance(score, str):
            raise ManualResultsError("score must be a string like '2-0'")
        note = entry.get("source_note")
        results.append(
            ManualResult(
                position=position,
                sign=sign or "",
                score=score,
                source_note=str(note) if note is not None else None,
            )
        )

    return ManualResults(
        draw_code=draw_code.strip(),
        source=source.strip(),
        results=tuple(results),
        checksum=_checksum(payload),
    )


def load_manual_results_file(path: str | Path) -> ManualResults:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:  # noqa: TRY003
        raise ManualResultsError(f"invalid JSON: {exc}") from exc
    return load_manual_results(payload)


def _position_to_match(slate: ProgolSlateModel) -> dict[int, Any]:
    return {sm.position: sm for sm in slate.matches}


def evaluate_manual_apply(
    session: Session, slate: ProgolSlateModel, manual: ManualResults
) -> dict[str, Any]:
    """Read-only: decide whether a manual results file is safe to apply.

    Enforces every guard rule (draw_code match, complete/in-range/unique
    positions, sign↔score agreement, no conflict with existing local results,
    high-confidence source). Writes nothing.
    """
    match_count = len(slate.matches)
    pos_to_sm = _position_to_match(slate)
    match_ids = [sm.match_id for sm in slate.matches]
    canonical = CanonicalResultRepository(session).get_with_conflict_info(match_ids)

    blockers: list[str] = []
    if manual.draw_code != slate.draw_code:
        blockers.append("draw_code_mismatch")

    positions = [r.position for r in manual.results]
    if len(positions) != len(set(positions)):
        blockers.append("duplicate_positions")
    out_of_range = [p for p in positions if p < 1 or p > match_count]
    if out_of_range:
        blockers.append("position_out_of_range")
    expected = set(range(1, match_count + 1))
    if set(positions) != expected:
        blockers.append("incomplete_positions")
    if len(manual.results) != match_count:
        blockers.append("match_count_mismatch")

    rows: list[dict[str, Any]] = []
    conflicts = 0
    missing_score = 0
    for r in sorted(manual.results, key=lambda x: x.position):
        sm = pos_to_sm.get(r.position)
        effective_sign = r.sign or (r.score_sign or "")
        score_sign = r.score_sign
        sign_mismatch = bool(r.sign and score_sign and r.sign != score_sign)
        if r.goals is None:
            missing_score += 1
        existing_sign = None
        existing_conflict = False
        if sm is not None and sm.match_id in canonical:
            cr = canonical[sm.match_id]
            existing_sign = _SIGN_FROM_CODE.get(cr.result.result_code)
            if existing_sign is not None and existing_sign != effective_sign:
                existing_conflict = True
                conflicts += 1
        rows.append(
            {
                "position": r.position,
                "sign": effective_sign,
                "score": r.score,
                "score_sign": score_sign,
                "sign_score_mismatch": sign_mismatch,
                "existing_local_sign": existing_sign,
                "conflicts_existing": existing_conflict,
                "source_note": r.source_note,
            }
        )

    if any(row["sign_score_mismatch"] for row in rows):
        blockers.append("sign_score_mismatch")
    if missing_score > 0:
        blockers.append("missing_score")
    if conflicts > 0:
        blockers.append("result_conflict")
    if manual.source_confidence != "high":
        blockers.append("low_source_confidence")

    blockers = list(dict.fromkeys(blockers))
    ready_to_apply = not blockers and match_count > 0

    return {
        "mode": "manual_results_apply_evaluation",
        "draw_code": slate.draw_code,
        "slate_id": slate.id,
        "source": manual.source,
        "source_confidence": manual.source_confidence,
        "checksum": manual.checksum,
        "match_count": match_count,
        "provided_count": len(manual.results),
        "coverage": round(len(set(positions) & expected) / match_count, 4) if match_count else 0.0,
        "conflicts": conflicts,
        "ready_to_apply": ready_to_apply,
        "blockers": blockers,
        "rows": rows,
        "write_safety": {"writes_performed": False, "snapshots_created": False},
    }


def _get_or_create_manual_source(session: Session) -> SourceModel:
    source = session.scalar(
        select(SourceModel).where(SourceModel.name == MANUAL_OFFICIAL_SOURCE_NAME)
    )
    if source is not None:
        return source
    source = SourceModel(
        name=MANUAL_OFFICIAL_SOURCE_NAME,
        base_url="manual://official-progol-results",
        kind="manual_official_results",
        parser_profile="generic",
        is_active=True,
        result_source_priority=MANUAL_OFFICIAL_SOURCE_PRIORITY,
    )
    session.add(source)
    session.flush()
    return source


def apply_manual_results(
    session: Session, slate: ProgolSlateModel, manual: ManualResults
) -> dict[str, Any]:
    """GUARDED write: persist manual official results into match_results.

    Re-runs the full evaluation and refuses if not ``ready_to_apply``. The
    caller is responsible for the typed confirmation token and for committing
    the transaction. Returns the exact delta written.
    """
    evaluation = evaluate_manual_apply(session, slate, manual)
    if not evaluation["ready_to_apply"]:
        return {
            "applied": False,
            "reason": "not_ready_to_apply",
            "blockers": evaluation["blockers"],
            "inserted": 0,
            "evaluation": evaluation,
        }

    source = _get_or_create_manual_source(session)
    pos_to_sm = _position_to_match(slate)
    played_at = datetime.now(timezone.utc)
    inserted_positions: list[int] = []

    for r in sorted(manual.results, key=lambda x: x.position):
        sm = pos_to_sm[r.position]
        goals = r.goals
        assert goals is not None  # guaranteed by missing_score guard
        home, away = goals
        # Idempotency: skip if this exact (match, source, played day) already exists.
        existing = session.scalar(
            select(MatchResultModel.id).where(
                MatchResultModel.match_id == sm.match_id,
                MatchResultModel.source_id == source.id,
            )
        )
        if existing is not None:
            continue
        session.add(
            MatchResultModel(
                match_id=sm.match_id,
                source_id=source.id,
                played_at=played_at,
                home_goals=home,
                away_goals=away,
                result_code=_CODE_FROM_SIGN[r.sign or r.score_sign or ""],
            )
        )
        inserted_positions.append(r.position)

    session.flush()
    return {
        "applied": True,
        "draw_code": slate.draw_code,
        "slate_id": slate.id,
        "source": MANUAL_OFFICIAL_SOURCE_NAME,
        "source_confidence": manual.source_confidence,
        "checksum": manual.checksum,
        "inserted": len(inserted_positions),
        "inserted_positions": inserted_positions,
        "evaluation": evaluation,
    }
