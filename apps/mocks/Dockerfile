# Self-contained (no workspace deps):
#   docker build -t sentinex/mocks:latest apps/mocks
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.17

COPY pyproject.toml .
COPY sentinex_mocks/ sentinex_mocks/
RUN uv pip install --system --no-cache -e .

RUN useradd --create-home --uid 1000 mocks
USER mocks

# MOCK_PROVIDER env var selects which provider to run (stripe|slack|twilio|sendgrid)
# The orchestrator sets this when launching mock containers.
ENV MOCK_PROVIDER=stripe

EXPOSE 4010
CMD ["sh", "-c", "uvicorn sentinex_mocks.${MOCK_PROVIDER}:app --host 0.0.0.0 --port 4010"]
