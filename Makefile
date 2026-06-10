.PHONY: dev-up dev-down dev-logs migrate test-unit test-integration test-e2e \
	lint fmt build-sandbox build-scan-images

# Per-scan images (proxy, mocks, seeded mock-db) are launched by the worker
# via the Docker socket, so they must exist on the host before scans run.
build-scan-images:
	docker build -f apps/proxy/Dockerfile -t sentinex/proxy:latest .
	docker build -t sentinex/mocks:latest apps/mocks
	docker build -f packages/sandbox-image/Dockerfile.mockdb -t sentinex/mock-db:latest packages/sandbox-image

dev-up: build-scan-images
	mkdir -p .data/uploads
	docker compose -f infra/docker-compose.dev.yml up -d --build

dev-down:
	docker compose -f infra/docker-compose.dev.yml down -v

dev-logs:
	docker compose -f infra/docker-compose.dev.yml logs -f

migrate:
	cd packages/core && uv run alembic upgrade head

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
	docker build --target langchain -t sentinex/sandbox-langchain:latest packages/sandbox-image/
	docker build --target mcp -t sentinex/sandbox-mcp:latest packages/sandbox-image/
	docker build --target crewai -t sentinex/sandbox-crewai:latest packages/sandbox-image/
	docker build --target autogen -t sentinex/sandbox-autogen:latest packages/sandbox-image/
	docker build --target raw -t sentinex/sandbox-raw_python:latest packages/sandbox-image/
