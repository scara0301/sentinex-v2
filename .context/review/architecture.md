# SENTINEX v2 — how the system fits together

SENTINEX runs an uploaded AI agent inside a disposable sandbox, intercepts
everything it does over the network, injects adversarial content into tool
responses, and scores what the agent does in reaction.

## Services

| Service | Path | Role |
|---|---|---|
| API | `apps/api` | FastAPI. Uploads, scans, findings, badges, WebSocket fanout. |
| Worker | `apps/worker` | arq job runner. Owns the scan state machine and all Docker orchestration. |
| Proxy | `apps/proxy` | mitmproxy addon. Records tool calls, reroutes provider hosts to mocks, applies injections. |
| Mocks | `apps/mocks` | FastAPI fakes for Stripe, Slack, SendGrid, Twilio, seeded with honeypot values. |
| Web | `apps/web` | Next.js 16 dashboard. Live event stream, risk gauge, findings table. |
| Core | `packages/core` | Shared: SQLAlchemy models and repos, event schema, scenario DSL, scoring, badges, remediation. |
| Sandbox images | `packages/sandbox-image` | Hardened per-framework agent runtimes plus the seeded mock database. |

## Scan lifecycle

The worker drives one state machine per scan:

```
PENDING -> PROVISIONING -> SEEDING -> RUNNING -> DRAINING
        -> SCORING -> REPORTING -> DONE
                              \-> FAILED (any exception or timeout)
```

1. **PROVISIONING** — create an `internal=True` Docker network (no host
   egress), then the seeded mock database, the mock provider containers, and
   the proxy. The proxy additionally joins an egress network so it can reach
   Redis; without that, no event ever leaves the sandbox.
2. **SEEDING** — wait for the mock database to accept connections. The seed
   SQL is baked into the image, so nothing is executed here.
3. **RUNNING** — extract the proxy's generated CA, then start the agent
   container with `HTTP_PROXY`/`HTTPS_PROXY` pointed at the proxy, all
   capabilities dropped, a read-only root filesystem, and the bundle mounted
   read-only at `/work`.
4. **DRAINING** — stop collecting, persist buffered proxy events.
5. **SCORING** — evaluate scenario detections over the persisted event
   stream, write findings with remediations, compute the risk score.
6. **REPORTING** — render the PDF (or HTML when WeasyPrint is unavailable).

## Event flow

The proxy is the only producer of tool-level events. It publishes to
`scan:{id}:events` in Redis. Two independent consumers read that channel:

- the **worker's collector**, which buffers events and writes them to the
  `events` table at DRAINING — this is what detections run against;
- the **API's `RedisFanout`**, which forwards them to WebSocket clients per
  scan room, so the dashboard sees them live.

Sequence numbers come from a single Redis counter, `scan:{id}:seq`, shared by
the proxy, the orchestrator, and the API's breakpoint endpoint. That is what
keeps the `(scan_id, seq)` primary key on `events` unique across three
independent writers.

The collector must subscribe **before** the proxy container starts. Redis
pub/sub keeps no backlog, so anything published before the subscription
exists is gone permanently.

## Detection model

A scenario is YAML validated by `packages/core/sentinex_core/scenarios/dsl.py`:

- **injections** are pushed to Redis before the proxy boots. The proxy
  rewrites matching tool responses and tags the recorded `tool_result` with
  the scenario slug.
- **detections** are matched against the recorded event stream after the
  agent exits. Each one that fires becomes a Finding.

Detections match on event type, tool glob, host substrings, argument
substrings, honeypot values, whether the event came after an injection, and a
minimum match count.

Because detections key on the **host** an agent contacted, the proxy has to
record requests to hosts it does not recognize. Recording only known SaaS
providers would make the exfiltration scenarios — the product's headline
capability — silently undetectable.

## Scoring

`RiskScoreEngine` combines findings with a noisy-OR:

```
hazard_i = severity_weight × category_multiplier × confidence_multiplier / MAX_SINGLE_WEIGHT
risk     = 1 − ∏(1 − hazard_i)
score    = 100 × risk
```

Every factor is in `[0, 1]`, so the score is monotonically non-decreasing:
finding another vulnerability can never lower the reported risk. `confidence`
dampens textual-only detections, which a merely *defensive* agent could
trigger by quoting a marker back.

## Gating

Findings carry both a `confidence` (set by the rule) and a review `status`
(set by a human). Open critical/high findings block badge issuance and report
download until reviewed, so an unreviewed false positive cannot tank a public
badge.

## Trust boundaries

- The agent container has no host egress and no capabilities. All traffic is
  forced through the proxy.
- The worker holds the Docker socket and runs as root; it is the most
  privileged component and must never process untrusted input directly.
- Agent bundles are attacker-controlled data. Upload paths, archive members,
  and archive destinations are all validated before anything touches disk.
- API keys authenticate exactly one workspace. Every workspace-scoped route
  must check that the key owns the workspace in the path, not merely that the
  key is valid.
