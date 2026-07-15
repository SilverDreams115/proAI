from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import session as db_session
from app.db.migrations import run_migrations
from app.services.slate_readiness_report_service import build_slate_readiness_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only active slate readiness report.")
    parser.add_argument("--draw-code", action="append", default=None)
    parser.add_argument("--include-archived", action="store_true")
    args = parser.parse_args()

    run_migrations(db_session.engine)
    draw_codes = set(args.draw_code or []) or None
    with db_session.SessionLocal() as session:
        report = build_slate_readiness_report(
            session,
            include_archived=args.include_archived,
            draw_codes=draw_codes,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
