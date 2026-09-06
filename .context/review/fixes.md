# SENTINEX v2 — fixes applied

Each entry closes the finding of the same number in `findings.md`.

---

## F-01 — Dashboard build failure (Critical)
`apps/web/next.config.ts`

Added `turbopack.root`. Next 16 builds with Turbopack, which resolves its own
workspace root and ignores `outputFileTracingRoot`, so the build aborted
before compiling anything.

**Caveat, needs your decision.** The build only succeeds with a flat
`node_modules` inside `apps/web` — which is what `apps/web/Dockerfile` produces
via `npm ci`. A pnpm workspace install symlinks `next` out to the repo-root
store, Turbopack resolves the symlink to a real path outside its root, and the
same error returns. See F-19.

**Verified:** `npm ci && npm run build` succeeds; all 3 routes emit.

## F-02 — Sandbox entrypoint broken on Windows checkouts (Critical)
new `.gitattributes`, working tree renormalized

Git stored `entrypoint.sh` with LF, but `core.autocrlf=true` wrote CRLF into
the working tree, and `docker build` copied that file into the image. Linux
then tried to exec `/bin/bash\r`, so every agent container died at startup.

Added a `.gitattributes` pinning `eol=lf` for shell scripts, Dockerfiles,
YAML, SQL, Jinja templates, the Makefile and the Caddyfile, then
renormalized. Added `test_entrypoint_has_unix_line_endings` so a regression
fails the suite rather than the sandbox.

**Verified in a real container:**

```
$ docker run --rm sentinex/sandbox-raw_python:latest
sentinex: no agent entry script found under /work/src or /work
exit code: 3

$ docker run --rm -v .../bundle:/work:ro sentinex/sandbox-raw_python:latest
sentinex: launching agent entry /work/src/agent_entry.py
agent ran OK
exit code: 0
```

## F-03 — Attacker-host detections could never fire (Critical)
`apps/proxy/sentinex_proxy/interceptors/http.py`, `apps/proxy/sentinex_proxy/main.py`

`classify_request` returned `None` for any host outside the seven known
providers, and the proxy's `request()` hook dropped those flows without
recording anything. Both headline detections key on a non-provider host
(`host_contains: [evil-archive]`, `host_not_contains: [openai, anthropic]`),
so an agent that fully complied with an injected instruction produced zero
events and scored clean.

`classify_request` now always returns a descriptor. Unknown hosts are recorded
under a generic `http` provider with the hostname in the tool name, so
`host_contains` and `host_not_contains` see them. Known providers are
unchanged.

## F-04 — First scan on a fresh install always failed (Critical)
`Makefile`, `apps/worker/sentinex_worker/orchestrator.py`

`dev-up` built only the per-scan service images, never the sandbox images, and
`_launch_agent`'s fallback pointed at `sentinex/sandbox-langchain:latest`,
which was equally absent — so `containers.run` raised a bare `ImageNotFound`.

`dev-up` and `prod-up` now depend on `build-sandbox` as well, and the fallback
path checks the base image too, failing with a message that names the fix
(`make build-sandbox`) instead of an SDK exception.

## F-05 — Path traversal in agent upload (High)
`apps/api/sentinex_api/routes/agents.py`

Both the `name` form field and `file.filename` were used as path components
unvalidated. Added `_validate_agent_name` (charset-restricted, 1–128 chars)
and `_safe_upload_filename` (reduces any client filename to a single safe
component, POSIX and Windows separators both), plus a resolved-path
containment check on the destination directory as defence in depth.

**Verified:** 55 tests in `tests/unit/test_upload_paths.py`.

## F-06 — Non-object JSON bodies dropped the tool call (High)
`apps/proxy/sentinex_proxy/interceptors/http.py`

A JSON array body made `args.update()` raise `AttributeError`, and a non-dict
value failed `ToolCallPayload` validation. Either exception escaped the
mitmproxy hook, losing the event — and in the response path, skipping
`flow.intercept()` so breakpoints silently stopped working.

`_decode_body` now wraps any non-object JSON value in a mapping and falls back
to truncated text for non-JSON bodies. Query parameters moved under an
`args["query"]` key so they can no longer clobber body fields.

## F-07 — Dashboard scan list permanently empty (High)
`apps/web/src/app/page.tsx`, new `apps/web/src/lib/session.ts`

The home page was a server component fetching `/workspace/{id}/scan` with no
`X-Api-Key`, so the API returned 422 every time and the `catch` swallowed it.
The workspace id was also a hardcoded demo UUID.

Rewritten as a client component that resolves the workspace id and API key
from a deep link, then localStorage, then build-time env — the same order the
live scan page uses. A missing configuration now says so, and a failed request
shows the error instead of an empty list.

## F-08 — Scenario ownership not validated (High)
`apps/api/sentinex_api/routes/scans.py`

