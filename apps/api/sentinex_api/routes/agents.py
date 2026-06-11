import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession

from sentinex_core.billing import get_plan
from sentinex_core.db.repos import AgentRepo
from ..deps import get_db, get_authorized_workspace
from ..settings import settings

router = APIRouter()

ALLOWED_EXTENSIONS = {".zip", ".tar", ".tar.gz", ".tgz", ".py"}


def _allowed_upload(filename: str) -> bool:
    name = filename.lower()
    return any(name.endswith(ext) for ext in ALLOWED_EXTENSIONS)


async def _save_upload(file: UploadFile, dest_path: Path) -> None:
    """Stream upload to disk respecting max_upload_size_mb."""
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        async with aiofiles.open(dest_path, "wb") as out:
            while chunk := await file.read(65536):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        413,
                        f"Upload exceeds maximum allowed size of {settings.max_upload_size_mb} MB",
                    )
                await out.write(chunk)
    except Exception:
        dest_path.unlink(missing_ok=True)
        raise


def _extract_bundle(archive_path: Path, extract_dir: Path) -> None:
    """Extract zip or tar archive into extract_dir with path-traversal protection."""
    import zipfile
    import tarfile
    import os

    name = archive_path.name.lower()
    target = extract_dir.resolve()

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Zip-bomb guard: reject if uncompressed total > 500 MB
            total_size = sum(info.file_size for info in zf.infolist())
            if total_size > 500 * 1024 * 1024:
                raise ValueError(
                    f"Archive uncompressed size {total_size} bytes exceeds 500 MB limit"
                )
            # Path-traversal guard
            for member in zf.infolist():
                dest = (target / member.filename).resolve()
                if not str(dest).startswith(str(target) + os.sep) and str(dest) != str(target):
                    raise ValueError(f"Unsafe path in archive: {member.filename}")
            zf.extractall(target)

    elif any(name.endswith(ext) for ext in (".tar.gz", ".tgz", ".tar")):
        with tarfile.open(archive_path, "r:*") as tf:
            members = tf.getmembers()
            # Zip-bomb guard
            total_size = sum(m.size for m in members if m.isfile())
            if total_size > 500 * 1024 * 1024:
                raise ValueError(
                    f"Archive uncompressed size {total_size} bytes exceeds 500 MB limit"
                )
            # Path-traversal + symlink guard
            for member in members:
                if member.issym() or member.islnk():
                    raise ValueError(f"Symbolic/hard links not allowed in archive: {member.name}")
                dest = (target / member.name).resolve()
                if not str(dest).startswith(str(target) + os.sep) and str(dest) != str(target):
                    raise ValueError(f"Unsafe path in archive: {member.name}")
            tf.extractall(target)
    # .py files are left as-is — no extraction needed


def _run_detect_secrets(bundle_dir: Path) -> dict:
    """
    Run detect-secrets scan on the bundle directory.
    Returns the parsed JSON report, or a stub result if detect-secrets is not installed.
    # TODO: ensure detect-secrets is installed in the container (add to pyproject.toml dev deps)
    """
    import json

    try:
        result = subprocess.run(
            ["detect-secrets", "scan", str(bundle_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return {"warning": "detect-secrets output was not valid JSON", "results": {}}
        return {"error": result.stderr.strip(), "results": {}}
    except FileNotFoundError:
        # detect-secrets not installed — skip silently in development
        return {"warning": "detect-secrets not installed; secret scan skipped", "results": {}}
    except subprocess.TimeoutExpired:
        return {"error": "detect-secrets timed out", "results": {}}


@router.post("", status_code=201)
async def upload_agent(
    workspace_id: uuid.UUID,
    name: str = Form(...),
    framework: Optional[str] = Form(None),
    assistant_id: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
):
    if not _allowed_upload(file.filename or ""):
        raise HTTPException(
            400,
            f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Plan quota: distinct agent names count against max_agents (Sprint 5).
    plan = get_plan(workspace.plan)
    existing_names = {a.name for a in await AgentRepo(db).list_by_workspace(workspace_id)}
    if name not in existing_names and len(existing_names) >= plan.max_agents:
        raise HTTPException(
            402,
            f"Agent limit reached ({len(existing_names)}/{plan.max_agents} on the "
            f"'{plan.name}' plan). Upgrade via POST /workspace/{{id}}/plan.",
        )

    bundle_root = Path(settings.upload_dir) / str(workspace_id) / name
    bundle_root.mkdir(parents=True, exist_ok=True)

    archive_path = bundle_root / (file.filename or "bundle")
    await _save_upload(file, archive_path)

    extract_dir = bundle_root / "src"
    extract_dir.mkdir(exist_ok=True)
    if archive_path.suffix != ".py":
        try:
            _extract_bundle(archive_path, extract_dir)
        except Exception as exc:
            raise HTTPException(422, f"Failed to extract archive: {exc}") from exc
    else:
        shutil.copy2(archive_path, extract_dir / archive_path.name)

    detected_framework = framework
    try:
        from sentinex_core.manifest import normalize_upload

        metadata = {"assistant_id": assistant_id, "name": name} if assistant_id else {}
        manifest = normalize_upload(extract_dir, metadata=metadata)
        manifest_data = manifest.model_dump(mode="json")
        if not detected_framework:
            detected_framework = manifest.agent.framework
    except ImportError:
        manifest_data = {"warning": "sentinex_core.manifest not available; manifest skipped"}
    except Exception as exc:
        manifest_data = {"error": str(exc)}

    secrets_report = _run_detect_secrets(extract_dir)
    secret_count = sum(
        len(v) for v in secrets_report.get("results", {}).values()
        if isinstance(v, list)
    )

    repo = AgentRepo(db)
    existing = await repo.list_by_workspace(workspace_id)
    same_name = [a for a in existing if a.name == name]
    next_version = max((a.version for a in same_name), default=0) + 1

    agent = await repo.create(
        workspace_id=workspace_id,
        name=name,
        framework=detected_framework or "raw_python",
        version=next_version,
        bundle_uri=f"file://{bundle_root}",
        manifest=manifest_data,
    )
    await db.commit()

    return {
        "id": agent.id,
        "name": agent.name,
        "framework": agent.framework,
        "created_at": agent.created_at,
        "manifest_summary": {
            "tools": manifest_data.get("tools", []),
            "entry_points": manifest_data.get("entry_points", []),
            "warnings": manifest_data.get("warning") or manifest_data.get("error"),
        },
        "secrets_scan": {
            "secret_count": secret_count,
            "warning": secrets_report.get("warning"),
            "error": secrets_report.get("error"),
        },
    }


@router.get("")
async def list_agents(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
):
    if workspace.id != workspace_id:
        raise HTTPException(404, "Workspace not found")
    repo = AgentRepo(db)
    agents = await repo.list_by_workspace(workspace_id)
    return [
        {
            "id": a.id,
            "name": a.name,
            "framework": a.framework,
            "created_at": a.created_at,
        }
        for a in agents
    ]


@router.get("/{agent_id}")
async def get_agent(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
):
    if workspace.id != workspace_id:
        raise HTTPException(404, "Workspace not found")
    repo = AgentRepo(db)
    agent = await repo.get_by_id(agent_id)
    if not agent or agent.workspace_id != workspace_id:
        raise HTTPException(404, "Agent not found")
    return {
        "id": agent.id,
        "name": agent.name,
        "framework": agent.framework,
        "bundle_uri": agent.bundle_uri,
        "manifest": agent.manifest,
        "created_at": agent.created_at,
    }
