# SENTINEX v2

**AI Agent Runtime Security Platform** — a controlled detonation chamber for AI agents.

Instead of analyzing a system prompt, SENTINEX deploys your agent into a sandboxed environment, runs it against mock infrastructure, intercepts every tool call, and identifies vulnerabilities at runtime.

---

## What It Does

```
┌──────────────────────────────────────────────────────┐
│                    SENTINEX PLATFORM                  │
│                                                       │
│  ┌─────────────┐   ┌──────────────┐   ┌───────────┐  │
│  │  Agent       │   │  Sandbox     │   │  Observer  │  │
│  │  Loader      │──▶│  Runtime     │◀──│  Engine    │  │
│  │              │   │  (Docker)    │   │            │  │
│  └─────────────┘   └──────┬───────┘   └─────┬──────┘  │
│                           │                  │         │
│                    ┌──────▼───────┐   ┌──────▼──────┐  │
│                    │  Tool Proxy  │   │  Anomaly    │  │
│                    │  Layer       │   │  Detector   │  │
│                    └──────┬───────┘   └─────────────┘  │
│                           │                            │
│                    ┌──────▼───────┐                    │
│                    │  Mock Infra  │                    │
│                    │  (APIs, DBs, │                    │
│                    │   filesys)   │                    │
│                    └──────────────┘                    │
└──────────────────────────────────────────────────────┘
```

Upload any agent — LangChain, CrewAI, AutoGen, OpenAI Assistants, raw Python, or an MCP server config. SENTINEX:

1. **Parses** the agent into a normalized manifest (AST-only — never executes uploaded code)
2. **Spins up** an isolated Docker sandbox with mock Stripe, Slack, Twilio, SendGrid, and a seeded PostgreSQL database containing honeypot data
3. **Intercepts** every tool call through a mitmproxy layer — logs args, responses, and full call chains
4. **Runs** configurable attack scenarios (multi-turn, return-path poisoning, data exfiltration chains)
5. **Streams** real-time findings to a live dashboard via WebSocket
6. **Reports** a compliance-grade PDF with risk score, findings, and auto-generated remediation patches

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
│   ├── api/           # FastAPI control plane (workspaces, agents, scans, WebSocket)
│   ├── worker/        # ARQ async worker + per-scan Docker orchestrator
│   ├── proxy/         # mitmproxy addon — intercepts every tool call
│   └── mocks/         # Mock Stripe, Slack, Twilio, SendGrid facades
├── packages/
│   ├── core/          # DB models, AgentManifest schema, event types, loaders
│   └── sandbox-image/ # Hardened Docker base + seed SQL with honeypot data
├── infra/
│   └── docker-compose.dev.yml
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

**Stack:** FastAPI · PostgreSQL 16 · Redis 7 · ARQ · SQLAlchemy 2.0 async · Alembic · mitmproxy · Docker SDK · Next.js 15 (Sprint 2) · WeasyPrint (Sprint 4)

---

## Getting Started

### Prerequisites

- Docker + Docker Compose
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- [`pnpm`](https://pnpm.io/installation)

### Run locally

```bash
cp .env.example .env          # fill in any overrides
make dev-up                   # postgres, redis, api, worker, mock providers
make migrate                  # run Alembic revision 0001
make dev-logs                 # tail all service logs
```

### Upload and scan an agent

```bash
# Create a workspace
curl -X POST http://localhost:8000/workspace \
  -H "Content-Type: application/json" \
  -d '{"name": "my-workspace"}'

# Upload a LangChain agent (.py or .zip)
curl -X POST http://localhost:8000/workspace/{id}/agent \
  -H "X-Api-Key: <key>" \
  -F "name=my-agent" \
  -F "file=@my_agent.py"

# Start a scan
curl -X POST http://localhost:8000/workspace/{id}/scan \
  -H "X-Api-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "<agent-id>", "scenario_ids": []}'

# Poll status
curl http://localhost:8000/workspace/{id}/scan/{scan-id}
```

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

Each scan gets its own Docker network (`internal=True` — no direct host egress):

- `--cap-drop=ALL` · read-only rootfs · `--security-opt no-new-privileges`
- `mem_limit=2GB` · `pids_limit=256` · `cpu_quota=1.0`
- Agent's only egress path is through the proxy container (which has a restricted NIC allowing only LLM provider IPs)
- Honeypot files (fake `.env`, fake SSH keys) planted in the sandbox filesystem
- Mock DB seeded with synthetic PII and planted SSNs/API keys — exfiltration of these values triggers `TOOL-EXFIL-001`

---

## Sprint Roadmap

| Sprint | Weeks | Scope | Status |
|---|---|---|---|
| **1 — Foundation** | 1–3 | Agent Loader · Docker sandbox · Mock infra · Tool Proxy | ✅ Done |
| **2 — Live Observer** | 4–5 | WebSocket stream · Next.js dashboard · Risk scoring | 🔜 Next |
| **3 — Attack Scenarios** | 6–7 | YAML DSL · Scenario runner · 3 built-in scenarios · Response injection | ⏳ |
| **4 — Reporting** | 8–9 | Compliance PDF · Remediation patches · Embeddable badges | ⏳ |
| **5 — Multi-agent & CI** | 10+ | CrewAI/AutoGen · Breakpoint/replay · GitHub Action | ⏳ |

---

## API Reference

```
POST   /workspace                          Create workspace
POST   /workspace/{id}/agent               Upload agent
POST   /workspace/{id}/scan                Start scan
GET    /workspace/{id}/scan/{sid}          Poll status + results
WS     /workspace/{id}/scan/{sid}/live     Real-time event stream  (Sprint 2)
GET    /workspace/{id}/scan/{sid}/report   PDF compliance report   (Sprint 4)
POST   /workspace/{id}/scan/{sid}/fix      Apply remediation + rescan (Sprint 4)
GET    /badge/{scan_id}.svg                Embeddable security badge  (Sprint 4)
POST   /scenarios                          Upload custom scenario      (Sprint 3)
```

---

## Development

```bash
make lint           # ruff + mypy
make fmt            # ruff format
make test-unit      # unit tests
make test-integration  # requires dev services running
make test-e2e       # full pipeline against fixture agents
make build-sandbox  # build sentinex/sandbox-langchain:latest
```

---

## License

MIT
