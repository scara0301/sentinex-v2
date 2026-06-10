# Build from the repo root so the sentinex-core workspace package is in
# context:  docker build -f apps/api/Dockerfile -t sentinex/api:latest .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.17

COPY packages/core packages/core
COPY apps/api apps/api
# Editable installs so dev compose can bind-mount source for hot reload.
RUN uv pip install --system --no-cache -e ./packages/core -e ./apps/api

# Upload/report storage roots (overridable via UPLOAD_DIR / REPORT_DIR).
RUN mkdir -p /data/uploads /data/reports

EXPOSE 8000
CMD ["uvicorn", "sentinex_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
