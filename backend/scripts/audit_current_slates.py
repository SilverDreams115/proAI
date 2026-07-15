from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any

from app.db.session import SessionLocal
from app.services.operational_prediction_audit_service import (
    OperationalPredictionAuditService,
)


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    gate = payload["publish_gate"]
    freshness = payload["freshness_monitor"]
    by_draw: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    for item in gate["blocked_positions"]:
        by_draw[str(item["draw_code"])] += 1
        for reason in item["reasons"]:
            by_reason[str(reason)] += 1
    return {
        "mode": payload["mode"],
        "generated_at": payload["generated_at"],
        "gate": {
            "allowed": gate["allowed"],
            "blocked_count": gate["blocked_count"],
            "warning_count": gate["warning_count"],
            "by_draw": dict(by_draw),
            "by_reason": dict(by_reason),
        },
        "placeholders": {
            "count": payload["placeholder_queue"]["count"],
            "items": [
                {
                    "draw_code": item["draw_code"],
                    "position": item["position"],
                    "match": item["match"],
                    "flags": item["flags"],
                }
                for item in payload["placeholder_queue"]["items"]
            ],
        },
        "freshness": {
            "status": freshness["status"],
            "attention_count": freshness["attention_count"],
            "pull_ready": freshness["pull_ready"],
            "slates": freshness["slates"],
        },
        "blocked_positions": gate["blocked_positions"],
        "warnings": gate["warnings"],
    }


def _print_text(report: dict[str, Any]) -> None:
    gate = report["gate"]
    print("Operational prediction audit")
    print(f"Gate allowed: {gate['allowed']}")
    print(f"Blocked: {gate['blocked_count']} | Warnings: {gate['warning_count']}")
    print(f"Blocked by draw: {gate['by_draw']}")
    print(f"Blocked by reason: {gate['by_reason']}")
    print(f"Placeholders: {report['placeholders']['count']}")
    for item in report["placeholders"]["items"]:
        print(f"  PH {item['draw_code']} #{item['position']}: {item['match']} ({', '.join(item['flags'])})")
    print(f"Freshness: {report['freshness']['status']} | pull_ready={report['freshness']['pull_ready']}")
    for slate in report["freshness"]["slates"]:
        print(
            f"  {slate['draw_code']}: {slate['pull_state']} "
            f"{slate['completed_count']}/{slate['match_count']} "
            f"sources={','.join(slate['sources']) or '-'}"
        )
    if report["blocked_positions"]:
        print("Blocked positions:")
        for item in report["blocked_positions"]:
            print(f"  BLK {item['draw_code']} #{item['position']}: {item['match']} -> {', '.join(item['reasons'])}")
    if report["warnings"]:
        print("Warnings:")
        for item in report["warnings"]:
            print(f"  WARN {item['draw_code']} #{item['position']}: {item['match']} -> {item['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only current active slate operational audit.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    with SessionLocal() as session:
        payload = OperationalPredictionAuditService(session).build()
    report = _summary(payload)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        _print_text(report)


if __name__ == "__main__":
    main()
