from pathlib import Path

import pytest


def _frontend_asset_text(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / "frontend" / name).read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_source_and_slate_prediction_workflow(client) -> None:
    source_response = await client.post(
        "/api/sources",
        json={
            "name": "Example Stats",
            "base_url": "https://example.com",
            "kind": "html_page",
            "is_active": True,
        },
    )
    assert source_response.status_code == 201

    slate_response = await client.post(
        "/api/slates",
        json={
            "label": "Progol 2301",
            "draw_code": "PG-2301",
            "week_type": "weekend",
            "matches": [
                {
                    "position": 1,
                    "competition": {
                        "name": "Liga MX",
                        "country": "Mexico",
                        "season": "2026-C",
                    },
                    "home_team": {"name": "Club A", "country": "Mexico"},
                    "away_team": {"name": "Club B", "country": "Mexico"},
                    "kickoff_at": "2026-05-16T20:00:00Z",
                    "venue": "Sample Stadium",
                }
            ],
        },
    )
    assert slate_response.status_code == 201
    slate_id = slate_response.json()["id"]

    prediction_response = await client.get(f"/api/predictions/slates/{slate_id}")
    assert prediction_response.status_code == 200
    assert len(prediction_response.json()) == 1

    feature_response = await client.get(f"/api/predictions/slates/{slate_id}/features")
    assert feature_response.status_code == 200
    assert feature_response.json()[0]["payload"]["venue_known"] is True

    worker_probe = await client.get(f"/api/predictions/slates/{slate_id}")
    assert worker_probe.status_code == 200


