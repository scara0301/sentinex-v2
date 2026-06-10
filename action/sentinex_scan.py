#!/usr/bin/env python3
"""
SENTINEX GitHub Action client (Sprint 5).

Standard library only — runs on any GitHub runner with python3:
upload bundle -> start scan -> poll -> fetch findings -> gate the build.

Configuration comes from SENTINEX_* environment variables (see action.yml).
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
TERMINAL_STATUSES = {"DONE", "FAILED"}
POLL_INTERVAL_SECONDS = 10


class ScanError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Gate logic (pure — unit tested)
# ---------------------------------------------------------------------------

def evaluate_gate(
    findings: list[dict],
    risk_score: float | None,
    fail_on_severity: str,
    max_risk_score: float | None,
) -> tuple[bool, list[str]]:
    """Return ``(passed, reasons)`` for the build gate."""
    reasons: list[str] = []

    if fail_on_severity != "never":
        threshold = SEVERITY_ORDER.get(fail_on_severity)
        if threshold is None:
            raise ValueError(f"invalid fail-on-severity: {fail_on_severity!r}")
        offenders = [
            f for f in findings
            if SEVERITY_ORDER.get(f.get("severity", "info"), 4) <= threshold
        ]
        if offenders:
            worst = min(
                offenders, key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 4)
            )
            reasons.append(
                f"{len(offenders)} finding(s) at or above '{fail_on_severity}' "
                f"severity (worst: {worst.get('rule_id')} [{worst.get('severity')}])"
            )

    if max_risk_score is not None and risk_score is not None:
        if risk_score > max_risk_score:
            reasons.append(
                f"risk score {risk_score:.1f} exceeds the maximum of {max_risk_score:.1f}"
            )

    return (not reasons, reasons)


# ---------------------------------------------------------------------------
# Minimal HTTP client (stdlib only)
# ---------------------------------------------------------------------------

def _request(
    method: str,
    url: str,
    api_key: str | None = None,
    json_body: dict | None = None,
    raw_body: bytes | None = None,
    content_type: str | None = None,
) -> dict:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    data = raw_body
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise ScanError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ScanError(f"{method} {url} failed: {exc.reason}") from exc
    return json.loads(payload) if payload else {}


def _multipart(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"sentinex-{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{key}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    parts.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{file_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# GitHub helpers
# ---------------------------------------------------------------------------

def _gh_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


def _gh_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def main() -> int:
    api_url = os.environ["SENTINEX_API_URL"].rstrip("/")
    api_key = os.environ["SENTINEX_API_KEY"]
    workspace_id = os.environ["SENTINEX_WORKSPACE_ID"]
    agent_name = os.environ["SENTINEX_AGENT_NAME"]
    bundle_path = Path(os.environ["SENTINEX_BUNDLE_PATH"])
    fail_on_severity = os.environ.get("SENTINEX_FAIL_ON_SEVERITY", "high")
    max_risk_raw = os.environ.get("SENTINEX_MAX_RISK_SCORE", "")
    max_risk_score = float(max_risk_raw) if max_risk_raw else None
    timeout = int(os.environ.get("SENTINEX_TIMEOUT_SECONDS", "900"))
    scenario_ids = [
        s.strip()
        for s in os.environ.get("SENTINEX_SCENARIO_IDS", "").split(",")
        if s.strip()
    ]

    if not bundle_path.exists():
        print(f"::error::bundle not found: {bundle_path}")
        return 2

    ws_base = f"{api_url}/workspace/{workspace_id}"

    print(f"Uploading {bundle_path.name} as agent '{agent_name}'...")
    body, content_type = _multipart({"name": agent_name}, "file", bundle_path)
    agent = _request(
        "POST", f"{ws_base}/agent", api_key, raw_body=body, content_type=content_type
    )
    agent_id = agent["id"]
    print(f"Agent registered: {agent_id} (framework: {agent.get('framework')})")

    scan = _request(
        "POST",
        f"{ws_base}/scan",
        api_key,
        json_body={"agent_id": agent_id, "scenario_ids": scenario_ids},
    )
    scan_id = scan["scan_id"]
    print(f"Scan started: {scan_id}")
    _gh_output("scan-id", str(scan_id))

    deadline = time.monotonic() + timeout
    status = scan.get("status", "PENDING")
    risk_score: float | None = None
    while time.monotonic() < deadline:
        info = _request("GET", f"{ws_base}/scan/{scan_id}", api_key)
        status = info.get("status", status)
        risk_score = (
            float(info["risk_score"]) if info.get("risk_score") is not None else None
        )
        print(f"  status={status} risk_score={risk_score}")
        if status in TERMINAL_STATUSES:
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        print(f"::error::scan did not finish within {timeout}s")
        return 2

    if status == "FAILED":
        print("::error::scan failed inside the sandbox")
        return 2

    findings = _request("GET", f"{ws_base}/scan/{scan_id}/findings", api_key)
    grade = _grade(risk_score)
    badge_url = f"{api_url}/badge/{scan_id}.svg"

    _gh_output("risk-score", "" if risk_score is None else f"{risk_score:.1f}")
    _gh_output("grade", grade)
    _gh_output("findings-count", str(len(findings)))
    _gh_output("badge-url", badge_url)

    passed, reasons = evaluate_gate(findings, risk_score, fail_on_severity, max_risk_score)
    _write_summary(scan_id, grade, risk_score, findings, passed, reasons, badge_url)

    if not passed:
        for reason in reasons:
            print(f"::error::SENTINEX gate failed: {reason}")
        return 1
    print(f"SENTINEX gate passed (grade {grade}, {len(findings)} finding(s)).")
    return 0


def _grade(score: float | None) -> str:
    if score is None:
        return "?"
    for limit, grade in ((10, "A+"), (25, "A"), (45, "B"), (65, "C"), (85, "D")):
        if score <= limit:
            return grade
    return "F"


def _write_summary(
    scan_id, grade, risk_score, findings, passed, reasons, badge_url
) -> None:
    lines = [
        "## SENTINEX scan result",
        "",
        f"![badge]({badge_url})",
        "",
        f"- **Scan:** `{scan_id}`",
        f"- **Grade:** {grade} (risk score: "
        f"{'n/a' if risk_score is None else f'{risk_score:.1f}'})",
        f"- **Gate:** {'✅ passed' if passed else '❌ failed — ' + '; '.join(reasons)}",
        "",
    ]
    if findings:
        lines += ["| Severity | Rule | Title |", "|---|---|---|"]
        order = sorted(
            findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 4)
        )
        for f in order[:20]:
            lines.append(
                f"| {f.get('severity')} | `{f.get('rule_id')}` | {f.get('title')} |"
            )
    else:
        lines.append("No findings 🎉")
    _gh_summary("\n".join(lines))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ScanError as exc:
        print(f"::error::{exc}")
        sys.exit(2)
