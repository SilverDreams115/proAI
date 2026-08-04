"""Capture a concurso from an official source by hand (GUARDED).

Covers the window where a concurso is live and sellable but LN has not
published its guía PDF yet. The PDF scrapers have nothing to observe, so
without this path the dashboard cannot surface a real concurso at all —
``/api/slates/visible`` only lists slates with official proposal lineage.

TuLotero is a licensed Progol reseller whose product pages mirror the
official programa, so a capture citing TuLotero carries official lineage
(see ``slate_classification_service._OFFICIAL_SOURCE_HINTS``). A capture
citing anything else is refused.

The capture file mirrors the manual-results templates::

    {
      "draw_code": "807",
      "week_type": "midweek",
      "source_url": "https://tulotero.mx/...",
      "capture_note": "where the fixture strings were actually read from",
      "registration_closes_at": "2026-08-04T23:00:00+00:00",
      "fixtures": [
        {"position": 1, "home": "Cincinnati", "away": "Pachuca"},
        ...
      ]
    }

Usage::

    python -m scripts.capture_operator_proposal --file capture.json --dry-run
    python -m scripts.capture_operator_proposal --file capture.json \\
        --apply --confirm CAPTURE-OPERATOR-PROPOSAL

Writing only records a *validated proposal*. Turning it into a slate is
still a separate, explicit promote — the same one a PDF-sourced proposal
goes through, so fixture resolution and placeholder marking are identical.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.db import session as db_session
from app.services.slate_classification_service import is_official_source_url
from app.services.slate_proposal_service import SlateProposalService

CONFIRM_TOKEN = "CAPTURE-OPERATOR-PROPOSAL"


def _load(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Capture file must be a JSON object.")
    return payload


def _describe(payload: dict[str, Any]) -> None:
    fixtures = payload.get("fixtures") or []
    print(f"== operator capture · {payload.get('draw_code')} ==")
    print(f"  week_type    : {payload.get('week_type')}")
    print(f"  source_url   : {payload.get('source_url')}")
    print(f"  official src : {is_official_source_url(payload.get('source_url'))}")
    print(f"  cierre       : {payload.get('registration_closes_at')}")
    print(f"  fixtures     : {len(fixtures)}")
    for fixture in fixtures:
        print(
            f"    pos{int(fixture.get('position', 0)):>3} "
            f"{fixture.get('home')} vs {fixture.get('away')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record a validated Progol proposal captured from an official source."
    )
    parser.add_argument("--file", required=True)
    parser.add_argument("--dry-run", action="store_true", help="(default)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--actor", default="operator")
    args = parser.parse_args(argv)

    payload = _load(args.file)
    _describe(payload)

    if not args.apply:
        print(f"\nDRY-RUN: nothing written. Re-run with --apply --confirm {CONFIRM_TOKEN}.")
        return 0
    if args.confirm != CONFIRM_TOKEN:
        print(f"\nBLOCKED: --apply requires --confirm {CONFIRM_TOKEN}. Nothing written.")
        return 2

    with db_session.SessionLocal() as session:
        try:
            proposal = SlateProposalService(session).record_operator_capture(
                draw_code=str(payload.get("draw_code") or ""),
                week_type=str(payload.get("week_type") or ""),
                source_url=str(payload.get("source_url") or ""),
                fixtures=list(payload.get("fixtures") or []),
                closes_at_iso=payload.get("registration_closes_at"),
                actor=args.actor,
                note=payload.get("capture_note"),
            )
        except ValueError as exc:
            print(f"\nREFUSED: {exc}")
            return 3
        session.commit()
        print(
            f"\nCAPTURED: proposal {proposal.id} · {proposal.draw_code} "
            f"· status={proposal.status}"
        )
        print("Promote it with POST /api/slates/proposed/{id}/promote when ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