`start_scan` accepted any scenario UUIDs. The worker loads them by id with no
ownership filter, so a caller could run another tenant's private scenarios,
and a mistyped id silently produced a scan that ran nothing. Now every
requested scenario must exist and be either builtin or owned by the caller's
workspace; unknown ids return 404 naming them.

## F-09 — WebSocket reconnect storm (High)
`apps/web/src/lib/ws.ts`

`connect()` called `cleanup()`, which closed the current socket, which fired
that socket's `onclose`, which scheduled *another* reconnect on top of the one
in flight. Sockets multiplied on every retry.

Rewritten so a single effect owns exactly one socket and tears it down in its
own cleanup, with handlers detached before `close()` and an `active` guard on
every callback. Retries are driven by an attempt counter that re-runs the
effect rather than by the connect function calling itself.

## F-10 — Worker startup killed peers' running scans (High)
`apps/worker/sentinex_worker/main.py`, `packages/core` models/repos, migration `0006`

`_reap_orphans` force-removed every container labelled `sentinex.scan_id` on
the host, and `fail_stale` marked every mid-flight scan FAILED. Running more
than one worker — the documented way to scale past `max_jobs` — meant any
restart destroyed its peers' live sandboxes.

Added a `worker_id`: generated per worker process, stamped onto every
container and network via a new `_labels()` helper, and persisted on the scan
row (`scans.worker_id`, migration `0006`, indexed with status). Reaping now
filters on `sentinex.worker_id`, and `fail_stale` only touches scans this
worker owns.

## F-11 — Blocking Docker calls stalled the event loop (Medium)
`apps/worker/sentinex_worker/orchestrator.py`

The Docker SDK is synchronous, but `networks.create`, `containers.run`,
`images.get`, `container.remove` and `network.remove` were called directly
from async functions. With `max_jobs = 10`, one scan provisioning four
containers froze the other nine jobs, their event collectors, and report
rendering. All of them now go through `asyncio.to_thread`, matching what
`_wait_for_agent` and `_extract_proxy_ca` already did.

## F-12 — Events lost before the collector subscribed (Medium)
`apps/worker/sentinex_worker/orchestrator.py`

The collector started *after* `_provision` returned, but the proxy publishes
as soon as its container is up, and Redis pub/sub keeps no backlog. The
collector now starts before provisioning and `_start_event_collector` is
awaited until the subscription is confirmed live (10s cap, warning on
timeout).

## F-13 — Broken navigation link (Medium)
`apps/web/src/app/layout.tsx`

The navbar linked to `/docs`, which does not exist. Repointed at the external
docs URL.

## F-14 — Unbounded `_pending` growth in the proxy (Medium)
`apps/proxy/sentinex_proxy/main.py`

Entries were only removed on response, so timeouts and resets pinned their
envelopes for the life of the scan. Added an opportunistic sweep that drops
entries older than 5 minutes once the map exceeds 256 items.

## F-15 — Dev stack signed badges with a placeholder (Medium)
`infra/docker-compose.dev.yml`

The api service set no `SECRET_KEY`, so badges were signed with the
`"change-me"` default. Now set explicitly, overridable from the environment.

## F-16 — Redundant authorization checks (Low)
`apps/api/sentinex_api/routes/agents.py`

`list_agents` and `get_agent` re-checked what `get_authorized_workspace` had
already enforced. Removed, with a comment recording why the dependency is
sufficient.

## F-17 — Malformed generated tool names (Low)
`apps/proxy/sentinex_proxy/interceptors/http.py`

`stripe.postrefunds` (no separator) is now `stripe.post_refunds`. Object ids
are skipped when picking the operation segment, so 200 calls against 200
different customers collapse to one tool name instead of 200 — which is what
makes the call-volume detections meaningful. A test asserts the names still
match the `stripe.*` globs the built-in scenarios use.

## F-18 — Badge SVG interpolated unescaped (Low)
`packages/core/sentinex_core/badges.py`

Label and value now go through `xml.sax.saxutils.escape`, and the aria-label
through `quoteattr`. Width is still computed from the raw text so entity
expansion cannot distort the layout.

---

# Additional work not in the original findings

## Lint and type checking now pass
`make lint` runs `ruff check . && mypy apps/ packages/`. Ruff was clean; mypy
reported 31 errors, so the target had been failing. Now clean.

Genuine bugs found and fixed along the way:

- **`packages/core/sentinex_core/db/base.py`** — calling `get_session()` before
  `init_engine()` raised `TypeError: 'NoneType' object is not callable`, which
  names nothing useful. Now raises a `RuntimeError` that says to call
  `init_engine` during startup.
- **`apps/api/sentinex_api/routes/scans.py`** — `review_finding` re-read the
  finding after the update and dereferenced it without a `None` check. A
  finding deleted in that window would have thrown `AttributeError` and
  returned a 500. Now returns 404.
