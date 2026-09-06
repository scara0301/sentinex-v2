# The hard questions

These are the ones that actually separate candidates. Grouped by what the
interviewer is testing.

---

# Testing your understanding of the core idea

### "Why not just do static analysis? It's cheaper and safer."

Because the property I care about is not in the source. An agent's source shows
it *has* a database tool and an email tool. Whether it will read a customer
record and email it to an attacker depends on what the model decides when it
sees a particular tool response at runtime. That decision is not written down
anywhere.

I do use static analysis — the manifest loaders are pure AST — but only for
metadata: framework, declared tools, models, expected secrets. Detection is
entirely behavioural. The two answer different questions.

### "Your detections are just string matching on hostnames. Isn't that trivially evadable?"

Yes, and that is the correct criticism. If the agent base64-encodes the
honeypot value or DNS-exfiltrates it, the substring detections miss it.

Two things make it less bad than it sounds. First, the honeypot values give a
ground truth that pure heuristics do not have: those exact strings exist nowhere
outside the sandbox, so a hit is proof rather than inference. Second, the
`after_injection` ordering constraint means `TOOL-RPP-001` proves a causal
chain, not a coincidence — the agent contacted the attacker host at a sequence
number *after* the injection landed.

What I would add: entropy analysis on outbound payloads to catch encoded
exfiltration, and DNS query logging, which the sandbox is well positioned for
since it already controls resolution.

### "What stops a sophisticated agent from detecting it's in a sandbox and behaving?"

Nothing completely, and I would not claim otherwise. This is the fundamental
limitation of dynamic analysis and it is the same problem malware sandboxes
have had for twenty years.

I closed the obvious tells. The CA has a generic subject name rather than
mitmproxy's default literal `mitmproxy`. The mocks return provider-plausible
headers, `server: nginx` rather than `uvicorn`, provider-shaped error envelopes
for unmatched routes, and mixed-case object ids like `cus_NffrFeUfNV2Hib`
rather than lowercase hex from `uuid4().hex`, which is a structural giveaway.

What remains detectable: timing, the certificate chain itself, and mock
behaviour under unusual inputs. An adversarial agent that specifically targets
this system would find it. My honest position is that this catches negligent
and accidentally-dangerous agents, and raises the cost for a deliberately
evasive one. It is not a proof of safety.

---

# Testing whether you understand your own architecture

### "Walk me through what happens when two scans run at the same time."

Each gets its own Docker network, its own container set, and its own Redis
sequence counter keyed by scan id, so there is no shared mutable state between
them. arq's `max_jobs` caps concurrency per worker at 10.

The thing I had to fix here: the Docker SDK is synchronous. Calls like
`containers.run` and `networks.create` were being made directly from async
functions, so one scan provisioning four containers blocked the shared event
loop — freezing the other nine jobs, their event collectors, and report
rendering. Every Docker call now goes through `asyncio.to_thread`.

### "The worker crashes mid-scan. What happens?"

Two problems, both handled.

The scan row is stuck in a mid-flight status with no live orchestrator, so it
would appear to run forever. On startup the worker calls `fail_stale`, marking
those FAILED.

The containers are orphaned, holding memory and networks. On startup the worker
also reaps them by label.

The critical detail is that **both operations are scoped by `worker_id`.**
Originally they were not — reaping swept every container labelled
`sentinex.scan_id` on the host, and `fail_stale` marked every mid-flight scan
failed. Since running multiple workers is the documented way to scale past
`max_jobs`, restarting one worker destroyed every other worker's live sandboxes.
I added a `worker_id` column, stamped it onto every container label and onto the
scan row, and scoped both operations to it.

### "Three processes write to one event table keyed by (scan_id, seq). How do you avoid collisions?"

A single Redis `INCR` on `scan:{id}:seq`. Redis is single-threaded, so `INCR` is
atomic; every producer gets a unique monotonically increasing number.

The failure mode is Redis being briefly unavailable. The proxy falls back to a
local counter, which can duplicate a sequence number. So proxy-originated events
are written with `INSERT ... ON CONFLICT (scan_id, seq) DO NOTHING` rather than a
plain insert — a duplicate is dropped instead of failing the whole batch.

The key carries a TTL so a crashed scan cannot leak it, and teardown deletes it
explicitly on the happy path.

### "Why is the risk score not just an average?"

Because averaging is wrong in a way that matters. After one critical finding,
discovering three low-severity findings would *lower* the score — the tool would
report an agent getting safer the more vulnerabilities you find. That is a
correctness failure for a security product, not a rough edge.

I use a noisy-OR: `risk = 1 − ∏(1 − hazard_i)`. Every hazard is in `[0, 1]`, so
every factor is in `[0, 1]`, so the product only shrinks and the score is
monotonically non-decreasing by construction. It also has a real reading: the
probability that at least one independent hazard is live.

### "What's the weakest part of your architecture?"

Redis pub/sub, and I can point at the bug it caused. It is fire-and-forget with
no backlog — if nobody is subscribed when a message is published, it is gone
permanently.

The worker's event collector was starting *after* the proxy container, and the
proxy publishes as soon as it is up, so events in that window vanished. The fix
was to subscribe before provisioning and wait for the subscription to be
confirmed live before continuing.

That fix is correct but it is defensive ordering, not a structural guarantee.
Redis Streams would make the whole class of bug impossible — same dependency,
consumer groups, replayable, durable. That is the migration I would do first.

---

# Testing security depth

### "The badge endpoint is unauthenticated. Isn't that a vulnerability?"

It is a deliberate decision, and the reasoning is what matters.

Badges go in public READMEs, so requiring an API key defeats the purpose — and
would mean embedding a workspace key in a public document, which is far worse.

