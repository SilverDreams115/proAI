"""R6.4 — Apply completed-slate results to match_results (GUARDED).

Writing external/validated results into the productive ``match_results`` table is
gated behind an explicit, typed confirmation AND a passing validation dry-run
(``ready_to_apply=true``). Until a real validation returns ready_to_apply, this
script refuses and exits non-zero — it never writes automatically.

Required::

    python -m scripts.apply_completed_slate_results --draw-code PG-2337 \
        --apply --confirm APPLY-COMPLETED-SLATE-RESULTS

Apply conditions (all must hold): coverage complete/acceptable, no conflicts,
high-confidence source for all matches, match_count matches, no critical
placeholders. In R6.4 the apply path is intentionally not implemented (no slate
is ready), so it reports BLOCKED/NOT-READY and writes nothing.
"""
from __future__ import annotations

import argparse

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.completed_slate_results_validation_service import (
    build_completed_slate_validation_for_draw_code,
)

CONFIRM_TOKEN = "APPLY-COMPLETED-SLATE-RESULTS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply completed-slate results (GUARDED — not active in R6.4)."
    )
    parser.add_argument("--draw-code", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)

    if not args.apply or args.confirm != CONFIRM_TOKEN:
        print(
            f"BLOCKED: apply requires --apply --confirm {CONFIRM_TOKEN}. "
            "No results were written."
        )
        return 2

    # Even with the token, a passing validation dry-run is mandatory.
    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            report = build_completed_slate_validation_for_draw_code(session, args.draw_code)
    if report is None:
        print("BLOCKED: slate not found. No results were written.")
        return 3
    if not report["ready_to_apply"]:
        print(
            f"NOT READY: {args.draw_code} is not ready_to_apply "
            f"(coverage={int(report['coverage'] * 100)}%, blockers={report['blockers']}). "
            "No results were written."
        )
        return 4

    # Reaching here would be a deliberate, reviewed opt-in. The write path is
    # intentionally not implemented in R6.4.
    print(
        "NOT IMPLEMENTED: the completed-slate results apply path is intentionally "
        "not implemented in R6.4. No results were written."
    )
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
