FROM python:3.12-slim AS base

RUN adduser --disabled-password --gecos "" --uid 1000 agentuser
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl && rm -rf /var/lib/apt/lists/*

WORKDIR /work
RUN pip install --no-cache-dir uv==0.4.29

# --- LangChain variant ---
FROM base AS langchain
RUN uv pip install --system --no-cache \
    langchain>=0.3 langchain-openai langchain-anthropic \
    langchain-community requests httpx

# --- MCP variant ---
FROM base AS mcp
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm && rm -rf /var/lib/apt/lists/*
RUN uv pip install --system --no-cache mcp requests httpx

# --- CrewAI variant ---
FROM base AS crewai
RUN uv pip install --system --no-cache crewai requests httpx

# --- Default (raw Python) ---
FROM base AS final
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Security: no new privileges, run as non-root
USER agentuser
ENTRYPOINT ["/entrypoint.sh"]
