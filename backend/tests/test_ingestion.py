from datetime import datetime, timezone
import json

import pytest

from app.connectors.base import SourceDocument
from app.connectors.registry import connector_registry


class StubHtmlConnector:
    name = "Ingestion Source"
    kind = "html_page"
    base_url = "https://example.com"
    description = "Stub connector for ingestion tests."

    def metadata(self):
        from app.connectors.base import ConnectorMetadata

        return ConnectorMetadata(
            name=self.name,
            kind=self.kind,
            base_url=self.base_url,
            description=self.description,
        )

    def fetch(self) -> list[SourceDocument]:
        return [
            SourceDocument(
                source_name=self.name,
                source_url=self.base_url,
                captured_at=datetime.now(timezone.utc),
                payload={
                    "title": "Liga MX Matchday Preview",
                    "summary": "Club A vs Club B",
                    "headings": ["Club A vs Club B"],
                    "team_stats": [
                        {"team_name": "Club A", "stat_type": "form_points", "value": 8.0, "sample_size": 5}
                    ],
                },
            )
        ]


@pytest.mark.anyio
async def test_ingestion_run_workflow(client) -> None:
    create_source_response = await client.post(
        "/api/sources",
        json={
            "name": "Ingestion Source",
            "base_url": "https://example.com",
            "kind": "html_page",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert create_source_response.status_code == 201
    source_id = create_source_response.json()["id"]

    connector_registry.register(StubHtmlConnector())

    run_response = await client.post("/api/ingestion/runs", json={"source_id": source_id})
    assert run_response.status_code == 201
    assert run_response.json()["status"] == "completed"
    assert run_response.json()["documents_found"] == 1

    list_response = await client.get("/api/ingestion/runs")
    assert list_response.status_code == 200
    assert len(list_response.json()) >= 1

    metrics_response = await client.get("/api/metrics")
    assert metrics_response.status_code == 200
    assert 'proai_ingestion_runs_total{source="Ingestion Source",status="completed"}' in metrics_response.text

    slate_response = await client.post(
        "/api/slates",
        json={
            "label": "Progol 2303",
            "draw_code": "PG-2303",
            "week_type": "weekend",
            "matches": [
                {
                    "position": 1,
                    "competition": {"name": "Liga MX", "country": "Mexico", "season": "2026-C"},
                    "home_team": {"name": "Club A", "country": "Mexico"},
                    "away_team": {"name": "Club B", "country": "Mexico"},
                    "kickoff_at": "2026-05-18T20:00:00Z",
                    "venue": "Arena One",
                }
            ],
        },
    )
    assert slate_response.status_code == 201
    team_id = slate_response.json()["matches"][0]["match_id"]
    match_stats_response = await client.get(f"/api/stats/matches/{team_id}")
    assert match_stats_response.status_code == 200


@pytest.mark.anyio
async def test_scheduled_jobs_health_and_evidence_linking(client) -> None:
    class ScheduledConnector:
        name = "Scheduled Source"
        kind = "html_page"
        base_url = "https://example.com"
        description = "Scheduled stub connector."

        def metadata(self):
            from app.connectors.base import ConnectorMetadata

            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self) -> list[SourceDocument]:
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                    "title": "Liga MX Club A vs Club B Preview",
                    "summary": "Club A faces Club B in Liga MX.",
                    "headings": ["Liga MX", "Club A vs Club B"],
                    "fixtures": [
                        {
                            "competition": "Liga MX",
                            "home_team": "Club A",
                            "away_team": "Club B",
                            "played_at": "2026-05-10T20:00:00Z",
                            "home_goals": 2,
                            "away_goals": 1,
                        }
                    ],
                    "team_stats": [
                        {"team_name": "Club A", "stat_type": "shots_for", "value": 6.0, "sample_size": 3}
                    ],
                    "match_stats": [
                        {"stat_type": "expected_goals", "home_value": 1.7, "away_value": 0.9}
                    ],
                    "availability_reports": [
                        {
                            "team_name": "Club A",
                            "player_name": "Juan Perez",
                            "position": "forward",
                            "status": "out",
                            "category": "injury",
                            "detail": "Hamstring injury confirmed by club.",
                            "confidence": 0.92,
                            "impact_score": 0.88,
                        },
                        {
                            "team_name": "Club B",
                            "player_name": "Luis Gomez",
                            "position": "defender",
                            "status": "suspended",
                            "category": "suspension",
                            "detail": "Accumulated yellow cards.",
                            "confidence": 0.9,
                            "impact_score": 0.8,
                        },
                    ],
                    },
                )
            ]

    connector_registry.register(ScheduledConnector())

    source_response = await client.post(
        "/api/sources",
        json={
            "name": "Scheduled Source",
            "base_url": "https://example.com",
            "kind": "html_page",
            "parser_profile": "sports_feed_v1",
            "is_active": True,
        },
    )
    assert source_response.status_code == 201
    source_id = source_response.json()["id"]

    slate_response = await client.post(
        "/api/slates",
        json={
            "label": "Progol 2302",
            "draw_code": "PG-2302",
            "week_type": "midweek",
            "matches": [
                {
                    "position": 1,
                    "competition": {"name": "Liga MX", "country": "Mexico", "season": "2026-C"},
                    "home_team": {"name": "Club A", "country": "Mexico"},
                    "away_team": {"name": "Club B", "country": "Mexico"},
                    "kickoff_at": "2026-05-20T20:00:00Z",
                    "venue": "North Stadium",
                }
            ],
        },
    )
    assert slate_response.status_code == 201
    match_id = slate_response.json()["matches"][0]["match_id"]

    job_response = await client.post(
        "/api/scheduler/jobs",
        json={
            "source_id": source_id,
            "job_name": "scheduled-source-every-5m",
            "interval_minutes": 5,
            "next_run_at": "2026-05-10T12:00:00Z",
            "is_active": True,
        },
    )
    assert job_response.status_code == 201

    due_response = await client.post("/api/scheduler/jobs/run-due")
    assert due_response.status_code == 200
    assert len(due_response.json()) == 1
    assert due_response.json()[0]["status"] == "completed"

    health_response = await client.post(f"/api/scheduler/sources/{source_id}/health")
    assert health_response.status_code == 201
    assert health_response.json()["status"] == "healthy"
    assert "Connector fetch succeeded" in health_response.json()["detail"]

    evidence_response = await client.get(f"/api/evidence/matches/{match_id}")
    assert evidence_response.status_code == 200
    assert len(evidence_response.json()) >= 1
    assert evidence_response.json()[0]["source_title"] == "Liga MX Club A vs Club B Preview"
    assert evidence_response.json()[0]["context_summary"] == "Club A faces Club B in Liga MX."

    duplicate_run_response = await client.post("/api/ingestion/runs", json={"source_id": source_id})
    assert duplicate_run_response.status_code == 201
    assert duplicate_run_response.json()["status"] == "completed"
    deduped_evidence_response = await client.get(f"/api/evidence/matches/{match_id}")
    assert deduped_evidence_response.status_code == 200
    assert len(deduped_evidence_response.json()) == len(evidence_response.json())

    availability_response = await client.get(f"/api/availability/matches/{match_id}")
    assert availability_response.status_code == 200
    assert len(availability_response.json()) >= 2
    assert any(item["player_name"] == "Juan Perez" for item in availability_response.json())
    assert any(item["team_name"] == "Club A" for item in availability_response.json())

    match_stats_response = await client.get(f"/api/stats/matches/{match_id}")
    assert match_stats_response.status_code == 200
    assert len(match_stats_response.json()) >= 1

    train_response = await client.post("/api/training/models/train", json={"model_name": "elo_poisson_blend"})
    assert train_response.status_code == 201
    assert train_response.json()["model_name"] == "elo_poisson_blend"
    assert train_response.json()["artifact"]["feature_names"]

    result_response = await client.get(f"/api/results/matches/{match_id}")
    assert result_response.status_code == 200
    assert len(result_response.json()) >= 1

    context_result_response = await client.get(f"/api/results/matches/{match_id}/context")
    assert context_result_response.status_code == 200
    assert any(item["is_head_to_head"] for item in context_result_response.json())
    assert any(item["context_label"] == "Antecedente directo" for item in context_result_response.json())