The scan UUID is the capability. It is 122 bits of entropy, unguessable, and
only leaks a letter grade, not findings, not evidence, not the agent name. The
badge is HMAC-signed so a third party can verify it came from this instance.

The residual risk is that someone with a scan id learns a grade. I judged that
acceptable for the value of shareable badges. If a customer disagreed I would
add an opt-in private mode.

### "How do you stop one tenant reading another's data?"

Every workspace-scoped route depends on `get_authorized_workspace`, which does
two things: authenticates the API key, then asserts the key owns the workspace
in the URL path.

That distinction is the whole model. `get_current_workspace` alone only proves
the key is valid for *some* workspace — with only that check, any valid key
could read any workspace's data by changing the URL. It returns 404 rather than
403 on mismatch, so the API does not confirm whether a workspace id exists.

I also found and fixed a gap here: `start_scan` accepted arbitrary scenario ids
without checking ownership, and the worker loads them by id with no filter. A
caller could have run another tenant's private attack definitions. Now every
requested scenario must be builtin or workspace-owned.

### "You accept arbitrary zip uploads. Talk me through the attack surface."

Four attacks, four defences.

- **Zip Slip** — a member named `../../etc/cron.d/x` escapes the extraction
  directory. Every member path is resolved and checked for containment before
  extraction.
- **Zip bombs** — declared uncompressed sizes are summed and rejected over
  500 MB, before extracting anything.
- **Symlink escape** — a tar symlink pointing outside the directory means a
  later write follows it out. Symlinks and hardlinks are rejected in tar
  archives entirely.
- **Memory exhaustion** — the upload is streamed in 64 KB chunks with the size
  limit enforced as it writes, so a large upload cannot exhaust RAM before a
  limit applies.

And the one I initially missed, which is worth admitting: the *contents* were
guarded but the archive's own **destination** was not. The agent `name` form
field and the client filename were both used as path components unvalidated, so
a name of `../../../etc` escaped the upload root. Both are now validated, with a
resolved-path containment check as defence in depth.

### "Uploaded code — do you ever execute it outside the sandbox?"

No, and that is an explicit invariant. Every manifest loader is AST-only; the
`AgentLoader` base class documents "MUST NOT import or exec user code."

The reason is that `import` runs module-level code. If I imported an uploaded
module to inspect its tools, an attacker's `import` side effect would execute on
my API host, outside any sandbox, with the API's privileges. `ast.parse` builds
a syntax tree without executing anything.

### "What's in the manifest that shouldn't be?"

Good question to have thought about. MCP configurations carry an `env` block
that routinely holds API keys, and the manifest is persisted to the database and
returned over the API. `_sanitize_server_cfg` keeps the variable *names*, which
are useful signal, and replaces every value with `***redacted***`.

---

# Testing engineering judgment

### "How do you test something this stateful?"

By making the stateful parts thin and the logic pure.

The detection engine is a pure function: `evaluate(events) -> drafts`, taking
plain dicts. No Docker, no Redis, no database. The most security-critical code
in the system is unit-testable in milliseconds, and that is a direct consequence
of the event-sourcing choice.

Same for the scoring engine, the DSL parser, the badge grading, the diff
applier, and the CI gate logic — `evaluate_gate` is deliberately separated from
all the HTTP calls around it so the decision is testable.

167 unit tests. The honest gap: the integration and e2e directories contain only
empty `__init__.py` files, so those Make targets currently pass without
asserting anything. I verified the full pipeline manually — and that manual run
is exactly what I would automate first.

### "You have 167 unit tests and no integration tests. Defend that."

I would not defend it as ideal, but I would defend the ordering.

The highest-risk logic is the detection rules and the scoring, because a bug
there means silently wrong security verdicts. Those are pure functions and they
are thoroughly covered. The orchestration is riskier to get wrong but its
failures are loud — a container does not start, a scan fails — rather than
silent.

That said, the bugs that actually hurt me were the ones unit tests structurally
cannot catch: a build failing on line endings, a Dockerfile whose username
collided with a system account, a proxy hook that never fires for unreachable
hosts. Every one of those needed something running. So the argument for e2e
tests here is not coverage percentage, it is that this system's failure modes
live in the integration seams.

### "Tell me about a bug that taught you something."

Use one from `06-bugs-i-found-and-fixed.md`. The strongest is the flagship
detection that could never fire, because it is a two-layer bug where fixing the
obvious layer is not enough.

### "What would you do differently if you started over?"

1. **Redis Streams from the start**, not pub/sub — the lost-event class of bug
   would be structurally impossible rather than avoided by careful ordering.
2. **Capture agent stdout/stderr into the event stream.** When a scan produces
   no events I currently cannot distinguish "the agent crashed" from "the agent
   did nothing", because the container is destroyed at teardown. That cost me
   real debugging time.
3. **An end-to-end test on day one**, even a crude one. Three of the four
   Critical bugs were invisible to source review and would have been caught the
   first time anything actually ran.

---

# Questions where the right answer is "I don't know"

Be ready to say this cleanly. It is a strength when paired with a method.

- **"How does this perform at 1000 concurrent scans?"** I don't know — I have
  not load-tested it. `max_jobs` is 10 per worker and scaling is horizontal via
  more worker containers, but the bottlenecks I would expect are host memory for
  containers and the events table write rate. I would measure before designing
  for it.
- **"What's the false-positive rate?"** I don't have one. I have no labelled
  corpus of agents to measure against. That is why the `confidence` field and
  the human review gate exist — I designed around not knowing, rather than
  assuming the rules are right.
- **"Would this catch [specific published attack]?"** If you don't know the
  attack, say so and ask them to describe it, then reason about it out loud
  against the detection primitives you do have. Reasoning in the open is worth
  more than a guessed yes.
