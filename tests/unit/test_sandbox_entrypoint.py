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

BASH = shutil.which("bash")


def _bash_works() -> bool:
    """A resolvable bash is not necessarily a working one.

    On Windows the `bash` found on PATH is often the WSL stub, which fails
    with an RPC error when no distribution is installed. Probe it so a broken
    interpreter is reported as a skip rather than as an entrypoint failure.
    """
    if BASH is None:
        return False
    try:
        probe = subprocess.run(
            [BASH, "-c", "echo ok"], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return False
    return probe.returncode == 0 and probe.stdout.strip() == "ok"


pytestmark = pytest.mark.skipif(
    not _bash_works(), reason="a working bash is not available"
)


def test_entrypoint_has_unix_line_endings():
    """The image runs this on Linux; a CR in the shebang breaks exec.

    A Windows checkout with core.autocrlf=true writes CRLF into the working
    tree, and `docker build` copies the working-tree file verbatim — the
    container then fails to start with "no such file or directory" because
    the interpreter path literally ends in a carriage return.
    """
    assert b"\r\n" not in ENTRYPOINT.read_bytes()


def _run(work: Path, env_extra: dict | None = None):
    """Run the entrypoint with /work pointed at ``work`` via a shim python."""
    bindir = work.parent / "bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "python"
    # Stub python: print the script path it was asked to run, then exit 0.
    # newline="\n" so the shim keeps a LF shebang on Windows too.
    shim.write_text('#!/bin/bash\necho "RAN:$1"\n', newline="\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    # The script hardcodes /work paths; rewrite to the temp work dir. POSIX
    # separators throughout, because str(Path) on Windows embeds backslashes
    # that bash would read as escapes.
    script = ENTRYPOINT.read_text().replace("/work", work.as_posix())

    # Inherit the real environment and prepend the shim dir using the
    # platform's separator. Replacing the environment outright — or joining
    # with ":" on Windows — leaves bash with an unusable PATH, which silently
    # resolves a different interpreter.
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([bindir.as_posix(), env.get("PATH", "")])
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [BASH, "-c", script],
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