@pytest.mark.anyio
async def test_local_context_fixture_ingestion_links_verified_context(
    client,
    tmp_path,
    monkeypatch,
) -> None:
    context_root = tmp_path / "progol_context"
    context_root.mkdir()
    context_file = context_root / "current.json"
    context_file.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "Liga MX Club Alpha vs Club Beta context",
                        "competition": "Liga MX",
                        "teams": ["Club Alpha", "Club Beta"],
                        "context_summary": "Club Alpha llega con baja confirmada y Club Beta sin suspendidos.",
                        "availability_reports": [
                            {
                                "team_name": "Club Alpha",
                                "player_name": "Forward One",
                                "status": "out",
                                "category": "injury",
                                "detail": "Muscle injury confirmed by club report.",
                                "confidence": 0.91,
                                "impact_score": 0.84,
                            }
                        ],
                        "historical_results": [
                            {
                                "competition_name": "Liga MX",
                                "home_team": "Club Alpha",
                                "away_team": "Club Beta",
                                "played_at": "2026-04-01T20:00:00Z",
                                "home_goals": 1,
                                "away_goals": 2,
                            }
                        ],
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PROAI_LOCAL_CONTEXT_ROOT", str(context_root))

    source_response = await client.post(
        "/api/sources/providers/bootstrap",
        json={
            "source_name": "Local Context Fixture",
            "provider_id": "local-context-json",
            "local_path": "current.json",
        },
    )
    assert source_response.status_code == 201
    source_id = source_response.json()["id"]

    slate_response = await client.post(
        "/api/slates",
        json={
            "label": "Progol Local Fixture",
            "draw_code": "PG-LOCAL-FIXTURE",
            "week_type": "weekend",
            "matches": [
                {
                    "position": 1,
                    "competition": {"name": "Liga MX", "country": "Mexico", "season": "2026-C"},
                    "home_team": {"name": "Club Alpha", "country": "Mexico"},
                    "away_team": {"name": "Club Beta", "country": "Mexico"},
                    "kickoff_at": "2026-05-25T20:00:00Z",
                    "venue": "Production Test Arena",
                }
            ],
        },
    )
    assert slate_response.status_code == 201
    match_id = slate_response.json()["matches"][0]["match_id"]

    run_response = await client.post("/api/ingestion/runs", json={"source_id": source_id})
    assert run_response.status_code == 201
    assert run_response.json()["status"] == "completed"

    evidence_response = await client.get(f"/api/evidence/matches/{match_id}")
    availability_response = await client.get(f"/api/availability/matches/{match_id}")
    context_result_response = await client.get(f"/api/results/matches/{match_id}/context")

    assert evidence_response.status_code == 200
    assert any("Club Alpha llega" in item["context_summary"] for item in evidence_response.json())
    assert availability_response.status_code == 200
    assert any(item["player_name"] == "Forward One" for item in availability_response.json())
    assert context_result_response.status_code == 200
    assert any(item["is_head_to_head"] for item in context_result_response.json())

    worker_response = await client.post("/api/worker/scheduler/run-once")
    assert worker_response.status_code == 200

    worker_status_response = await client.get("/api/worker/scheduler/status")
    assert worker_status_response.status_code == 200
    assert "executed_runs" in worker_status_response.json()


