PYTHON ?= .venv/bin/python
PYTEST_ARGS ?= -q

.PHONY: lint typecheck test frontend-smoke load-smoke check up down restart rebuild logs health ready docker-up docker-build update-current-context refresh-current ensure-current-job evaluate calibration production-check bootstrap-local-prod

lint:
	$(PYTHON) -m ruff check backend frontend

typecheck:
	$(PYTHON) -m mypy --config-file backend/pyproject.toml backend/app

test:
	$(PYTHON) backend/scripts/run_pytest.py $(PYTEST_ARGS)

frontend-smoke:
	$(PYTHON) backend/scripts/frontend_smoke.py

load-smoke:
	$(PYTHON) backend/scripts/http_load_smoke.py

check: lint typecheck test

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

confidence-report:
	.venv/bin/python backend/scripts/current_progol_confidence_report.py

production-check:
	docker compose exec proai sh -c 'cd /app/backend && python -m app.cli production-check'

bootstrap-local-prod: restart ready production-check
