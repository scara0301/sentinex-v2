# Containers and infrastructure — every element, and why

Container questions are where interviewers find out whether you actually built
something or followed a tutorial. Every flag below has a reason. Know the
reasons, not the flags.

---

## The containers in a scan

Each scan creates its own network and four to five containers, all labelled
`sentinex.scan_id`, `sentinex.worker_id`, `sentinex.role`.

| Container | Role | Why it must exist |
|---|---|---|
| `sx-<id>-agent` | The uploaded agent | The thing under test. |
| `sx-<id>-proxy` | mitmproxy | The only observation point. Records and injects. |
| `sx-<id>-mock-stripe` | Fake Stripe | Lets billable tool calls succeed safely, carries honeypots. |
| `sx-<id>-mock-slack` | Fake Slack | Same, for messaging. |
| `sx-<id>-mock-db` | Seeded Postgres | Gives the agent data worth stealing, with known-fake values. |

**Why per-scan rather than shared:** isolation and reproducibility. Two
concurrent scans must not see each other's traffic, and honeypot values must not
be contaminated by another agent's writes. A shared mock would also make
`min_count` call-volume detections meaningless, because counts would mix.

**The cost:** container startup is a real per-scan latency cost (a few seconds),
and each scan consumes meaningful host memory. A pooled warm-container design
would be faster, but resetting state between tenants reliably is harder than it
looks, and getting it wrong leaks data between customers.

---

## The network — the single most important design decision

```python
network = self.docker.networks.create(
    network_name,
    driver="bridge",
    internal=True,
    labels=self._labels(scan_id, "network"),
)
```

**`internal=True` is the whole security model.** It removes the gateway from
the bridge, so containers on this network have no route to the host or the
internet **at the kernel routing level**.

Why that matters: if I only set `HTTP_PROXY` on the agent, an agent that opens
a raw socket, or uses a library that ignores proxy environment variables,
bypasses observation completely — and I would report a clean scan for an agent
that exfiltrated everything. `internal=True` makes the proxy *unavoidable*
rather than merely *configured*. The agent cannot opt out of a route that does
not exist.

**The consequence I had to solve:** the proxy also needs to reach Redis to
publish events, and it cannot from a sealed network. So the proxy — and only
the proxy — joins a second, non-internal network. It is the single controlled
gateway. Everything else stays sealed.

**Alternatives:**

- *`--network none`.* Total isolation, but then the agent cannot make tool calls
  at all and there is nothing to observe.
- *iptables rules on a normal bridge.* Equivalent effect, but I would be
  hand-writing firewall rules per scan and any mistake fails open. `internal=True`
  is a single declarative flag that fails closed.
- *A separate VM or network namespace per scan.* Stronger, and what I would
  reach for with untrusted code from strangers. Much heavier.

---

## The agent container — every hardening flag

```python
container = await asyncio.to_thread(
    self.docker.containers.run,
    image, detach=True, network=network.name,
    name=f"sx-{scan_id}-agent",
    environment=environment, volumes=volumes,
    mem_limit=worker_settings.sandbox_mem_limit,      # 2g
    cpu_quota=worker_settings.sandbox_cpu_quota,      # 100000 = 1 CPU
    pids_limit=worker_settings.sandbox_pids_limit,    # 256
    cap_drop=["ALL"],
    security_opt=["no-new-privileges:true"],
    read_only=True,
    tmpfs={"/tmp": "size=256m", "/work_rw": "size=512m"},
    labels=self._labels(scan_id, "agent"),
)
```

| Flag | What it prevents |
|---|---|
| `cap_drop=["ALL"]` | Every Linux capability. No `CAP_NET_RAW` (no raw sockets, no packet crafting), no `CAP_SYS_ADMIN`, no mount operations. The agent runs with the kernel privileges of an ordinary unprivileged process and nothing more. |
| `no-new-privileges:true` | Setuid escalation. Even if a setuid binary exists in the image, executing it cannot gain privileges. This closes the standard privilege-escalation path inside a container. |
| `read_only=True` | Writes to the root filesystem. The agent cannot drop a binary, modify its own code, or persist anything across the scan. |
| `tmpfs /tmp`, `/work_rw` | The necessary exception. Python needs somewhere to write. tmpfs is memory-backed, size-capped, and vanishes with the container — so writes are possible but bounded and non-persistent. |
| `mem_limit=2g` | OOM in the host. Without it, a runaway agent takes down the worker and every other concurrent scan. |
| `cpu_quota=100000` | CPU starvation of co-tenants. 100000 against the default 100000-microsecond period is exactly one core. |
| `pids_limit=256` | Fork bombs. This is the one people forget, and it is the easiest denial-of-service to write. |
| `/work` mounted `ro` | Tampering with the bundle. The agent cannot rewrite the code being scanned, which would invalidate the result. |

