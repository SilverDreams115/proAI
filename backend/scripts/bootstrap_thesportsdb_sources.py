"""Register TheSportsDB v1 sources for the South-American / non-UK leagues
that football-data.co.uk does not cover (Fase 6.5).

For each (league_id, league_label, seasons) tuple this script:
  1. upserts a SourceModel pointing at the TheSportsDB v1 endpoint;
  2. triggers a one-off ingestion run so the historical data lands in
     the database immediately;
  3. registers a weekly refresh job via SchedulerService so the worker
     keeps the league up-to-date going forward.

The script is idempotent: re-running it just refreshes existing rows.

Note: TheSportsDB's free tier does NOT expose Copa Libertadores or Copa
Sudamericana as standalone leagues. Those competitions require a
different connector. This script covers what TheSportsDB actually
serves on the free key (`3`).

Usage (inside the proai container):
    python -m scripts.bootstrap_thesportsdb_sources
"""
from __future__ import annotations

import sys
from urllib.parse import quote


# (league_id, label, country, seasons)
# League IDs verified against TheSportsDB lookupleague.php during F6.5.
THESPORTSDB_LEAGUES: list[tuple[str, str, str, list[str]]] = [
    ("4351", "Brasileirao", "Brazil", ["2024", "2025", "2026"]),
    # Brasileiro Serie B (second tier) — covers Avaí, Criciuma and
    # other relegated/promoted sides that show up in Progol when
    # Serie A teams are off-cycle. League id verified via TSDB
    # lookupleague.php (May 2026).
    ("4395", "Brasileirao Serie B", "Brazil", ["2024", "2025", "2026"]),
    ("4350", "Liga MX", "Mexico", ["2022-2023", "2023-2024", "2024-2025", "2025-2026"]),
    ("4346", "MLS", "United States", ["2024", "2025", "2026"]),
    ("4627", "Primera Division Chile", "Chile", ["2024", "2025", "2026"]),
    ("4633", "J1 League", "Japan", ["2024", "2025", "2026"]),
    # International Friendlies covers the Progol amistosos (México vs
    # Australia, EUA vs Senegal, Noruega vs Suecia, etc.). TheSportsDB
    # league id 4562 is the only catalog that exposes national team
    # friendlies on the free tier — football-data.org leaves them out.
    # All friendlies for a year sit in round=1 so the connector's empty-
    # round early exit lands quickly.
    ("4562", "International Friendlies", "World", ["2024", "2025", "2026"]),
    # Mexican Liga de Expansión MX (second tier): covers Tampico/Tepatitlán
    # and similar Progol lower-tier MX fixtures. Apertura/Clausura split
    # uses the YYYY-YYYY season string. Backfilled 2022-2024 so teams
    # that sat out of the latest torneo still carry real form data.
    ("4654", "Liga de Expansion MX", "Mexico", ["2022-2023", "2023-2024", "2024-2025", "2025-2026"]),
    # Spanish La Liga 2 (LaLiga Hypermotion): covers Ceuta/Albacete and
    # other Spanish second-tier Progol fixtures.
    ("4400", "Spanish La Liga 2", "Spain", ["2024-2025", "2025-2026"]),
    # Swedish Allsvenskan (top tier): covers Degerfors/Brommapojkarna and
    # other Nordic Progol fixtures. Calendar-year season.
    ("4347", "Swedish Allsvenskan", "Sweden", ["2024", "2025", "2026"]),
    # World Cup Qualifying confederation leagues (Block 3 — evidence quality
    # pass). These provide historical form data for national teams that
    # rarely appear in International Friendlies (Brazil, Paraguay, Ecuador,
    # African sides, Asian sides). The data predates the 211-day anchor
    # window for PG-2336 kickoffs but enriches the analysis panel with
    # real match history. All competition names normalize to
    # "international-friendlies" via COMPETITION_ALIAS_SLUGS.
    # CONMEBOL WCQ 2026: last qualifier Sep 9 2025 — Brazil, Paraguay,
    # Ecuador, Uruguay, etc.
    ("5515", "World Cup Qualifying CONMEBOL", "South America", ["2026"]),
    # CAF WCQ 2026: last qualifier Oct 13 2025 — Egypt, Ivory Coast,
    # Senegal, Morocco, etc.
    ("5514", "World Cup Qualifying CAF", "Africa", ["2026"]),
    # AFC WCQ 2026: last qualifier Jun 10 2025 — Japan, South Korea,
    # Australia, etc.
    ("5513", "World Cup Qualifying AFC", "Asia", ["2026"]),
    # CONCACAF WCQ 2026: covers Canada, Mexico, USA qualifying rounds.
    ("5516", "World Cup Qualifying CONCACAF", "CONCACAF", ["2026"]),
]


def _source_for(league_id: str, label: str, seasons: list[str]) -> dict[str, object]:
    season_csv = ",".join(seasons)
    return {
        "name": f"TSDB {label}",
        "base_url": (
            f"https://www.thesportsdb.com/api/v1/json/3?league={quote(league_id)}"
            f"&seasons={quote(season_csv, safe=',-')}"
            f"&competition={quote(label)}"
        ),
        "kind": "thesportsdb_season",
        "parser_profile": "sports_feed_v1",
        "is_active": True,
    }


def main() -> int:
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
    refresh_interval_minutes = 7 * 24 * 60

    registered: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    try:
        for league_id, label, _country, seasons in THESPORTSDB_LEAGUES:
            payload = _source_for(league_id, label, seasons)
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
            except Exception as exc:  # pragma: no cover - reported and continues
                failed.append((source.name, str(exc)))
                print(f"[ingest]   {source.name} -> FAILED ({exc})")

            try:
                job = scheduler.ensure_refresh_job(
                    source_id=source.id,
                    job_name=f"refresh-{source.id[:8]}-tsdb-{league_id}",
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
