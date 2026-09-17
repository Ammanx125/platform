.PHONY: db-up db-down db-reset migrate revision downgrade dev

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