"""R7.1 — Generate a manual official-results template for a completed slate.

Reads the slate's real fixtures (read-only) and emits a JSON template with one
entry per position pre-filled with the match label in ``source_note``, so an
operator only has to fill ``sign`` (L/E/V) and ``score`` (e.g. "2-0") from an
official source (Pronósticos / TuLotero). The template is intentionally
INCOMPLETE (sign/score null) and will be rejected by the validator until filled.

It writes nothing to the database.

Usage::

    python -m scripts.make_manual_results_template --draw-code PG-2337
    python -m scripts.make_manual_results_template --draw-code PGM-800 --out /tmp/pgm800.json
"""
from __future__ import annotations

import argparse
import json
import sys

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.repositories.slate_repository import SlateRepository


def build_template(session, draw_code: str) -> dict:
    slate = SlateRepository(session).find_by_draw_code(draw_code)
    if slate is None:
        raise SystemExit(f"slate not found for draw code {draw_code!r}")
    results = []
    for sm in sorted(slate.matches, key=lambda m: m.position):
        match = sm.match
        label = f"{match.home_team.name} vs {match.away_team.name}"
        results.append(
            {
                "position": sm.position,
                "sign": None,  # fill with L / E / V from the official source
                "score": None,  # fill with "H-A", e.g. "2-0"
                "source_note": label,
            }
        )
    return {
        "draw_code": slate.draw_code,
        "source": "manual_official",
        "source_note": "Pronósticos/TuLotero - pendiente de llenar (sign + score por posición)",
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a manual results template (R7.1, read-only).")
    parser.add_argument("--draw-code", required=True)
    parser.add_argument("--out", help="write to this path instead of stdout")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            template = build_template(session, args.draw_code)

    payload = json.dumps(template, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"wrote {args.out} ({len(template['results'])} posiciones)")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
