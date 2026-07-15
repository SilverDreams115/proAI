from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONTEXT_PATH = Path("data/progol_context/current.json")
ARTICLE_SOURCE_URL = "https://www.reporteindigo.com/amp/deportes/guia-de-la-quiniela-progol-2334-asi-van-los-pronosticos-20260519-0052.html"
SUPPORTED_CONTEST_TYPES = {"progol", "progol_media_semana", "progol_revancha"}


def _load_context(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return {"items": payload}
    if not isinstance(payload, dict):
        raise ValueError("current context must be a JSON object or list")
    payload.setdefault("items", [])
    if not isinstance(payload["items"], list):
        raise ValueError("current context items must be a list")
    return payload


def _fixture_key(fixture: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(fixture.get("competition") or "").strip().lower(),
        str(fixture.get("home_team") or "").strip().lower(),
        str(fixture.get("away_team") or "").strip().lower(),
    )


def _item_key(item: dict[str, Any]) -> tuple[str, str, str] | None:
    teams = item.get("teams")
    if not isinstance(teams, list) or len(teams) < 2:
        return None
    return (
        str(item.get("competition") or "").strip().lower(),
        str(teams[0] or "").strip().lower(),
        str(teams[1] or "").strip().lower(),
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_active_or_future_catalog(item: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    metadata = item.get("catalog_metadata")
    if isinstance(metadata, dict):
        closes_at = _parse_datetime(metadata.get("registration_closes_at"))
        if closes_at is not None and closes_at >= now:
            return True
    fixtures = item.get("fixture_candidates") or item.get("fixtures") or []
    if not isinstance(fixtures, list):
        return False
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        kickoff = _parse_datetime(fixture.get("kickoff_at"))
        if kickoff is not None and kickoff >= now:
            return True
    return False


def _select_current_progol(items: list[Any]) -> dict[str, Any]:
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("catalog_metadata")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("contest_type") in SUPPORTED_CONTEST_TYPES and item.get("fixture_candidates"):
            candidates.append(item)
    if not candidates:
        raise ValueError("No Progol catalog item with fixture_candidates found.")
    active = [item for item in candidates if _is_active_or_future_catalog(item)]
    if not active:
        raise ValueError("No active or future Progol catalog item with fixture_candidates found.")
    return max(active, key=lambda item: int(item.get("catalog_metadata", {}).get("draw_number") or 0))


def _context_item_for_fixture(catalog: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    draw_number = int(catalog.get("catalog_metadata", {}).get("draw_number") or 0)
    home = str(fixture.get("home_team") or "").strip()
    away = str(fixture.get("away_team") or "").strip()
    competition = str(fixture.get("competition") or "Progol").strip()
    title = f"{home} vs {away} - contexto Progol {draw_number}"
    source_url = str(catalog.get("source_url") or ARTICLE_SOURCE_URL)
    return {
        "title": title,
        "source_url": source_url,
        "competition": competition,
        "teams": [home, away],
        "summary": f"Contexto minimo generado para {competition}: {home} vs {away}.",
        "context_summary": (
            f"Partido {fixture.get('position')} del Progol {draw_number}. "
            "El contexto minimo fue generado desde la papeleta local vigente; "
            "requiere evidencia externa adicional para lesiones, suspendidos y alineaciones."
        ),
        "article_prediction": None,
        "historical_results": [],
        "availability_reports": [],
    }


def enrich_context(path: Path, *, backup: bool = True) -> dict[str, Any]:
    payload = _load_context(path)
    items = payload["items"]
    catalog = _select_current_progol(items)
    fixtures = catalog.get("fixture_candidates") or []
    if not isinstance(fixtures, list):
        raise ValueError("fixture_candidates must be a list")

    existing_keys = {_item_key(item) for item in items if isinstance(item, dict)}
    existing_keys.discard(None)
    added = 0
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        key = _fixture_key(fixture)
        if key in existing_keys:
            continue
        items.append(_context_item_for_fixture(catalog, fixture))
        existing_keys.add(key)
        added += 1

    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    payload["coverage"] = {
        "progol_draw": catalog.get("catalog_metadata", {}).get("draw_number"),
        "fixtures": len(fixtures),
        "context_items_added": added,
        "context_items_total": sum(1 for item in items if isinstance(item, dict) and _item_key(item) is not None),
    }

    if backup and path.exists():
        backup_path = path.with_suffix(f".{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.bak")
        shutil.copy2(path, backup_path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return payload["coverage"]


def _contest_type_for_week_type(week_type: str) -> str:
    if week_type == "midweek":
        return "progol_media_semana"
    if week_type == "revancha":
        return "progol_revancha"
    return "progol"


def _draw_number(draw_code: str) -> int:
    digits = "".join(ch for ch in str(draw_code or "") if ch.isdigit())
    return int(digits or 0)


def export_from_active_slates(path: Path, *, backup: bool = True) -> dict[str, Any]:
    from app.db import session as db_session
    from app.db.migrations import run_migrations
    from app.models.tables import MatchModel
    from app.models.tables import ProgolSlateMatchModel
    from app.models.tables import ProgolSlateModel
    from app.repositories.slate_repository import SlateRepository
    from app.services.slate_service import SlateService
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload

    run_migrations(db_session.engine)
    session = db_session.SessionLocal()
    try:
        slate_service = SlateService(SlateRepository(session))
        statement = (
            select(ProgolSlateModel)
            .where(ProgolSlateModel.is_archived.is_(False))
            .options(
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.competition),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.home_team),
                joinedload(ProgolSlateModel.matches)
                .joinedload(ProgolSlateMatchModel.match)
                .joinedload(MatchModel.away_team),
            )
        )
        slates = [
            slate
            for slate in session.scalars(statement).unique()
            if not slate_service.is_closed(slate)
        ]
        if not slates:
            raise ValueError("No active non-archived Progol slates found in DB.")

        items: list[dict[str, Any]] = []
        for slate in sorted(slates, key=lambda item: (item.week_type, item.draw_code)):
            draw_number = _draw_number(slate.draw_code)
            fixtures: list[dict[str, Any]] = []
            for link in sorted(slate.matches, key=lambda item: item.position):
                match = link.match
                fixtures.append(
                    {
                        "position": link.position,
                        "competition": match.competition.name,
                        "country": match.competition.country,
                        "season": match.competition.season,
                        "competition_is_placeholder": bool(match.competition.is_placeholder),
                        "home_team": match.home_team.name,
                        "home_country": match.home_team.country,
                        "home_is_placeholder": bool(match.home_team.is_placeholder),
                        "away_team": match.away_team.name,
                        "away_country": match.away_team.country,
                        "away_is_placeholder": bool(match.away_team.is_placeholder),
                        "kickoff_at": match.kickoff_at.isoformat(),
                        "venue": match.venue,
                    }
                )
            items.append(
                {
                    "title": slate.label,
                    "summary": f"{slate.draw_code} exportada desde slates activas en DB.",
                    "source_url": "db://progol_slates",
                    "catalog_metadata": {
                        "contest_type": _contest_type_for_week_type(slate.week_type),
                        "draw_number": draw_number,
                        "draw_code": slate.draw_code,
                        "match_count": len(fixtures),
                        "registration_closes_at": slate.registration_closes_at.isoformat()
                        if slate.registration_closes_at
                        else None,
                    },
                    "fixture_candidates": fixtures,
                }
            )

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "active_slates_db",
            "items": items,
            "coverage": {
                "active_slates": len(items),
                "draw_codes": [item["catalog_metadata"]["draw_code"] for item in items],
                "fixtures": sum(len(item["fixture_candidates"]) for item in items),
            },
        }
        if backup and path.exists():
            backup_path = path.with_suffix(f".{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.bak")
            shutil.copy2(path, backup_path)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return payload["coverage"]
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Update and validate the local Progol current context.")
    parser.add_argument("--path", type=Path, default=DEFAULT_CONTEXT_PATH)
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--from-db", action="store_true", help="Export current context from active slates in the configured DB.")
    args = parser.parse_args()
    coverage = (
        export_from_active_slates(args.path, backup=not args.no_backup)
        if args.from_db
        else enrich_context(args.path, backup=not args.no_backup)
    )
    print(json.dumps(coverage, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