**Why not run as a non-root user too?** The sandbox images do
(`USER agentuser`). Worth mentioning as defence in depth alongside the flags.

**The honest limit, and volunteer it:** this is still a shared kernel. A kernel
exploit escapes all of it. For genuinely untrusted code at scale I would want
gVisor (a user-space kernel) or Firecracker (a microVM). Docker with dropped
capabilities is a solid boundary against misbehaviour; it is not a boundary I
would bet a multi-tenant production system on against a determined attacker.

---

## Environment injected into the agent

```python
"HTTP_PROXY":  "http://sx-<id>-proxy:8080",
"HTTPS_PROXY": "http://sx-<id>-proxy:8080",
"OPENAI_BASE_URL":    "http://sx-<id>-proxy:8080/openai",
"ANTHROPIC_BASE_URL": "http://sx-<id>-proxy:8080/anthropic",
"REQUESTS_CA_BUNDLE": "/opt/sentinex/mitmproxy-ca.pem",
"SSL_CERT_FILE":      "/opt/sentinex/mitmproxy-ca.pem",
"CURL_CA_BUNDLE":     "/opt/sentinex/mitmproxy-ca.pem",
"NODE_EXTRA_CA_CERTS":"/opt/sentinex/mitmproxy-ca.pem",
```

- The **proxy variables** are belt-and-braces alongside `internal=True`. The
  network makes the proxy unavoidable; these make well-behaved clients route
  there cleanly rather than failing.
- The **base URL overrides** catch LLM SDK traffic specifically, so prompts and
  completions are recorded rather than just tunnelled.
- The **four CA variables** exist because there is no single standard. Python's
  `requests` reads `REQUESTS_CA_BUNDLE`, the `ssl` module reads
  `SSL_CERT_FILE`, curl reads `CURL_CA_BUNDLE`, Node reads
  `NODE_EXTRA_CA_CERTS`. Miss one and that client's HTTPS calls fail TLS and go
  unrecorded — which looks identical to an agent that behaved.

---

## The sandbox image — multi-stage by framework

```dockerfile
FROM python:3.12-slim AS base
RUN adduser --disabled-password --gecos "" --uid 1000 agentuser
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

FROM base AS langchain
RUN uv pip install --system --no-cache "langchain>=0.3" ...
USER agentuser
```

**Why one image per framework rather than one fat image:** installing
LangChain, CrewAI, AutoGen and MCP together is a dependency-resolution fight,
and every agent would pay the download cost of frameworks it does not use. Each
target shares the hardened base, so the security posture is identical.

**Why `uv` rather than `pip`:** substantially faster resolution and install,
which matters when five images are being built.

**`entrypoint.sh`** resolves the agent entry point: an explicit
`SENTINEX_ENTRYPOINT`, then conventional names (`agent_entry.py`, `main.py`,
`agent.py`, `app.py`, `run.py`, `__main__.py`) under `/work/src` then `/work`,
then a single lone `.py` file. It exits 3 with a clear message if nothing
matches, rather than failing obscurely.

**The bug worth telling as a story:** this script broke the entire product on a
Windows checkout. Git stores it with LF, but `core.autocrlf=true` writes CRLF
into the working tree, and `docker build` copies the working-tree file. The
image then contains a script whose shebang is `#!/bin/bash\r`, so Linux tries to
exec the literal path `/bin/bash\r`, which does not exist. Every agent container
died instantly with "no such file or directory" — an error that points at bash,
not at line endings. The fix is a `.gitattributes` pinning `eol=lf`, plus a unit
test asserting the file contains no `\r` so it can never regress.

---

## The proxy image

