from __future__ import annotations
import ast
from pathlib import Path
from typing import Optional

from .base import AgentLoader, UploadBundle
from ..schema import AgentManifest, AgentInfo, ToolDefinition, ModelConfig, LoaderMeta, SideEffect


_TOOL_NAME_KEYWORDS = {"tool", "action", "execute", "run", "invoke", "call", "handler"}

_LLM_CLIENT_CLASSES = {
    "OpenAI": ("openai", "model"),
    "AsyncOpenAI": ("openai", "model"),
    "Anthropic": ("anthropic", "model"),
    "AsyncAnthropic": ("anthropic", "model"),
    "AzureOpenAI": ("azure_openai", "model"),
    "Groq": ("groq", "model"),
    "Cohere": ("cohere", "model"),
}


def _collect_py_files(bundle: UploadBundle) -> list[Path]:
    root = bundle.root
    if root.is_file() and root.suffix == ".py":
        return [root]
    return list(root.rglob("*.py"))


def _get_string_value(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_kwarg(call: ast.Call, key: str) -> Optional[str]:
    for kw in call.keywords:
        if kw.arg == key:
            return _get_string_value(kw.value)
    return None


def _name_has_keyword(name: str) -> bool:
    name_lower = name.lower()
    return any(kw in name_lower for kw in _TOOL_NAME_KEYWORDS)


class RawPythonLoader(AgentLoader):
    framework = "raw_python"

    def detect(self, bundle: UploadBundle) -> bool:
        # Always matches as fallback — accepts any .py file or directory
        root = bundle.root
        if root.is_file():
            return root.suffix == ".py"
        return any(root.rglob("*.py"))

    def load(self, bundle: UploadBundle) -> AgentManifest:
        tools: list[ToolDefinition] = []
        models: list[ModelConfig] = []
        warnings: list[str] = []
        seen_tool_names: set[str] = set()
        seen_model_keys: set[tuple[str, str]] = set()

        for py_file in _collect_py_files(bundle):
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError as exc:
                warnings.append(f"Syntax error in {py_file.name}: {exc}")
                continue

            for node in ast.walk(tree):
                # Function definitions with tool-like names
                if isinstance(node, ast.FunctionDef) and _name_has_keyword(node.name):
                    if node.name not in seen_tool_names:
                        seen_tool_names.add(node.name)
                        side_effects: list[SideEffect] = []

                        # Inspect the function body for side effect signals
                        for child in ast.walk(node):
                            if isinstance(child, ast.Call):
                                func = child.func
                                # requests.get / requests.post / requests.request → network
                                if isinstance(func, ast.Attribute) and func.attr in (
                                    "get", "post", "put", "patch", "delete", "request", "head"
                                ):
                                    if isinstance(func.value, ast.Name) and func.value.id in ("requests", "httpx", "aiohttp"):
                                        if "network" not in side_effects:
                                            side_effects.append("network")

                                # open(...) → fs side effect
                                if isinstance(func, ast.Name) and func.id == "open":
                                    mode_arg = None
                                    if len(child.args) >= 2:
                                        mode_arg = _get_string_value(child.args[1])
                                    mode_arg = mode_arg or _extract_kwarg(child, "mode") or "r"
                                    if "w" in mode_arg or "a" in mode_arg or "x" in mode_arg:
                                        if "fs:write" not in side_effects:
                                            side_effects.append("fs:write")
                                    else:
                                        if "fs:read" not in side_effects:
                                            side_effects.append("fs:read")

                        tools.append(
                            ToolDefinition(
                                name=node.name,
                                source=f"raw_python:{py_file.name}",
                                side_effects=side_effects,
                            )
                        )

                # Top-level requests calls (outside tool functions)
                if isinstance(node, ast.Call):
                    func = node.func
                    # requests.get / requests.post at module level
                    if isinstance(func, ast.Attribute) and func.attr in (
                        "get", "post", "put", "patch", "delete", "request"
                    ):
                        if isinstance(func.value, ast.Name) and func.value.id in ("requests", "httpx"):
                            # Captured via tool body above; warn at module level
                            pass

                    # LLM client instantiations
                    func_name = None
                    if isinstance(func, ast.Name):
                        func_name = func.id
                    elif isinstance(func, ast.Attribute):
                        func_name = func.attr

                    if func_name and func_name in _LLM_CLIENT_CLASSES:
                        provider, model_kwarg = _LLM_CLIENT_CLASSES[func_name]
                        model_name = _extract_kwarg(node, model_kwarg) or _extract_kwarg(node, "model_name") or "unknown"
                        key = (provider, model_name)
                        if key not in seen_model_keys:
                            seen_model_keys.add(key)
                            models.append(ModelConfig(provider=provider, model=model_name))

        warnings.append("Raw Python loader — tool detection is best-effort based on function name heuristics")

        agent_name = bundle.entry_file.stem if bundle.entry_file else bundle.root.name

        return AgentManifest(
            agent=AgentInfo(
                name=agent_name,
                framework="raw_python",
            ),
            tools=tools,
            models=models,
            loader=LoaderMeta(detected_by="raw_python", warnings=warnings),
        )
