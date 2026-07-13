import os
from ipaddress import ip_address

import pytest
from httpx import ASGITransport
from httpx import AsyncClient

SLOW_TEST_FILES = {
    "test_adaptive_retraining.py",
    "test_backtest_publisher.py",
    "test_e2e_prediction_pipeline.py",
    "test_expected_goals_service.py",
    "test_ingestion.py",
    "test_neural_baseline.py",
    "test_openapi_schema.py",
    "test_train_rating_experiment.py",
    "test_validate_rating_candidate.py",
    "test_worker_routes.py",
}

os.environ.setdefault("PROAI_DATABASE_URL", "sqlite:///./backend/data/test_bootstrap.db")
# Tests run inside a production-tagged container by default, which flips
# auth_required on and rejects every unauthenticated call. Force the
# environment back to "test" *before* anything else imports
# `app.core.settings` — `setdefault` is not enough because the
# container's PROAI_ENVIRONMENT=production is already set.
os.environ["PROAI_ENVIRONMENT"] = "test"
os.environ["PROAI_AUTH_REQUIRED"] = "false"
# TrustedHostMiddleware is configured from PROAI_ALLOWED_HOSTS; the
# container sets it to the deployed host list, which rejects the
# httpx test client (base_url=http://testserver). Reopen the
# allowlist for the test process only.
os.environ["PROAI_ALLOWED_HOSTS"] = "*"
# Worker routes are gated by PROAI_ENABLE_WORKER_ROUTES; the
# production deployment turns them off so the public API stays
# slim. Tests that exercise /api/worker/scheduler/run-once need
# them on regardless of the inherited PROAI_ENVIRONMENT.
os.environ["PROAI_ENABLE_WORKER_ROUTES"] = "true"


def pytest_collection_modifyitems(items):
    for item in items:
        if item.get_closest_marker("anyio"):
            item.add_marker(pytest.mark.integration)
        if item.path.name in SLOW_TEST_FILES:
            item.add_marker(pytest.mark.slow)


@pytest.fixture(autouse=True)
def deterministic_source_dns(monkeypatch):
    def fake_resolve_host_ips(hostname: str):
        try:
            return [ip_address(hostname)]
        except ValueError:
            pass
        if hostname in {"localhost", "0.0.0.0"} or hostname.endswith(".local") or hostname.endswith(".internal"):
            return [ip_address("127.0.0.1")]
        return [ip_address("93.184.216.34")]

    monkeypatch.setattr("app.connectors.http._resolve_host_ips", fake_resolve_host_ips)


@pytest.fixture(autouse=True)
def reset_prediction_cache():
    # The PredictionService keeps a module-level TTL cache keyed by
    # slate.id. Tests routinely reuse the same fixture id ("slate-1"),
    # so without a per-test reset later tests would observe a previous
    # run's cached response instead of recomputing against their own
    # fixtures.
    from app.services.prediction_service import invalidate_slate_prediction_cache

    invalidate_slate_prediction_cache()
    yield
    invalidate_slate_prediction_cache()


@pytest.fixture
async def client(tmp_path):
    from app.connectors.registry import connector_registry
    from app.db.session import configure_session
    from app.main import app
    from app.parsers.registry import parser_registry
    from app.workers.scheduler_worker import worker

    test_database_path = tmp_path / "test_proai.db"
    configure_session(f"sqlite:///{test_database_path}")
    connector_registry.clear()
    parser_registry.reset()
    worker._state.executed_runs = 0
    worker._state.last_polled_at = None
    worker._state.last_executed_at = None

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as test_client:
            yield test_client
