# Code walkthrough — module by module, function by function

Organised the way you should explain it: core first (everything depends on it),
then the services that use it.

---

# packages/core — the shared library

## `db/base.py`

Holds the SQLAlchemy async engine as module-level state.

- **`Base`** — the declarative base all models inherit from. Carries the
  metadata Alembic autogenerates against.
- **`init_engine(database_url)`** — builds the async engine and sessionmaker.
  Called once at startup by both the API and worker. Two settings matter:
  `pool_pre_ping=True` issues a cheap liveness check before handing out a
  pooled connection, which prevents "server closed the connection unexpectedly"
  after a database restart or an idle timeout. `expire_on_commit=False` means
  ORM objects stay usable after `commit()` — without it, touching any attribute
  post-commit triggers a lazy reload, and on an async session a lazy load
  raises. That single flag prevents a whole class of confusing crash.
- **`get_session()`** — an async context manager yielding a session. Raises a
  clear `RuntimeError` if `init_engine` was never called.

**Why module-level global state:** it is effectively a singleton connection
pool, and FastAPI's dependency system layers cleanly on top via `get_db`. The
cost is that it is awkward to run two different databases in one process, which
this system never needs.

## `db/models.py`

Nine tables. The ones worth explaining:

- **`Workspace`** — the tenant boundary. Stores `api_key_hash`, never the key.
  SHA-256, and the plaintext is shown exactly once at creation.
- **`Agent`** — unique on `(workspace_id, name, version)`. Versions are
  integers that increment per name, so re-uploading creates v2 rather than
  overwriting v1. That matters because a scan references an agent, and you must
  be able to reproduce what was scanned.
- **`Event`** — composite primary key `(scan_id, seq)`. No surrogate id. This
  is the event-sourcing choice: identity *is* position in the scan's stream.
- **`Finding`** — carries `confidence` (machine) and `status` (human) as
  separate fields, plus the review audit columns.
- **`Scan.worker_id`** — which worker process owns this scan while it runs, so
  crash recovery can be scoped correctly.

**The `Finding` ↔ `Remediation` relationship is worth knowing**, because it is
the kind of thing an interviewer probes. Each side has its own physical foreign
key pointing at the other table — `findings.remediation_id` and
`remediations.finding_id`. There is no single shared FK, so SQLAlchemy cannot
infer a consistent one-to-many direction and refuses to configure the mapper if
you declare `back_populates` on both sides. Each relationship is therefore
declared independently, scoped to its own `foreign_keys`. Neither is read
through the ORM anywhere — everything goes through the id columns and repo
methods — so this is safe. The `remediation_id` FK is `DEFERRABLE INITIALLY
DEFERRED` because of the circular reference: you insert a finding, insert its
remediation, then set the pointer, all inside one transaction.

**Indexes:** `(scan_id, severity)`, `(scan_id, status, severity)` for the
badge-gating query, `(workspace_id, created_at)` for scan listing,
`(worker_id, status)` for crash recovery. Each one exists for a specific query
in the codebase.

## `db/repos.py` — the repository layer

One class per aggregate. Every method takes an `AsyncSession` injected at
construction; **no repo ever commits.** Commit is the caller's decision, which
is what makes it possible to compose several repo calls into one transaction —
`_run_detections` creates a finding, creates a remediation, and links them, then
commits once.

Methods worth calling out:

- **`WorkspaceRepo.get_by_api_key(hash)`** — the authentication lookup.
- **`ScanRepo.claim(scan_id, worker_id)`** — stamps ownership at scan start.
- **`ScanRepo.fail_stale(statuses, worker_id)`** — on worker startup, marks
  scans stuck in a mid-flight status *belonging to this worker* as FAILED. They
  have no live orchestrator, so they would otherwise appear to run forever. The
  `worker_id` filter is essential: without it, starting one worker fails every
  other worker's running scans.
- **`EventRepo.bulk_upsert(rows)`** — `INSERT ... ON CONFLICT (scan_id, seq) DO
  NOTHING`. Used for proxy-originated events, where the sequence counter is
  best-effort: if Redis hiccups the proxy falls back to a local counter and can
  emit a duplicate seq. Idempotent insert tolerates that instead of failing the
  whole batch.
- **`FindingRepo.count_unreviewed_blocking(scan_id)`** — counts open
  critical/high findings. This single query is the badge and report gate.
