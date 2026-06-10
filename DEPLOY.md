# Deploying SENTINEX to Production

SENTINEX's worker orchestrates per-scan sandbox containers through the
host Docker daemon, so the deployment target is **a VM with Docker
Engine** — not a serverless/PaaS platform. Everything below assumes a
single host running the stack in `infra/docker-compose.prod.yml`.

## What you need

- A VM with **8 GB RAM / 4 vCPU** or better (each scan spawns ~4
  containers; the agent sandbox alone is capped at 2 GB). Ubuntu 22.04+
  or Debian 12+ recommended.
- Two DNS **A records** pointing at the VM:
  - `api.yourdomain.com` — the API (+ WebSocket live stream)
  - `app.yourdomain.com` — the dashboard
- Inbound **ports 80 and 443** open (Caddy obtains Let's Encrypt
  certificates automatically). Nothing else needs to be exposed.

## 1. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
```

## 2. Clone and configure

```bash
git clone https://github.com/scara0301/sentinex-v2 /opt/sentinex/app
cd /opt/sentinex/app
cp .env.example .env
```

Edit `.env` and set (uncomment the production block):

| Variable | Value |
|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` — signs scan badges |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
| `SENTINEX_API_DOMAIN` | `api.yourdomain.com` |
| `SENTINEX_APP_DOMAIN` | `app.yourdomain.com` |
| `ACME_EMAIL` | your email (Let's Encrypt expiry notices) |
| `SENTINEX_DATA_DIR` | host dir for agent bundles, default `/opt/sentinex/data` |

Create the data directory:

```bash
sudo mkdir -p /opt/sentinex/data/uploads
```

## 3. Build the scan-time images

The worker launches these by name for every scan, so they must exist on
the host before the first scan runs:

```bash
make build-scan-images   # sentinex/proxy, sentinex/mocks, sentinex/mock-db
make build-sandbox       # agent sandboxes: langchain, mcp, crewai, autogen, raw
```

## 4. Start the stack and migrate

```bash
make prod-up
make prod-migrate
```

Verify:

```bash
curl https://api.yourdomain.com/health
# {"status":"ok","version":"2.0.0"}
```

Open `https://app.yourdomain.com` for the dashboard.

## 5. First scan

```bash
# Create a workspace (returns the API key — store it!)
curl -X POST https://api.yourdomain.com/workspace \
  -H "Content-Type: application/json" -d '{"name": "prod"}'

# Upload an agent and start a scan — see README "Scan an agent end-to-end"
```

## CI integration

Point the GitHub Action (repo-root `action.yml`) at your instance:

```yaml
- uses: your-org/sentinex-v2@master
  with:
    api-url: https://api.yourdomain.com
    api-key: ${{ secrets.SENTINEX_API_KEY }}
    workspace-id: ${{ vars.SENTINEX_WORKSPACE_ID }}
    agent-name: my-agent
    bundle-path: agents/my_agent.py
    fail-on-severity: high
```

## Operations

**Logs** — `make prod-logs` (or `docker compose ... logs -f worker` for
scan orchestration only).

**Upgrades**

```bash
git pull
make build-scan-images build-sandbox
make prod-up        # rebuilds service images and restarts
make prod-migrate
```

**Backups** — the system of record is the `sentinex_pgdata` volume plus
the uploads directory:

```bash
docker compose --env-file .env -f infra/docker-compose.prod.yml \
  exec postgres pg_dump -U sentinex sentinex | gzip > backup-$(date +%F).sql.gz
tar czf uploads-$(date +%F).tar.gz -C /opt/sentinex/data uploads
```

**Capacity** — the worker runs up to 5 concurrent scans
(`max_jobs` in `apps/worker/sentinex_worker/main.py`); plan limits
(free/pro/enterprise) cap per-workspace usage. Scale vertically first;
multiple workers on separate Docker hosts also work since jobs are
distributed via Redis (each host needs the scan-time images and a copy
of the uploads volume — use shared storage for `SENTINEX_DATA_DIR`).

## Security notes for a public deployment

- **Workspace creation is open** (`POST /workspace` is unauthenticated by
  design — that's the SaaS signup). Abuse is bounded by free-plan quotas:
  10 scans/month, 1 concurrent scan, 3 agents, no custom scenarios.
- **Uploaded agents are untrusted code.** They execute only inside the
  hardened sandbox (all capabilities dropped, read-only rootfs,
  `no-new-privileges`, memory/CPU/PID limits, internal-only network with
  egress restricted to the recording proxy). The isolation boundary is
  the Docker container — keep the host kernel and Docker Engine patched.
- **The worker holds the Docker socket**, which is root-equivalent on the
  host. Do not run unrelated workloads on the same VM.
- Postgres and Redis are reachable only on the internal compose network;
  do not add `ports:` mappings for them.
- Set a real `SECRET_KEY` before issuing any badges — badge signatures
  are HMACs over it.
