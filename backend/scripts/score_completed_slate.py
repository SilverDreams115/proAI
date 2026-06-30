"""R7.0 — Post-jornada learning scoring CLI (read-only).

Compares predictions vs canonical official results for a completed slate and
prints the learning scorecard (hits, top-1/top-2, Brier, log-loss) plus a
per-position breakdown with error type and guardrail status. Writes nothing.

Usage::

    python -m scripts.score_completed_slate --draw-code PG-2337
    python -m scripts.score_completed_slate --draw-code PGM-800
    python -m scripts.score_completed_slate --all-comparable --json
    python -m scripts.score_completed_slate --draw-code PG-2337 --attribution
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.repositories.slate_repository import SlateRepository
from app.services.learning_error_attribution_service import build_error_attribution
from app.services.learning_slate_scoring_service import (
    score_comparable_slates,
    score_slate_for_draw_code,
)


def _print_score(report: dict[str, Any]) -> None:
    s = report["score"]
    print(f"== {report['draw_code']} ({report['week_type']}) comparable={report['comparable']} ==")
    print(f"  lineage={report['classification']} canonical={report['canonical_results']}/{report['match_count']} "
          f"money_mode_blocked={report['money_mode_blocked']}")
    print(f"  hits={s['hits']}/{s['total']} hit_rate={s['hit_rate']} "
          f"top1={s['top1_hits']} top2_cov={s['top2_covered']} brier={s['brier']} logloss={s['logloss']}")
    for p in report["by_position"]:
        print(f"    pos{p['position']:>2} pred={p['prediction'] or '-'} actual={p['actual'] or '-'} "
              f"hit={p['hit']} p_actual={p['probability_assigned_to_actual']} "
              f"err={p['error_type']} guard={p['guardrail_status']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post-jornada learning scoring (R7.0, read-only).")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--draw-code")
    scope.add_argument("--all-comparable", action="store_true")
    parser.add_argument("--attribution", action="store_true", help="show error attribution summary")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            if args.all_comparable:
                report: Any = score_comparable_slates(session)
            else:
                report = score_slate_for_draw_code(session, args.draw_code)
                if report is None:
                    raise SystemExit("slate not found for the requested draw code")
                if args.attribution:
                    slate = SlateRepository(session).find_by_draw_code(args.draw_code)
                    report = {"score": report, "attribution": build_error_attribution(session, slate)}

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0

    if args.all_comparable:
        print(f"scored slates: {report['slate_count']} · comparable={report['comparable_count']} "
              f"({', '.join(report['comparable_slates']) or 'none'})")
        for slate_report in report["slates"]:
            _print_score(slate_report)
        return 0

    if args.attribution:
        _print_score(report["score"])
        attr = report["attribution"]
        print(f"  attribution: {attr['summary']['by_error_type']}")
        return 0

    _print_score(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
