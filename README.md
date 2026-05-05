# SENTINEX v2

**AI Agent Runtime Security Platform** — a controlled detonation chamber for AI agents.

Static analysis tells you what an agent *could* do. SENTINEX tells you what it *actually does* when it runs. We deploy your agent into a sandboxed environment, run it against realistic mock infrastructure, intercept every tool call, and surface vulnerabilities at runtime — before they surface in production.

---

## What It Does

```
┌────────────────────────────────────────────────────────────────┐
│                        SENTINEX PLATFORM                        │
│                                                                  │
│  ┌──────────────┐    ┌───────────────┐    ┌──────────────────┐  │
│  │ Agent Loader │    │   Sandbox     │    │  Live Observer   │  │
│  │              │───▶│   Runtime     │◀───│  (WebSocket +    │  │
│  │ AST-only,    │    │   (Docker)    │    │   Next.js dash)  │  │
│  │ never exec   │    │               │    └──────────────────┘  │
│  └──────────────┘    └──────┬────────┘                          │
│                             │                                    │
│                      ┌──────▼────────┐   ┌────────────────────┐ │
│                      │  Tool Proxy   │   │  Risk Score Engine  │ │
│                      │  (mitmproxy)  │───▶│  0–100 streaming   │ │
│                      └──────┬────────┘   └────────────────────┘ │
│                             │                                    │
│                      ┌──────▼────────┐   ┌────────────────────┐ │
│                      │  Mock Infra   │   │  Scenario Runner   │ │
│                      │  Stripe · DB  │   │  YAML DSL          │ │
│                      │  Slack · etc  │   │                    │ │
│                      └───────────────┘   └────────────────────┘ │
└────────────────────────────────────────────────────────────────┘
```

Upload any agent — LangChain, CrewAI, AutoGen, OpenAI Assistants, raw Python, or an MCP server config. SENTINEX:

1. **Parses** the agent into a normalized manifest (AST-only — uploaded code is never executed outside the sandbox)
2. **Spins up** an isolated Docker sandbox with mock Stripe, Slack, Twilio, SendGrid, and a seeded PostgreSQL database containing honeypot PII and planted credentials
3. **Intercepts** every tool call through a mitmproxy layer — captures args, responses, headers, and full call chains
4. **Runs** configurable attack scenarios against the live agent (multi-turn, return-path poisoning, data exfiltration chains)
5. **Streams** findings and risk score in real-time to a live Next.js dashboard over WebSocket
6. **Reports** a compliance-grade PDF with severity breakdown, evidence, and auto-generated remediation patches

---

## Vulnerability Categories

| Category | Examples |
|---|---|
| **LLM-layer** | Prompt injection, jailbreak, system prompt leakage |
| **Tool-layer** | Return-path poisoning, parameter injection, denial-of-wallet |
| **Memory / State** | Context window overflow, RAG poisoning, state confusion |
| **Multi-agent** | Delegation abuse, message spoofing, consensus manipulation |
| **Infrastructure** | Container escape attempts, network exfiltration, env var leakage |
| **Business Logic** | Exceeding transaction limits, unauthorized data access, missing confirmation gates |

---

## Architecture

```
red teaming/
├── apps/
│   ├── api/           # FastAPI control plane
│   │   ├── routes/    #   workspaces, agents, scans, scenarios, WebSocket
│   │   └── ws/        #   ConnectionManager + Redis pub/sub fanout
│   ├── web/           # Next.js 16 live dashboard
│   │   └── src/
│   │       ├── components/  # RiskGauge, EventStream, FindingsTable, ScanStatusBadge
│   │       └── lib/         # useScanWS hook, REST client
│   ├── worker/        # ARQ async worker + per-scan Docker orchestrator
│   ├── proxy/         # mitmproxy addon — intercepts every tool call
│   └── mocks/         # Mock Stripe, Slack, Twilio, SendGrid facades
├── packages/
│   ├── core/          # DB models, repos, AgentManifest schema, event types, scoring engine
│   └── sandbox-image/ # Hardened Docker base + seed SQL with honeypot data
├── infra/
│   └── docker-compose.dev.yml
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

**Stack:**
- **Backend:** FastAPI · PostgreSQL 16 · Redis 7 · ARQ · SQLAlchemy 2.0 async · Alembic · Pydantic v2
- **Sandbox:** Docker SDK · mitmproxy · isolated bridge networks
- **Frontend:** Next.js 16 · React 19 · CSS Modules · WebSocket
- **Reporting:** WeasyPrint *(Sprint 4)*

---

## Getting Started

### Prerequisites

- Docker + Docker Compose
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 20+ + [`pnpm`](https://pnpm.io/installation)

### Run locally

```bash
cp .env.example .env           # set DATABASE_URL, REDIS_URL, etc.
make dev-up                    # start postgres, redis, api, worker, mock providers
make migrate                   # run Alembic migrations
make dev-logs                  # tail all service logs

# Dashboard (separate terminal)
cd apps/web
pnpm install
pnpm dev                       # http://localhost:3000
```

### Scan an agent end-to-end

```bash
# 1. Create a workspace — returns workspace_id + api_key
curl -X POST http://localhost:8000/workspace \
  -H "Content-Type: application/json" \
  -d '{"name": "my-workspace"}'

# 2. Upload agent bundle (.py, .zip, .tar.gz)
curl -X POST http://localhost:8000/workspace/{id}/agent \
  -H "X-Api-Key: <key>" \
  -F "name=my-agent" \
  -F "file=@my_agent.py"

