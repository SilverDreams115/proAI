"""R6.1 — Operational Money Mode runner (read-only, single daily command).

The one command an operator runs to review every active/upcoming Progol slate
and get a clear JUGAR / NO JUGAR per slate. It orchestrates, in a strict order,
the existing read-only services:

    active_slate_scope -> money_mode_validation -> ticket_canary_dry_run
    -> money_mode -> write-safety audit -> counts before/after

It activates nothing, integrates no optimizer, never touches the real ticket
and writes no row. The session is set ``READ ONLY`` on PostgreSQL and always
rolled back, and the run captures the ten tracked counts before/after to prove
delta-zero.

Usage::

    python -m scripts.operate_money_mode --active-upcoming
    python -m scripts.operate_money_mode --draw-code PG-2338
    python -m scripts.operate_money_mode --draw-code PGM-801
    python -m scripts.operate_money_mode --active-upcoming --json
    python -m scripts.operate_money_mode --active-upcoming --markdown /tmp/money_mode_report.md
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.services.money_mode_operations_service import run_operational_money_mode

_DECISION_LABEL = {
    "JUGAR_BALANCEADO": "JUGAR · BALANCEADO",
    "JUGAR_SOLO_BALANCEADO": "JUGAR SOLO BALANCEADO",
    "JUGAR_SOLO_CONSERVADOR": "JUGAR SOLO CONSERVADOR",
    "JUGAR_SOLO_AGRESIVO": "JUGAR SOLO AGRESIVO",
    "JUGAR_CON_CAUTELA": "JUGAR CON CAUTELA",
    "NO_JUGAR": "NO JUGAR",
}


def _decision_label(status: str) -> str:
    return _DECISION_LABEL.get(status, status)


def _render_human(report: dict[str, Any]) -> str:
    if report.get("error"):
        return f"ERROR: {report['error']}"
    lines: list[str] = []
    lines.append("=" * 64)
    lines.append("OPERATIONAL MONEY MODE — read-only")
    lines.append(f"scope={report['scope']} slates={report['slate_count']} "
                 f"jugables={report['playable_slate_count']} "
                 f"bloqueadas={report['blocked_slate_count']}")
    lines.append("=" * 64)
    for entry in report["slates"]:
        st = entry["status"]
        lines.append("")
        lines.append(f"SLATE      : {st['draw_code']} ({st['week_type']}, {st['match_count']} partidos)")
        lines.append(f"STATUS     : prediction={st['prediction_status']} "
                     f"money_mode_ready={st['money_mode_ready']} "
                     f"blockers={st['data_blockers'] or 'ninguno'}")
        lines.append(f"DECISION   : {_decision_label(st['decision'])} (confianza={st['confidence']})")
        lines.append(f"             {st['reason']}")
        lines.append(f"RECOMMENDED: {st['recommended_ticket'] or 'ninguno'}")
        lines.append(f"DO_NOT_SIMPLE: {st['do_not_simple_positions'] or 'ninguno'}")
        lines.append(f"WARNINGS   : {st['warnings'] or 'ninguno'}")
        lines.append(f"WRITE_SAFETY: write_safety_ok={entry['write_safety_ok']}")
    ws = report["write_safety"]
    delta = report["counts_delta"]
    nonzero = {k: v for k, v in delta.items() if v != 0}
    lines.append("")
    lines.append("-" * 64)
    lines.append(f"COUNTS_DELTA : {'ZERO' if report['counts_delta_zero'] else nonzero}")
    lines.append(f"WRITE_SAFETY : read_only={ws['read_only']} writes={ws['writes_performed']} "
                 f"snapshots={ws['snapshots_created']} audit_passed={ws['audit_passed']}")
    lines.append("-" * 64)
    return "\n".join(lines)


def _render_markdown(report: dict[str, Any]) -> str:
    if report.get("error"):
        return f"# Operational Money Mode\n\n**ERROR:** {report['error']}\n"
    out: list[str] = []
    out.append("# Operational Money Mode — reporte accionable")
    out.append("")
    out.append(f"- **Generado:** {report['generated_at']}")
    out.append(f"- **Scope:** {report['scope']}")
    out.append(f"- **Slates:** {report['slate_count']} · "
               f"jugables: {report['playable_slate_count']} · "
               f"bloqueadas: {report['blocked_slate_count']}")
    out.append(f"- **Read-only:** sí · **counts delta cero:** "
               f"{'sí' if report['counts_delta_zero'] else 'NO'} · "
               f"**write-safety audit:** {'pass' if report['write_safety']['audit_passed'] else 'FAIL'}")
    out.append("")
    out.append("| slate | tipo | partidos | decisión | boleto | predicción | NO SIMPLE |")
    out.append("|---|---|---:|---|---|---|---|")
    for entry in report["slates"]:
        st = entry["status"]
        out.append(
            f"| {st['draw_code']} | {st['week_type']} | {st['match_count']} | "
            f"**{_decision_label(st['decision'])}** | {st['recommended_ticket'] or 'ninguno'} | "
            f"{st['prediction_status']} | {len(st['do_not_simple_positions'])} pos |"
        )
    out.append("")
    for entry in report["slates"]:
        st = entry["status"]
        out.append(f"## {st['draw_code']} → {_decision_label(st['decision'])}")
        out.append("")
        out.append(f"- **Motivo:** {st['reason']}")
        out.append(f"- **Boleto recomendado:** {st['recommended_ticket'] or 'ninguno'}")
        out.append(f"- **Partidos NO SIMPLE:** {st['do_not_simple_positions'] or 'ninguno'}")
        out.append(f"- **Revisión obligatoria:** {st['must_review_positions'] or 'ninguna'}")
        out.append(f"- **Warnings:** {st['warnings'] or 'ninguno'}")
        out.append("")
    delta = report["counts_delta"]
    out.append("## Counts before/after")
    out.append("")
    out.append("| tabla | before | after | delta |")
    out.append("|---|---:|---:|---:|")
    for name in report["counts_before"]:
        out.append(f"| {name} | {report['counts_before'][name]} | "
                   f"{report['counts_after'][name]} | {delta[name]} |")
    out.append("")
    out.append("## Restricciones respetadas")
    out.append("")
    out.append("no full activation · no training · no optimizer productivo · no ticket "
               "integration real · no ticket/prediction/feature writes · no results apply · "
               "no API-Football online · read-only + rollback · guardrail NO SIMPLE respetado.")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operational Money Mode runner (R6.1, read-only).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--active-upcoming", action="store_true")
    mode.add_argument("--draw-code")
    mode.add_argument("--slate-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", metavar="PATH", help="write a markdown report to PATH")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            report = run_operational_money_mode(
                session,
                draw_code=args.draw_code,
                slate_id=args.slate_id,
                active_upcoming=args.active_upcoming,
            )

    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as handle:
            handle.write(_render_markdown(report))
        print(f"markdown escrito en {args.markdown}")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    elif not args.markdown:
        print(_render_human(report))

    if report.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
