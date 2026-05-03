.PHONY: dev-up dev-down dev-logs migrate test-unit test-integration test-e2e lint fmt build-sandbox

dev-up:
	docker compose -f infra/docker-compose.dev.yml up -d

dev-down:
	docker compose -f infra/docker-compose.dev.yml down -v

dev-logs:
	docker compose -f infra/docker-compose.dev.yml logs -f

migrate:
	cd apps/api && uv run alembic upgrade head

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v

test-e2e:
	uv run pytest tests/e2e -v

lint:
	uv run ruff check . && uv run mypy apps/ packages/

fmt:
	uv run ruff format .

build-sandbox:
	docker build -t sentinex/sandbox-langchain:latest packages/sandbox-image/
