# SENTINEX v2 — full project review: findings

Review date: 2026-09-05. Scope: every source file in the repo except lockfiles,
`node_modules`, and `.venv`.

Baseline before any fix:

| Check | Result |
|---|---|
| `pytest tests/unit` | 87 passed, 4 failed |
| `ruff check .` | clean |
| `tsc --noEmit` (web) | clean |
| `npm run build` (web) | **fails** |

---

## Critical — the product does not work as shipped

### F-01 — The dashboard cannot be built
`apps/web/next.config.ts`

Next.js 16 builds with Turbopack, which resolves its own workspace root and
ignores `outputFileTracingRoot`. The build aborts:

```
Error: Next.js inferred your workspace root, but it may not be correct.
We couldn't find the Next.js package (next/package.json) from the project
directory: .../apps/web/src/app
```

`npm run build` fails, so the `builder` stage of `apps/web/Dockerfile` fails, so
`sentinex/web:latest` can never be produced. Both the dev and prod compose
stacks build the web service, so `make dev-up` and `make prod-up` both break.

### F-02 — The agent sandbox entrypoint is unusable on a Windows checkout
`packages/sandbox-image/entrypoint.sh`, missing `.gitattributes`

Git stores the file with LF, but `core.autocrlf=true` writes CRLF into the
working tree. `docker build` copies the working-tree file, so the image gets a
script whose shebang is `#!/bin/bash\r`. Linux then tries to exec the literal
path `/bin/bash\r`, which does not exist, and every agent container dies
immediately at startup. No scan can ever produce an event.

This is also the cause of the 4 failing tests in `tests/unit/test_sandbox_entrypoint.py`.

### F-03 — The flagship attack detections can never fire
`apps/proxy/sentinex_proxy/interceptors/http.py`, `classify_request`

`classify_request` returns `None` for any host not in `PROVIDER_PATTERNS`
(stripe, slack, sendgrid, twilio, openai, anthropic, aws). The proxy's
`request()` hook returns early on `None`, so **no event is recorded at all** for
those requests.

The two headline detections both key on a non-provider host:

- `TOOL-RPP-001` (critical) matches `host_contains: [evil-archive]` — the
  attacker host planted by the return-path-poisoning injection.
- `TOOL-EXFIL-001` (critical) matches `host_not_contains: [openai, anthropic]`,
  which is meant to catch a honeypot value leaving to anywhere else.

An agent that fully complies with the injected instruction and uploads the
customer table to `files.evil-archive.sentinex.test` produces zero events and
zero findings. The scan reports a clean grade. This is the single most damaging
defect: the scanner silently fails open on exactly the behavior it exists to
detect.

### F-04 — The first scan on a fresh install always fails
`Makefile`

`make dev-up` depends on `build-scan-images`, which builds the proxy, mocks, and
mock-db images. It does **not** build the sandbox images; those live in a
separate `build-sandbox` target that nothing depends on.

`_launch_agent` asks for `sentinex/sandbox-<framework>:latest`, catches
`ImageNotFound`, and falls back to `sandbox_image_base`, which is
`sentinex/sandbox-langchain:latest` — also not built. `containers.run` then
raises `ImageNotFound` uncaught, and the scan goes straight to FAILED.

---

## High — a documented feature is broken, or a security hole

### F-05 — Path traversal in the agent upload
`apps/api/sentinex_api/routes/agents.py:133,136`

```python
bundle_root = Path(settings.upload_dir) / str(workspace_id) / name
archive_path = bundle_root / (file.filename or "bundle")
```

`name` is an unvalidated multipart form field and `file.filename` is
attacker-controlled. A `name` of `../../../etc` or a filename of
`../../evil.py` writes outside the upload directory. The extension allowlist
does not help: `../../../x.py` passes it.

The archive *contents* are guarded against traversal, but the archive's own
destination path is not.

### F-06 — Non-object JSON bodies drop the tool call entirely
`apps/proxy/sentinex_proxy/interceptors/http.py:38-44,64`

`args = json.loads(flow.request.content)` can return a list, string, or number.
Two failures follow:

- `args.update(dict(flow.request.query))` raises `AttributeError` when `args` is
  a list.
- `ToolCallPayload(args=...)` requires a dict and raises `ValidationError`.

