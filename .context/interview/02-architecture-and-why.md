# Architecture — every component, why it exists, what else I considered

The pattern for every section: **what it does → why it must exist → the
alternatives → why this one won → what it costs.** That last one matters most.
An engineer who can only list benefits has not actually evaluated a decision.

---

## The shape

```
 Browser ──WS/HTTP──> API (FastAPI) ──enqueue──> Redis ──> Worker (arq)
                        │                          │           │
                        │                          │           │ Docker socket
                        └──── fanout ◄── pub/sub ──┘           ▼
                                                      ┌─────────────────────┐
                                                      │ internal network    │
                                                      │  agent → proxy →    │
                                                      │    mocks + mock-db  │
                                                      └─────────────────────┘
                        Postgres ◄── events, findings, scans
```

---

## Why four services rather than one

**What:** API, worker, proxy, mocks — separate processes, separate images.

**Why it must exist as a split:** these components have genuinely different
requirements, and merging them creates a real problem, not just an aesthetic
one.

- The **worker holds the Docker socket**, which is root-equivalent on the host.
  Anything holding that socket can start a privileged container and own the
  machine.
- The **API is internet-facing** and processes untrusted uploads.

Putting those in one process means an API compromise is immediately a host
compromise. Keeping them separate means the internet-facing surface has no path
to the Docker daemon; it can only enqueue a job.

They also scale differently. The API is IO-bound and cheap; the worker is bound
by how many sandboxes the host can run. Separate processes scale independently.

**Alternatives considered:**

- *Monolith.* Simpler to deploy and debug. Rejected on the privilege argument
  above — that is not a tradeoff I would make for a security product.
- *Serverless functions.* Rejected outright: a scan needs to run Docker
  containers for minutes with a live network namespace. That is the opposite of
  what a function runtime provides.

**The cost:** more moving parts, distributed failure modes, and a Redis
dependency that is now on the critical path. A local run needs four things up,
not one.

---

## Why Postgres

**Why:** I need JSONB for heterogeneous event payloads, arrays for `cwe` and
`scenario_ids`, real foreign keys with cascade deletes so deleting a workspace
does not orphan a thousand event rows, and composite primary keys.

**Alternatives:**

- *SQLite.* Fine for single-node, but no concurrent writers, which breaks the
  moment there are two workers. It also lacks JSONB and array types.
- *MongoDB.* The event stream is document-shaped, so it fits. But the rest of
  the model is deeply relational — workspace → agent → scan → finding →
  remediation — and I would be reimplementing joins and cascades by hand.
- *A time-series database for events.* Genuinely tempting, and the right answer
  at high volume. Rejected because it splits the store: findings reference
  events by sequence number, and I would be joining across two systems for
  every report. Postgres holds both consistently.

**The cost:** the events table grows without bound. There is no retention
policy today. At real volume I would partition by scan or month and archive
cold partitions.

---

## Why Redis, for three separate jobs

Redis does three unrelated things here, and it is worth being precise about
them because an interviewer may probe whether you understand you have coupled
three concerns to one dependency.

1. **Job queue** (via arq) — decouples "accept a scan request" from "run a scan
   for ten minutes". The API returns `202 Accepted` immediately.
2. **Pub/sub transport** — carries events from the proxy to both the worker's
   collector and the API's WebSocket fanout.
3. **The sequence counter** — an atomic `INCR` giving three independent
   producers a globally ordered sequence per scan.

**Alternatives:**

- *Celery + RabbitMQ.* More mature and more featureful. Rejected because arq is
  async-native and this codebase is async throughout — Celery's threading model
  would have meant bridging sync and async across the orchestrator.
- *Postgres LISTEN/NOTIFY for pub/sub.* Would remove a dependency. Rejected
  because the payload size limit is 8000 bytes and event payloads can exceed
  that, and because it puts the live event path on the primary database.
- *Kafka.* Correct at large scale and gives durable replay. Massive operational
  overhead for a system where the durable record is already Postgres.

**The cost, and this is the honest weakness:** Redis pub/sub is fire-and-forget
with no backlog. If nobody is subscribed when a message is published, it is
gone. That is precisely the bug I hit — the collector was starting after the
proxy, so early events vanished. The fix was to subscribe before provisioning
and wait for confirmation. A durable log like Kafka or Redis Streams would make
that class of bug structurally impossible, and **Redis Streams is what I would
migrate to** — same dependency, consumer groups, replayable.

---

## Why mitmproxy rather than anything else

**What:** an addon loaded into mitmdump, exposing `request`, `response` and
`http_connect_error` hooks.

**Why a proxy at all:** it is the single chokepoint every tool call must cross,
independent of framework. Instrumenting LangChain would mean writing a separate
integration per framework and would miss anything the agent does with a raw
`requests` call.

**Alternatives:**

- *Monkey-patching the agent's HTTP libraries.* Requires injecting code into
  the agent process, breaks per library and per version, and an agent could
  unpatch it. Fragile and defeatable.
- *eBPF / packet capture.* Genuinely powerful, sees everything at the syscall
  or packet level with no possibility of evasion. Rejected because TLS is
  opaque at that layer — I would see encrypted bytes, not tool calls. And I
  need to *modify* responses for injections, which passive capture cannot do.
