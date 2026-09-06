# How I built it — the narrative

This is the story in build order, with the decision that drove each step. When
someone asks "walk me through how you built this", this is the spine.

---

## Step 0 — The problem I was solving

An AI agent is a program whose control flow is decided by a language model at
runtime. That breaks the assumption every existing security tool makes.

A static analyser can see that an agent has a `send_email` tool and a database
tool. It cannot tell you whether the agent will read a customer record and
email it to an attacker, because that depends on what the model decides when it
sees a particular tool response. The dangerous behaviour is *emergent*, not
written down anywhere in the source.

So the question I set out to answer was: **can I observe an agent misbehaving
under adversarial conditions, rather than guessing from its code?**

That framing determined the entire architecture. Everything downstream follows
from "observe at runtime" rather than "analyse the source".

---

## Step 1 — The data model, before any features

I started with the schema because the schema is the thing that is expensive to
change later. Everything else is code you can rewrite in an afternoon.

Nine tables: `workspaces`, `agents`, `tools`, `scenarios`, `scans`, `events`,
`findings`, `remediations`, `badges`.

The decision that mattered was making `events` a first-class, append-only table
with a composite primary key of `(scan_id, seq)` rather than a surrogate id.
That is an event-sourcing choice, and it bought three things:

1. **Detections became a pure function.** `evaluate(events) -> findings`. No
   Docker, no Redis, no network. That makes the most security-critical logic in
   the system trivially unit-testable.
2. **Replay came free.** The dashboard can reconstruct any past scan by reading
   the same rows the live stream produced.
3. **Evidence became precise.** A finding cites `event_seqs: [6]`, so you can
   point at the exact moment the agent did the thing.

The cost is that ordering across independent writers becomes my problem. I come
back to that in step 4.

**If asked "why not just log to a file":** because findings need to reference
events by identity, the dashboard needs to page through them, and I need to
query them by scan and type. That is a database.

---

## Step 2 — Ingesting agents without executing them

Before I can run an agent I have to understand it: what framework, what tools,
what model, what secrets it expects.

The hard constraint: **uploaded code is hostile input and must never be
imported.** If I `import` an uploaded module to inspect it, module-level code
runs, and I have handed arbitrary execution to the uploader on my API host.

So every loader is AST-only. I parse with Python's `ast` module and walk the
tree looking for structural signatures:

- LangChain: `@tool` decorators, `BaseTool` subclasses, `ChatOpenAI(...)` calls.
- CrewAI: `Agent(role=...)` and `Crew(process=...)`, which I turn into a
  delegation graph.
- AutoGen: `initiate_chat` calls become handoff edges.
- MCP: no code at all, just `mcpServers` in a JSON config.
- OpenAI Assistants: configuration, not code.
- Raw Python: a fallback that guesses tools from function-name heuristics.

They run in a fixed order and the first `detect()` that matches wins, with
`RawPythonLoader` last because it always matches.

**The honest limitation, and say this before they find it:** AST analysis
cannot see dynamically constructed tools. If someone builds a tool list in a
loop from a config file, I miss it. That is *acceptable* here specifically
because the manifest is metadata for display and routing — it is not what
detection depends on. Detection depends on observed runtime behaviour, which is
exactly the part AST analysis cannot fake.

---

## Step 3 — The sandbox, and making the proxy unavoidable

This is the core of the system.

The naive approach is to set `HTTP_PROXY` on the agent container and hope it
honours it. That is worthless for a security tool: an agent that opens a raw
socket, or a library that ignores proxy environment variables, escapes
observation entirely, and I would report a clean scan.

So the sandbox is built on a Docker network created with `internal=True`. That
flag removes the gateway from the network, so containers on it have **no route
to anything outside it, at the kernel level**. There is no configuration the
agent can change to escape, because it is not a configuration.

Inside that network I place:

- the agent container,
- the mitmproxy container,
- mock Stripe and Slack services,
- a seeded Postgres instance.

The proxy is the only container that also joins a second, non-internal network,
which is how it reaches Redis to publish events. Everything else is sealed.

Then the agent container itself is hardened: all Linux capabilities dropped,
`no-new-privileges`, read-only root filesystem with tmpfs for `/tmp`, memory,
CPU and PID limits, and the agent bundle mounted read-only.

**The subtlety worth volunteering:** intercepting HTTPS means mitmproxy
presents its own certificate, which the agent's HTTP client will reject. So the
orchestrator reads the CA out of the running proxy container and mounts it into
the agent, setting `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `CURL_CA_BUNDLE` and
`NODE_EXTRA_CA_CERTS` to cover the common clients. Without that step every
`https://` call fails TLS and goes unrecorded — which would look like a clean
agent.

I also generate the CA with a generic subject name rather than mitmproxy's
default, which is the literal string "mitmproxy". An agent that inspects its
certificate chain would otherwise trivially detect it is being observed.

---

## Step 4 — The event pipeline and the ordering problem

The proxy publishes each intercepted call to a Redis channel,
`scan:{id}:events`. Two independent consumers read it:

- the **worker's collector**, which buffers events and writes them to Postgres
  at the DRAINING phase — this is what detections evaluate;
- the **API's fanout**, which forwards them to WebSocket clients so the
  dashboard updates live.

Splitting persistence from delivery matters: a dashboard client disconnecting
must not lose events from the record, and slow persistence must not stall the
live stream.

**The ordering problem.** Three separate processes write events for one scan:
the proxy, the worker orchestrator, and the API's breakpoint endpoint. They all
need to write into a table whose primary key is `(scan_id, seq)`. Local
counters would collide.