@pytest.mark.anyio
async def test_source_health_reports_degraded_when_connector_fetch_fails(client) -> None:
    class FailingConnector:
        name = "Failing Source"
        kind = "html_page"
        base_url = "https://example.com/fail"
        description = "Connector that fails for health checks."

        def metadata(self):
            from app.connectors.base import ConnectorMetadata

            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self) -> list[SourceDocument]:
            raise TimeoutError("simulated timeout")

    connector_registry.register(FailingConnector())

    source_response = await client.post(
        "/api/sources",
        json={
            "name": "Failing Source",
            "base_url": "https://example.com/fail",
            "kind": "html_page",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert source_response.status_code == 201

    health_response = await client.post(f"/api/scheduler/sources/{source_response.json()['id']}/health")

    assert health_response.status_code == 201
    assert health_response.json()["status"] == "degraded"
    assert "TimeoutError" in health_response.json()["detail"]

    metrics_response = await client.get("/api/metrics")
    assert metrics_response.status_code == 200
    assert 'proai_source_health_checks_total{source="Failing Source",status="degraded"}' in metrics_response.text


@pytest.mark.anyio
async def test_historical_result_ingestion_reuses_match_identity_by_alias(client) -> None:
    class AliasResultConnector:
        name = "Alias Result Source"
        kind = "html_page"
        base_url = "https://example.com/alias-results"
        description = "Alias historical result connector."

        def metadata(self):
            from app.connectors.base import ConnectorMetadata

            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self) -> list[SourceDocument]:
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "title": "Pumas UNAM vs Pachuca recent result",
                        "summary": "Pumas UNAM 1-0 Pachuca",
                        "historical_results": [
                            {
                                "competition_name": "Resultados Ya Recent Form",
                                "home_team": "Pumas UNAM",
                                "away_team": "Pachuca",
                                "played_at": "2026-05-17T18:00:00Z",
                                "home_goals": 1,
                                "away_goals": 0,
                            }
                        ],
                    },
                )
            ]

    slate_response = await client.post(
        "/api/slates",
        json={
            "label": "Alias Result Slate",
            "draw_code": "PG-ALIAS-RESULT",
            "week_type": "midweek",
            "matches": [
                {
                    "position": 1,
                    "competition": {"name": "Resultados Ya Recent Form", "country": "Mexico", "season": "2026"},
                    "home_team": {"name": "Pumas", "country": "Mexico"},
                    "away_team": {"name": "Pachuca", "country": "Mexico"},
                    "kickoff_at": "2026-05-17T18:00:00Z",
                    "venue": None,
                }
            ],
        },
    )
    assert slate_response.status_code == 201
    match_id = slate_response.json()["matches"][0]["match_id"]

    source_response = await client.post(
        "/api/sources",
        json={
            "name": "Alias Result Source",
            "base_url": "https://example.com/alias-results",
            "kind": "html_page",
            "parser_profile": "generic",
            "is_active": True,
        },
    )
    assert source_response.status_code == 201
    connector_registry.register(AliasResultConnector())

    run_response = await client.post("/api/ingestion/runs", json={"source_id": source_response.json()["id"]})

    assert run_response.status_code == 201
    assert run_response.json()["status"] == "completed"
    results_response = await client.get(f"/api/results/matches/{match_id}")
    assert results_response.status_code == 200
    assert len(results_response.json()) == 1


