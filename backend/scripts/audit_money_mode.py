"""R6.0 — Money Mode Release Candidate auditor (read-only).

Prints the play/don't-play decision, the recommended ticket and the three
ticket profiles (aggressive / balanced / conservative) for a draw-code, a
slate-id, or every active/upcoming slate. It activates nothing, integrates no
optimizer and writes no row (no predictions, ticket snapshots or feature
snapshots). The session is set READ ONLY on PostgreSQL and always rolled back.

Usage::

    python -m scripts.audit_money_mode --draw-code PG-2338
    python -m scripts.audit_money_mode --draw-code PGM-801
    python -m scripts.audit_money_mode --active-upcoming
    python -m scripts.audit_money_mode --draw-code PG-2338 --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.money_mode_service import (
    build_active_slates_money_mode,
    build_money_mode_for_draw_code,
    build_money_mode_for_slate_id,
)


def _payload_for_args(session, args: argparse.Namespace) -> dict[str, Any]:
    if args.active_upcoming:
        return build_active_slates_money_mode(session)
    if args.slate_id is not None:
        report = build_money_mode_for_slate_id(session, args.slate_id)
    else:
        report = build_money_mode_for_draw_code(session, args.draw_code)
    if report is None:
        raise SystemExit("slate not found for the requested scope")
    return report


def _print_ticket(label: str, ticket: dict[str, Any]) -> None:
    star = " *RECOMENDADO*" if ticket.get("recommended") else ""
    cov = ticket.get("coverage_estimate") or {}
    cost = ticket["estimated_cost"]
    cost_txt = f"${cost}" if cost is not None else f"n/d ({ticket['cost_note']})"
    print(
        f"  {label:12}{star}\n"
        f"     playable={ticket['playable']} cubre_no_simple={ticket['covers_all_no_simple']} "
        f"riesgo={ticket['risk_level']}\n"
        f"     simples={ticket['simple_count']} no_simple={ticket['no_simple_count']} "
        f"dobles={ticket['double_count']} triples={ticket['triple_count']}\n"
        f"     combinaciones={ticket['estimated_combinations']} costo={cost_txt}\n"
        f"     no_cubiertos={ticket['uncovered_no_simple_positions']} "
        f"E[aciertos]={cov.get('expected_correct')} jackpot={cov.get('jackpot_probability')} "
        f"target_met={cov.get('target_met')}"
    )


def _print_slate(report: dict[str, Any]) -> None:
    s = report["slate"]
    d = report["decision"]
    print(f"== {s['draw_code']} ({s['week_type']}, {s['match_count']} partidos) ==")
    print(f"  DECISION: {d['status']}  (confianza={d['confidence']})")
    print(f"  motivo  : {d['reason']}")
    print(f"  boleto recomendado: {d['recommended_ticket']}")
    print(f"  NO SIMPLE: {report['do_not_simple_positions']}")
    print(f"  revision obligatoria: {report['must_review_positions']}")
    print(f"  canary influye en: {report['canary_influence_positions']}")
    for label in ("aggressive", "balanced", "conservative"):
        _print_ticket(label, report["tickets"][label])
    print("  --- justificacion por partido ---")
    for m in report["matches"]:
        print(
            f"    pos{m['position']:>2} {m['match'][:32]:32} {m['recommendation']:<10} "
            f"pick={'/'.join(m['money_mode_pick']) or '-':<5}({m['money_mode_pick_type']}) "
            f"canary={m['canary_active']} riesgo={m['risk']} motivos={m['reason']}"
        )
    ws = report["write_safety"]
    print(f"  write_safety: writes={ws['writes_performed']} snapshots={ws['snapshots_created']}")


def _print_human(report: dict[str, Any]) -> None:
    if report.get("mode") == "money_mode_release_candidate_active_upcoming":
        print(
            f"active/upcoming: {report['slate_count']} slates, "
            f"jugables={report['playable_slate_count']}"
        )
        for slate_report in report["slates"]:
            _print_slate(slate_report)
    else:
        _print_slate(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Money Mode RC audit (R6.0).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--slate-id")
    mode.add_argument("--draw-code")
    mode.add_argument("--active-upcoming", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            report = _payload_for_args(session, args)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
