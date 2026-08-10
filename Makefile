PYTHON ?= .venv/bin/python
PYTEST_ARGS ?= backend/tests -q
PYTEST_FAST_ARGS ?= backend/tests -q -m "not integration and not slow"
PYTEST_INTEGRATION_ARGS ?= backend/tests -q -m "integration and not slow"
PYTEST_SLOW_ARGS ?= backend/tests -q -m "slow"

.PHONY: lint typecheck test test-fast test-integration test-slow test-all frontend-test frontend-smoke load-smoke check release-check up down restart rebuild logs health ready docker-up docker-build update-current-context update-current-context-from-db audit-current refresh-current ensure-current-job evaluate calibration production-check bootstrap-local-prod confidence-report confidence-report-docker

lint:
	$(PYTHON) -m ruff check backend frontend

typecheck:
	$(PYTHON) -m mypy --config-file backend/pyproject.toml backend/app

test:
	$(PYTHON) backend/scripts/run_pytest.py $(PYTEST_ARGS)

test-fast:
	$(PYTHON) backend/scripts/run_pytest.py $(PYTEST_FAST_ARGS)

test-integration:
	$(PYTHON) backend/scripts/run_pytest.py $(PYTEST_INTEGRATION_ARGS)

test-slow:
	$(PYTHON) backend/scripts/run_pytest.py $(PYTEST_SLOW_ARGS)

test-all: test

frontend-test:
	npm --prefix frontend test

frontend-smoke:
	$(PYTHON) backend/scripts/frontend_smoke.py

load-smoke:
	$(PYTHON) backend/scripts/http_load_smoke.py

check: lint typecheck test-fast

release-check: check test-integration test-slow frontend-test frontend-smoke production-check health
	docker compose ps

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose up -d --build

# Force-build both proai AND worker images. Both services use the same
# Dockerfile but compose tags them under separate names, so `docker
# compose build proai` alone leaves the worker on the previous build.
# Use this whenever you change backend code that the worker also runs.
rebuild:
	docker compose build proai worker
	docker compose up -d

logs:
	docker compose logs -f --tail=120

health:
	curl -fsS http://127.0.0.1:8000/api/health

ready:
	curl -fsS http://127.0.0.1:8000/api/ready

docker-up:
	docker compose up -d

docker-build:
	docker compose up -d --build

update-current-context:
	$(PYTHON) backend/scripts/update_current_context.py

update-current-context-from-db:
	docker compose run --rm --user root -e PYTHONPATH=/app/backend -v ./data/progol_context:/tmp/progol_context proai sh -c 'cd /app/backend && python scripts/update_current_context.py --from-db --path /tmp/progol_context/current.json'

audit-current:
	docker compose exec proai sh -c 'cd /app/backend && PYTHONPATH=/app/backend python scripts/audit_current_slates.py'

refresh-current:
	docker compose exec proai sh -c 'cd /app/backend && python -m app.cli refresh-current'

ensure-current-job:
	docker compose exec proai sh -c 'cd /app/backend && python -m app.cli ensure-current-job'

evaluate:
	docker compose exec proai sh -c 'cd /app/backend && python -m app.cli evaluate --mode competitions'

calibration:
	docker compose exec proai sh -c 'cd /app/backend && python -m app.cli evaluate --mode calibration'

publish-backtest:
	docker compose exec proai sh -c 'cd /app/backend && python -m app.cli publish-backtest'

# The report reads the API, which requires auth. Take the key from the
# environment when it is already exported, otherwise off .env, so the target
# does not depend on the caller having sourced anything.
confidence-report:
	@PROAI_AUTH_API_KEY="$${PROAI_AUTH_API_KEY:-$$(sed -n 's/^PROAI_AUTH_API_KEY=//p' .env | tr -d "\"'")}" \
		$(PYTHON) backend/scripts/current_progol_confidence_report.py

# Runs inside the SERVING container rather than a throwaway one, because the
# base URL has to satisfy TrustedHostMiddleware: PROAI_ALLOWED_HOSTS lists
# localhost/127.0.0.1, never the compose service name, so a separate container
# calling http://proai:8000 is answered 400 "Invalid host header" before the
# route is ever reached. From inside the serving container 127.0.0.1 is both
# reachable and an allowed Host, and PROAI_AUTH_API_KEY is already in its
# environment. The report is then copied out instead of being written through
# a root-owned bind mount.
confidence-report-docker:
	docker compose exec -T --workdir /app/backend proai \
		python scripts/current_progol_confidence_report.py \
		--base-url http://127.0.0.1:8000 --output /tmp/current_progol_confidence.md
	docker compose cp proai:/tmp/current_progol_confidence.md reports/current_progol_confidence.md

production-check:
	docker compose exec proai sh -c 'cd /app/backend && python -m app.cli production-check'

bootstrap-local-prod: restart ready production-check
