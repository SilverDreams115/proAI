"""Register football-data.org sources for CONMEBOL competitions (Fase 6.6).

football-data.org's free tier exposes Copa Libertadores under code `CLI`.
Sudamericana is not on the free tier; the script registers Libertadores
only. Premier League (`PL`) is included because the existing connector
hardcoded that one — bringing it under the new factory keeps a single
code-path for future leagues.

Prerequisites:
  * `PROAI_FOOTBALL_DATA_API_KEY` set in the container environment.
    Get a free key at https://www.football-data.org/client/register.
  * Free tier is rate-limited to 10 requests/min, 100/day. The script
    registers one source per competition and triggers one fetch — well
    under the daily cap.

Usage (inside the proai container):
    python -m scripts.bootstrap_football_data_org
"""
from __future__ import annotations

import os
import sys
from urllib.parse import quote


# (competition_code, label, country, season_year)
# football-data.org's free tier accepts a `season=<year>` query and
# returns 100+ historical matches per season for the listed competitions.
# We register one source per (competition, season) so each can be
# refreshed independently and re-ingested without re-fetching the
# whole archive.
FOOTBALL_DATA_COMPETITIONS: list[tuple[str, str, str, int]] = [
    ("CLI", "Copa Libertadores", "South America", 2024),
    ("CLI", "Copa Libertadores", "South America", 2025),
    ("CLI", "Copa Libertadores", "South America", 2026),
]


def _source_for(
    code: str,
    label: str,
    season: int,
) -> dict[str, object]:
    query = f"competition={quote(code)}&season={season}"
    return {
        "name": f"FD-ORG {label} {season}",
        "base_url": f"https://api.football-data.org?{query}",
        "kind": "football_data_api",
        "parser_profile": "sports_feed_v1",
        "is_active": True,
    }


def main() -> int:
    if not os.environ.get("PROAI_FOOTBALL_DATA_API_KEY"):
        print(
            "PROAI_FOOTBALL_DATA_API_KEY is not set. Register at "
            "https://www.football-data.org/client/register, then export "
            "the key in .env and restart the container."
        )
        return 2

    from sqlalchemy import select

    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.models.tables import SourceModel
    from app.repositories.ingestion_repository import IngestionRepository
    from app.repositories.scheduler_repository import SchedulerRepository
    from app.services.ingestion_service import IngestionService
    from app.services.scheduler_service import SchedulerService

    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    ingestion = IngestionService(IngestionRepository(session))
    scheduler = SchedulerService(SchedulerRepository(session), IngestionRepository(session))
    # Conmebol season is short and matches are spread out -- daily
    # refresh keeps the model honest without exhausting the free quota
    # (one call per day per competition is fine under 100/day).
    refresh_interval_minutes = 24 * 60

    registered: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    try:
        for code, label, _country, season in FOOTBALL_DATA_COMPETITIONS:
            payload = _source_for(code, label, season)
            existing = session.scalar(select(SourceModel).where(SourceModel.name == payload["name"]))
            if existing is None:
                source = SourceModel(**payload)  # type: ignore[arg-type]
                session.add(source)
                session.flush()
                session.refresh(source)
            else:
                existing.base_url = str(payload["base_url"])
                existing.kind = str(payload["kind"])
                existing.parser_profile = str(payload["parser_profile"])
                existing.is_active = True
                source = existing
            session.commit()
            print(f"[register] {source.name} -> {source.base_url}")

            try:
                run = ingestion.run_for_source(source.id)
                if run.status == "completed":
                    registered.append((source.name, run.id))
                    print(f"[ingest]   {source.name} -> {run.status} run_id={run.id}")
                else:
                    failed.append((source.name, run.error_message or run.status))
                    print(f"[ingest]   {source.name} -> {run.status} ({run.error_message})")
            except Exception as exc:  # pragma: no cover
                failed.append((source.name, str(exc)))
                print(f"[ingest]   {source.name} -> FAILED ({exc})")

            try:
                job = scheduler.ensure_refresh_job(
                    source_id=source.id,
                    job_name=f"refresh-{source.id[:8]}-fdorg-{code}-{season}",
                    interval_minutes=refresh_interval_minutes,
                )
                print(f"[schedule] {source.name} -> {job.job_name} every {refresh_interval_minutes}min")
            except Exception as exc:  # pragma: no cover
                print(f"[schedule] {source.name} -> FAILED ({exc})")
    finally:
        session.close()

    print()
    print(f"completed: {len(registered)}, failed: {len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
