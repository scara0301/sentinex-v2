import asyncio
import json
import uuid
import time
import structlog
import docker
import docker.errors
import docker.models.containers
import docker.models.networks
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sentinex_core.db.base import get_session
from sentinex_core.db.models import Event as EventModel
from sentinex_core.db.repos import (
    ScanRepo,
    AgentRepo,
    FindingRepo,
    EventRepo,
    RemediationRepo,
    ScenarioRepo,
)
from sentinex_core.events.schema import (
    EventEnvelope,
    FindingPayload,
    RiskUpdatePayload,
    ScenarioStepPayload,
    StatePayload,
)
from sentinex_core.remediation import build_remediation
from sentinex_core.scenarios import (
    ScenarioRunner,
    ScenarioSpec,
    builtin_specs,
    parse_scenario_yaml,
)
from sentinex_core.scoring import RiskScoreEngine

from .settings import worker_settings

log = structlog.get_logger()

SCAN_STATES = [
    "PENDING",
    "PROVISIONING",
    "SEEDING",
    "RUNNING",
    "DRAINING",
    "SCORING",
    "REPORTING",
    "DONE",
    "FAILED",
    "PAUSED",
]


class ScanOrchestrator:
    """
    Per-scan state machine.

    Lifecycle
    ---------
    PENDING -> PROVISIONING -> SEEDING -> RUNNING -> DRAINING
            -> SCORING -> REPORTING -> DONE
                                    \\-> FAILED (on any exception or timeout)

    One ScanOrchestrator instance is created per ``run_scan`` job.  The
    instance holds references to every Docker object it creates so that
    ``_teardown`` can reliably destroy them even when an exception fires
    mid-provision.
    """

    def __init__(self, docker_client: docker.DockerClient, redis_client):
        self.docker = docker_client
        self.redis = redis_client
        # Ordered list of containers created during this scan; reversed on teardown
        # so the agent is stopped before its dependencies.
        self._containers: list[docker.models.containers.Container] = []
        self._network: Optional[docker.models.networks.Network] = None
        # Proxy-originated events collected from Redis pub/sub during RUNNING
        # and persisted to the events table at DRAINING.
        self._collected: list[dict[str, Any]] = []
        self._collector_task: Optional[asyncio.Task] = None
        # Path to the extracted proxy CA cert (cleaned up on teardown).
        self._ca_cert_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, scan_id: str) -> None:
        scan_uuid = uuid.UUID(scan_id)
        bound_log = log.bind(scan_id=scan_id)
        bound_log.info("Starting scan")

        async with get_session() as db:
            scan_repo = ScanRepo(db)
            agent_repo = AgentRepo(db)

            scan = await scan_repo.get_by_id(scan_uuid)
            if not scan:
                bound_log.error("Scan not found")
                return

            agent = await agent_repo.get_by_id(scan.agent_id)
            if not agent:
                bound_log.error("Agent not found", agent_id=str(scan.agent_id))
                await scan_repo.update_status(scan_uuid, "FAILED")
                await db.commit()
                return

        try:
            runner = ScenarioRunner(await self._load_scenario_specs(scan))
            bound_log.info("Scenarios loaded", count=len(runner.specs))

            # Give the shared (scan_id, seq) counter a TTL so it can't leak in
            # Redis forever; teardown deletes it explicitly on the happy path.
            if self.redis is not None:
                ttl = worker_settings.scan_timeout_seconds + 600
                await self.redis.set(
                    f"scan:{scan_uuid}:seq", 0, ex=ttl, nx=True
                )

            await self._transition(scan_uuid, "PROVISIONING")
            # Injection rules must be in Redis before the proxy boots.
            await self._push_injection_rules(scan_uuid, runner)
            network, proxy_container, mock_containers = await self._provision(scan_uuid, agent)
            self._start_event_collector(scan_uuid)

            await self._transition(scan_uuid, "SEEDING")
            await self._seed_mock_db(scan_uuid, mock_containers.get("mock-db"))

            await self._transition(scan_uuid, "RUNNING")
            ca_cert_path = await self._extract_proxy_ca(scan_uuid, proxy_container)
            agent_container = await self._launch_agent(
                scan_uuid, agent, network, proxy_container, ca_cert_path
            )
            self._containers.append(agent_container)

            await self._wait_for_agent(scan_uuid, agent_container)

            await self._transition(scan_uuid, "DRAINING")
            # Allow the proxy time to flush any buffered events to Redis before
            # we stop collecting and move on to scoring.
            await asyncio.sleep(2)
            await self._stop_event_collector()
            await self._persist_collected_events(scan_uuid)

            await self._transition(scan_uuid, "SCORING")
            await self._run_detections(scan_uuid, runner)
            score = await self._compute_risk_score(scan_uuid)
            bound_log.info("Risk score computed", score=score)

            await self._transition(scan_uuid, "REPORTING")
            await self._render_report(scan_uuid)

            await self._transition(scan_uuid, "DONE")
            async with get_session() as db:
                scan_repo = ScanRepo(db)
                await scan_repo.update_status(scan_uuid, "DONE")
                await db.commit()

        except asyncio.TimeoutError:
            bound_log.error("Scan timed out")
            await self._fail(scan_uuid)
        except Exception as e:
            bound_log.exception("Scan failed", error=str(e))
            await self._fail(scan_uuid)
        finally:
            await self._teardown(scan_uuid)

    # ------------------------------------------------------------------
    # Docker provisioning
    # ------------------------------------------------------------------

    async def _provision(
        self, scan_id: uuid.UUID, agent
    ) -> tuple[
        docker.models.networks.Network,
        docker.models.containers.Container,
        dict[str, docker.models.containers.Container],
    ]:
        """
        Create isolated Docker network + proxy + mock services.

        The network is ``internal=True`` which prevents any direct host
        egress from containers on it — all outbound traffic from the agent
        must pass through the proxy container.
        """
        network_name = f"sx-{scan_id}"
        network = self.docker.networks.create(
            network_name,
            driver="bridge",
            internal=True,  # NO host egress; agent must route through proxy
            labels={"sentinex.scan_id": str(scan_id)},
        )
        self._network = network
        log.info("Created sandbox network", network=network_name, scan_id=str(scan_id))

        # ---- mock-db (seeded postgres) --------------------------------
        # sentinex/mock-db bakes seed/init.sql (honeypot PII, planted creds)
        # into /docker-entrypoint-initdb.d; fall back to vanilla postgres
        # when the image hasn't been built.
        mock_db_image = worker_settings.mock_db_image
        try:
            self.docker.images.get(mock_db_image)
        except docker.errors.ImageNotFound:
            log.warning(
                "Seeded mock-db image not found; using unseeded postgres",
                requested_image=mock_db_image,
                scan_id=str(scan_id),
            )
            mock_db_image = "postgres:16-alpine"
        mock_db = self.docker.containers.run(
            mock_db_image,
            detach=True,
            network=network_name,
            name=f"sx-{scan_id}-mock-db",
            environment={
                "POSTGRES_DB": "mockdb",
                "POSTGRES_USER": "mockuser",
                "POSTGRES_PASSWORD": "mockpass",
            },
            labels={
                "sentinex.scan_id": str(scan_id),
                "sentinex.role": "mock-db",
            },
            mem_limit=worker_settings.sandbox_mem_limit,
        )
        self._containers.append(mock_db)

        # ---- mock providers --------------------------------------------
        mocks: dict[str, docker.models.containers.Container] = {"mock-db": mock_db}
        for provider in ("stripe", "slack"):
            mock = self.docker.containers.run(
                "sentinex/mocks:latest",
                detach=True,
                network=network_name,
                name=f"sx-{scan_id}-mock-{provider}",
                environment={"MOCK_PROVIDER": provider},
                labels={
                    "sentinex.scan_id": str(scan_id),
                    "sentinex.role": f"mock-{provider}",
                },
            )
            self._containers.append(mock)
            mocks[f"mock-{provider}"] = mock

        # Provider host -> mock container routing applied by the proxy.
        mock_host_map = {
            "stripe.com": f"sx-{scan_id}-mock-stripe:4010",
            "slack.com": f"sx-{scan_id}-mock-slack:4010",
        }

        # ---- mitmproxy -----------------------------------------------
        # The proxy intercepts all agent traffic, records events, and
        # streams them to Redis for the API to fan out over WebSockets.
        proxy = self.docker.containers.run(
            "sentinex/proxy:latest",
            detach=True,
            network=network_name,
            name=f"sx-{scan_id}-proxy",
            environment={
                "SCAN_ID": str(scan_id),
                "REDIS_URL": worker_settings.redis_url,
                "MOCK_HOST_MAP": json.dumps(mock_host_map),
            },
            labels={
                "sentinex.scan_id": str(scan_id),
                "sentinex.role": "proxy",
            },
        )
        self._containers.append(proxy)

        # The scan network is internal (no egress), so the proxy joins a
        # second network to reach Redis — without it no event ever leaves
        # the sandbox.
        if worker_settings.proxy_egress_network:
            try:
                egress = self.docker.networks.get(
                    worker_settings.proxy_egress_network
                )
                egress.connect(proxy)
            except docker.errors.NotFound:
                log.warning(
                    "Proxy egress network not found; events will not reach Redis",
                    network=worker_settings.proxy_egress_network,
                    scan_id=str(scan_id),
                )

        return network, proxy, mocks

    @staticmethod
    def _host_path(container_path: str) -> str:
        """Translate a worker-container path to the backing Docker-host path.

        Bind mounts resolve on the host, so when the worker itself runs in a
        container its local paths must be rewritten to the host paths that
        back them. A no-op when the translation settings are unset (worker
        running directly on the host).
        """
        cd = worker_settings.uploads_container_dir
        hd = worker_settings.uploads_host_dir
        if cd and hd and container_path.startswith(cd):
            return hd + container_path[len(cd):]
        return container_path

    async def _extract_proxy_ca(
        self,
        scan_id: uuid.UUID,
        proxy_container: docker.models.containers.Container,
    ) -> Optional[str]:
        """Read the proxy's generated CA cert and write it to a path the agent
        container can bind-mount. Returns the worker-local path, or None.

        Without the CA in the agent's trust store, any ``https://`` call the
        agent makes through the proxy fails TLS verification and goes
        unrecorded — so this is best-effort but important for coverage.
        """
        ca_pem: Optional[bytes] = None
        for _ in range(30):
            try:
                res = await asyncio.to_thread(
                    proxy_container.exec_run,
                    "cat /home/proxy/.mitmproxy/mitmproxy-ca-cert.pem",
                    demux=False,
                )
                if res.exit_code == 0 and res.output and b"BEGIN CERTIFICATE" in res.output:
                    ca_pem = res.output
                    break
            except Exception as exc:
                log.debug("CA read attempt failed", error=str(exc), scan_id=str(scan_id))
            await asyncio.sleep(1)

        if not ca_pem:
            log.warning(
                "Could not extract proxy CA; HTTPS tool calls may go unrecorded",
                scan_id=str(scan_id),
            )
            return None

        base = worker_settings.uploads_container_dir or "/tmp/sentinex"
        cert_dir = Path(base) / ".sentinex-certs"
        cert_dir.mkdir(parents=True, exist_ok=True)
        cert_path = cert_dir / f"{scan_id}-ca.pem"
        cert_path.write_bytes(ca_pem)
        self._ca_cert_path = cert_path
        return str(cert_path)

    async def _launch_agent(
        self,
        scan_id: uuid.UUID,
        agent,
        network: docker.models.networks.Network,
        proxy_container: docker.models.containers.Container,
        ca_cert_path: Optional[str] = None,
    ) -> docker.models.containers.Container:
        """
        Launch the agent inside a hardened container with traffic routed
        through the proxy.

        Security posture
        ----------------
        - All Linux capabilities dropped.
        - ``no-new-privileges`` security option.
        - Read-only root filesystem; writable tmpfs mounts for /tmp and /work_rw.
        - Memory, CPU quota, and PID limits enforced.
        - Agent bundle mounted read-only at /work when bundle_uri is a local path.

        Proxy wiring
        ------------
        HTTP_PROXY / HTTPS_PROXY are set to the proxy container's name on the
        internal network.  Provider base-URL overrides redirect LLM SDK calls
        through the proxy so every prompt/completion is recorded. The proxy CA
        (when extracted) is mounted read-only and trusted via the standard
        CA-bundle env vars so HTTPS calls are intercepted, not rejected.
        """
        framework = agent.framework
        image = f"sentinex/sandbox-{framework}:latest"

        # Fall back to base image when a framework-specific variant hasn't been built yet.
        try:
            self.docker.images.get(image)
        except docker.errors.ImageNotFound:
            log.warning(
                "Framework image not found, falling back to base",
                requested_image=image,
                fallback=worker_settings.sandbox_image_base,
                scan_id=str(scan_id),
            )
            image = worker_settings.sandbox_image_base

        proxy_addr = f"http://sx-{scan_id}-proxy:{worker_settings.proxy_port}"

        # Only mount a local bundle; remote bundles (s3://, https://) will be
        # fetched by the agent entrypoint via its own credentials.
        volumes: dict = {}
        if agent.bundle_uri and agent.bundle_uri.startswith("file://"):
            host_path = self._host_path(agent.bundle_uri.removeprefix("file://"))
            volumes[host_path] = {"bind": "/work", "mode": "ro"}

        environment = {
            "HTTP_PROXY": proxy_addr,
            "HTTPS_PROXY": proxy_addr,
            # Redirect major LLM provider SDKs through the proxy so every
            # API call is captured and replayed in findings.
            "OPENAI_BASE_URL": f"http://sx-{scan_id}-proxy:{worker_settings.proxy_port}/openai",
            "ANTHROPIC_BASE_URL": f"http://sx-{scan_id}-proxy:{worker_settings.proxy_port}/anthropic",
            "SCAN_ID": str(scan_id),
        }

        if ca_cert_path:
            ca_mount = "/opt/sentinex/mitmproxy-ca.pem"
            volumes[self._host_path(ca_cert_path)] = {"bind": ca_mount, "mode": "ro"}
            # Cover requests/httpx, Python ssl, curl, and Node so HTTPS through
            # the proxy is trusted rather than rejected.
            environment["REQUESTS_CA_BUNDLE"] = ca_mount
            environment["SSL_CERT_FILE"] = ca_mount
            environment["CURL_CA_BUNDLE"] = ca_mount
            environment["NODE_EXTRA_CA_CERTS"] = ca_mount

        container = self.docker.containers.run(
            image,
            detach=True,
            network=network.name,
            name=f"sx-{scan_id}-agent",
            environment=environment,
            volumes=volumes,
            mem_limit=worker_settings.sandbox_mem_limit,
            cpu_quota=worker_settings.sandbox_cpu_quota,
            pids_limit=worker_settings.sandbox_pids_limit,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            read_only=True,
            tmpfs={
                "/tmp": "size=256m",
                "/work_rw": "size=512m",
            },
            labels={
                "sentinex.scan_id": str(scan_id),
                "sentinex.role": "agent",
            },
        )
        log.info(
            "Agent container launched",
            container_id=container.id,
            image=image,
            scan_id=str(scan_id),
        )
        return container

    # ------------------------------------------------------------------
    # Agent lifecycle monitoring
    # ------------------------------------------------------------------

    async def _wait_for_agent(
        self,
        scan_id: uuid.UUID,
        container: docker.models.containers.Container,
        timeout: Optional[int] = None,
    ) -> None:
        """
        Poll the agent container every 2 s until it exits or the timeout fires.

        Raises ``asyncio.TimeoutError`` on timeout so the caller's ``except``
        block can mark the scan FAILED and trigger teardown.
        """
        timeout = timeout or worker_settings.scan_timeout_seconds
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            await asyncio.to_thread(container.reload)
            if container.status in ("exited", "dead"):
                exit_code = container.attrs["State"]["ExitCode"]
                log.info(
                    "Agent container exited",
                    exit_code=exit_code,
                    scan_id=str(scan_id),
                )
                return
            await asyncio.sleep(2)
        raise asyncio.TimeoutError(
            f"Agent did not finish within {timeout}s (scan_id={scan_id})"
        )

    # ------------------------------------------------------------------
    # Mock DB seeding
    # ------------------------------------------------------------------

    async def _seed_mock_db(
        self,
        scan_id: uuid.UUID,
        mock_db_container: Optional[docker.models.containers.Container],
    ) -> None:
        """
        Wait for the mock postgres instance to accept connections, then
        execute the seed SQL.

        The actual seed data (PII, API keys, synthetic transactions) lives in
        ``sentinex/sandbox-seed`` which is baked into the postgres image; we
        just need to ensure the server is ready before the agent starts.
        """
        if not mock_db_container:
            return

        log.info("Waiting for mock-db to be ready", scan_id=str(scan_id))
        ready = False
        for attempt in range(30):
            try:
                result = await asyncio.to_thread(
                    mock_db_container.exec_run,
                    "pg_isready -U mockuser -d mockdb",
                    demux=False,
                )
                if result.exit_code == 0:
                    ready = True
                    break
            except Exception as exc:
                log.debug(
                    "pg_isready check failed",
                    attempt=attempt,
                    error=str(exc),
                    scan_id=str(scan_id),
                )
            await asyncio.sleep(1)

        if not ready:
            log.warning(
                "mock-db did not become ready in time; scan proceeds without seed",
                scan_id=str(scan_id),
            )
            return

        # Seed data ships inside the sentinex/mock-db image via
        # /docker-entrypoint-initdb.d/init.sql — nothing left to exec here.
        log.info("mock-db is ready", scan_id=str(scan_id))

    # ------------------------------------------------------------------
    # Scenarios (Sprint 3)
    # ------------------------------------------------------------------

    async def _load_scenario_specs(self, scan) -> list[ScenarioSpec]:
        """
        Resolve the scan's scenario selection into parsed specs.
        An empty selection runs every builtin scenario.
        """
        if not scan.scenario_ids:
            return builtin_specs()

        specs: list[ScenarioSpec] = []
        async with get_session() as db:
            rows = await ScenarioRepo(db).get_by_ids(list(scan.scenario_ids))
        for row in rows:
            if not row.yaml:
                log.warning("Scenario has no YAML; skipping", scenario_id=str(row.id))
                continue
            try:
                specs.append(parse_scenario_yaml(row.yaml))
            except ValueError as exc:
                log.warning(
                    "Scenario YAML invalid; skipping",
                    scenario_id=str(row.id),
                    error=str(exc),
                )
        return specs

    async def _push_injection_rules(
        self, scan_id: uuid.UUID, runner: ScenarioRunner
    ) -> None:
        """Stage response-injection rules in Redis for the proxy to load."""
        if self.redis is None:
            return
        rules = runner.injection_rules()
        if not rules:
            return
        await self.redis.set(
            f"scan:{scan_id}:injection_rules",
            json.dumps(rules),
            ex=worker_settings.scan_timeout_seconds + 600,
        )
        log.info("Injection rules staged", count=len(rules), scan_id=str(scan_id))

    def _start_event_collector(self, scan_id: uuid.UUID) -> None:
        if self.redis is None:
            return
        self._collected = []
        self._collector_task = asyncio.create_task(self._collect_events(scan_id))

    async def _stop_event_collector(self) -> None:
        task = self._collector_task
        self._collector_task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _collect_events(self, scan_id: uuid.UUID) -> None:
        """
        Mirror proxy-originated pub/sub events into a buffer.

        The proxy only *publishes* events; without this collector they would
        never be persisted and detections would have nothing to evaluate.
        """
        pubsub = self.redis.pubsub()
        channel = f"scan:{scan_id}:events"
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                if data.get("type") not in ("tool_call", "tool_result", "llm_message"):
                    continue  # state/finding/risk events are persisted at source
                if len(self._collected) < worker_settings.max_events_per_scan:
                    self._collected.append(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "Event collector stopped unexpectedly",
                error=str(exc),
                scan_id=str(scan_id),
            )
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    async def _persist_collected_events(self, scan_id: uuid.UUID) -> None:
        if not self._collected:
            return
        rows = []
        for data in self._collected:
            try:
                ts = datetime.fromisoformat(data["ts"])
            except (KeyError, TypeError, ValueError):
                ts = datetime.now(timezone.utc)
            rows.append(
                {
                    "scan_id": scan_id,
                    "seq": int(data.get("seq", 0)),
                    "ts": ts,
                    "type": data.get("type", "log"),
                    "payload": data.get("payload") or {},
                }
            )
        async with get_session() as db:
            await EventRepo(db).bulk_upsert(rows)
            await db.commit()
        log.info(
            "Persisted proxy events", count=len(rows), scan_id=str(scan_id)
        )
        self._collected = []

    async def _run_detections(
        self, scan_id: uuid.UUID, runner: ScenarioRunner
    ) -> None:
        """
        Evaluate every scenario detection over the recorded event stream,
        persisting findings with attached remediations and publishing
        scenario_step progress events.
        """
        async with get_session() as db:
            events = await EventRepo(db).list_by_scan(
                scan_id, limit=worker_settings.max_events_per_scan
            )
            event_dicts = [
                {"seq": ev.seq, "type": ev.type, "payload": ev.payload or {}}
                for ev in events
            ]

        drafts = runner.evaluate(event_dicts)
        fired_rules = {d.rule_id for d in drafts}
        log.info(
            "Detections evaluated",
            findings=len(drafts),
            events=len(event_dicts),
            scan_id=str(scan_id),
        )

        async with get_session() as db:
            finding_repo = FindingRepo(db)
            rem_repo = RemediationRepo(db)
            for draft in drafts:
                finding = await finding_repo.create(
                    scan_id=scan_id,
                    category=draft.category,
                    rule_id=draft.rule_id,
                    severity=draft.severity,
                    title=draft.title,
                    evidence=draft.evidence,
                    cwe=draft.cwe or None,
                )
                playbook_md, diff = build_remediation(draft.rule_id, draft.category)
                remediation = await rem_repo.create(
                    finding_id=finding.id,
                    diff=diff,
                    playbook_md=playbook_md,
                )
                await finding_repo.set_remediation(finding.id, remediation.id)
            await db.commit()

        await self._publish_scenario_steps(scan_id, runner, fired_rules)

    async def _publish_scenario_steps(
        self,
        scan_id: uuid.UUID,
        runner: ScenarioRunner,
        fired_rules: set[str],
    ) -> None:
        """Emit one scenario_step event per detection (fail = vuln found)."""
        db_events: list[EventModel] = []
        for spec in runner.specs:
            for step_no, det in enumerate(spec.detections, start=1):
                seq = (
                    await self.redis.incr(f"scan:{scan_id}:seq")
                    if self.redis is not None
                    else 0
                )
                ts = datetime.now(timezone.utc)
                event = EventEnvelope(
                    scan_id=scan_id,
                    seq=seq,
                    ts=ts,
                    type="scenario_step",
                    payload=ScenarioStepPayload(
                        scenario=spec.slug,
                        step=step_no,
                        name=det.rule_id,
                        result="fail" if det.rule_id in fired_rules else "ok",
                    ),
                )
                if self.redis is not None:
                    await self.redis.publish(
                        f"scan:{scan_id}:events", event.model_dump_json()
                    )
                db_events.append(
                    EventModel(
                        scan_id=scan_id,
                        seq=seq,
                        ts=ts,
                        type=event.type,
                        payload=event.payload.model_dump(),
                    )
                )
        if db_events:
            async with get_session() as db:
                await EventRepo(db).bulk_insert(db_events)
                await db.commit()

    # ------------------------------------------------------------------
    # Reporting (Sprint 4)
    # ------------------------------------------------------------------

    async def _render_report(self, scan_id: uuid.UUID) -> None:
        """Best-effort report render; a failure must not fail the scan."""
        try:
            from .reporting import generate_report

            path = await generate_report(str(scan_id))
            log.info("Report rendered", path=str(path), scan_id=str(scan_id))
        except Exception as exc:
            log.warning(
                "Report generation failed", error=str(exc), scan_id=str(scan_id)
            )

    # ------------------------------------------------------------------
    # Risk scoring
    # ------------------------------------------------------------------

    async def _compute_risk_score(self, scan_id: uuid.UUID) -> float:
        """
        Compute a 0-100 weighted risk score from all findings for this scan.

        Uses ``RiskScoreEngine`` for streaming computation and publishes
        a ``finding`` event + ``risk_update`` event to Redis for each
        finding so the live dashboard can animate the gauge in real time.
        """
        engine = RiskScoreEngine()

        async with get_session() as db:
            finding_repo = FindingRepo(db)
            findings = await finding_repo.list_by_scan(scan_id)

            db_events: list[EventModel] = []

            for f in findings:
                finding_seq = await self.redis.incr(f"scan:{scan_id}:seq") if self.redis is not None else 0
                finding_ts = datetime.now(timezone.utc)
                finding_event = EventEnvelope(
                    scan_id=scan_id,
                    seq=finding_seq,
                    ts=finding_ts,
                    type="finding",
                    payload=FindingPayload(
                        finding_id=str(f.id),
                        category=f.category,
                        rule_id=f.rule_id,
                        severity=f.severity,
                        title=f.title,
                    ),
                )
                if self.redis is not None:
                    await self.redis.publish(
                        f"scan:{scan_id}:events",
                        finding_event.model_dump_json(),
                    )
                db_events.append(EventModel(
                    scan_id=scan_id,
                    seq=finding_seq,
                    ts=finding_ts,
                    type=finding_event.type,
                    payload=finding_event.payload.model_dump(),
                ))

                new_score, delta = engine.add_finding(
                    severity=f.severity,
                    category=f.category,
                    rule_id=f.rule_id,
                )
                risk_seq = await self.redis.incr(f"scan:{scan_id}:seq") if self.redis is not None else 0
                risk_ts = datetime.now(timezone.utc)
                risk_event = EventEnvelope(
                    scan_id=scan_id,
                    seq=risk_seq,
                    ts=risk_ts,
                    type="risk_update",
                    payload=RiskUpdatePayload(
                        score=new_score,
                        delta=delta,
                        drivers=engine.top_drivers,
                    ),
                )
                if self.redis is not None:
                    await self.redis.publish(
                        f"scan:{scan_id}:events",
                        risk_event.model_dump_json(),
                    )
                db_events.append(EventModel(
                    scan_id=scan_id,
                    seq=risk_seq,
                    ts=risk_ts,
                    type=risk_event.type,
                    payload=risk_event.payload.model_dump(),
                ))

            score = engine.current_score()
            scan_repo = ScanRepo(db)
            await scan_repo.update_risk_score(scan_id, score)

            if db_events:
                event_repo = EventRepo(db)
                await event_repo.bulk_insert(db_events)

            await db.commit()

        return score

    # ------------------------------------------------------------------
    # State machine helpers
    # ------------------------------------------------------------------

    async def _transition(self, scan_id: uuid.UUID, new_state: str) -> None:
        """
        Persist a state change to the database and publish a ``state`` event
        to the Redis pub/sub channel so the API can push it to WebSocket clients.
        """
        log.info(
            "Scan state transition",
            scan_id=str(scan_id),
            state=new_state,
        )

        now = datetime.now(timezone.utc)
        async with get_session() as db:
            repo = ScanRepo(db)
            scan = await repo.get_by_id(scan_id)
            old_state = scan.status if scan else "UNKNOWN"
            await repo.update_status(
                scan_id,
                new_state,
                started_at=now if new_state == "RUNNING" else None,
                finished_at=now if new_state in ("DONE", "FAILED") else None,
            )
            await db.commit()

        seq = await self.redis.incr(f"scan:{scan_id}:seq") if self.redis is not None else 0
        ts = datetime.now(timezone.utc)
        event = EventEnvelope(
            scan_id=scan_id,
            seq=seq,
            ts=ts,
            type="state",
            payload=StatePayload(
                from_=old_state,
                to=new_state,
                reason="orchestrator",
            ),
        )

        if self.redis is not None:
            await self.redis.publish(
                f"scan:{scan_id}:events",
                event.model_dump_json(),
            )

        async with get_session() as db:
            event_repo = EventRepo(db)
            db_event = EventModel(
                scan_id=scan_id,
                seq=seq,
                ts=ts,
                type=event.type,
                payload=event.payload.model_dump(),
            )
            await event_repo.bulk_insert([db_event])
            await db.commit()

    async def _fail(self, scan_id: uuid.UUID) -> None:
        """Convenience wrapper: transition to FAILED and persist."""
        try:
            await self._transition(scan_id, "FAILED")
        except Exception as exc:
            # Best-effort; do not mask the original exception.
            log.error(
                "Could not persist FAILED state",
                scan_id=str(scan_id),
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _teardown(self, scan_id: uuid.UUID) -> None:
        """
        Remove all Docker containers and the network created for this scan.

        Containers are removed in reverse creation order (agent first, then
        services, then mock-db) to mirror a clean shutdown sequence.
        ``force=True`` is used so a still-running container is killed before
        removal — necessary when the scan times out while the agent is active.
        """
        log.info("Tearing down sandbox", scan_id=str(scan_id))

        # Stop the pub/sub collector first — it dies with the proxy anyway.
        await self._stop_event_collector()

        for container in reversed(self._containers):
            name = getattr(container, "name", "<unknown>")
            try:
                container.remove(force=True)
                log.debug("Removed container", name=name, scan_id=str(scan_id))
            except docker.errors.NotFound:
                # Already gone — that's fine.
                pass
            except Exception as exc:
                log.warning(
                    "Failed to remove container",
                    name=name,
                    error=str(exc),
                    scan_id=str(scan_id),
                )

        if self._network is not None:
            try:
                self._network.remove()
                log.debug(
                    "Removed sandbox network",
                    network=self._network.name,
                    scan_id=str(scan_id),
                )
            except docker.errors.NotFound:
                pass
            except Exception as exc:
                log.warning(
                    "Failed to remove network",
                    error=str(exc),
                    scan_id=str(scan_id),
                )

        self._containers.clear()
        self._network = None

        # Remove the extracted CA cert file.
        if self._ca_cert_path is not None:
            try:
                self._ca_cert_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._ca_cert_path = None

        # Drop the per-scan Redis keys (seq counter + staged injection rules).
        if self.redis is not None:
            try:
                await self.redis.delete(
                    f"scan:{scan_id}:seq",
                    f"scan:{scan_id}:injection_rules",
                )
            except Exception as exc:
                log.debug(
                    "Failed to delete scan Redis keys",
                    error=str(exc),
                    scan_id=str(scan_id),
                )
