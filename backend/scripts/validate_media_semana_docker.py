from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


API_BASE_URL = os.getenv("PROAI_VALIDATION_API_BASE_URL", "http://127.0.0.1:8000/api").rstrip("/")
API_KEY = os.getenv("PROAI_VALIDATION_API_KEY", "local-dev-secret")
HTTP_TIMEOUT_SECONDS = float(os.getenv("PROAI_VALIDATION_HTTP_TIMEOUT", "180"))
SKIP_HISTORY_IMPORT = os.getenv("PROAI_SKIP_HISTORY_IMPORT", "").strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class HistoricalSource:
    source_name: str
    season_path: str


HISTORICAL_SOURCES = [
    HistoricalSource("Historical EPL 2023-24", "mmz4281/2324/E0.csv"),
    HistoricalSource("Historical EPL 2024-25", "mmz4281/2425/E0.csv"),
    HistoricalSource("Historical LaLiga 2024-25", "mmz4281/2425/SP1.csv"),
    HistoricalSource("Historical SerieA 2024-25", "mmz4281/2425/I1.csv"),
    HistoricalSource("Historical Bundesliga 2024-25", "mmz4281/2425/D1.csv"),
]

MEDIA_SEMANA_795_MATCHES = [
    {
        "position": 1,
        "competition": {"name": "Liga MX", "country": "Mexico", "season": "2025-26"},
        "home_team": {"name": "Pachuca", "country": "Mexico"},
        "away_team": {"name": "Pumas", "country": "Mexico"},
        "kickoff_at": "2026-05-14T01:00:00Z",
        "venue": "Estadio Hidalgo",
    },
    {
        "position": 2,
        "competition": {"name": "Liga MX", "country": "Mexico", "season": "2025-26"},
        "home_team": {"name": "C. Azul", "country": "Mexico"},
        "away_team": {"name": "Guadalajara", "country": "Mexico"},
        "kickoff_at": "2026-05-14T03:00:00Z",
        "venue": "Ciudad de los Deportes",
    },
    {
        "position": 3,
        "competition": {"name": "La Liga", "country": "Spain", "season": "2025-26"},
        "home_team": {"name": "Espanyol", "country": "Spain"},
        "away_team": {"name": "Ath. Bilbao", "country": "Spain"},
        "kickoff_at": "2026-05-13T19:00:00Z",
        "venue": "RCDE Stadium",
    },
    {
        "position": 4,
        "competition": {"name": "La Liga", "country": "Spain", "season": "2025-26"},
        "home_team": {"name": "Valencia", "country": "Spain"},
        "away_team": {"name": "Rayo Vallec", "country": "Spain"},
        "kickoff_at": "2026-05-14T17:00:00Z",
        "venue": "Mestalla",
    },
    {
        "position": 5,
        "competition": {"name": "La Liga", "country": "Spain", "season": "2025-26"},
        "home_team": {"name": "Girona", "country": "Spain"},
        "away_team": {"name": "R. Sociedad", "country": "Spain"},
        "kickoff_at": "2026-05-14T19:30:00Z",
        "venue": "Montilivi",
    },
    {
        "position": 6,
        "competition": {"name": "Ligue 1", "country": "France", "season": "2025-26"},
        "home_team": {"name": "Brest", "country": "France"},
        "away_team": {"name": "Estrasburgo", "country": "France"},
        "kickoff_at": "2026-05-13T18:30:00Z",
        "venue": "Francis-Le Ble",
    },
    {
        "position": 7,
        "competition": {"name": "Ligue 1", "country": "France", "season": "2025-26"},
        "home_team": {"name": "Lens", "country": "France"},
        "away_team": {"name": "Paris SG", "country": "France"},
        "kickoff_at": "2026-05-13T19:00:00Z",
        "venue": "Bollaert-Delelis",
    },
    {
        "position": 8,
        "competition": {"name": "MLS", "country": "United States", "season": "2026"},
        "home_team": {"name": "Cincinnati", "country": "United States"},
        "away_team": {"name": "Miami", "country": "United States"},
        "kickoff_at": "2026-05-14T00:30:00Z",
        "venue": "TQL Stadium",
    },
    {
        "position": 9,
        "competition": {"name": "MLS", "country": "United States", "season": "2026"},
        "home_team": {"name": "Seattle", "country": "United States"},
        "away_team": {"name": "San Jose", "country": "United States"},
        "kickoff_at": "2026-05-14T02:30:00Z",
        "venue": "Lumen Field",
    },
]