- **`ScenarioRepo.list_visible(workspace_id)`** — builtin scenarios OR the
  caller's own. The tenancy filter.

**Why a repository layer rather than querying in routes:** the queries are
reused across the API, worker and jobs, and it keeps SQLAlchemy out of HTTP
handlers so both are independently testable.

## `events/schema.py`

Pydantic models for every event payload type plus an `EventEnvelope` wrapper
carrying `v`, `scan_id`, `seq`, `ts`, `type`, `payload`.

**Why an explicit `type` field rather than inferring from payload shape:** the
payloads are structurally similar enough that a discriminated union on shape
alone would be ambiguous, and an explicit tag means consumers can route without
parsing the payload at all. `v: Literal[1]` is a schema version so the wire
format can evolve.

`StatePayload` uses `from_` with `alias="from"` because `from` is a Python
keyword, with `populate_by_name=True` so both work.

## `scenarios/dsl.py`

- **`InjectionSpec`** — `tool` (an fnmatch glob), `mode` (merge or replace),
  `payload`, `max_hits` (0 = unlimited).
- **`MatchSpec`** — the detection condition: `event` type, `tool` glob,
  `host_contains`, `host_not_contains`, `args_contain`,
  `args_contain_honeypot`, `after_injection`, `min_count`.
- **`DetectionSpec`** — `rule_id` (regex-constrained to
  `^[A-Z0-9][A-Z0-9-]{2,63}$`), severity, category, title, CWE list, the match,
  and `confidence`.
- **`ScenarioSpec`** — the whole document. `detections` has `min_length=1`, so
  a scenario that detects nothing is rejected at parse time.
- **`_known_category`** — a field validator rejecting categories outside the
  known set, because an unknown category silently falls back to a 1.0 score
  multiplier and would quietly mis-score.
- **`parse_scenario_yaml(text)`** — `yaml.safe_load` (never `load`, which can
  instantiate arbitrary Python objects), then Pydantic validation, converting
  every failure into a `ValueError` the API turns into a 422.

## `scenarios/runner.py`

The pure detection engine. This is the file to walk them through if they ask to
see code.

- **`injection_rules()`** — flattens every scenario's injections into the JSON
  the proxy loads from Redis.
- **`evaluate(events)`** — sorts by `seq`, computes first-injection points, then
  for each detection collects matching events and emits a `FindingDraft` when
  the count reaches `min_count`. Evidence records the scenario slug, match
  count, and up to 25 event sequence numbers.
- **`_first_injected_seqs(events)`** — maps scenario slug → sequence number of
  the first `tool_result` it poisoned. The `None` key holds the earliest
  injection across all scenarios, used as a fallback so a detection can anchor
  on "after any injection".
- **`_matches(det, event, injected_at)`** — the predicate. Checks type, then
  `after_injection` (requires `seq > injected_at`, strictly greater — the
  causing event cannot be the caused one), then tool glob, then host
  substrings, then needles.
- **`_searchable_blob(payload)`** — **this one is a security control, not a
  helper.** It serialises *only* `args`, `content` and `response` before
  substring matching. If it serialised the whole payload, the `host` field
  would be searchable, and a detection matching the string `evil-archive` in
  arguments would also fire on the transport metadata recording that the host
  was `evil-archive` — a false positive caused by the detector observing itself.
  Restricting the search to agent-controlled and tool-returned fields prevents
  that.

## `scoring/engine.py`

- **`FindingEntry.__post_init__`** — computes `weight = severity ×
  category_multiplier × confidence_multiplier`.
- **`MAX_SINGLE_WEIGHT`** — the largest weight any single finding can produce
  (critical × the highest category multiplier). Used to normalise into `[0, 1]`
  so one maximal finding alone pins the score at 100.
- **`add_finding(...)`** — computes the hazard, multiplies it into a running
  survival product, recomputes, returns `(new_score, delta)`.
- **`_compute()`** — `min(100, 100 × (1 − survival))`.
- **`top_drivers`** — the five highest-weight rule ids, for the dashboard.
- **`compute_from_findings(tuples)`** — one-shot construction used by the
  orchestrator.

The invariant to state clearly: **the score is monotonically non-decreasing**,
because every `(1 − hazard)` factor is in `[0, 1]` so the product only shrinks.

## `badges.py`

- **`grade_for_score(score)`** — bands: ≤10 A+, ≤25 A, ≤45 B, ≤65 C, ≤85 D,
  else F. `None` → `"?"`.
