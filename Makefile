.PHONY: worker worker-once ci deploy-check

worker:
	python -m app.workers.main

ci:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e .
	$(PYTHON) -m pip install ruff mypy pytest pytest-asyncio
	ruff check .
	mypy app
	pytest -q
	$(PYTHON) -m alembic upgrade head
	$(PYTHON) -m compileall app

deploy-check:
	$(PYTHON) -m compileall app
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port 8001 > /tmp/sansa-smoke.log 2>&1 &
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do \
		curl -fsS http://127.0.0.1:8001/health >/tmp/sansa-health.txt && break || sleep 2; \
	done
	curl -fsS http://127.0.0.1:8001/health
	curl -fsS http://127.0.0.1:8001/health/db

# keep the old target for backwards compat if you like
worker-legacy:
	python -m app.workers.jobs

db-up:
	docker compose -f docker-compose.dev.yml up -d

db-down:
	docker compose -f docker-compose.dev.yml down

db-reset:
	docker compose -f docker-compose.dev.yml down -v
	docker compose -f docker-compose.dev.yml up -d

PYTHON := python
ifneq ($(wildcard .venv/Scripts/python.exe),)
PYTHON := .venv/Scripts/python.exe
else ifneq ($(wildcard .venv/bin/python),)
PYTHON := .venv/bin/python
endif

migrate:
	$(PYTHON) -m alembic upgrade head

revision:
	$(PYTHON) -m alembic revision --autogenerate -m "$(m)"

downgrade:
	$(PYTHON) -m alembic downgrade -1

dev:
	$(PYTHON) -m uvicorn app.main:app --reload

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app

test:
	pytest -q

test-concurrency:
	pytest -q -m concurrency