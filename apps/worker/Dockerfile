# Build from the repo root so the sentinex-core workspace package is in
# context:  docker build -f apps/worker/Dockerfile -t sentinex/worker:latest .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Native libraries required by WeasyPrint (Sprint 4 PDF reports).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libharfbuzz-subset0 \
    libffi8 \
    shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.17

COPY packages/core packages/core
COPY apps/worker apps/worker
# --no-sources: each app's pyproject declares
# `sentinex-core = { workspace = true }`, but the build context here holds only
# this app plus packages/core — the root pyproject that defines the uv
# workspace is absent, so uv fails with "references a workspace ... but is not
# a workspace member". Ignoring tool.uv.sources resolves sentinex-core from the
# editable path installed in the same command instead.
RUN uv pip install --system --no-cache --no-sources -e ./packages/core -e ./apps/worker

RUN mkdir -p /data/uploads /data/reports

# Runs as root: the worker drives the host Docker daemon through the socket
# mounted at /var/run/docker.sock.
CMD ["arq", "sentinex_worker.main.WorkerSettings"]
