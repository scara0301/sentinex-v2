"""
Compliance report generation (Sprint 4).

Renders an HTML report from scan data and converts it to PDF with
WeasyPrint. When WeasyPrint (or its native Pango/Cairo stack) is not
available — e.g. local dev outside Docker — the HTML report is written
instead so the pipeline still produces an artifact.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from jinja2 import Environment, PackageLoader, select_autoescape

from sentinex_core.badges import grade_for_score
from sentinex_core.db.base import get_session
from sentinex_core.db.repos import AgentRepo, FindingRepo, RemediationRepo, ScanRepo

from .settings import worker_settings

log = structlog.get_logger()

_GRADE_COLORS = {
    "A+": "#15803d",
    "A": "#15803d",
    "B": "#65a30d",
    "C": "#ca8a04",
    "D": "#ea580c",
    "F": "#b91c1c",
    "?": "#6b7280",
}

_env = Environment(
    loader=PackageLoader("sentinex_worker", "templates"),
    autoescape=select_autoescape(["html"]),
)


async def generate_report(scan_id: str) -> Path:
    """Render the report for a scan and return the path of the artifact."""
    scan_uuid = uuid.UUID(scan_id)

    async with get_session() as db:
        scan = await ScanRepo(db).get_by_id(scan_uuid)
        if scan is None:
            raise ValueError(f"scan {scan_id} not found")
        agent = await AgentRepo(db).get_by_id(scan.agent_id)
        findings = await FindingRepo(db).list_by_scan(scan_uuid, limit=500)
        remediations = await RemediationRepo(db).list_by_finding_ids(
            [f.id for f in findings]
        )

    playbooks = {r.finding_id: r.playbook_md for r in remediations}

    severity_counts: dict[str, int] = {}
    finding_rows = []
    for f in findings:
        severity_counts[f.severity] = severity_counts.get(f.severity, 0) + 1
        finding_rows.append(
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "category": f.category,
                "title": f.title,
                "cwe": f.cwe or [],
                "evidence": f.evidence,
                "evidence_json": json.dumps(f.evidence, indent=2, default=str)
                if f.evidence
                else "",
                "playbook_md": playbooks.get(f.id) or "",
            }
        )

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    finding_rows.sort(key=lambda r: severity_order.get(str(r["severity"]), 9))

    score = float(scan.risk_score) if scan.risk_score is not None else None
    grade = grade_for_score(score)

    html = _env.get_template("report.html.j2").render(
        scan_id=str(scan.id),
        agent_name=agent.name if agent else "unknown",
        agent_version=agent.version if agent else "?",
        agent_framework=agent.framework if agent else "unknown",
        started_at=scan.started_at.isoformat() if scan.started_at else None,
        finished_at=scan.finished_at.isoformat() if scan.finished_at else None,
        scenario_count=len(scan.scenario_ids or []) or "all builtin",
        risk_score=f"{score:.1f}" if score is not None else "n/a",
        grade=grade,
        grade_color=_GRADE_COLORS.get(grade, "#6b7280"),
        severity_counts=severity_counts,
        findings=finding_rows,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    out_dir = Path(worker_settings.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = out_dir / f"{scan_id}.pdf"
    try:
        from weasyprint import HTML  # noqa: PLC0415 — heavy import, native deps

        HTML(string=html).write_pdf(str(pdf_path))
        log.info("Report PDF written", scan_id=scan_id, path=str(pdf_path))
        return pdf_path
    except Exception as exc:
        log.warning(
            "WeasyPrint unavailable; writing HTML report instead",
            scan_id=scan_id,
            error=str(exc),
        )
        html_path = out_dir / f"{scan_id}.html"
        html_path.write_text(html, encoding="utf-8")
        return html_path
