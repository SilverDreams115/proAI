"""R7.6 — Prediction lineage audit (read-only, never writes).

Reports how many persisted predictions are fully traceable vs "blind"
(missing slate_id / composition_hash / sanity_audit / raw-display-decision
vectors), broken down by draw_code. It does NOT modify or backfill anything —
historical blind rows are reported, not repaired.

Usage::

    python -m scripts.audit_prediction_lineage
    python -m scripts.audit_prediction_lineage --json
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from sqlalchemy import select

from app.db import session as db_session
from app.db.session import read_only_transaction
from app.domain.prediction_lineage import check_prediction_lineage
from app.models.tables import PredictionModel, ProgolSlateModel


def _audit(session) -> dict[str, Any]:
    slate_code = dict(
        session.execute(select(ProgolSlateModel.id, ProgolSlateModel.draw_code)).all()
    )
    rows = session.scalars(select(PredictionModel)).all()

    total = len(rows)
    with_slate_id = with_audit = with_hash = with_version = with_vectors = persistable = 0
    by_draw: dict[str, dict[str, int]] = {}

    for p in rows:
        audit: dict[str, Any] | None = None
        if p.sanity_audit_json:
            try:
                audit = json.loads(p.sanity_audit_json)
            except (json.JSONDecodeError, TypeError):
                audit = None

        has_slate = p.slate_id is not None
        has_audit = audit is not None
        has_hash = p.composition_hash is not None
        has_version = p.slate_version is not None
        has_vectors = bool(
            audit
            and all(isinstance(audit.get(k), dict) and audit.get(k) for k in
                    ("raw_probabilities", "display_probabilities", "decision_probabilities"))
        )
        check = check_prediction_lineage(
            match_id=p.match_id,
            slate_id=p.slate_id,
            composition_hash=p.composition_hash,
            slate_version=p.slate_version,
            recommended_outcome=p.recommended_outcome,
            sanity_audit=audit,
        )

        with_slate_id += has_slate
        with_audit += has_audit
        with_hash += has_hash
        with_version += has_version
        with_vectors += has_vectors
        persistable += check.complete

        code = slate_code.get(p.slate_id, "(no slate_id)")
        bucket = by_draw.setdefault(
            code, {"total": 0, "with_sanity_audit": 0, "with_slate_id": 0, "persistable": 0}
        )
        bucket["total"] += 1
        bucket["with_sanity_audit"] += has_audit
        bucket["with_slate_id"] += has_slate
        bucket["persistable"] += check.complete

    return {
        "mode": "prediction_lineage_audit",
        "writes_performed": False,
        "backfill_performed": False,
        "total_predictions": total,
        "with_slate_id": with_slate_id,
        "without_slate_id": total - with_slate_id,
        "with_sanity_audit": with_audit,
        "without_sanity_audit": total - with_audit,
        "with_composition_hash": with_hash,
        "without_composition_hash": total - with_hash,
        "with_slate_version": with_version,
        "with_raw_display_decision": with_vectors,
        "without_raw_display_decision": total - with_vectors,
        "persistable_under_future_policy": persistable,
        "blind_under_future_policy": total - persistable,
        "by_draw_code": by_draw,
        "note": (
            "read-only audit; historical blind rows are NOT backfilled. "
            "The lineage contract applies to future persisted predictions only."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prediction lineage audit (R7.6, read-only).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with db_session.SessionLocal() as session:
        with read_only_transaction(session):
            report = _audit(session)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return 0

    print(f"prediction lineage audit (writes={report['writes_performed']})")
    print(f"  total predictions       : {report['total_predictions']}")
    print(f"  with slate_id           : {report['with_slate_id']} (without {report['without_slate_id']})")
    print(f"  with sanity_audit       : {report['with_sanity_audit']} (without {report['without_sanity_audit']})")
    print(f"  with composition_hash   : {report['with_composition_hash']} (without {report['without_composition_hash']})")
    print(f"  with raw/display/decision: {report['with_raw_display_decision']} (without {report['without_raw_display_decision']})")
    print(f"  persistable (future)    : {report['persistable_under_future_policy']} "
          f"(blind {report['blind_under_future_policy']})")
    print("  by draw_code:")
    for code, b in sorted(report["by_draw_code"].items()):
        print(f"    {code:>14}: total={b['total']} sanity_audit={b['with_sanity_audit']} "
              f"slate_id={b['with_slate_id']} persistable={b['persistable']}")
    print(f"  note: {report['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
