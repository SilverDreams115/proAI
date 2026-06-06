from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONTEXT_PATH = Path("data/progol_context/current.json")
ARTICLE_SOURCE_URL = "https://www.reporteindigo.com/amp/deportes/guia-de-la-quiniela-progol-2334-asi-van-los-pronosticos-20260519-0052.html"


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


def _select_current_progol(items: list[Any]) -> dict[str, Any]:
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("catalog_metadata")
        if not isinstance(metadata, dict):
            continue
        if metadata.get("contest_type") == "progol" and item.get("fixture_candidates"):
            candidates.append(item)
    if not candidates:
        raise ValueError("No Progol catalog item with fixture_candidates found.")
    return max(candidates, key=lambda item: int(item.get("catalog_metadata", {}).get("draw_number") or 0))


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Update and validate the local Progol current context.")
    parser.add_argument("--path", type=Path, default=DEFAULT_CONTEXT_PATH)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    coverage = enrich_context(args.path, backup=not args.no_backup)
    print(json.dumps(coverage, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
