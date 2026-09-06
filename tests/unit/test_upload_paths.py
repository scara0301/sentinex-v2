"""
Tests for agent-upload path handling.

Both the agent ``name`` form field and the client-supplied filename become
path components under the upload root. Neither was validated, so a name of
``../../etc`` or a filename of ``../../evil.py`` wrote outside
``settings.upload_dir``. The archive *contents* were already guarded; the
archive's own destination was not.
"""

from pathlib import PurePosixPath

import pytest
from fastapi import HTTPException

from sentinex_api.routes.agents import (
    _allowed_upload,
    _safe_upload_filename,
    _validate_agent_name,
)


# ---------------------------------------------------------------------------
# Agent name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    ["billing-agent", "agent_v2", "Agent.1", "a", "A1", "x" * 128],
)
def test_accepts_ordinary_names(name):
    assert _validate_agent_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "../../etc",
        "..",
        ".",
        "../secrets",
        "a/../../b",
        "sub/dir",
        "back\\slash",
        "/absolute",
        "C:/windows",
        "",
        ".hidden",          # must not start with a dot
        "-leading-dash",    # must start alphanumeric
        "x" * 129,          # too long
        "name with spaces",
        "semi;colon",
        "null\x00byte",
        "new\nline",
    ],
)
def test_rejects_unsafe_names(name):
    with pytest.raises(HTTPException) as exc:
        _validate_agent_name(name)
    assert exc.value.status_code == 400


def test_traversal_name_cannot_escape_upload_root(tmp_path):
    """The end-to-end property the validator exists to guarantee."""
    upload_root = (tmp_path / "uploads").resolve()
    upload_root.mkdir()
    with pytest.raises(HTTPException):
        name = _validate_agent_name("../../../etc")
        # Unreachable; asserts the property if the validator ever regresses.
        assert (upload_root / name).resolve().is_relative_to(upload_root)


# ---------------------------------------------------------------------------
# Upload filename
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,expected",
    [
        ("bundle.zip", "bundle.zip"),
        ("agent.tar.gz", "agent.tar.gz"),
        ("my_agent.py", "my_agent.py"),
        # Directory components are stripped, POSIX and Windows style.
        ("../../evil.py", "evil.py"),
        ("/etc/passwd", "passwd"),
        ("..\\..\\evil.zip", "evil.zip"),
        ("C:\\Windows\\evil.zip", "evil.zip"),
        ("dir/sub/agent.zip", "agent.zip"),
        # Unusable values fall back to a fixed safe name.
        ("", "bundle"),
        (None, "bundle"),
        ("..", "bundle"),
        (".", "bundle"),
        ("/", "bundle"),
    ],
)
def test_upload_filename_is_reduced_to_one_component(filename, expected):
    assert _safe_upload_filename(filename) == expected


@pytest.mark.parametrize(
    "filename",
    ["../../evil.py", "/etc/passwd", "..\\..\\evil.zip", "dir/sub/agent.zip"],
)
def test_safe_filename_has_no_directory_component(filename):
    safe = _safe_upload_filename(filename)
    assert PurePosixPath(safe).name == safe
    assert "/" not in safe and "\\" not in safe
    assert safe not in ("..", ".")


def test_safe_filename_stays_inside_root(tmp_path):
    root = (tmp_path / "bundle").resolve()
    root.mkdir()
    for hostile in ("../../evil.py", "/etc/passwd", "..\\..\\evil.zip"):
        dest = (root / _safe_upload_filename(hostile)).resolve()
        assert dest.is_relative_to(root)


# ---------------------------------------------------------------------------
# Extension allowlist (unchanged behavior, pinned against regressions)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename", ["a.zip", "a.tar", "a.tar.gz", "a.tgz", "a.py", "A.ZIP"]
)
def test_allowed_extensions(filename):
    assert _allowed_upload(filename)


@pytest.mark.parametrize(
    "filename", ["a.exe", "a.sh", "a", "a.zip.txt", "a.py.bak", ""]
)
def test_disallowed_extensions(filename):
    assert not _allowed_upload(filename)


def test_traversal_filename_still_needs_an_allowed_extension():
    """The allowlist alone never protected the path — both checks apply."""
    assert _allowed_upload("../../../x.py")
    assert _safe_upload_filename("../../../x.py") == "x.py"