- **`render_badge_svg(grade, score, label)`** — builds a shields.io-style SVG.
  Width is computed from character count; text is XML-escaped.
- **`sign_badge(secret, scan_id, grade, signed_at)`** — HMAC-SHA256 over the
  identity tuple, so a third party can verify the badge was issued by this
  instance.
- **`verify_badge(...)`** — uses `hmac.compare_digest`, which is
  constant-time. A naive `==` leaks information through timing and would allow
  forging a signature byte by byte.

## `manifest/` — the loaders

- **`normalize_upload(root, entry_file, metadata)`** — tries each loader in
  order, first `detect()` match wins. `RawPythonLoader` is last and always
  matches.
- **`AgentLoader`** — abstract base with `detect()` and `load()`. Its docstring
  carries the rule that matters: **MUST NOT import or exec user code.**

Per loader, the distinguishing logic:

- **LangChain** — walks the AST for `@tool` decorators, `BaseTool` /
  `StructuredTool` subclasses, and LLM constructor calls mapped to
  `(provider, model_kwarg)`. Also harvests `os.getenv("X")` and
  `os.environ["X"]` into `secrets_required`.
- **CrewAI** — `Agent(role=...)` becomes graph nodes; `Crew(process=...)`
  becomes edges. Sequential means a handoff chain in roster order; hierarchical
  synthesises a manager node delegating to every member; `allow_delegation=True`
  adds delegate edges to every other member.
- **AutoGen** — agent class instantiations become nodes, `x.initiate_chat(y)`
  calls become handoff edges, `GroupChat(agents=[...])` becomes broadcast edges.
- **MCP** — no code. Reads `mcpServers` from `mcp.json` or
  `claude_desktop_config.json`. **`_sanitize_server_cfg` keeps env variable
  *names* but replaces every value with `***redacted***`**, because MCP env
  blocks routinely hold API keys and the manifest is persisted to the database
  and returned over the API.
- **OpenAI Assistants** — configuration rather than code, from an
  `assistant_id` form field or an `assistant.json`.
- **Raw Python** — fallback. Guesses tools from function names containing
  `tool`, `action`, `execute`, `run`, `invoke`, `call`, `handler`, and infers
  side effects: `requests`/`httpx`/`aiohttp` calls → `network`, `open()` with a
  write mode → `fs:write`. It appends a warning saying detection is
  heuristic — being explicit about low confidence rather than presenting a
  guess as fact.

## `remediation/`

- **`build_remediation(rule_id, category)`** — returns
  `(playbook_md, diff_or_None)`. Exact rule templates for the six known rules;
  otherwise a per-category fallback playbook with no diff.
- **`_new_file_diff(path, content)`** — synthesises a unified diff creating
  `sentinex_policy.yaml`, a guardrail policy file added to the bundle.
- **`apply_unified_diff(root, diff_text)`** — a minimal unified-diff applier
  supporting file creation (`--- /dev/null`) and in-place modification.
- **`_safe_join(root, rel)`** — resolves and asserts containment, so a patch
  containing `../../etc/passwd` cannot escape the bundle. Patches are
  attacker-influenced input.
- **`_locate(original, expected, hint)`** — fuzzy hunk location, searching
  outward from the stated line up to 50 lines, so a patch still applies if the
  file drifted slightly.

---

# apps/api — the control plane

## `deps.py`

- **`get_db()`** — yields a session per request.
- **`get_current_workspace(x_api_key, db)`** — hashes the key, looks up the
  workspace, 401 if absent. Proves the key is valid for *some* workspace.
- **`get_authorized_workspace(workspace_id, x_api_key, db)`** — additionally
  asserts the key owns the workspace in the path. **This distinction is the
  whole tenancy model.** `get_current_workspace` alone would let any valid key
  read any workspace's data by changing the URL. It returns 404 rather than 403
  on mismatch, so the API does not confirm that a workspace id exists.

## `main.py`

- **`lifespan(app)`** — startup initialises the engine, seeds builtin
  scenarios, creates the arq pool, and constructs the WebSocket manager and
  Redis fanout onto `app.state`. Shutdown closes both.
- **`_seed_builtin_scenarios()`** — upserts the packaged YAML scenarios so they
  are selectable by id. Wrapped in a try/except that only warns, because
  failing here (for example, migrations not yet applied) must not prevent the
  API from starting.

## `routes/workspaces.py`

