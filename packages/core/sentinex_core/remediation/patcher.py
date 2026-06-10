"""
Minimal unified-diff applier used by the ``apply_fix`` worker job.

Supports the subset of unified diff syntax our remediation templates emit
plus simple hand-written patches: file creation (``--- /dev/null``) and
in-place modification with context lines. Paths are confined to the
target root; ``a/``/``b/`` prefixes are stripped.
"""

from __future__ import annotations

import re
from pathlib import Path


class PatchError(Exception):
    pass


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _strip_prefix(path: str) -> str:
    path = path.strip()
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _safe_join(root: Path, rel: str) -> Path:
    candidate = (root / rel).resolve()
    root = root.resolve()
    if not candidate.is_relative_to(root):
        raise PatchError(f"patch path escapes target root: {rel}")
    return candidate


def apply_unified_diff(root: Path, diff_text: str) -> list[str]:
    """Apply ``diff_text`` under ``root``. Returns the list of files written."""
    lines = diff_text.splitlines()
    changed: list[str] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("--- "):
            i += 1
            continue
        old_path = _strip_prefix(lines[i][4:])
        if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            raise PatchError(f"malformed diff: missing +++ after line {i + 1}")
        new_path = _strip_prefix(lines[i + 1][4:])
        i += 2

        hunks: list[tuple[int, list[str]]] = []
        while i < len(lines) and _HUNK_RE.match(lines[i]):
            match = _HUNK_RE.match(lines[i])
            assert match is not None
            old_start = int(match.group(1))
            i += 1
            body: list[str] = []
            # An entirely empty line is an empty *context* line — many tools
            # emit "" instead of " " for those.
            while i < len(lines) and (
                lines[i] == "" or lines[i][:1] in (" ", "+", "-", "\\")
            ):
                if lines[i] == "":
                    body.append(" ")
                elif not lines[i].startswith("\\"):  # skip "\ No newline" markers
                    body.append(lines[i])
                i += 1
            hunks.append((old_start, body))

        if not hunks:
            raise PatchError(f"diff for {new_path} contains no hunks")
        _apply_file_patch(root, old_path, new_path, hunks)
        changed.append(new_path)
    if not changed:
        raise PatchError("diff contained no file sections")
    return changed


def _apply_file_patch(
    root: Path,
    old_path: str,
    new_path: str,
    hunks: list[tuple[int, list[str]]],
) -> None:
    target = _safe_join(root, new_path)

    if old_path == "/dev/null":
        if target.exists():
            raise PatchError(f"cannot create {new_path}: file already exists")
        content_lines = []
        for _, body in hunks:
            for line in body:
                if line.startswith("-"):
                    raise PatchError("new-file diff cannot contain removals")
                content_lines.append(line[1:])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(content_lines) + "\n", encoding="utf-8")
        return

    source = _safe_join(root, old_path)
    if not source.exists():
        raise PatchError(f"cannot patch missing file: {old_path}")
    original = source.read_text(encoding="utf-8").splitlines()

    result: list[str] = []
    cursor = 0  # index into original
    for old_start, body in hunks:
        # Hunk line numbers are 1-based; tolerate small drift by searching
        # for the hunk's leading context near the stated position.
        expected = [line[1:] for line in body if line[0] in (" ", "-")]
        start = _locate(original, expected, old_start - 1)
        if start < cursor:
            raise PatchError(f"overlapping hunks in {new_path}")
        result.extend(original[cursor:start])
        pos = start
        for line in body:
            tag, text = line[0], line[1:]
            if tag == " ":
                if pos >= len(original) or original[pos] != text:
                    raise PatchError(f"context mismatch in {new_path} near line {pos + 1}")
                result.append(text)
                pos += 1
            elif tag == "-":
                if pos >= len(original) or original[pos] != text:
                    raise PatchError(f"removal mismatch in {new_path} near line {pos + 1}")
                pos += 1
            elif tag == "+":
                result.append(text)
        cursor = pos
    result.extend(original[cursor:])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(result) + "\n", encoding="utf-8")


def _locate(original: list[str], expected: list[str], hint: int) -> int:
    """Find where a hunk's old lines start, searching outward from ``hint``."""
    if not expected:
        return max(0, min(hint, len(original)))
    max_fuzz = 50
    for offset in range(max_fuzz + 1):
        for start in (hint - offset, hint + offset):
            if 0 <= start <= len(original) - len(expected):
                if original[start : start + len(expected)] == expected:
                    return start
    raise PatchError("hunk does not apply: context not found")
