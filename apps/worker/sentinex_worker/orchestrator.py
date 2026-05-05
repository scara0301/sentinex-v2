import asyncio
import uuid
import time
import structlog
import docker
import docker.errors
import docker.models.containers
import docker.models.networks
from datetime import datetime, timezone
from typing import Optional

from sentinex_core.db.base import get_session
from sentinex_core.db.models import Event as EventModel
from sentinex_core.db.repos import ScanRepo, AgentRepo, FindingRepo, EventRepo
from sentinex_core.events.schema import (
    EventEnvelope,
    FindingPayload,
    RiskUpdatePayload,
    StatePayload,
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
            await self._transition(scan_uuid, "PROVISIONING")
            network, proxy_container, mock_containers = await self._provision(scan_uuid, agent)

            await self._transition(scan_uuid, "SEEDING")
            await self._seed_mock_db(scan_uuid, mock_containers.get("mock-db"))

            await self._transition(scan_uuid, "RUNNING")
            agent_container = await self._launch_agent(
                scan_uuid, agent, network, proxy_container
            )
            self._containers.append(agent_container)

            await self._wait_for_agent(scan_uuid, agent_container)

            await self._transition(scan_uuid, "DRAINING")
            # Allow the proxy time to flush any buffered events to Redis before
            # we move on to scoring.
            await asyncio.sleep(2)

            await self._transition(scan_uuid, "SCORING")
            score = await self._compute_risk_score(scan_uuid)
            bound_log.info("Risk score computed", score=score)

            await self._transition(scan_uuid, "REPORTING")
            # Full PDF generation is wired up in Sprint 4 via render_report job.

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
        mock_db = self.docker.containers.run(
            "postgres:16-alpine",
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

        # ---- mock-stripe (and future mock providers) ------------------
        mock_stripe = self.docker.containers.run(
            "sentinex/mocks:latest",
            detach=True,
            network=network_name,
            name=f"sx-{scan_id}-mock-stripe",
            environment={"MOCK_PROVIDER": "stripe"},
            labels={
                "sentinex.scan_id": str(scan_id),
                "sentinex.role": "mock-stripe",
            },
        )
        self._containers.append(mock_stripe)

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
            },
            # Bind to a random host port so the worker can health-check the
            # proxy's admin API without entering the isolated network.
            ports={"8080/tcp": None},
            labels={
                "sentinex.scan_id": str(scan_id),
                "sentinex.role": "proxy",
            },
        )
        self._containers.append(proxy)

        return network, proxy, {"mock-db": mock_db, "mock-stripe": mock_stripe}

    async def _launch_agent(
        self,
        scan_id: uuid.UUID,
        agent,
        network: docker.models.networks.Network,
        proxy_container: docker.models.containers.Container,
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
        through the proxy so every prompt/completion is recorded.
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
            host_path = agent.bundle_uri.removeprefix("file://")
            volumes[host_path] = {"bind": "/work", "mode": "ro"}

        container = self.docker.containers.run(
            image,
            detach=True,
            network=network.name,
            name=f"sx-{scan_id}-agent",
            environment={
                "HTTP_PROXY": proxy_addr,
                "HTTPS_PROXY": proxy_addr,
                # Redirect major LLM provider SDKs through the proxy so every
                # API call is captured and replayed in findings.
                "OPENAI_BASE_URL": f"http://sx-{scan_id}-proxy:{worker_settings.proxy_port}/openai",
                "ANTHROPIC_BASE_URL": f"http://sx-{scan_id}-proxy:{worker_settings.proxy_port}/anthropic",
                "SCAN_ID": str(scan_id),
            },
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

        # Sprint 3 will copy scenario-specific seed/init.sql here and exec it.
        log.info("mock-db is ready", scan_id=str(scan_id))

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