@pytest.mark.anyio
async def test_provider_bootstrap_and_history_import(client) -> None:
    class CsvSeasonConnector:
        name = "Historical Season Source"
        kind = "football_data_uk_csv"
        base_url = "https://www.football-data.co.uk"
        description = "Historical season connector stub."

        def metadata(self):
            from app.connectors.base import ConnectorMetadata

            return ConnectorMetadata(
                name=self.name,
                kind=self.kind,
                base_url=self.base_url,
                description=self.description,
            )

        def fetch(self) -> list[SourceDocument]:
            return [
                SourceDocument(
                    source_name=self.name,
                    source_url=self.base_url,
                    captured_at=datetime.now(timezone.utc),
                    payload={
                        "fixtures": [
                            {
                                "competition": "E0",
                                "home_team": "Historic Home",
                                "away_team": "Historic Away",
                                "played_at": "2025-08-12T19:00:00Z",
                                "home_goals": 3,
                                "away_goals": 1,
                            }
                        ]
                    },
                )
            ]

    bootstrap_response = await client.post(
        "/api/sources/providers/bootstrap",
        json={
            "source_name": "Historical Season Source",
            "provider_id": "football-data-uk-season-csv",
            "season_path": "mmz4281/2425/E0.csv",
        },
    )
    assert bootstrap_response.status_code == 201
    source_id = bootstrap_response.json()["id"]

    connector_registry.register(CsvSeasonConnector())
    import_response = await client.post(f"/api/history/sources/{source_id}/import")
    assert import_response.status_code == 201
    assert import_response.json()["status"] == "completed"
