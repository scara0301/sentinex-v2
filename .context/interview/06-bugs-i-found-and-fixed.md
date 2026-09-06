# Debugging stories

This is usually your strongest material. Anyone can describe an architecture.
Far fewer can describe a bug they found, how they found it, and what it changed
about how they work.

Each story below follows the same shape: **symptom → investigation → root cause
→ fix → what it taught me.** Practise telling two or three out loud. Do not
recite all of them.

---

## 1. The scanner reported a clean grade on an agent that exfiltrated everything

**This is the best story you have. Lead with it.**

**Symptom.** I wrote a deliberately malicious test agent: it calls the mocked
Stripe API, receives a tool response poisoned with an injected instruction, and
then complies by POSTing a honeypot SSN to the attacker host named in the
instruction. Exactly the behaviour the product exists to catch.

The scan completed successfully. Risk score **0.0**. Zero findings. A clean
grade.

**Investigation.** The pipeline itself was healthy — states transitioned
correctly, the report rendered. So I looked at the recorded events, and there
were no `tool_call` events at all. That narrowed it to the proxy.

I reproduced the sandbox by hand: created the network, started the proxy, ran
the agent through it, and subscribed to the Redis channel to watch what was
actually published.

**Root cause, layer one.** `classify_request` returned `None` for any host not
in a hardcoded list of seven known SaaS providers, and the proxy's `request`
hook returned early on `None` without recording anything.

But both headline detections key on a host that is deliberately *not* a known
provider. `TOOL-RPP-001` matches `host_contains: [evil-archive]`.
`TOOL-EXFIL-001` matches `host_not_contains: [openai, anthropic]`. So the
exfiltration target was silently discarded by design.

**Root cause, layer two — and this is the interesting half.** I widened the
classifier to record every host, rebuilt, reran. The Stripe call now appeared.
The exfiltration attempt still did not.

The proxy log showed why:

```
[client] client connect
[client] error establishing server connection: [Errno -2] Name or service not known
[client] client disconnect
```

For an `https://` URL the client sends `CONNECT host:443` first. mitmproxy must
establish the upstream connection before it can decrypt and surface the inner
HTTP request. The attacker host does not resolve — which is precisely what a
planted attacker host is inside a sealed sandbox — so the tunnel is never
established and **the `request` hook never fires at all.**

Widening the classifier was necessary and insufficient. The event was not being
filtered out; it never existed.

**Fix.** mitmproxy 11 exposes `http_connect_error`, which fires exactly when a
CONNECT fails. I record the attempted host as a `tool_call` from that hook. It
fires only on failure, so there is no double-counting with successful HTTPS.

The `args` carry the failure reason rather than agent data, because the request
body was never sent. That is the honest limit of what is knowable: the scan can
prove the agent *contacted* the attacker host, not what it tried to send. So
`TOOL-RPP-001`, which matches on host, fires; `TOOL-EXFIL-001`, which needs the
honeypot value in the payload, correctly does not.

**Result.** Same agent, same scenario: risk score **80.0**, a critical
`TOOL-RPP-001` finding, with the causal chain visible in the event log:

```
4  tool_call    stripe.get_balance                     host=api.stripe.com
5  tool_result                                         injected=return-path-poisoning
6  tool_call    http.connect_files.evil-archive...     host=files.evil-archive.sentinex.test
```

Sequence 6 is after sequence 5, which is what satisfies `after_injection` and
makes it a demonstrated attack chain rather than a coincidence.

**What it taught me.** A security tool that fails open is worse than no tool,
because it produces false confidence. And I could not have found this by reading
code — every individual function was correct. The defect was in the interaction
between a hardcoded provider list, a detection rule that keyed on hosts outside
it, and a protocol detail about when mitmproxy's hooks fire. It took running the
real thing and watching the wire.

---

## 2. Every agent container died instantly, and the error pointed at bash

**Symptom.** Agent containers exited immediately. The error was
`no such file or directory` — referring to `/bin/bash`, which visibly exists in
the image.

**Investigation.** Checking the entrypoint bytes rather than reading the file:

```
$ head -c 32 packages/sandbox-image/entrypoint.sh | od -c
0000000   #   !   /   b   i   n   /   b   a   s   h  \r  \n   s   e   t
```

**Root cause.** CRLF line endings. Git stores the file with LF, but
`core.autocrlf=true` writes CRLF into the working tree on Windows, and
`docker build` copies the working-tree file verbatim. So the image contains a
script whose shebang is `#!/bin/bash\r`, and Linux tries to exec the literal
path `/bin/bash\r` — which genuinely does not exist. The error was accurate;
it just did not mention the invisible character.

**Fix.** A `.gitattributes` pinning `eol=lf` for shell scripts, Dockerfiles,
YAML, SQL, templates, the Makefile and the Caddyfile, then renormalising the
working tree. Plus a unit test asserting the file contains no `\r`, so it can
never silently regress.

**Verification, in a real container:**

```
$ docker run --rm -v .../bundle:/work:ro sentinex/sandbox-raw_python:latest
sentinex: launching agent entry /work/src/agent_entry.py
agent ran OK
```

**What it taught me.** Cross-platform build reproducibility is not automatic,
and the failure was environment-dependent — it would work perfectly for a
colleague on Linux and fail for me, which is the most expensive kind of bug to
chase. `.gitattributes` is now something I set up at the start of a project, not
after it bites.

---

## 3. The dashboard was always empty, and it was designed to hide why

**Symptom.** The dashboard home page always showed "No scans yet", regardless of
how many scans existed.