- **`create_workspace`** — generates `sx-<43 url-safe chars>` via
  `secrets.token_urlsafe(32)` (`secrets`, not `random` — `random` is a
  predictable Mersenne Twister), stores the SHA-256, returns the plaintext once.

## `routes/agents.py`

- **`_validate_agent_name(name)`** — the name becomes a directory component, so
  it is restricted to `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. Without this,
  `../../etc` escapes the upload root.
- **`_safe_upload_filename(filename)`** — reduces a client-supplied filename to
  a single path component, handling both POSIX and Windows separators.
- **`_save_upload(file, dest)`** — streams in 64 KB chunks, enforcing the size
  limit *as it writes*. Reading the whole upload into memory first would let a
  large upload exhaust RAM before any limit could apply.
- **`_extract_bundle(archive, dir)`** — the hostile-input function. For zip:
  sums declared uncompressed sizes and rejects over 500 MB (zip-bomb guard),
  then resolves every member path and rejects anything landing outside the
  target (Zip Slip). For tar: the same, plus rejecting symlinks and hardlinks,
  which can otherwise point outside the extraction directory even when the
  member path looks safe.
- **`_run_detect_secrets(dir)`** — shells out to `detect-secrets`, degrading to
  a stub if absent.
- **`upload_agent(...)`** — validates, saves, extracts, builds the manifest,
  scans for secrets, computes the next version, persists.

## `routes/scans.py`

- **`start_scan`** — verifies the agent belongs to the workspace, **validates
  every requested scenario is builtin or workspace-owned**, creates the scan,
  enqueues `run_scan`, returns 202.
- **`list_scan_findings`** — joins remediations in one batched query by id list
  rather than per finding, avoiding N+1.
- **`review_finding`** — records a human decision, re-reads, returns the
  updated row.
- **`get_scan_report`** — gates on `count_unreviewed_blocking`, serves the PDF
  or HTML if rendered, otherwise enqueues a render with a **fixed job id**
  (`render_report:{scan_id}`) so repeated polling deduplicates instead of
  queuing a hundred renders.
- **`control_scan`** — breakpoints. Publishes to the control channel, allocates
  a sequence number, records and broadcasts a `breakpoint` event, and reflects
  PAUSED/RUNNING on the scan row.
- **`apply_scan_fix`** — refuses if the finding was dismissed in review, if
  there is no remediation, if the remediation is playbook-only, or if it was
  already applied. Then enqueues with a fixed job id for idempotency.

## `routes/badges.py`

- **`get_badge`** — deliberately unauthenticated, because badges go in public
  READMEs; the scan UUID is the capability and the only leak is a letter grade.
  Serves a stored badge if present; a `?` badge for unfinished scans; a
  `PENDING` badge (never persisted) while critical findings are unreviewed; and
  otherwise grades, signs, persists and serves. Catches `IntegrityError` from a
  concurrent request creating the badge first and serves the stored one.

## `ws/manager.py` and `ws/fanout.py`

- **`ConnectionManager`** — a `dict[scan_id, set[WebSocket]]` room registry.
  `broadcast` collects sockets that throw and prunes them, so one dead client
  cannot break delivery to the rest.
- **`RedisFanout`** — **reference-counted** subscriptions. The first client
  watching a scan starts a listener task; the last to disconnect cancels it.
  Without ref counting, two viewers on one scan would either double-subscribe
  or the first to leave would cut off the second.

## `routes/scans_ws.py`

- **`_resolve_api_key(websocket, query_api_key)`** — reads the key from the
  offered subprotocols (`["sentinex-api-key", "<key>"]`) or falls back to the
  query parameter for non-browser clients.
- **`scan_live_ws(...)`** — authenticates *before* accepting, verifies the scan
  belongs to the workspace, joins the room, subscribes, then loops handling
  `resume_from` replay and `ping`. The `finally` block always leaves the room
  and unsubscribes.

---

# apps/worker — orchestration

## `main.py`

- **`WORKER_ID`** — hostname plus random suffix, identifying this process.
- **`startup(ctx)`** — initialises the engine and Docker client, reaps this
  worker's orphaned containers, fails this worker's stale scans.
- **`_reap_orphans(client)`** — removes containers and networks labelled with
  **this** worker id. Scoping matters: an unscoped sweep destroys other running
  workers' sandboxes.
- **`WorkerSettings`** — arq's configuration class: the job functions, Redis
  settings, `max_jobs`, and `job_timeout` set to the scan timeout plus 60
  seconds so arq never kills a job before the orchestrator's own timeout fires.

## `orchestrator.py` — the state machine

`run(scan_id)` is the whole lifecycle. Order matters and every step earns its
place:

1. Load scan and agent; claim the scan for this worker.
2. Load scenario specs (empty selection means all builtins).
3. Initialise the Redis sequence counter with a TTL.
4. **PROVISIONING** — push injection rules to Redis *before* the proxy boots,
   start the event collector *before* provisioning, then create the network and
   containers.
5. **SEEDING** — wait for the mock database to accept connections.
6. **RUNNING** — extract the proxy CA, launch the agent, poll until it exits.
7. **DRAINING** — sleep 2s to let the proxy flush, stop collecting, persist.
8. **SCORING** — run detections, persist findings and remediations, compute the
   score, publish `finding` and `risk_update` events.
9. **REPORTING** — render, best-effort.
10. **DONE**, or **FAILED** on any exception, with `_teardown` in a `finally`.

Methods:

- **`_labels(scan_id, role)`** — every container and network gets
  `sentinex.scan_id`, `sentinex.worker_id` and `sentinex.role`. Labels are how
  cleanup finds things after a crash.
- **`_provision(...)`** — creates the `internal=True` network, the seeded mock
  database (falling back to vanilla Postgres if the seeded image is absent), the
  mock providers, and the proxy. Connects the proxy to the egress network so it
  can reach Redis. Every Docker call goes through `asyncio.to_thread` because
  the Docker SDK is synchronous and would otherwise block the shared event loop
  for every concurrent scan.
- **`_host_path(container_path)`** — translates a worker-container path to the
  backing host path. Bind mounts are resolved by the Docker daemon on the
  *host*, so when the worker is itself containerised its own paths are
  meaningless to the daemon. This is a subtle one and worth mentioning — it is
  the kind of bug that only appears in production.
- **`_extract_proxy_ca(...)`** — polls the proxy container for up to 30 seconds
  reading the CA, writes it where the agent can bind-mount it.
- **`_launch_agent(...)`** — resolves the framework image with a fallback,
  builds the environment (proxy variables, provider base-URL overrides, CA
  bundle paths), and runs the container with the hardening flags.
- **`_wait_for_agent(...)`** — polls every 2 seconds until exit or timeout,
  raising `asyncio.TimeoutError` so the caller marks the scan FAILED.
- **`_collect_events(scan_id)`** — subscribes to the scan channel and buffers
  `tool_call`, `tool_result` and `llm_message` events, capped at
  `max_events_per_scan`. Other event types are persisted at their source.
- **`_run_detections(...)`** — loads events, runs the runner, persists each
  finding with a generated remediation, then publishes one `scenario_step` event
  per detection so the dashboard shows which attacks were attempted and which
  succeeded — including the ones that found nothing.
- **`_compute_risk_score(...)`** — feeds findings through the engine, emitting a
  `finding` and a `risk_update` event per finding so the gauge animates.
- **`_transition(scan_id, state)`** — persists the status and publishes a
  `state` event. Sets `started_at` on RUNNING and `finished_at` on DONE/FAILED.
- **`_teardown(scan_id)`** — removes containers in **reverse creation order**
  (agent first, then services, then the database) with `force=True`, removes the
  network, deletes the CA file and the per-scan Redis keys. Runs in a `finally`,
  so it executes on the failure path too.

## `jobs/apply_fix.py`

Copies the bundle, applies the patch to the copy — **never mutating the
original** — registers the patched bundle as a new agent version with retry on
the unique constraint, creates a verification rescan, and enqueues it. Snapshots
ORM attributes into locals before any rollback, because a rollback expires ORM
instances and a lazy reload on an async session raises.

## `reporting.py`

Renders a Jinja template with `autoescape` enabled, converts to PDF with
WeasyPrint, and falls back to writing HTML when WeasyPrint's native
Pango/Cairo stack is unavailable — so local development still produces an
artifact.

---

# apps/proxy — interception

## `interceptors/http.py`

- **`classify_request(flow)`** — **always** returns a descriptor. Known
  providers get a provider-prefixed tool name; everything else is recorded under
  a generic `http` provider with the hostname in the name. This matters
  enormously: the exfiltration detections match on *host*, so dropping
  unrecognised hosts would make a complying agent invisible.
- **`_operation_segment(path)`** — picks the last path segment that names an
  operation, skipping object ids. `/v1/customers/cus_NffrFeUfNV2Hib` becomes
  `customers`, so 200 calls to 200 different customers collapse to one tool
  name — which is what makes the call-volume detections meaningful.
- **`_decode_body(content, key)`** — JSON bodies are legitimately arrays or
  scalars, and the payload models require a mapping. Non-objects are wrapped
  rather than passed through; non-JSON is kept as truncated text.
- **`classify_connect_attempt(flow, error)`** — describes a CONNECT that never
  became a request.

## `main.py` — the addon

- **`running()`** — creates the async Redis client on mitmproxy's own event
  loop, loads injection rules, starts the breakpoint control listener.
- **`request(flow)`** — classifies, assigns a `chain_id`, reroutes provider
  hosts to mocks, publishes a `tool_call`. **The event records the original
  host, not the mock**, because rerouting is an implementation detail and
  findings must read in terms of what the agent intended.
- **`response(flow)`** — pops the pending request, applies injections,
  classifies, publishes a `tool_result` carrying `injected` (false or the
  scenario slug), and holds the flow if paused at a breakpoint.
- **`http_connect_error(flow)`** — records HTTPS CONNECT attempts that failed
  before the request hook. For an `https://` URL the client sends `CONNECT`
  first; if the upstream is unreachable — which is exactly what a planted
  attacker host is inside a sealed sandbox — the tunnel is never established and
  `request` never fires. Without this hook, the single most important behaviour
  the product detects would be invisible.
