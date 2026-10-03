.PHONY: worker worker-once

worker:
	python -m app.workers.main

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