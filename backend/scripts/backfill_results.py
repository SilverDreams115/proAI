"""Manual, safe backfill of real Progol results from a JSON file.

Phase A.1. There is no JSON-scores ingest endpoint today — the only path is
the operator-pasted LN *acta* text (POST /api/slates/{id}/ingest-results).
This script fills that gap for controlled backfills / validation: it maps
results to matches by **slate position** (the Progol casillero), writes them
through the SAME canonical path the live pipeline uses
(``LiveResultService.record_observation`` -> ``match_results``), and never
invents, overwrites blindly, or deletes anything.

    python backend/scripts/backfill_results.py --slate-id <id> --file results.json            # dry-run
    python backend/scripts/backfill_results.py --slate-id <id> --file results.json --apply     # write
    python backend/scripts/backfill_results.py --slate-id <id> --file results.json --apply --force

results.json (a list, one row per casillero):

    [
      {"position": 1, "home_score": 2, "away_score": 1, "status": "finished", "source": "manual"}
    ]

Optional per-row "home"/"away" team names are validated against the slate
(reported as a mismatch, never silently trusted).

Safety:
  * DRY-RUN by default — nothing is written unless ``--apply`` is passed.
  * ``position`` must exist in the slate; scores must be non-negative ints
    for a finished row (else the row is skipped and reported).
  * An existing FINAL result is NOT overwritten without ``--force``.
  * A result from another source with a DIFFERENT outcome is reported as a
    conflict; it is left in place (CanonicalResultRepository excludes
    conflicting matches at read time) — never deleted.
  * Results are NEVER fabricated: only what the file states is written.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.domain.entities import MatchResultStatus
from app.models.tables import MatchResultModel, SourceModel
from app.repositories.source_repository import SourceRepository
from app.services.live_result_service import LiveResultService, compute_result_code

# Manual backfill sources sit just below the official LN feed in priority so a
# real LN result still wins canonical selection when both agree.
_BACKFILL_SOURCE_PRIORITY = 45
_LETTER = {"1": "L", "X": "E", "2": "V"}


@dataclass
class BackfillReport:
    slate_id: str
    draw_code: str
    dry_run: bool
    force: bool
    planned: list[dict[str, Any]] = field(default_factory=list)
    recorded: int = 0
    skipped_existing: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    unmapped_positions: list[int] = field(default_factory=list)
    invalid_rows: list[dict[str, Any]] = field(default_factory=list)
    team_mismatches: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slate_id": self.slate_id,
            "draw_code": self.draw_code,
            "dry_run": self.dry_run,
            "force": self.force,
            "recorded": self.recorded,
            "skipped_existing": self.skipped_existing,
            "conflicts": self.conflicts,
            "unmapped_positions": self.unmapped_positions,
            "invalid_rows": self.invalid_rows,
            "team_mismatches": self.team_mismatches,
            "planned": self.planned,
        }


def _normalize(name: str) -> str:
    return "".join(ch for ch in (name or "").lower().strip() if ch.isalnum())


def _ensure_source(session: Any, label: str) -> SourceModel:
    """Find-or-create a dedicated manual backfill source (per label) so its
    rows are traceable and removable, and so conflicts vs the LN feed surface
    naturally."""
    name = f"Manual Backfill ({label})"
    repo = SourceRepository(session)
    existing = repo.get_by_name(name)
    if existing is not None:
        return existing
    source = SourceModel(
        name=name,
        base_url="manual://backfill",
        kind="manual_backfill",
        parser_profile="generic",
        is_active=True,
        result_source_priority=_BACKFILL_SOURCE_PRIORITY,
    )
    session.add(source)
    session.flush()
    return source


def _validate_row(row: dict[str, Any], pos_map: dict[int, Any], report: BackfillReport) -> tuple[Any, str | None] | None:
    """Return (slate_match, result_code) for a writable finished row, or None
    (after recording why) when the row is unmapped / invalid / pending."""
    position = row.get("position")
    if not isinstance(position, int):
        report.invalid_rows.append({"row": row, "error": "missing_or_non_int_position"})
        return None
    sm = pos_map.get(position)
    if sm is None:
        report.unmapped_positions.append(position)
        return None

    status = str(row.get("status", "finished")).lower()
    if status not in {"finished", "full_time", "final"}:
        # Only finished rows are backfilled here; pending/live carry no result.
        report.invalid_rows.append({"position": position, "error": f"unsupported_status:{status}"})
        return None

    home_score = row.get("home_score")
    away_score = row.get("away_score")
    if not isinstance(home_score, int) or not isinstance(away_score, int) or home_score < 0 or away_score < 0:
        report.invalid_rows.append({"position": position, "error": "invalid_scores"})
        return None

    # Optional team-name validation (never silently trusted).
    parsed_home, parsed_away = row.get("home"), row.get("away")
    if parsed_home and _normalize(parsed_home) != _normalize(sm.match.home_team.name):
        report.team_mismatches.append(
            {"position": position, "file": f"{parsed_home} vs {parsed_away}",
             "slate": f"{sm.match.home_team.name} vs {sm.match.away_team.name}"}
        )
    code = compute_result_code(home_score, away_score)
    return sm, code


def run_backfill(
    session: Any,
    slate: Any,
    rows: list[dict[str, Any]],
    *,
    apply: bool = False,
    force: bool = False,
    source_label: str = "manual",
) -> BackfillReport:
    """Core backfill (no I/O). Writes only when ``apply`` is True."""
    report = BackfillReport(slate_id=slate.id, draw_code=slate.draw_code, dry_run=not apply, force=force)
    pos_map = {sm.position: sm for sm in slate.matches}
    live = LiveResultService(session)
    source = _ensure_source(session, source_label) if apply else None
    observed_at = datetime.now(timezone.utc)

    for row in rows:
        validated = _validate_row(row, pos_map, report)
        if validated is None:
            continue
        sm, code = validated
        pos = sm.position

        # Inspect existing canonical-store rows for this match.
        existing = session.scalars(
            select(MatchResultModel).where(MatchResultModel.match_id == sm.match_id)
        ).all()
        existing_codes = {r.result_code for r in existing}

        if not existing_codes:
            action = "record"
        elif existing_codes == {code}:
            # A matching result already exists — don't duplicate without force.
            action = "record" if force else "skip_existing"
            if action == "skip_existing":
                report.skipped_existing += 1
        else:
            # A DIFFERENT outcome is already on record -> conflict. Never
            # delete it; only write the conflicting row when forced (which
            # makes CanonicalResultRepository exclude the match at read time).
            report.conflicts.append(
                {"position": pos, "incoming": code, "existing": sorted(c for c in existing_codes if c)}
            )
            action = "record" if force else "skip_conflict"

        report.planned.append(
            {
                "position": pos,
                "match": f"{sm.match.home_team.name} vs {sm.match.away_team.name}",
                "score": f"{row['home_score']}-{row['away_score']}",
                "result_code": code,
                "actual_result": _LETTER.get(code or ""),
                "action": action,
            }
        )

        if action != "record" or not apply:
            continue

        live.record_observation(
            match_id=sm.match_id,
            source_id=source.id,  # type: ignore[union-attr]
            status=MatchResultStatus.FULL_TIME,
            home_goals=int(row["home_score"]),
            away_goals=int(row["away_score"]),
            is_final=True,
            observed_at=observed_at,
        )
        report.recorded += 1

    if apply:
        session.commit()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate-id", required=True, help="Target slate id.")
    parser.add_argument("--file", required=True, help="Path to results JSON.")
    parser.add_argument("--apply", action="store_true", help="Actually write (default is dry-run).")
    parser.add_argument("--dry-run", action="store_true", help="Explicit no-op (default behaviour).")
    parser.add_argument("--force", action="store_true", help="Overwrite existing / write a conflicting result.")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run

    from app.db.session import SessionLocal
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService

    with open(args.file, encoding="utf-8") as fh:
        rows = json.load(fh)
    if not isinstance(rows, list):
        raise SystemExit("results file must be a JSON list of result rows.")

    session = SessionLocal()
    try:
        slate = SlateService(SlateRepository(session)).get_slate(args.slate_id)
        if slate is None:
            raise SystemExit(f"Slate {args.slate_id} not found.")
        report = run_backfill(session, slate, rows, apply=apply, force=args.force)
    finally:
        session.close()

    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if report.dry_run:
        print("\n[dry-run] No se escribió nada. Usa --apply para aplicar.")
    else:
        print(f"\n[apply] {report.recorded} resultado(s) escrito(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