- **`_next_seq()`** — Redis `INCR` on the shared counter, with a local fallback.
- **`_route_to_mock(flow)`** — rewrites scheme, host and port for matching
  provider hosts.

## `injection.py`

- **`load_from_raw(raw)`** — parses rules from Redis, marking itself loaded
  regardless so callers stop re-fetching on every response.
- **`apply(flow, tool_name)`** — first matching rule wins; respects `max_hits`;
  `replace` swaps the body wholesale, `merge` deep-merges into the parsed JSON
  and skips non-JSON bodies. Returns the scenario slug on success, `False`
  otherwise — which is what becomes the `injected` field.
- **`_deep_merge(base, extra)`** — recursive dict merge, so an injection can add
  a nested field without destroying the rest of a realistic response.

## `chain_tracker.py`

Maintains in-proxy state: call counts per tool, and sensitive values harvested
from responses by regex (SSN, API key, email) plus the pre-seeded honeypots.
**Its outputs are advisory only** — authoritative detection runs post-scan over
the persisted event stream. Say that explicitly, because otherwise it looks like
duplicated detection logic.

## `gen_ca.py`

Pre-generates the mitmproxy CA store with a generic subject name before mitmdump
boots, because mitmproxy's default certificate common name is the literal string
`mitmproxy` — a trivial fingerprint for any agent that inspects the certificate
chain of a supposedly third-party HTTPS call.