@pytest.mark.anyio
async def test_source_creation_rejects_insecure_or_unsupported_inputs(client) -> None:
    localhost_response = await client.post(
        "/api/sources",
        json={
            "name": "Local Feed",
            "base_url": "https://127.0.0.1/private.json",
            "kind": "json_feed",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert localhost_response.status_code == 422
    assert "Private or non-routable source hosts" in localhost_response.json()["detail"]

    invalid_kind_response = await client.post(
        "/api/sources",
        json={
            "name": "Odd Feed",
            "base_url": "https://example.com/feed.json",
            "kind": "shell_script",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert invalid_kind_response.status_code == 422
    assert "Unsupported source kind" in invalid_kind_response.json()["detail"]

    invalid_pair_response = await client.post(
        "/api/sources",
        json={
            "name": "Bad Pair Feed",
            "base_url": "https://example.com/feed.csv",
            "kind": "csv_feed",
            "parser_profile": "sports_feed_v1",
            "is_active": True,
        },
    )
    assert invalid_pair_response.status_code == 422
    assert "is not supported for kind" in invalid_pair_response.json()["detail"]


@pytest.mark.anyio
async def test_provider_bootstrap_rejects_private_feed_urls(client) -> None:
    response = await client.post(
        "/api/sources/providers/bootstrap",
        json={
            "source_name": "Private Injury Feed",
            "provider_id": "injury-feed-json",
            "feed_url": "https://localhost/injuries.json",
        },
    )

    assert response.status_code == 422
    assert "Input should be a valid URL" in response.text or "Local or internal source hosts are not allowed." in response.text


@pytest.mark.anyio
async def test_frontend_shell_supports_eight_match_slate(client) -> None:
    matches = []
    for idx in range(8):
        matches.append(
            {
                "position": idx + 1,
                "competition": {
                    "name": f"League {idx}",
                    "country": "Global",
                    "season": "2026",
                },
                "home_team": {"name": f"Home {idx}", "country": "Global"},
                "away_team": {"name": f"Away {idx}", "country": "Global"},
                "kickoff_at": f"2026-05-{16 + (idx // 2):02d}T2{idx % 4}:00:00Z",
                "venue": f"Venue {idx}",
            }
        )

    slate_response = await client.post(
        "/api/slates",
        json={
            "label": "Progol Visual 8",
            "draw_code": "PG-VIS-8",
            "week_type": "weekend",
            "matches": matches,
        },
    )
    assert slate_response.status_code == 201
    slate_id = slate_response.json()["id"]

    prediction_response = await client.get(f"/api/predictions/slates/{slate_id}")
    feature_response = await client.get(f"/api/predictions/slates/{slate_id}/features")
    ticket_response = await client.get(f"/api/predictions/slates/{slate_id}/ticket")
    quality_response = await client.get(f"/api/predictions/slates/{slate_id}/quality")
    calibration_response = await client.post(
        "/api/training/models/evaluate/calibration",
        json={"min_training_matches": 1, "confidence_threshold": 0.5},
    )
    page_response = await client.get("/")
    config_js = _frontend_asset_text("config.js")
    ui_utils_js = _frontend_asset_text("ui-utils.js")
    api_client_js = _frontend_asset_text("api-client.js")
    app_js = _frontend_asset_text("app.js")
    styles_css = _frontend_asset_text("styles.css")

    assert prediction_response.status_code == 200
    assert len(prediction_response.json()) == 8
    assert feature_response.status_code == 200
    assert len(feature_response.json()) == 8
    assert ticket_response.status_code == 200
    ticket_payload = ticket_response.json()
    assert ticket_payload["snapshot_id"]
    assert len(ticket_payload["recommendations"]) == 8
    assert set(ticket_payload["recommendations"][0]["decisions"]) == {"simple", "doubles", "full"}
    assert ticket_payload["recommendations"][0]["validation"]["label"]
    assert quality_response.status_code == 200
    assert len(quality_response.json()) == 8
    assert "quality_score" in quality_response.json()[0]
    assert calibration_response.status_code == 200
    assert "bins" in calibration_response.json()
    assert page_response.status_code == 200
    assert "Quiniela inteligente" in page_response.text
    assert "?v=" in page_response.text
    assert "config.js" in page_response.text
    assert "ui-utils.js" in page_response.text
    assert "api-client.js" in page_response.text
    assert "login-form" in page_response.text
    assert "auth-password" in page_response.text
    assert 'key: "simple"' in config_js
    assert 'key: "doubles"' in config_js
    assert 'key: "full"' in config_js
    assert "function displayPicks" in ui_utils_js
    assert "function safeFetch" in api_client_js
    assert "function loginWithPassword" in api_client_js
    # Outcomes are rendered as L/E/V via displayOutcome, never positional 1/X/2.
    assert "displayOutcome(key)" in app_js
    assert "<strong>1</strong>" not in app_js
    assert "doubleLimitForSlate" in app_js
    assert "chooseModelDoubleMatchIds" in app_js
    assert "ticketRecommendationFor" in app_js
    # Fase 3 UI/UX: semantic decision panel. Señal base / Estrategia / Riesgo
    # are distinct; the ambiguous "Fijo" type badge is gone; clean prob bars.
    assert "badge-signal" in app_js
    assert "prob-bar" in app_js
    assert "Acción recomendada" in app_js
    # Confidence headline now uses the degraded presentation band (never shows
    # "Alta" on a capped/flagged pick); wired via headlineConfidence().
    assert "headlineConfidence" in app_js
    assert "dh-badge-type" not in app_js
    # Fase 3.1: strategy comes from the backend field (resolveTicketStrategy);
    # counters use product fields, NOT raw confidence_band; the per-card tech
    # accordion has a guard so it never selects the card.
    assert "resolveTicketStrategy" in app_js
    assert "isTechAccordionTarget" in app_js
    assert 'confidence_band === "high"' not in app_js
    assert "Fijo defendible" not in app_js
    # Fase 3.3: prob bars are CSP-safe — NO inline style attributes (style-src
    # 'self' blocks them); width comes from a discrete .w-N class.
    assert 'style="width:' not in app_js
    assert 'style="margin-top' not in app_js
    assert "probBarWidthClass" in app_js
    # And the CSS provides the discrete width classes.
    assert ".prob-bar-fill.w-60" in styles_css
    assert ".prob-bar-fill.w-100" in styles_css
    assert "Calidad de datos" in app_js
    assert "Estado de producción" in app_js
    assert "rutas HTTP cerradas" in app_js
    assert "match-menu" in page_response.text
    assert "ticket-tabs" in page_response.text
    assert "ops-panel" in page_response.text
    assert "risk-high" in styles_css


@pytest.mark.anyio
async def test_refresh_current_progol_from_local_context(client, tmp_path, monkeypatch) -> None:
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    kickoff_at = (now + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    closes_at = (now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    context_root = tmp_path / "progol_context"
    context_root.mkdir()
    context_file = context_root / "current.json"
    context_file.write_text(
        f"""
        {{
          "items": [
            {{
              "title": "Progol 2999",
              "summary": "Concurso vigente",
              "catalog_metadata": {{
                "contest_type": "progol",
                "draw_number": 2999,
                "match_count": 1,
                "registration_closes_at": "{closes_at}"
              }},
              "fixture_candidates": [
                {{
                  "position": 1,
                  "competition": "LaLiga",
                  "country": "Spain",
                  "season": "2025-26",
                  "home_team": "R. Sociedad",
                  "away_team": "Ath. Bilbao",
                  "kickoff_at": "{kickoff_at}",
                  "venue": "Anoeta"
                }}
              ]
            }}
          ]
        }}
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("PROAI_LOCAL_CONTEXT_ROOT", str(context_root))

    response = await client.post(
        "/api/slates/current/refresh",
        json={"source_name": "Test Current Context", "local_path": "current.json"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["draw_code"] == "PG-2999"
    assert payload["match_count"] == 1
    assert payload["prediction_count"] == 1
    assert payload["ingestion_status"] == "completed"
    assert payload["step_durations_ms"]["ingest_context_document"] >= 0

    slate_response = await client.get("/api/slates")
    assert slate_response.status_code == 200
    slates = slate_response.json()
    assert slates[0]["draw_code"] == "PG-2999"
    assert slates[0]["matches"][0]["home_team_name"] == "R. Sociedad"

    readiness_response = await client.get(f"/api/slates/{payload['slate_id']}/readiness-report")
    assert readiness_response.status_code == 200
    readiness_payload = readiness_response.json()
    assert readiness_payload["mode"] == "slate_readiness_report"
    assert readiness_payload["slates"][0]["draw_code"] == "PG-2999"
    assert readiness_payload["slates"][0]["matches"][0]["flags"]
    assert "actionable_blockers" in readiness_payload["slates"][0]["matches"][0]


def test_current_progol_rejects_stale_local_context() -> None:
    from datetime import datetime, timedelta, timezone

    from app.services.current_progol_service import CurrentProgolService

    now = datetime.now(timezone.utc)
    stale = {
        "catalog_metadata": {
            "contest_type": "progol_media_semana",
            "draw_number": 797,
            "registration_closes_at": (now - timedelta(days=5)).isoformat(),
        },
        "fixture_candidates": [
            {
                "position": 1,
                "competition": "Liga MX",
                "home_team": "A",
                "away_team": "B",
                "kickoff_at": (now - timedelta(days=4)).isoformat(),
            }
        ],
    }
    service = CurrentProgolService.__new__(CurrentProgolService)

    with pytest.raises(ValueError, match="No active or future Progol item"):
        service._select_current_progol_item([stale])


def test_current_progol_fixture_preserves_placeholder_flags() -> None:
    from datetime import datetime, timedelta, timezone

    from app.services.current_progol_service import CurrentProgolService

    service = CurrentProgolService.__new__(CurrentProgolService)
    match = service._fixture_to_match(
        1,
        {
            "position": 1,
            "competition": "Progol Concurso 9999",
            "competition_is_placeholder": True,
            "home_team": "Placeholder FC",
            "home_is_placeholder": True,
            "away_team": "Canonical FC",
            "away_is_placeholder": False,
            "kickoff_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )

    assert match.competition.is_placeholder is True
    assert match.home_team.is_placeholder is True
    assert match.away_team.is_placeholder is False


@pytest.mark.anyio
async def test_discover_slate_from_catalog_and_fixture_sources(client) -> None:
    from datetime import datetime, timezone, timedelta

    from app.connectors.base import ConnectorMetadata
    from app.connectors.base import SourceDocument
    from app.connectors.registry import connector_registry

    class CatalogConnector:
        name = "TuLotero Progol Catalog"
        kind = "progol_catalog_html"
        base_url = "https://tulotero.mx/progol/"
        description = "Catalog stub."

        def metadata(self):
            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self):
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": "Progol Catalog",
                        "summary": "14 partidos con local empate visitante",
                        "headings": ["Progol"],
                        "catalog_metadata": {"contest_type": "progol", "match_count": 14},
                    },
                )
            ]

    class FixtureConnector:
        name = "Fixture Feed"
        kind = "json_feed"
        base_url = "https://example.com/fixtures.json"
        description = "Fixture stub."

        def metadata(self):
            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self):
            base_time = datetime.now(timezone.utc) + timedelta(hours=1)
            documents = []
            for idx in range(14):
                documents.append(
                    SourceDocument(
                        source_name=self.name,
                        source_url=self.base_url,
                        captured_at=datetime.now(timezone.utc),
                        payload={
                            "title": f"League {idx} Club {idx}A vs Club {idx}B",
                            "summary": f"Club {idx}A vs Club {idx}B",
                            "headings": [f"League {idx}"],
                            "fixture_candidates": [
                                {
                                    "competition": f"League {idx}",
                                    "country": "Global",
                                    "season": "2026",
                                    "home_team": f"Club {idx}A",
                                    "away_team": f"Club {idx}B",
                                    "kickoff_at": (base_time + timedelta(hours=idx)).isoformat(),
                                    "venue": f"Venue {idx}",
                                }
                            ],
                        },
                    )
                )
            return documents

    catalog_source = await client.post(
        "/api/sources/providers/bootstrap",
        json={
            "source_name": "TuLotero Progol Catalog",
            "provider_id": "tulotero-progol-catalog",
        },
    )
    fixture_source = await client.post(
        "/api/sources",
        json={
            "name": "Fixture Feed",
            "base_url": "https://example.com/fixtures.json",
            "kind": "json_feed",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert catalog_source.status_code == 201
    assert fixture_source.status_code == 201

    catalog_source_id = catalog_source.json()["id"]
    fixture_source_id = fixture_source.json()["id"]
    connector_registry.register(CatalogConnector())
    connector_registry.register(FixtureConnector())

    catalog_run = await client.post("/api/ingestion/runs", json={"source_id": catalog_source_id})
    fixture_run = await client.post("/api/ingestion/runs", json={"source_id": fixture_source_id})
    assert catalog_run.status_code == 201
    assert fixture_run.status_code == 201

    discovery = await client.post(
        "/api/slates/discover",
        json={
            "catalog_source_id": catalog_source_id,
            "fixture_source_ids": [fixture_source_id],
            "label": "Progol 2400",
            "draw_code": "PG-2400",
            "week_type": "weekend",
        },
    )
    assert discovery.status_code == 201
    body = discovery.json()
    assert body["match_target"] == 14
    assert len(body["matches"]) == 14
    assert body["persisted_slate_id"] is not None


@pytest.mark.anyio
async def test_refresh_slate_auto_ingests_and_upserts(client) -> None:
    from datetime import datetime, timezone, timedelta

    from app.connectors.base import ConnectorMetadata
    from app.connectors.base import SourceDocument
    from app.connectors.registry import connector_registry

    class AutoCatalogConnector:
        name = "Auto Catalog"
        kind = "progol_catalog_html"
        base_url = "https://tulotero.mx/progol-media-semana/"
        description = "Catalog stub."

        def metadata(self):
            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self):
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": "Progol Media Semana Catalog",
                        "summary": "9 partidos",
                        "headings": ["Media Semana"],
                        "catalog_metadata": {"contest_type": "progol_media_semana", "match_count": 9},
                    },
                )
            ]

    class AutoFixtureConnector:
        name = "Auto Fixture Feed"
        kind = "json_feed"
        base_url = "https://example.com/auto-fixtures.json"
        description = "Fixture stub."

        def metadata(self):
            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self):
            base_time = datetime.now(timezone.utc) + timedelta(hours=1)
            documents = []
            for idx in range(9):
                documents.append(
                    SourceDocument(
                        source_name=self.name,
                        source_url=self.base_url,
                        captured_at=datetime.now(timezone.utc),
                        payload={
                            "title": f"Cup {idx} Team {idx}A vs Team {idx}B",
                            "summary": f"Team {idx}A vs Team {idx}B",
                            "fixture_candidates": [
                                {
                                    "competition": f"Cup {idx}",
                                    "country": "World",
                                    "season": "2026",
                                    "home_team": f"Team {idx}A",
                                    "away_team": f"Team {idx}B",
                                    "kickoff_at": (base_time + timedelta(hours=idx)).isoformat(),
                                    "venue": f"Ground {idx}",
                                }
                            ],
                        },
                    )
                )
            return documents

    catalog_source = await client.post(
        "/api/sources/providers/bootstrap",
        json={
            "source_name": "Auto Catalog",
            "provider_id": "tulotero-progol-media-semana-catalog",
            "feed_url": "https://tulotero.mx/progol-media-semana/",
        },
    )
    fixture_source = await client.post(
        "/api/sources",
        json={
            "name": "Auto Fixture Feed",
            "base_url": "https://example.com/auto-fixtures.json",
            "kind": "json_feed",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert catalog_source.status_code == 201
    assert fixture_source.status_code == 201

    catalog_source_id = catalog_source.json()["id"]
    fixture_source_id = fixture_source.json()["id"]
    connector_registry.register(AutoCatalogConnector())
    connector_registry.register(AutoFixtureConnector())

    refresh = await client.post(
        "/api/slates/refresh",
        json={
            "catalog_source_id": catalog_source_id,
            "fixture_source_ids": [fixture_source_id],
            "discovery": {
                "label": "Progol MS Auto",
                "draw_code": "PGM-AUTO",
                "week_type": "midweek",
            },
        },
    )
    assert refresh.status_code == 201
    body = refresh.json()
    assert len(body["ingested_source_ids"]) == 2
    assert body["discovery"]["persisted_slate_id"] is not None
    assert len(body["discovery"]["matches"]) == 9

    refresh_again = await client.post(
        "/api/slates/refresh",
        json={
            "catalog_source_id": catalog_source_id,
            "fixture_source_ids": [fixture_source_id],
            "discovery": {
                "label": "Progol MS Auto Updated",
                "draw_code": "PGM-AUTO",
                "week_type": "midweek",
            },
        },
    )
    assert refresh_again.status_code == 201
    assert refresh_again.json()["discovery"]["persisted_slate_id"] == body["discovery"]["persisted_slate_id"]


@pytest.mark.anyio
async def test_discovery_uses_active_draw_number_from_catalog_metadata(client) -> None:
    from datetime import datetime, timezone, timedelta

    from app.connectors.base import ConnectorMetadata
    from app.connectors.base import SourceDocument
    from app.connectors.registry import connector_registry

    class CurrentCatalogConnector:
        name = "Current Media Catalog"
        kind = "progol_catalog_html"
        base_url = "https://example.com/progol-media-semana"
        description = "Current catalog stub."

        def metadata(self):
            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self):
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": "Programa / Resultados Progol 1/2 Semana 796",
                        "summary": "9 partidos",
                        "headings": ["Progol 1/2 Semana 796"],
                        "catalog_metadata": {
                            "contest_type": "progol_media_semana",
                            "draw_number": 796,
                            "match_count": 9,
                        },
                    },
                )
            ]

    class CurrentFixtureConnector:
        name = "Current Media Fixtures"
        kind = "json_feed"
        base_url = "https://example.com/current-media-fixtures.json"
        description = "Current fixtures stub."

        def metadata(self):
            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self):
            base_time = datetime.now(timezone.utc) - timedelta(hours=1)
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": f"Media Semana 796 Team {idx}A vs Team {idx}B",
                        "summary": f"Team {idx}A vs Team {idx}B",
                        "fixture_candidates": [
                            {
                                "position": idx + 1,
                                "competition": "Media Semana",
                                "country": "World",
                                "season": "2026",
                                "home_team": f"Team {idx}A",
                                "away_team": f"Team {idx}B",
                                "kickoff_at": (base_time + timedelta(hours=8 - idx)).isoformat(),
                                "venue": f"Ground {idx}",
                            }
                        ],
                    },
                )
                for idx in range(9)
            ]

    catalog_source = await client.post(
        "/api/sources",
        json={
            "name": "Current Media Catalog",
            "base_url": "https://example.com/progol-media-semana",
            "kind": "progol_catalog_html",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    fixture_source = await client.post(
        "/api/sources",
        json={
            "name": "Current Media Fixtures",
            "base_url": "https://example.com/current-media-fixtures.json",
            "kind": "json_feed",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert catalog_source.status_code == 201
    assert fixture_source.status_code == 201

    connector_registry.register(CurrentCatalogConnector())
    connector_registry.register(CurrentFixtureConnector())
    catalog_source_id = catalog_source.json()["id"]
    fixture_source_id = fixture_source.json()["id"]

    await client.post("/api/ingestion/runs", json={"source_id": catalog_source_id})
    await client.post("/api/ingestion/runs", json={"source_id": fixture_source_id})

    discovery = await client.post(
        "/api/slates/discover",
        json={
            "catalog_source_id": catalog_source_id,
            "fixture_source_ids": [fixture_source_id],
            "week_type": "midweek",
        },
    )

    assert discovery.status_code == 201
    body = discovery.json()
    assert body["label"] == "Progol Media Semana 796"
    assert body["draw_code"] == "PGM-796"
    assert len(body["matches"]) == 9
    assert body["matches"][0]["home_team"]["name"] == "Team 0A"
    assert body["matches"][8]["home_team"]["name"] == "Team 8A"


@pytest.mark.anyio
async def test_list_slates_prioritizes_current_match_window(client) -> None:
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)

    async def create(draw_code: str, kickoff_at: datetime) -> None:
        response = await client.post(
            "/api/slates",
            json={
                "label": draw_code,
                "draw_code": draw_code,
                "week_type": "midweek",
                "matches": [
                    {
                        "position": 1,
                        "competition": {"name": "Window League", "country": "World", "season": "2026"},
                        "home_team": {"name": f"{draw_code} Home", "country": "World"},
                        "away_team": {"name": f"{draw_code} Away", "country": "World"},
                        "kickoff_at": kickoff_at.isoformat(),
                        "venue": "Window Ground",
                    }
                ],
            },
        )
        assert response.status_code == 201

    await create("PGM-795", now - timedelta(days=5))
    await create("PGM-975", now + timedelta(days=10))
    await create("PGM-796", now + timedelta(minutes=15))

    response = await client.get("/api/slates")

    assert response.status_code == 200
    assert response.json()[0]["draw_code"] == "PGM-796"


@pytest.mark.anyio
async def test_list_slates_hides_closed_registration_by_default(client) -> None:
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)

    async def create(draw_code: str, closes_at: datetime) -> None:
        response = await client.post(
            "/api/slates",
            json={
                "label": draw_code,
                "draw_code": draw_code,
                "week_type": "weekend",
                "registration_closes_at": closes_at.isoformat(),
                "matches": [
                    {
                        "position": 1,
                        "competition": {"name": "Close League", "country": "World", "season": "2026"},
                        "home_team": {"name": f"{draw_code} Home", "country": "World"},
                        "away_team": {"name": f"{draw_code} Away", "country": "World"},
                        "kickoff_at": (now + timedelta(days=3)).isoformat(),
                        "venue": "Close Ground",
                    }
                ],
            },
        )
        assert response.status_code == 201

    await create("PG-CLOSED", now - timedelta(hours=1))
    await create("PG-OPEN", now + timedelta(days=1))

    active_response = await client.get("/api/slates")
    all_response = await client.get("/api/slates?include_closed=true")

    assert active_response.status_code == 200
    assert [item["draw_code"] for item in active_response.json()] == ["PG-OPEN"]
    assert all_response.status_code == 200
    assert {item["draw_code"] for item in all_response.json()} == {"PG-CLOSED", "PG-OPEN"}
    assert next(item for item in all_response.json() if item["draw_code"] == "PG-CLOSED")["is_closed"] is True


@pytest.mark.anyio
async def test_discovery_supports_revancha_slate(client) -> None:
    from datetime import datetime, timezone, timedelta

    from app.connectors.base import ConnectorMetadata
    from app.connectors.base import SourceDocument
    from app.connectors.registry import connector_registry

    class RevanchaFixtureConnector:
        name = "Revancha Fixtures"
        kind = "json_feed"
        base_url = "https://example.com/revancha-fixtures.json"
        description = "Revancha fixture stub."

        def metadata(self):
            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self):
            base_time = datetime.now(timezone.utc) + timedelta(days=2)
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": "Progol Revancha 2334",
                        "summary": "7 partidos",
                        "catalog_metadata": {
                            "contest_type": "progol_revancha",
                            "draw_number": 2334,
                            "match_count": 7,
                            "registration_closes_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                        },
                        "fixture_candidates": [
                            {
                                "position": idx + 1,
                                "competition": "Revancha League",
                                "country": "World",
                                "season": "2026",
                                "home_team": f"R{idx} Home",
                                "away_team": f"R{idx} Away",
                                "kickoff_at": (base_time + timedelta(hours=idx)).isoformat(),
                            }
                            for idx in range(7)
                        ],
                    },
                )
            ]

    source_response = await client.post(
        "/api/sources",
        json={
            "name": "Revancha Fixtures",
            "base_url": "https://example.com/revancha-fixtures.json",
            "kind": "json_feed",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert source_response.status_code == 201
    connector_registry.register(RevanchaFixtureConnector())
    await client.post("/api/ingestion/runs", json={"source_id": source_response.json()["id"]})
    slates_after_ingestion = await client.get("/api/slates")
    assert any(item["draw_code"] == "PGR-2334" for item in slates_after_ingestion.json())

    discovery = await client.post(
        "/api/slates/discover",
        json={
            "fixture_source_ids": [source_response.json()["id"]],
            "week_type": "revancha",
        },
    )

    assert discovery.status_code == 201
    body = discovery.json()
    assert body["label"] == "Progol Revancha 2334"
    assert body["draw_code"] == "PGR-2334"
    assert body["match_target"] == 7
    assert len(body["matches"]) == 7


@pytest.mark.anyio
async def test_connector_and_normalization_endpoints(client) -> None:
    await client.post(
        "/api/sources",
        json={
            "name": "Connector Probe",
            "base_url": "https://example.org",
            "kind": "html_page",
            "is_active": True,
        },
    )

    connector_response = await client.get("/api/sources/connectors")
    assert connector_response.status_code == 200
    assert any(item["kind"] == "html_page" for item in connector_response.json())

    provider_response = await client.get("/api/sources/providers")
    assert provider_response.status_code == 200
    assert any(item["provider_id"] == "sports-feed-v1-json" for item in provider_response.json())
    assert any(item["provider_id"] == "football-data-uk-season-csv" for item in provider_response.json())
    assert any(item["provider_id"] == "injury-feed-json" for item in provider_response.json())
    assert any(item["provider_id"] == "disciplinary-feed-json" for item in provider_response.json())
    assert any(item["provider_id"] == "tulotero-progol-catalog" for item in provider_response.json())
    assert any(item["provider_id"] == "local-context-json" for item in provider_response.json())

    normalization_response = await client.post(
        "/api/normalization/preview",
        json={
            "team_name": "Club Deportivo Guadalajara",
            "competition_name": "Liga MX Apertura",
        },
    )
    assert normalization_response.status_code == 200
    assert normalization_response.json()[0]["normalized_value"] == "deportivo-guadalajara"


@pytest.mark.anyio
async def test_structured_availability_provider_bootstrap(client) -> None:
    response = await client.post(
        "/api/sources/providers/bootstrap",
        json={
            "source_name": "Liga MX Injury Wire",
            "provider_id": "injury-feed-json",
            "feed_url": "https://example.com/injuries.json",
        },
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "availability_json_feed"


@pytest.mark.anyio
async def test_progol_catalog_provider_bootstrap(client) -> None:
    response = await client.post(
        "/api/sources/providers/bootstrap",
        json={
            "source_name": "TuLotero Progol Catalog Bootstrap",
            "provider_id": "tulotero-progol-catalog",
        },
    )
    assert response.status_code == 201
    assert response.json()["kind"] == "progol_catalog_html"
