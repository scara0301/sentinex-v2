from __future__ import annotations
import ast
from pathlib import Path

from .base import AgentLoader, UploadBundle
from ..schema import AgentManifest, AgentInfo, LoaderMeta


def _collect_py_files(bundle: UploadBundle) -> list[Path]:
    root = bundle.root
    if root.is_file() and root.suffix == ".py":
        return [root]
    return list(root.rglob("*.py"))


def _has_autogen_import(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "autogen" or node.module.startswith("autogen."):
                return True
            if node.module == "pyautogen" or node.module.startswith("pyautogen."):
                return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("autogen", "pyautogen") or alias.name.startswith("autogen."):
                    return True
    return False


class AutoGenLoader(AgentLoader):
    framework = "autogen"

    def detect(self, bundle: UploadBundle) -> bool:
        for py_file in _collect_py_files(bundle):
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue
            if _has_autogen_import(tree):
                return True
        return False

    def load(self, bundle: UploadBundle) -> AgentManifest:
        warnings: list[str] = [
            "AutoGen loader is a stub — full multi-agent graph extraction not yet implemented"
        ]

        agent_name = bundle.entry_file.stem if bundle.entry_file else bundle.root.name

        return AgentManifest(
            agent=AgentInfo(
                name=agent_name,
                framework="autogen",
            ),
            loader=LoaderMeta(detected_by="autogen", warnings=warnings),
        )