---

# apps/web — the dashboard

- **`lib/session.ts`** — resolves the workspace id and API key from a deep
  link, then `localStorage`, then build-time env, using `useSyncExternalStore`.
  That is the React-sanctioned pattern for browser-only external state: it
  provides a defined server snapshot, so there is no hydration mismatch and no
  cascading render.
- **`lib/ws.ts` / `useScanWS`** — one effect owns exactly one socket and tears
  it down in its own cleanup. Handlers are detached before `close()`, and an
  `active` flag guards every callback. Retries are driven by an attempt counter
  that re-runs the effect rather than the connect function calling itself —
  which is what previously multiplied connections on every retry.
- **`RiskGauge`** — animates the score with `requestAnimationFrame` and cubic
  easing; the pulse is derived from animation progress rather than held in
  separate state.
- **`EventStream`** — renders the last 200 events and auto-scrolls only when the
  user is already near the bottom, so scrolling back through history is not
  yanked away by incoming events.

---

# action/sentinex_scan.py — the CI gate

Standard library only, so it runs on any GitHub runner with no `pip install`.
Uploads the bundle via a hand-rolled multipart body, starts a scan, polls to a
terminal state, fetches findings, and gates.

- **`evaluate_gate(findings, risk_score, fail_on_severity, max_risk_score)`** —
  pure, and therefore unit-tested. Returns `(passed, reasons)`. Separating the
  decision from the IO is what makes the gate testable at all.
