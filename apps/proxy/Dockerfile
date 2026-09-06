# Build from the repo root so the sentinex-core workspace package is in
# context:  docker build -f apps/proxy/Dockerfile -t sentinex/proxy:latest .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.17

COPY packages/core packages/core
COPY apps/proxy apps/proxy
# --no-sources: each app's pyproject declares
# `sentinex-core = { workspace = true }`, but the build context here holds only
# this app plus packages/core — the root pyproject that defines the uv
# workspace is absent, so uv fails with "references a workspace ... but is not
# a workspace member". Ignoring tool.uv.sources resolves sentinex-core from the
# editable path installed in the same command instead.
RUN uv pip install --system --no-cache --no-sources -e ./packages/core -e ./apps/proxy

# mitmproxy writes its CA bundle to confdir; pin it to a deterministic path
# the orchestrator reads from to plant the CA in agent sandboxes.
# NOT "proxy": Debian's base image already ships a system account with that
# name, so `useradd proxy` exits 9 ("user already exists") and the image fails
# to build.
RUN useradd --create-home --uid 1000 sentinex
USER sentinex

ENV MITMPROXY_CONFDIR=/home/sentinex/.mitmproxy

EXPOSE 8080
# gen_ca pre-generates the CA store with a generic CN before mitmdump starts
# (mitmproxy's default CN is the literal string "mitmproxy", a trivial
# fingerprint) — see sentinex_proxy/gen_ca.py. The addon is loaded by file
# path; it uses absolute imports because mitmdump imports -s scripts outside
# the package context.
CMD ["sh", "-c", "python -m sentinex_proxy.gen_ca && exec mitmdump -p 8080 -s /app/apps/proxy/sentinex_proxy/main.py --set ssl_insecure=true --set confdir=$MITMPROXY_CONFDIR"]