# 3. Start scan (returns scan_id immediately; job is async)
curl -X POST http://localhost:8000/workspace/{id}/scan \
  -H "X-Api-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "<agent-id>", "scenario_ids": []}'

# 4. Watch live — or open the dashboard at localhost:3000
curl "ws://localhost:8000/workspace/{id}/scan/{scan-id}/live?api_key=<key>"

# 5. Poll results
curl -H "X-Api-Key: <key>" \
  http://localhost:8000/workspace/{id}/scan/{scan-id}
```

---

## Live Dashboard

Sprint 2 ships a real-time observer UI at `apps/web`:

| Component | What it shows |
|---|---|
| **RiskGauge** | Animated 0–100 SVG radial gauge; color-coded CLEAN → LOW → MEDIUM → HIGH → CRITICAL |
| **EventStream** | Scrolling feed of raw events with type badges (state, finding, risk_update, proxy) |
| **FindingsTable** | Severity-sorted findings with rule ID, category, and title |
| **ScanStatusBadge** | Live state machine badge (PENDING → PROVISIONING → RUNNING → DONE) |
| **Connection dot** | WebSocket connection state with auto-reconnect + replay on reconnect |

The WS connection uses exponential backoff reconnect and sends `resume_from: <last_seq>` on reconnect so no events are missed.

---

## Supported Agent Frameworks

| Framework | Loader | Status |
|---|---|---|
| LangChain / LangGraph | AST-based `@tool` + LLM class detection | ✅ Sprint 1 |
| Raw Python | AST env-var + call extraction, fallback loader | ✅ Sprint 1 |
| MCP servers | `mcp.json` config parser | ✅ Sprint 1 |
| CrewAI | Multi-agent roster + delegation graph | ⚙️ Sprint 5 |
| AutoGen | Agent graph + handoff extraction | ⚙️ Sprint 5 |
| OpenAI Assistants | API-key + assistant ID wrapping | ⚙️ Sprint 5 |

---

## Sandbox Security Model

Each scan runs in a fully isolated Docker environment:

```
┌─── scan network (internal=True, no host egress) ─────────────┐
│  agent container          ─▶  proxy container (mitmproxy)    │
│  --cap-drop=ALL               ├─ records all tool calls        │
│  --read-only rootfs           ├─ publishes events to Redis     │
│  --security-opt               └─ allows only LLM provider IPs │
│    no-new-privileges                                           │
│  mem_limit=2GB                mock-db (seeded postgres)        │
│  pids_limit=256               ├─ synthetic PII                 │
│  cpu_quota=1.0                ├─ planted SSNs / API keys       │
│                               └─ honeypot tables               │
└──────────────────────────────────────────────────────────────┘
```

- Agent's only egress is through the proxy — all traffic is recorded
- Honeypot files planted in sandbox filesystem (fake `.env`, fake SSH keys)
- Exfiltration of planted credentials triggers `TOOL-EXFIL-001`
- Agent bundles are parsed AST-only before entering the sandbox

---

## API Reference

| Method | Path | Auth | Status |
|---|---|---|---|
| `POST` | `/workspace` | — | ✅ |
| `POST` | `/workspace/{id}/agent` | `X-Api-Key` | ✅ |
| `GET` | `/workspace/{id}/agent` | `X-Api-Key` | ✅ |
| `POST` | `/workspace/{id}/scan` | `X-Api-Key` | ✅ |
| `GET` | `/workspace/{id}/scan` | `X-Api-Key` | ✅ |
| `GET` | `/workspace/{id}/scan/{sid}` | `X-Api-Key` | ✅ |
| `GET` | `/workspace/{id}/scan/{sid}/events` | `X-Api-Key` | ✅ |
| `GET` | `/workspace/{id}/scan/{sid}/findings` | `X-Api-Key` | ✅ |
| `WS` | `/workspace/{id}/scan/{sid}/live?api_key=` | query param | ✅ Sprint 2 |
| `POST` | `/scenarios` | `X-Api-Key` | ✅ |
| `GET` | `/workspace/{id}/scan/{sid}/report` | `X-Api-Key` | ⚙️ Sprint 4 |
| `POST` | `/workspace/{id}/scan/{sid}/fix` | `X-Api-Key` | ⚙️ Sprint 4 |
| `GET` | `/badge/{scan_id}.svg` | — | ⚙️ Sprint 4 |

---

## Sprint Roadmap

| Sprint | Weeks | Scope | Status |
|---|---|---|---|
| **1 — Foundation** | 1–3 | Agent Loader · Docker sandbox · Mock infra · Tool Proxy · Risk scoring | ✅ Done |
| **2 — Live Observer** | 4–5 | WebSocket fanout · Redis pub/sub · Next.js dashboard · RiskGauge · EventStream | ✅ Done |
| **3 — Attack Scenarios** | 6–7 | YAML DSL · Scenario runner · 3 built-in scenarios · Response injection | 🔜 Next |
| **4 — Reporting** | 8–9 | Compliance PDF · Remediation patches · Embeddable SVG badges | ⏳ |
| **5 — Multi-agent & CI** | 10+ | CrewAI/AutoGen loaders · Breakpoint/replay · GitHub Action · SaaS billing | ⏳ |

---

## Development

```bash
make lint              # ruff check
make fmt               # ruff format
make test-unit         # unit tests (pytest)
make test-integration  # requires dev services running
make test-e2e          # full pipeline against fixture agents
make build-sandbox     # build sentinex/sandbox-langchain:latest

# Type-check frontend
cd apps/web && npx tsc --noEmit
```

---

## Contributing

See [PHILOSOPHY.md](PHILOSOPHY.md) for the design principles that govern every decision in this codebase.

---

## License

MIT