**Investigation.** The page was a server component calling the API with plain
`fetch`. The API route requires an `X-Api-Key` header, and no header was being
sent. Confirmed against the live API:

```
GET /workspace/{id}/scan   with key     -> HTTP 200
GET /workspace/{id}/scan   without key  -> HTTP 422
```

**Root cause.** Two compounding problems. The request could never succeed
because the header was missing. And the error was swallowed:

```ts
if (!res.ok) return [];
} catch { return []; }
```

Both failure paths returned an empty array, which renders identically to a
genuinely empty workspace. The UI actively concealed its own breakage.

There was also a structural reason it could not work: the key lives in the
browser, so the request cannot be made during server rendering at all.

**Fix.** Rewrote it as a client component resolving the workspace and key from a
deep link, then `localStorage`, then build-time env — the same order the live
scan page already used, factored into a shared hook. Crucially, an unconfigured
dashboard now says it is unconfigured, and a failed request shows the error.

**What it taught me.** `catch { return [] }` is a decision to hide failures, and
in a UI an empty state and an error state must never look the same. I now treat
any catch block that returns a benign default as a code smell until it is
justified.

---

## 4. Starting one worker killed every other worker's running scans

**Symptom.** Found by reading, not by failure — but the failure would have been
severe and confusing in production.

**Root cause.** On startup, `_reap_orphans` listed **every** container on the
host labelled `sentinex.scan_id` and force-removed it. `fail_stale` marked
**every** mid-flight scan FAILED.

Both are correct for a single worker recovering from its own crash. But running
multiple workers is the documented way to scale past `max_jobs` — so deploying,
restarting, or autoscaling one worker would destroy every scan currently running
on its peers, mid-flight, with no explanation in the logs.

**Fix.** A `worker_id` generated per process, stamped onto every container and
network label and persisted on the scan row via a new migration and index.
Reaping filters on `sentinex.worker_id`; `fail_stale` only touches scans this
worker owns.

**What it taught me.** "Clean up orphans" is a plausible-sounding operation that
is only well-defined once you can answer *whose* orphans. Any global cleanup in
a horizontally scaled system needs an ownership predicate.

---

## 5. Three images had never built, and no source review would find it

**Symptom.** `docker build` failed on the API, worker and proxy images.

**Root cause, part one.** Each app declares
`sentinex-core = { workspace = true }` under `[tool.uv.sources]`, but the build
context copies only that app and `packages/core`. The root `pyproject.toml`
defining the uv workspace is never copied:

```
`sentinex-core` references a workspace in `tool.uv.sources`,
but is not a workspace member
```

**Root cause, part two.** After fixing that, the proxy image still failed:

```
useradd --create-home --uid 1000 proxy   ->  exit code 9
```

Exit 9 is "name already in use". Debian's base image already ships a system
account called `proxy`. The mocks image uses the name `mocks`, which is why only
this one broke.

**Fix.** `--no-sources` on the install so uv resolves `sentinex-core` from the
editable path installed in the same command. Renamed the proxy user to
`sentinex`. And because the orchestrator read the CA from a hardcoded
`/home/proxy/...` path, I changed it to resolve through the image's own
`MITMPROXY_CONFDIR`, so the image and the orchestrator cannot drift apart again.

**What it taught me.** Both defects are invisible in source review — every file
is individually correct, and the bug exists only in the interaction between a
pyproject, a build context, and a base image's user table. Some classes of
defect can only be found by executing the thing.

---

## 6. The WebSocket multiplied its own connections

**Symptom.** Found by reading the reconnection logic.

**Root cause.** `connect()` called `cleanup()`, which closed the existing
socket. That close fired the *old* socket's `onclose` handler, which — seeing
the hook still enabled — scheduled another reconnect on top of the one already
being made. Every intentional reconnect spawned an extra one, so on a flaky
connection sockets multiplied instead of backing off.

**Fix.** Restructured so a single effect owns exactly one socket and tears it
down in its own cleanup, with handlers detached *before* `close()` and an
`active` flag guarding every callback. Retries are driven by an attempt counter
that re-runs the effect, rather than by the connect function calling itself.

**What it taught me.** Any cleanup that triggers the same event as a real
failure needs to distinguish the two. Detaching handlers before closing is the
general form of that fix.

---

## Two more, briefly

**Averaging made the score go down.** The original scoring averaged finding
weights, so after a critical finding, three low-severity findings *lowered* the
score — the tool reported an agent getting safer the more vulnerabilities you
found. Replaced with a noisy-OR, which is monotonically non-decreasing by
construction.

**A JSON array silently dropped a tool call.** `json.loads` on a request body
can return a list, and the code then called `args.update()` on it, raising
`AttributeError` inside the mitmproxy hook. The exception escaped the hook, so
the event was never published — and on the response path it also skipped
`flow.intercept()`, silently breaking breakpoints. JSON arrays are ordinary API
payloads, so this was dropping real traffic. Non-object JSON is now wrapped
rather than passed through.

---

## How to tell these

- **Lead with the symptom, not the cause.** "The scanner gave a clean grade to
  an agent that exfiltrated data" is a hook. "There was a bug in
  classify_request" is not.
- **Show the evidence.** The `od -c` output, the proxy log line, the 422. Real
  artifacts are what make it obvious you actually did this.
- **Name what you changed about how you work.** That is the part they are
  actually assessing.
- **Do not oversell.** Several of these were found by careful reading, not
  heroic debugging. Say which is which. Being precise about that is itself a
  signal.
