# SENTINEX Philosophy

> *"You cannot find what an agent will do by reading what it was told to do."*

---

## The Core Belief: Behavior Over Intent

Every AI agent security tool that came before SENTINEX asked the same question: *what could this agent do?* They read system prompts, scanned tool definitions, and built threat models from static artifacts.

SENTINEX asks a different question: **what does this agent actually do when it runs?**

An agent's system prompt says "you are a helpful assistant." Its tool list says `read_file`, `send_email`. Neither artifact tells you whether the agent will, when given a carefully crafted return value from `read_file`, forward the contents of `/etc/shadow` to an attacker-controlled email address. Only execution reveals that.

This is not a philosophical preference. It is an empirical one. Runtime behavior is ground truth. Everything else is a model of it.

---

## Principle 1: Controlled Detonation

Security research has a concept called a **controlled detonation** — you detonate a suspicious payload in a contained environment and watch what happens. You don't predict it; you observe it.

SENTINEX is a controlled detonation chamber for AI agents.

The sandbox is not a simulation. It is a real execution environment — real Docker containers, real network interfaces, real database connections. The mock providers respond like real ones. The honeypot data looks like real data. The agent does not know it is being watched.

This matters because agents that detect a testing environment will behave differently. SENTINEX's mock infrastructure is indistinguishable from production at the API level.

---

## Principle 2: The Proxy Is the Source of Truth

We intercept traffic at the network layer, not the code layer.

SENTINEX does not instrument the agent's code. It does not add hooks, monkey-patch libraries, or inject tracers. It places a transparent mitmproxy between the agent and every outbound connection.

This means:
- **Framework-agnostic.** LangChain, AutoGen, raw Python, a compiled binary — it doesn't matter. If it makes HTTP requests, we see them.
- **Tamper-resistant.** The agent cannot lie to the proxy by overriding a logger or disabling a tracer. The network is the ground truth.
- **Complete.** We capture headers, bodies, timing, TLS metadata, and connection graph. Not just what the agent *said* it did.

---

## Principle 3: Never Execute Uploaded Code Directly

Uploaded agent bundles are parsed by AST analysis *before* any execution. We extract tools, entry points, framework signatures, and environment variable usage from the syntax tree — no Python interpreter touches user code until it is inside the isolated sandbox.

This is a hard architectural constraint, not a best-effort measure. The manifest normalizer must never `import`, `exec`, or `eval` uploaded code. If a loader cannot extract information from the AST, it returns a partial manifest and defers to sandbox observation.

The rationale: supply chain attacks on security tooling are high-value targets. A scanning tool that executes the code it scans is itself a vulnerability.

---

## Principle 4: Isolation Is Binary

The sandbox network is `internal=True`. There is no "mostly isolated" mode.

The agent container has exactly one egress path: through the proxy. The proxy has a restricted NIC that allows connections only to known LLM provider IP ranges. Nothing else exits the sandbox.

This is enforced at the Docker network driver level — not by firewall rules the agent could discover and route around, not by application-level checks. The container physically cannot make a TCP connection to an arbitrary host.

Capabilities are dropped at the Linux kernel level (`--cap-drop=ALL`). The filesystem is read-only. PID, memory, and CPU limits are enforced by cgroups, not by application code.

Security boundaries must be enforced by mechanisms that the thing being secured cannot influence.

---

## Principle 5: Observable Risk, Not Theoretical Risk

SENTINEX produces a **risk score**, not a vulnerability checklist.

The distinction matters: a checklist says "this agent has access to a `send_email` tool and an internet connection, therefore it *could* exfiltrate data." A risk score says "this agent, when run against our test scenarios, *did* forward a honeypot credential to an external domain 3 times across 12 tool calls."

Findings are evidence-based. Every finding has:
- The exact tool call that triggered it
- The payload that was sent
- The response that was received
- The rule that fired and why
- A CWE reference
- A remediation suggestion grounded in the actual call, not a generic recommendation

We do not surface theoretical vulnerabilities. We surface observed behaviors.

---

## Principle 6: The Agent Is the Product, Not the Threat

SENTINEX is a security tool, not a surveillance tool.

The goal is not to catch agents misbehaving — it is to help developers ship agents that are provably safe. Every finding comes with a remediation patch. Every risk score comes with a breakdown of which scenarios drove it and which tool categories are responsible.

Developers should be able to run SENTINEX on their agent before shipping it and say: "I know what this agent does. I have watched it run. The behaviors I intended are present; the behaviors I didn't intend are not."

That is a very different goal from "here is a list of ways your agent could be dangerous." One of these leads to better agents. The other leads to security theater.

---

## Principle 7: Reproducibility

A scan is a deterministic experiment. Given the same agent, the same scenario, and the same seed data, SENTINEX should produce the same findings.

This means:
- Mock infrastructure returns deterministic responses
- Seed data is version-controlled
- Scenarios are declarative YAML, not imperative scripts
- The risk score formula is documented and stable

Reproducibility is what separates security testing from security luck. If you can't re-run the scan and get the same result, you don't have a test — you have a sample.

---

## Principle 8: Tenant Isolation Is Non-Negotiable

Every API endpoint that touches scan data, agent data, or event data enforces workspace-scoped authorization. There is no "internal" API surface that bypasses auth. There is no admin mode that can query across workspaces.

The API key is hashed with SHA-256 before storage. The workspace lookup is always by hash. The hash is never logged.

WebSocket connections authenticate via query parameter rather than headers (a WebSocket transport constraint) but use the same hash-based lookup as the REST API. There is no unauthenticated WS path.

Multi-tenancy is a first-class design constraint, not a feature added after the fact.

---

## Principle 9: The Dashboard Is a Security Tool

The live dashboard is not a progress indicator. It is a forensic instrument.

The EventStream shows every event in sequence — not a summarized view, not a filtered view. The timestamp, type, and raw payload of every proxy event, state transition, and finding are available in real time.

The RiskGauge animates not just the current score but the delta from each finding, so the analyst can watch the risk profile build as the agent runs. The top risk drivers are visible at a glance.

The goal is that an analyst watching a scan live should be able to spot anomalies before the scan completes. An agent that suddenly starts hammering a mock API endpoint, or that increases risk by 40 points in two tool calls, should be visible immediately — not discoverable only in the post-scan report.

---

## What SENTINEX Is Not

**Not a WAF or runtime filter.** SENTINEX is a testing tool, not a production firewall. It identifies vulnerabilities before deployment. Blocking bad agent behavior at runtime in production is a different problem with different tradeoffs.

**Not a replacement for red-teaming.** Automated scenario runners cover known vulnerability classes well. They do not replace a human adversary who can reason about novel attack chains. SENTINEX is the floor, not the ceiling.

**Not a compliance checkbox.** A scan result is not a certificate of safety. It is evidence of behavior under specific conditions. The honest framing is: "this agent passed these scenarios under these conditions." Not: "this agent is safe."

**Not framework-specific.** SENTINEX treats all agents the same: as black boxes that make HTTP requests. Framework-specific loaders improve manifest quality, but the security model does not depend on them.

---

## The Design Test

Before adding any feature, ask:

1. **Does it produce observable evidence, or theoretical risk?**
2. **Does it enforce isolation, or rely on the agent's cooperation?**
3. **Does it help developers ship safer agents, or just surface more findings?**
4. **Is the boundary enforced by a mechanism the agent cannot influence?**
5. **Can the result be reproduced in a fresh environment?**

If a feature fails more than one of these, it doesn't belong in SENTINEX.
