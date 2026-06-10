# Build from the repo root so the sentinex-core workspace package is in
# context:  docker build -f apps/proxy/Dockerfile -t sentinex/proxy:latest .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.17

COPY packages/core packages/core
COPY apps/proxy apps/proxy
RUN uv pip install --system --no-cache -e ./packages/core -e ./apps/proxy

# mitmproxy writes its CA bundle to ~/.mitmproxy; give it a real home.
RUN useradd --create-home --uid 1000 proxy
USER proxy

EXPOSE 8080
# The addon is loaded by file path; it uses absolute imports because
# mitmdump imports -s scripts outside the package context.
CMD ["mitmdump", "-p", "8080", "-s", "/app/apps/proxy/sentinex_proxy/main.py", "--set", "ssl_insecure=true"]
