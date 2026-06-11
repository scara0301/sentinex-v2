"""
Tests the sandbox entrypoint's agent-resolution logic.

The script execs `python <entry>`; we shim `python` with a stub on PATH that
prints the resolved script path so we can assert what would run, without
needing a real interpreter or Docker.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "sandbox-image"
    / "entrypoint.sh"
)

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available"
)


def _run(work: Path, env_extra: dict | None = None):
    """Run the entrypoint with /work pointed at ``work`` via a shim python."""
    bindir = work.parent / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "python"
    # Stub python: print the script path it was asked to run, then exit 0.
    shim.write_text('#!/bin/bash\necho "RAN:$1"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    # The script hardcodes /work paths; rewrite to the temp work dir.
    script = ENTRYPOINT.read_text().replace("/work", str(work))

    env = {"PATH": f"{bindir}:{os.environ['PATH']}"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
    )


def test_resolves_src_agent_entry(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "agent_entry.py").write_text("# agent")
    result = _run(work)
    assert result.returncode == 0, result.stderr
    assert "RAN:" in result.stdout
    assert result.stdout.strip().endswith("src/agent_entry.py")


def test_resolves_single_py_file(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "my_bot.py").write_text("# agent")
    result = _run(work)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("src/my_bot.py")


def test_prefers_conventional_name_over_lone_file(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "src" / "main.py").write_text("# entry")
    (work / "src" / "helpers.py").write_text("# not entry")
    result = _run(work)
    assert result.stdout.strip().endswith("src/main.py")


def test_fails_when_no_entry(tmp_path):
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    result = _run(work)
    assert result.returncode == 3
    assert "no agent entry script" in result.stderr