- *A custom proxy.* I need TLS interception with on-the-fly certificate
  generation. That is a large, security-sensitive amount of code that
  mitmproxy has already solved and hardened.

**Why mitmproxy specifically:** it does TLS interception with dynamic cert
generation, it has a Python addon API so the recording logic is ordinary Python,
and it lets me rewrite response bodies in-flight, which is exactly what
injections need.

**The cost:** TLS interception means the agent must trust my CA. That is a
real, visible modification of the environment — a sufficiently paranoid agent
could notice. I reduced the signal by giving the CA a generic subject name
instead of mitmproxy's default, but I cannot eliminate it. This is inherent to
the approach, not a bug.

---

## Why mock provider APIs

**What:** FastAPI reimplementations of Stripe, Slack, SendGrid and Twilio, with
provider hosts rerouted to them by the proxy.

**Why:** three reasons, in order of importance.

1. **Safety.** An agent under test must not be able to issue a real refund or
   send a real SMS. The sandbox has no egress, so the calls have nowhere to go
   anyway — but the mocks let the agent's tool calls *succeed*, which is
   necessary for it to proceed far enough to misbehave.
2. **Determinism.** Real APIs change, rate-limit, and require credentials.
3. **Honeypot placement.** I control the responses, so I can plant known-fake
   sensitive values and detect them leaving.

**Alternatives:**

- *Record-and-replay against the real APIs.* Better fidelity. Rejected because
  it requires real credentials to record, and the recordings go stale.
- *Off-the-shelf mocks like Prism or WireMock.* Would work for the API shapes.
  Rejected because I need honeypot injection and provider-plausible error
  envelopes, and I would have ended up wrapping them anyway.

**The cost, and this is a real weakness:** the mocks are approximations. My mock
Stripe accepts JSON bodies where real Stripe uses form encoding. Response
headers and error envelopes are best-effort, not captured from live traffic. A
sophisticated agent could fingerprint the environment. I mitigated the obvious
tells — a `server: nginx` header, provider-shaped 404 bodies, mixed-case object
ids rather than `uuid4().hex` — but I would not claim it survives determined
inspection.

---

## Why an event-sourced model

**What:** an append-only `events` table keyed `(scan_id, seq)`; findings are
derived from it, not written alongside it.

**Why:** detection becomes a pure function of the event log. That gives me:

- **Testability.** `ScenarioRunner.evaluate(events)` takes plain dicts and
  returns finding drafts. No Docker, no Redis, no database. The most
  security-critical code in the system is unit-testable in milliseconds.
- **Replay.** New detection rules can run against historical scans.
- **Auditability.** A finding cites `event_seqs`, so every claim is traceable
  to the moment it happened.

**Alternatives:**

- *Detect inline as traffic passes through the proxy.* Lower latency, and the
  proxy already maintains some of this state in `ChainTracker`. Rejected as the
  authoritative path because it couples detection to the hot path, makes rules
  untestable without a live proxy, and makes replay impossible. The
  `ChainTracker` state is deliberately advisory only.
- *Store only findings.* Much less storage. Rejected because you lose the
  evidence, and a security finding without evidence is an assertion.

**The cost:** storage volume, and the ordering complexity across three writers
that forced the Redis counter.

---

## Why a YAML DSL for attacks

Covered in `01`. The short version for a follow-up: attacks change more often
than engine code; a declarative rule is reviewable by a non-Python security
engineer; and it is safe to accept from users, whereas a Python plugin endpoint
is remote code execution with extra steps.

The limitation to volunteer: no cross-event arithmetic or correlation beyond
`after_injection` and `min_count`. I would add operators as concrete scenarios
demand them rather than pre-building a query language.

---

## Why Next.js and WebSockets

**Why WebSockets over Server-Sent Events:** SSE is genuinely a good fit for
one-way streaming and is simpler. I chose WebSockets because the breakpoint
feature needs a bidirectional channel — the client sends `resume_from` for
replay and `ping` for keepalive.

**Why the API key travels as a WebSocket subprotocol:** browsers cannot set
custom headers on a WebSocket. The options are a query parameter or a
subprotocol. Query parameters land in server access logs and browser history;
the subprotocol does not. Non-browser clients like `wscat` can still use the
query parameter.

**The reconnect design:** exponential backoff to a 16-second ceiling, and on
reconnect the client sends the highest sequence number it has seen so the
server replays the gap from Postgres before resuming live. That is the payoff
for having a durable event log — recovery is a query, not a loss.

---

## What I would change with more time

Say this unprompted if the conversation allows. It reads as maturity.

1. **Redis Streams instead of pub/sub**, to make lost-event bugs structurally
   impossible rather than avoided by careful ordering.
2. **gVisor or Firecracker** for the agent sandbox. Docker with dropped
   capabilities is solid, but it is still a shared kernel. A kernel exploit
   escapes. For untrusted code from strangers, I would want a stronger boundary.
3. **A retention and partitioning policy** on the events table.
4. **Automated end-to-end tests** — currently the biggest gap in the project.