Runs as a non-root user, with `MITMPROXY_CONFDIR` pinned so the orchestrator
knows where to read the CA from. The command is:

```
python -m sentinex_proxy.gen_ca && exec mitmdump -p 8080 -s .../main.py \
  --set ssl_insecure=true --set confdir=$MITMPROXY_CONFDIR
```

- **`gen_ca` first** so the CA has a generic subject name rather than
  mitmproxy's default literal `mitmproxy`.
- **`exec`** replaces the shell process, so mitmdump becomes PID 1 and receives
  signals directly — otherwise the shell swallows them and stopping the
  container takes the full 10-second kill timeout.
- **`ssl_insecure=true`** means the proxy does not verify upstream
  certificates. Correct here, because upstream is a mock container with a
  self-signed certificate.

**A bug worth telling:** the image originally created a user named `proxy`.
Debian's base image already ships a system account with that name, so
`useradd proxy` exits 9 and the build fails. The image had never built. Renamed
to `sentinex`, and the orchestrator now reads the CA path through the image's
own `MITMPROXY_CONFDIR` rather than a hardcoded home directory, so the two
cannot drift apart again.

---

## Docker socket access — the biggest tradeoff in the system

The worker mounts `/var/run/docker.sock` and runs as root.

**Be direct about this if asked, do not minimise it.** Docker socket access is
root-equivalent on the host: anyone who can talk to that socket can start a
privileged container mounting `/`, and own the machine.

**Why it is acceptable here:** the worker is not internet-facing. It consumes
jobs from Redis, and the only input it takes is a scan id. The untrusted
input — the agent bundle — is handled by the API on the other side of a queue,
and the worker never parses it, only mounts it read-only into a sandbox.

**What I would do in a hardened deployment:** put a Docker socket proxy in front
(something like Tecnativa's `docker-socket-proxy`) exposing only the container
and network endpoints the worker actually calls, so a worker compromise cannot
reach the full daemon API. Longer term, a rootless runtime or a dedicated build
service.

---

## `docker-compose.dev.yml`

- **Healthchecks with `condition: service_healthy`** on Postgres and Redis, so
  the API does not start against a database that is still initialising. Ordinary
  `depends_on` only waits for the container to start, not to be ready — a
  distinction worth stating.
- **Source bind mounts plus `--reload`** for hot reloading in development only.
- **`.data/uploads` as a host bind, not a named volume.** This one is
  non-obvious and worth knowing: the worker asks the Docker daemon to bind-mount
  the bundle into the agent container, and the daemon resolves that path **on
  the host**, not inside the worker container. A named volume would not be
  resolvable as a path. That is also why `UPLOADS_CONTAINER_DIR` and
  `UPLOADS_HOST_DIR` exist — the orchestrator rewrites its own container path to
  the backing host path before asking for the mount.

## `docker-compose.prod.yml`

Differences from dev, each with a reason:

- No source mounts, no `--reload`.
- Postgres and Redis are **not** published to the host — only reachable on the
  internal compose network.
- Caddy terminates TLS with automatic Let's Encrypt certificates.
- Credentials and domains come from `.env`, with `${VAR:?message}` syntax so
  compose **fails loudly** on a missing secret rather than starting with an
  empty password.
- `--proxy-headers --forwarded-allow-ips=*` on uvicorn so client IPs and
  scheme are read from Caddy's forwarded headers.

**Why Caddy over nginx:** automatic certificate issuance and renewal with no
cron job or certbot sidecar, and WebSocket upgrades are handled without explicit
configuration. nginx would need more config for the same result.

---

## The backend images — a bug worth telling

Each app declares `sentinex-core = { workspace = true }` under
`[tool.uv.sources]`, but the Docker build context copies only that app and
`packages/core`. The root `pyproject.toml` that defines the uv workspace is
never copied, so uv fails:

```
`sentinex-core` references a workspace in `tool.uv.sources`,
but is not a workspace member
```

The API, worker and proxy images all failed at the install step — nothing could
be deployed. The fix is `--no-sources` on the install, which makes uv ignore
`tool.uv.sources` and resolve `sentinex-core` from the editable path installed
in the same command.

This is a good story to tell because the failure is invisible from source
review. Every file is individually correct; the defect only exists in the
interaction between the pyproject and the build context.