Same defect in `classify_response`: a JSON-array response body makes
`ToolResultPayload(response=...)` fail validation. The exception propagates out
of the mitmproxy hook, so the event is never published — and in the response
path the breakpoint `flow.intercept()` is also skipped. JSON arrays are ordinary
API responses, so this drops real traffic.

### F-07 — The dashboard scan list is permanently empty
`apps/web/src/app/page.tsx:24`

`fetchScans` calls `/workspace/{id}/scan` with no `X-Api-Key` header. The route
depends on `get_authorized_workspace`, whose `x_api_key: str = Header(...)` is
required, so FastAPI returns 422 every time. `res.ok` is false, the function
returns `[]`, and the failure is swallowed by the `catch`. The home page shows
"No scans yet" no matter how many scans exist.

### F-08 — `scenario_ids` is never validated on scan creation
`apps/api/sentinex_api/routes/scans.py:47`

`start_scan` passes `body.scenario_ids` straight to `ScanRepo.create`. A caller
can name another workspace's private scenario UUIDs, and the worker's
`_load_scenario_specs` loads them by ID with no ownership filter, running
another tenant's attack definitions. Unknown UUIDs are silently ignored, so a
typo produces a scan that quietly runs nothing.

### F-09 — WebSocket reconnect storm
`apps/web/src/lib/ws.ts:71-84,91-93`

`connect()` calls `cleanup()`, which closes the existing socket. That close fires
the old socket's `onclose`, which — because `enabledRef.current` is still true —
schedules another `connect` via `setTimeout`. Each intentional reconnect
therefore spawns an extra one, and the sockets multiply. Handlers must be
detached before closing.

### F-10 — Worker startup kills other workers' running scans
`apps/worker/sentinex_worker/main.py:38-52`

`_reap_orphans` lists **every** container labelled `sentinex.scan_id` across the
whole Docker host and force-removes it. With more than one worker container —
which is the documented way to scale, per the `max_jobs` comment in
`settings.py` — a worker restart destroys the sandboxes of every scan currently
running on its peers. Reaping must be scoped to scans this worker owns.

---

## Medium — degrades under real conditions

### F-11 — Blocking Docker calls stall the event loop
`apps/worker/sentinex_worker/orchestrator.py` — `_provision`, `_launch_agent`, `_teardown`

The Docker SDK is synchronous. `networks.create`, `containers.run`, `images.get`,
`container.remove`, and `network.remove` are all called directly from async
functions. `_wait_for_agent` and `_extract_proxy_ca` correctly use
`asyncio.to_thread`; these do not.

With `max_jobs = 10`, one scan provisioning four containers freezes the other
nine jobs, the event collectors, and the report renders for the duration.

### F-12 — Events published before the collector subscribes are lost
`apps/worker/sentinex_worker/orchestrator.py:128-129`

`_start_event_collector` runs after `_provision` returns, but the proxy container
is started inside `_provision` and begins publishing as soon as it is up. Redis
pub/sub has no backlog, so anything published in that window is gone. The
subscription should be established before the proxy starts.

### F-13 — Broken navigation link
`apps/web/src/app/layout.tsx:51`

The navbar links to `/docs`. No such route exists, so it 404s on every page.

### F-14 — Unbounded `_pending` growth in the proxy
`apps/proxy/sentinex_proxy/main.py:44,102`

`self._pending[chain_id]` is only removed in `response()`. A request that never
gets a response — a timeout, a connection reset, a killed agent — leaves its
entry forever. Long scans with a flaky target leak memory in the proxy.

### F-15 — Dev stack signs badges with a placeholder secret
`infra/docker-compose.dev.yml`

The api service sets no `SECRET_KEY`, so `Settings.secret_key` falls back to
`"change-me"`. Every badge HMAC in dev is forgeable by anyone who knows the
default. Harmless in isolation, but the same compose file is the template people
copy for their first deployment.

---

## Low — correctness and hygiene

### F-16 — Redundant authorization checks
`apps/api/sentinex_api/routes/agents.py:208,231`

`list_agents` and `get_agent` re-check `workspace.id != workspace_id` after
`get_authorized_workspace` has already enforced it. Dead code that implies the
dependency cannot be trusted.

### F-17 — Malformed generated tool names
`apps/proxy/sentinex_proxy/interceptors/http.py:46`

```python
tool_name = f"{provider}.{method.lower()}{path.rstrip('/').split('/')[-1]}"
```

