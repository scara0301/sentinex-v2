# Hardened agent sandbox images. Each framework variant is a build target:
#   docker build --target langchain -t sentinex/sandbox-langchain:latest packages/sandbox-image/
#   docker build --target mcp       -t sentinex/sandbox-mcp:latest       packages/sandbox-image/
#   docker build --target crewai    -t sentinex/sandbox-crewai:latest    packages/sandbox-image/
#   docker build --target raw       -t sentinex/sandbox-raw_python:latest packages/sandbox-image/
FROM python:3.12-slim AS base

RUN adduser --disabled-password --gecos "" --uid 1000 agentuser
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && rm -rf /var/lib/apt/lists/*

WORKDIR /work
RUN pip install --no-cache-dir uv==0.8.17

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
# ENTRYPOINT is inherited by every variant stage below.
ENTRYPOINT ["/entrypoint.sh"]

# --- LangChain variant ---
FROM base AS langchain
# NOTE: version specifiers must be quoted — unquoted `>=` is a shell redirect.
RUN uv pip install --system --no-cache \
    "langchain>=0.3" "langchain-openai" "langchain-anthropic" \
    "langchain-community" "requests" "httpx"
USER agentuser

# --- MCP variant ---
FROM base AS mcp
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm && rm -rf /var/lib/apt/lists/*
RUN uv pip install --system --no-cache "mcp" "requests" "httpx"
USER agentuser

# --- CrewAI variant ---
FROM base AS crewai
RUN uv pip install --system --no-cache "crewai" "requests" "httpx"
USER agentuser

# --- Raw Python (default target) ---
FROM base AS raw
RUN uv pip install --system --no-cache "requests" "httpx"
USER agentuser
