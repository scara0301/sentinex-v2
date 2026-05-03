# Sentinex

A red teaming platform with a Python monorepo backend and a web frontend.

## Structure

```
apps/
  api/          – REST API (FastAPI + Alembic)
  worker/       – Background task worker
  proxy/        – Proxy service
  mocks/        – Mock services for testing
  web/          – Web frontend (Node.js)
packages/
  core/         – Shared Python library
  sandbox-image/ – Docker sandbox image (LangChain)
infra/          – Docker Compose configs
tests/          – Unit, integration, and e2e test suites
```

## Prerequisites

- [uv](https://github.com/astral-sh/uv)
- [pnpm](https://pnpm.io)
- Docker

## Getting Started

```bash
# Start dev services (Postgres, Redis, etc.)
make dev-up

# Run database migrations
make migrate
```

## Development

```bash
make dev-logs       # Tail service logs
make dev-down       # Stop and remove dev containers
```

## Testing

```bash
make test-unit          # Unit tests
make test-integration   # Integration tests (requires dev services)
make test-e2e           # End-to-end tests
```

## Linting & Formatting

```bash
make lint   # ruff + mypy
make fmt    # ruff format
```

## Building

```bash
make build-sandbox   # Build the LangChain sandbox Docker image
```