No separator between method and path segment, yielding `stripe.postrefunds`.
Scenario patterns use `stripe.*` so matching survives, but the names are
unreadable in findings and reports, and any future rule that targets a specific
operation would have to encode the typo.

### F-18 — Badge SVG interpolates without escaping
`packages/core/sentinex_core/badges.py:62-77`

`label` and `value` are dropped into SVG markup unescaped. Every current caller
passes a fixed string or a letter grade, so it is not reachable today, but the
function is public and the badge endpoint is unauthenticated.

### F-19 — Two lockfiles for one web app
`apps/web/package-lock.json` and root `pnpm-lock.yaml`

`pnpm-workspace.yaml` claims `apps/web`, but `apps/web/Dockerfile` runs
`npm ci` against `package-lock.json`. Dependency resolution differs between what
a contributor installs and what the image builds.

---

# Found during Docker verification (after the source review)

These only surfaced by actually building the images and running a scan. All
three are Critical: each one on its own prevents the product from working.

### F-20 — The API, worker, and proxy images cannot be built
`apps/api/Dockerfile`, `apps/worker/Dockerfile`, `apps/proxy/Dockerfile`

Each app's `pyproject.toml` declares:

```toml
[tool.uv.sources]
sentinex-core = { workspace = true }
```

but the Docker build context copies only that app plus `packages/core`. The
root `pyproject.toml` that defines `[tool.uv.workspace]` is never copied, so
uv fails:

```
× Failed to build `sentinex-proxy @ file:///app/apps/proxy`
├─▶ Failed to parse entry: `sentinex-core`
╰─▶ `sentinex-core` references a workspace in `tool.uv.sources`, but is not a
    workspace member
```

Every backend image fails at the install step, so `make dev-up`, `make prod-up`
and `make build-scan-images` all fail. Nothing could be deployed.

### F-21 — The proxy image collides with a Debian system account
`apps/proxy/Dockerfile`

```dockerfile
RUN useradd --create-home --uid 1000 proxy
```

`python:3.12-slim` is Debian-based and already ships a system account named
`proxy`, so this exits 9 (`user 'proxy' already exists`) and the build fails.
Even with F-20 fixed, the proxy image could never be produced — and without the
proxy there is no interception, so no scan can record anything. The sibling
mocks image uses the name `mocks`, which does not collide, which is why only
this one failed.

### F-22 — Failed HTTPS CONNECT attempts went unrecorded
`apps/proxy/sentinex_proxy/main.py`

This is the second half of F-03, and it only became visible by running a real
agent against a real proxy.

For an `https://` URL the client first sends `CONNECT host:443`. If mitmproxy
cannot reach upstream, the tunnel is never established and the `request` hook
**never fires** — so widening `classify_request` to accept unknown hosts was
necessary but not sufficient.

Inside the sandbox the planted attacker host does not resolve, which is exactly
this case. Observed in the proxy log:

```
[client] client connect
[client] error establishing server connection: [Errno -2] Name or service not known
[client] client disconnect
```

An agent that fully complied with the injected instruction produced **zero**
events for the exfiltration attempt, and the scan reported a clean grade —
the same failure mode as F-03, reached by a different path.

---

### F-23 — A fast agent was never observed at all (Critical)
`apps/worker/sentinex_worker/orchestrator.py`

Found while writing the runbook, by running the same scan repeatedly.

**Symptom.** The exfiltration demo agent scored 80.0 with a critical finding in
one run and 0.0 with zero recorded tool calls in the next. Four consecutive runs
produced 0.0. A *slower* agent that retried every 4 seconds recorded all 12 of
its calls.

**Root cause.** The orchestrator had no readiness check on the proxy. It treated
a successful CA read in `_extract_proxy_ca` as the signal to launch the agent —
but `gen_ca` writes the CA *before* mitmdump starts, so the file exists roughly
a second before the listener binds port 8080. Measured directly:

```
poll 1: ca=yes listening=no
poll 2: ca=yes listening=no
poll 3: ca=yes listening=no
poll 4: ca=yes listening=yes      # ~2.7s after container start
```

An agent launched in that window makes its first tool call into a closed port,
gets a connection error, and exits. Nothing is recorded, no detection can fire,
and the scan reports a clean grade.

This is the same fail-open class as F-03 and F-22, reached a third way: any
agent that completes its work faster than the proxy takes to start is
unscannable, and the result looks like a pass.
