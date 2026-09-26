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

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

downgrade:
	alembic downgrade -1
dev:
	uvicorn app.main:app --reload
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