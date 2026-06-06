"""Register and ingest football-data.co.uk CSV sources for every league
the historical engine should cover (Fase 6.2).

football-data.co.uk publishes CSVs per (league, season). We register one
source per league × season combination, then trigger an ingestion run
for each. After running this script the database has historical
results for every European league supported by the provider, which is
what the model needs to leave the bias-driven extrapolation behind.

Leagues not on football-data.co.uk (Liga MX, Brasileirao, MLS, J1,
Chilean Primera) require an external provider; this script is a
no-op for those — the operator must configure a separate connector.

Usage (from inside the proai container):
    python -m scripts.bootstrap_football_data_sources

The script is idempotent: existing sources keep their id, and an
ingestion run is triggered for every successfully registered source.
"""
from __future__ import annotations

import sys
from typing import Iterable


_FOOTBALL_DATA_HOST = "https://www.football-data.co.uk"


# (league_code, season_code, human_label, country)
# Season code follows football-data.co.uk convention: 2425 == 2024-25.
LEAGUE_SEASONS: list[tuple[str, str, str, str]] = [
    # Premier League — already loaded, re-run keeps the row fresh.
    ("E0", "2324", "Premier League", "England"),
    ("E0", "2425", "Premier League", "England"),
    # Championship
    ("E1", "2324", "Championship", "England"),
    ("E1", "2425", "Championship", "England"),
    # La Liga (Spain)
    ("SP1", "2324", "La Liga", "Spain"),
    ("SP1", "2425", "La Liga", "Spain"),
    # Ligue 1 (France)
    ("F1", "2324", "Ligue 1", "France"),
    ("F1", "2425", "Ligue 1", "France"),
    # Serie A (Italy)
    ("I1", "2324", "Serie A", "Italy"),
    ("I1", "2425", "Serie A", "Italy"),
    # Bundesliga (Germany)
    ("D1", "2324", "Bundesliga", "Germany"),
    ("D1", "2425", "Bundesliga", "Germany"),
    # Eredivisie (Netherlands)
    ("N1", "2324", "Eredivisie", "Netherlands"),
    ("N1", "2425", "Eredivisie", "Netherlands"),
    # Liga Portugal
    ("P1", "2324", "Liga Portugal", "Portugal"),
    ("P1", "2425", "Liga Portugal", "Portugal"),
    # Belgian Pro League
    ("B1", "2324", "Belgian Pro League", "Belgium"),
    ("B1", "2425", "Belgian Pro League", "Belgium"),
    # Turkish Super Lig
    ("T1", "2324", "Turkish Super Lig", "Turkey"),
    ("T1", "2425", "Turkish Super Lig", "Turkey"),
    # Greek Super League
    ("G1", "2324", "Greek Super League", "Greece"),
    ("G1", "2425", "Greek Super League", "Greece"),
    # Scottish Premiership
    ("SC0", "2324", "Scottish Premiership", "Scotland"),
    ("SC0", "2425", "Scottish Premiership", "Scotland"),
]


def _season_label(code: str) -> str:
    """`2425` -> `24-25`."""
    if len(code) == 4:
        return f"{code[:2]}-{code[2:]}"
    return code


def _source_for(league: str, season: str, league_label: str) -> dict[str, object]:
    csv_path = f"mmz4281/{season}/{league}.csv"
    return {
        "name": f"FD-UK {league_label} {_season_label(season)}",
        "base_url": f"{_FOOTBALL_DATA_HOST}/{csv_path}",
        "kind": "football_data_uk_csv",
        "parser_profile": "sports_feed_v1",
        "is_active": True,
    }


def main(seasons: Iterable[tuple[str, str, str, str]] = LEAGUE_SEASONS) -> int:
    # Defer SQLAlchemy imports so this file can be parsed in environments
    # where the package is not on the path yet (tests, CI lint).
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
    # Football-data CSVs are weekly refreshes — once-a-week scheduling
    # gives us fresh results without hammering the host.
    refresh_interval_minutes = 7 * 24 * 60
    registered: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    try:
        for league, season, label, _country in seasons:
            payload = _source_for(league, season, label)
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
                    failed.append((source.name, run.error_detail or run.status))
                    print(f"[ingest]   {source.name} -> {run.status} ({run.error_detail})")
            except Exception as exc:  # pragma: no cover - reported and continues
                failed.append((source.name, str(exc)))
                print(f"[ingest]   {source.name} -> FAILED ({exc})")

            # F6.4: register a recurring refresh so the worker picks new
            # results up automatically once a week.
            try:
                job = scheduler.ensure_refresh_job(
                    source_id=source.id,
                    job_name=f"refresh-{source.id[:8]}-{league}-{season}",
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