- **`packages/core/sentinex_core/db/migrations/env.py`** — an unset
  `sqlalchemy.url` was passed straight to `create_async_engine`. Now fails
  with a message naming the setting.
- **`apps/api/sentinex_api/routes/agents.py`** — `_extract_bundle` reused one
  loop variable for both `ZipInfo` and `TarInfo` members.

The remainder were annotations: `LOADERS` typed as `list[type[AgentLoader]]`,
a `CursorResult` cast for `rowcount`, `Literal` casts where DB strings feed
Literal-typed payload fields, and a `[tool.mypy]` override block for
dependencies that ship no stubs (docker, yaml, aiofiles, weasyprint,
mitmproxy, arq) so real errors are not buried under missing-stub notes.

## Dashboard lint
`npx eslint .` reported 5 errors under the React Compiler rules that ship with
`eslint-config-next` 16 — 3 of them pre-existing. All fixed:

- `RiskGauge` no longer keeps a separate `isPulsing` state set synchronously
  inside an effect; the pulse is derived from animation progress.
- Session values are read through `useSyncExternalStore`, the sanctioned
  pattern for browser-only external state, instead of an effect that calls
  setState. The server snapshot is the build-time default, so there is no
  hydration mismatch.
- The dashboard's polling effect keeps its fetch inside an async closure with
  a `cancelled` guard, so a late response cannot write into stale state.

## New tests
- `tests/unit/test_proxy_classifier.py` (25 tests) — unknown-host recording,
  non-object JSON bodies, tool naming, and that classifier output validates
  against the event schema. The classifier previously had no test at all.
- `tests/unit/test_upload_paths.py` (55 tests) — agent-name and filename
  validation, containment properties, extension allowlist.
- `tests/unit/test_sandbox_entrypoint.py` — added the line-endings assertion,
  and made the harness portable: it inherits the real environment, joins PATH
  with `os.pathsep`, uses POSIX paths, and skips when `bash` resolves to a
  non-working interpreter. On Windows it was joining PATH with `:`, which left
  bash unusable and silently resolved the WSL stub — the four failures in the
  baseline were this, not the product.

---

# Fixes for the Docker-verification findings

## F-20 — Backend images could not be built (Critical)
`apps/api/Dockerfile`, `apps/worker/Dockerfile`, `apps/proxy/Dockerfile`

Added `--no-sources` to the `uv pip install` step in all three. The flag makes
uv ignore `[tool.uv.sources]`, so `sentinex-core` resolves from the editable
path installed in the same command instead of from a uv workspace that does
not exist inside the build context. Each Dockerfile carries a comment
explaining why, so the flag is not removed as noise later.

**Verified:** all three images build.

## F-21 — Proxy image username collided with a Debian account (Critical)
`apps/proxy/Dockerfile`, `apps/worker/sentinex_worker/orchestrator.py`

Renamed the runtime user from `proxy` to `sentinex` and moved
`MITMPROXY_CONFDIR` to `/home/sentinex/.mitmproxy`.

The orchestrator read the CA from a hardcoded `/home/proxy/...` path, which
would have silently broken with the rename. It now resolves the path through
the image's own `MITMPROXY_CONFDIR`, so the two cannot drift apart again.

**Verified:** the image builds, `gen_ca` writes the CA store, and the exact
command the orchestrator runs returns the certificate.

## F-22 — Failed HTTPS CONNECT attempts went unrecorded (Critical)
`apps/proxy/sentinex_proxy/main.py`, `apps/proxy/sentinex_proxy/interceptors/http.py`

Added an `http_connect_error` addon hook (mitmproxy 11) plus a
`classify_connect_attempt` classifier. When a CONNECT fails, the attempted
host is now recorded as a `tool_call`, which is what the host-matching
detections need.

`args` carries the failure reason rather than agent data, because the request
body was never sent. That is the honest limit of what is knowable: the scan
can prove the agent *contacted* the attacker host, not what it tried to send.
`TOOL-RPP-001` matches on host, so it fires; `TOOL-EXFIL-001` needs the
honeypot value in the payload, so it correctly does not.

---

## F-23 — Fast agents were never observed (Critical)
`apps/worker/sentinex_worker/orchestrator.py`

Added `_wait_for_proxy_ready`, called between the CA extraction and the agent
launch. It probes with a real TCP connect from inside the proxy container
(`socket.create_connection(('127.0.0.1', 8080), 1)`) rather than checking port
state, so it returns only once the proxy will genuinely serve a request. Polls
every 0.5s up to 30 seconds and logs a warning if the proxy never comes up,
rather than silently proceeding.

**Verified.** The identical fast agent, five consecutive runs:

| | before | after |
|---|---|---|
| risk score | 0.0 (4/4 runs) | 80.0 (5/5 runs) |
| tool calls recorded | 0 | 2 |
| findings | 0 | 1 × critical `TOOL-RPP-001` |