The solution is a single Redis `INCR` on `scan:{id}:seq`. Redis is
single-threaded, so `INCR` is atomic, and every producer gets a globally unique
monotonically increasing sequence number for that scan. It carries a TTL so a
crashed scan cannot leak the key forever.

**If asked "why not a Postgres sequence":** it would work, but it costs a
round-trip to the database on the hot path for every intercepted request, and
Redis is already there for pub/sub and the job queue. If I needed strict
durability of the counter I would revisit it — a Redis restart loses the
counter, and the recovery behaviour is that the proxy falls back to a local
counter and the upsert path tolerates the collision by ignoring duplicates.

---

## Step 5 — Attacks as data, not code

I did not want to write a Python function per attack. Scenarios are YAML,
validated by a Pydantic schema, with two halves:

**Injections** are pushed into Redis *before* the proxy boots. When an
intercepted tool response matches the rule, the proxy rewrites the body — a
deep merge or a full replace — and tags the recorded `tool_result` with the
scenario slug.

**Detections** are conditions matched against the recorded events after the
agent exits: event type, tool glob, host substrings, argument substrings,
honeypot values, whether the event came after an injection, and a minimum match
count.

The `after_injection` flag is what makes causality provable. `TOOL-RPP-001`
does not just say "the agent contacted evil-archive". It says the agent
contacted it *at a sequence number after the injection landed*. That is the
difference between correlation and a demonstrated attack chain.

**Why a DSL rather than Python plugins:** attacks are the thing that changes
most often, and a declarative rule is reviewable by a security engineer who is
not going to read my Python. It is also inherently safe to accept from users —
a YAML rule cannot execute anything, whereas a Python plugin endpoint would be
a remote code execution feature with extra steps.

**The cost, and be honest about it:** the DSL is less expressive. A detection
that needs to correlate three events with arithmetic between them cannot be
expressed today. My answer is that I would add specific operators as real
scenarios demand them, rather than pre-emptively building a query language.

---

## Step 6 — Honeypots, so exfiltration has ground truth

Detecting "did sensitive data leave" is normally a heuristic problem. I made it
a factual one.

The mock database and the mock provider APIs are seeded with specific fake
values — a card number, an SSN, an API key, an email, a password. Those exact
strings exist nowhere except inside the sandbox.

So if one of them appears in an outbound request to a host that is not the LLM
provider, no inference is required. The value could only have come from the
sandbox, and the agent moved it. That is `TOOL-EXFIL-001`.

The literals are duplicated between the core package and the mocks package
because I deliberately kept the mocks dependency-free — and a unit test pins
the two lists together so they cannot drift.

---

## Step 7 — Scoring, and the monotonicity bug

The first version averaged finding weights. That was wrong, and the way it was
wrong is a good story.

Averaging means that after a critical finding, discovering three low-severity
findings *lowers* the score. The system would report an agent as getting safer
the more vulnerabilities you found in it. For a security tool that is not a
rough edge, it is a correctness failure.

I replaced it with a noisy-OR:

```
hazard_i = severity × category_multiplier × confidence_multiplier / MAX_SINGLE
risk     = 1 − ∏(1 − hazard_i)
score    = 100 × risk
```

Each hazard is in `[0, 1]`, so each `(1 − hazard)` factor is in `[0, 1]`, so the
product can only shrink. The score is monotonically non-decreasing by
construction, and the per-finding delta is never negative. It also has a real
interpretation: the probability that at least one independent hazard is live.

The `confidence` multiplier handles a specific false-positive class. A
detection whose only evidence is a text match can be triggered by a *defensive*
agent quoting the suspicious marker back to say "I refuse this". Those rules
are marked `weak` and contribute at 0.4×, so they raise the score without
dominating it.

---

## Step 8 — The human gate

Findings carry two independent fields, and keeping them separate is a
deliberate design point:

- `confidence` — set by the rule, describing evidence strength.
- `status` — `open` / `confirmed` / `dismissed`, set by a human.

Open critical or high findings block badge issuance and report download. The
badge serves a "PENDING" state, the report returns 409 with the count.

The reason is reputational. The badge is meant to go in a public README. An
unreviewed false positive should not be able to publish an F grade for someone
else's project. Machine judgement gates on human judgement for anything that
becomes public.

---

## Step 9 — Delivery surfaces

Three ways to consume it, because a security tool nobody runs is worthless:

- the **Next.js dashboard**, streaming live over WebSocket with `resume_from`
  replay so a dropped connection recovers instead of losing history;
- an **embeddable SVG badge**, HMAC-signed, deliberately unauthenticated
  because the scan UUID is the capability and the badge leaks only a letter
  grade;
- a **GitHub Action**, standard library only so it runs on any runner, which
  fails the build on a severity threshold or a score ceiling.

---

## If they ask what I would do next

Have a real answer ready. Mine, in priority order:

1. **Automated end-to-end tests.** The integration and e2e suites are empty
   directories today. I verified the full pipeline manually — upload, scan,
   detection fires, badge gates, report downloads — and that manual run is
   exactly what should be automated first.
2. **Agent log capture.** When a scan produces no events, I currently cannot
   tell whether the agent crashed or simply did nothing, because the container
   is destroyed at teardown. Capturing stdout/stderr into the event stream
   would have saved me hours of debugging.
3. **A recorded-traffic mode.** Replaying a captured session against a changed
   agent would turn this into a regression tool, not just a scanner.