MEDIA_SEMANA_795_RESULTS = ["1", "X", "1", "X", "X", "2", "2", "2", "1"]


def api_request(method: str, path: str, payload: dict | None = None) -> object:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE_URL}{path}",
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-API-Key": API_KEY,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with {exc.code}: {detail}") from exc


def ensure_historical_source(source: HistoricalSource) -> str:
    sources = api_request("GET", "/sources")
    assert isinstance(sources, list)
    for item in sources:
        if item["name"] == source.source_name:
            return item["id"]
    created = api_request(
        "POST",
        "/sources/providers/bootstrap",
        {
            "source_name": source.source_name,
            "provider_id": "football-data-uk-season-csv",
            "season_path": source.season_path,
        },
    )
    return created["id"]


def import_history() -> None:
    for source in HISTORICAL_SOURCES:
        source_id = ensure_historical_source(source)
        print(f"Importing history: {source.source_name} ({source_id})")
        api_request("POST", f"/history/sources/{source_id}/import")


def train_model() -> object:
    return api_request("POST", "/training/models/train", {"model_name": "elo_poisson_blend"})


def create_validation_slate() -> str:
    existing = api_request("GET", "/slates")
    assert isinstance(existing, list)
    for item in existing:
        if item["draw_code"] == "PG-MS-795-VALIDATION":
            return item["id"]
    created = api_request(
        "POST",
        "/slates",
        {
            "label": "Progol Media Semana 795 Validation",
            "draw_code": "PG-MS-795-VALIDATION",
            "week_type": "midweek",
            "matches": MEDIA_SEMANA_795_MATCHES,
        },
    )
    return created["id"]


def recommended_symbol(recommended_outcome: str) -> str:
    return {"1": "1", "X": "X", "2": "2"}.get(recommended_outcome, recommended_outcome)


def validate_predictions() -> dict[str, object]:
    slate_id = create_validation_slate()
    predictions = api_request("GET", f"/predictions/slates/{slate_id}")
    assert isinstance(predictions, list)

    hits = 0
    allowed_hits = 0
    allowed_total = 0
    rows = []
    for prediction, actual in zip(predictions, MEDIA_SEMANA_795_RESULTS, strict=True):
        predicted = recommended_symbol(prediction["recommended_outcome"])
        live_pick_allowed = bool(prediction["live_pick_allowed"])
        hit = predicted == actual
        hits += int(hit)
        if live_pick_allowed:
            allowed_total += 1
            allowed_hits += int(hit)
        rows.append(
            {
                "position": prediction["position"],
                "match": f"{prediction['home_team_name']} vs {prediction['away_team_name']}",
                "competition": prediction["competition_name"],
                "predicted": predicted,
                "actual": actual,
                "hit": hit,
                "live_pick_allowed": live_pick_allowed,
                "competition_readiness": prediction["competition_readiness"],
                "confidence_band": prediction["confidence_band"],
            }
        )

    return {
        "matches": rows,
        "hit_rate": round(hits / len(predictions), 4),
        "hits": hits,
        "total": len(predictions),
        "allowed_hit_rate": round(allowed_hits / allowed_total, 4) if allowed_total else None,
        "allowed_hits": allowed_hits,
        "allowed_total": allowed_total,
    }


def main() -> int:
    if not SKIP_HISTORY_IMPORT:
        import_history()
    train_summary = train_model()
    validation_summary = validate_predictions()
    print(
        json.dumps(
            {
                "training_sample_size": train_summary["training_sample_size"],
                "validation": validation_summary,
            },
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
