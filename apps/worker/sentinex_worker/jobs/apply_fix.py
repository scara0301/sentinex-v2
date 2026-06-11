"""
apply_fix job (Sprint 4): apply a remediation's diff to a copy of the
agent bundle, register the patched bundle as a new agent version, and
enqueue a rescan so the fix can be verified.
"""

import shutil
import uuid
from pathlib import Path

import structlog
from sqlalchemy.exc import IntegrityError

from sentinex_core.db.base import get_session
from sentinex_core.db.repos import (
    AgentRepo,
    FindingRepo,
    RemediationRepo,
    ScanRepo,
)
from sentinex_core.remediation import PatchError, apply_unified_diff

log = structlog.get_logger()


async def apply_fix(ctx, scan_id: str, remediation_id: str):
    bound_log = log.bind(scan_id=scan_id, remediation_id=remediation_id)
    rem_uuid = uuid.UUID(remediation_id)

    async with get_session() as db:
        rem_repo = RemediationRepo(db)
        remediation = await rem_repo.get_by_id(rem_uuid)
        if remediation is None:
            bound_log.error("Remediation not found")
            return
        if remediation.applied:
            bound_log.info("Remediation already applied; skipping")
            return
        if not remediation.diff:
            bound_log.warning("Remediation has no diff (playbook-only)")
            return

        finding = await FindingRepo(db).get_by_id(remediation.finding_id)
        scan = await ScanRepo(db).get_by_id(finding.scan_id) if finding else None
        agent_repo = AgentRepo(db)
        agent = await agent_repo.get_by_id(scan.agent_id) if scan else None
        if not finding or not scan or not agent:
            bound_log.error("Finding/scan/agent chain incomplete")
            return

        if not agent.bundle_uri or not agent.bundle_uri.startswith("file://"):
            bound_log.error("Agent bundle is not a local path", uri=agent.bundle_uri)
            return
        bundle_root = Path(agent.bundle_uri.removeprefix("file://"))
        if not bundle_root.exists():
            bound_log.error("Agent bundle missing on disk", path=str(bundle_root))
            return

        # Snapshot the values we need into locals: a rollback below would expire
        # these ORM instances, and a lazy reload on an async session crashes.
        diff = remediation.diff
        agent_ws_id = agent.workspace_id
        agent_name = agent.name
        agent_framework = agent.framework
        agent_manifest = agent.manifest
        scan_ws_id = scan.workspace_id
        scan_scenario_ids = list(scan.scenario_ids or [])
        scan_config = scan.config

        # Copy the bundle and patch the copy — never mutate the original.
        new_root = bundle_root.parent / f"{bundle_root.name}-fix-{remediation_id[:8]}"
        if new_root.exists():
            shutil.rmtree(new_root)
        shutil.copytree(bundle_root, new_root)
        try:
            changed = apply_unified_diff(new_root, diff)
        except PatchError as exc:
            shutil.rmtree(new_root, ignore_errors=True)
            bound_log.error("Patch failed to apply", error=str(exc))
            return
        bound_log.info("Patch applied", files=changed)

        # Register the patched bundle as the next agent version. Retry on the
        # (workspace, name, version) unique constraint in case a concurrent
        # apply_fix for the same agent grabbed the version first.
        patched_agent = None
        for _ in range(5):
            siblings = await agent_repo.list_by_workspace(agent_ws_id)
            next_version = max(
                (a.version for a in siblings if a.name == agent_name), default=0
            ) + 1
            try:
                patched_agent = await agent_repo.create(
                    workspace_id=agent_ws_id,
                    name=agent_name,
                    framework=agent_framework,
                    version=next_version,
                    manifest=agent_manifest,
                    bundle_uri=f"file://{new_root}",
                )
                await db.flush()
                break
            except IntegrityError:
                await db.rollback()
        if patched_agent is None:
            shutil.rmtree(new_root, ignore_errors=True)
            bound_log.error("Could not allocate a patched agent version")
            return

        # Rescan with the same scenario selection to verify the fix.
        rescan = await ScanRepo(db).create(
            workspace_id=scan_ws_id,
            agent_id=patched_agent.id,
            scenario_ids=scan_scenario_ids,
            config=scan_config,
        )
        await rem_repo.mark_applied(rem_uuid, rescan.id)
        await db.commit()

    redis = ctx.get("redis")
    if redis is not None:
        await redis.enqueue_job("run_scan", str(rescan.id))
        bound_log.info("Rescan enqueued", rescan_id=str(rescan.id))
    else:
        bound_log.warning("No arq redis in context; rescan not enqueued")
