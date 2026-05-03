from __future__ import annotations
import ast
from pathlib import Path
from typing import Optional

from .base import AgentLoader, UploadBundle
from ..schema import AgentManifest, AgentInfo, ToolDefinition, ModelConfig, LoaderMeta


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


def _has_crewai_import(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("crewai"):
            return True
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("crewai"):
                    return True
    return False


class CrewAILoader(AgentLoader):
    framework = "crewai"

    def detect(self, bundle: UploadBundle) -> bool:
        for py_file in _collect_py_files(bundle):
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue
            if _has_crewai_import(tree):
                return True
        return False

    def load(self, bundle: UploadBundle) -> AgentManifest:
        warnings: list[str] = [
            "CrewAI loader is a stub — full support in Sprint 5"
        ]
        tools: list[ToolDefinition] = []
        models: list[ModelConfig] = []
        seen_tool_names: set[str] = set()
        seen_model_keys: set[tuple[str, str]] = set()

        _llm_classes = {
            "ChatOpenAI": ("openai", "model_name"),
            "OpenAI": ("openai", "model_name"),
            "ChatAnthropic": ("anthropic", "model"),
            "ChatGroq": ("groq", "model_name"),
        }

        for py_file in _collect_py_files(bundle):
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError as exc:
                warnings.append(f"Syntax error in {py_file.name}: {exc}")
                continue

            for node in ast.walk(tree):
                # @tool decorated functions
                if isinstance(node, ast.FunctionDef):
                    for decorator in node.decorator_list:
                        dname = None
                        if isinstance(decorator, ast.Name):
                            dname = decorator.id
                        elif isinstance(decorator, ast.Attribute):
                            dname = decorator.attr
                        elif isinstance(decorator, ast.Call):
                            if isinstance(decorator.func, ast.Name):
                                dname = decorator.func.id
                            elif isinstance(decorator.func, ast.Attribute):
                                dname = decorator.func.attr
                        if dname == "tool" and node.name not in seen_tool_names:
                            seen_tool_names.add(node.name)
                            tools.append(
                                ToolDefinition(name=node.name, source="crewai.tool")
                            )

                # LLM instantiations
                if isinstance(node, ast.Call):
                    func_name = None
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    if func_name and func_name in _llm_classes:
                        provider, model_kwarg = _llm_classes[func_name]
                        model_name = _extract_kwarg(node, model_kwarg) or _extract_kwarg(node, "model") or "unknown"
                        key = (provider, model_name)
                        if key not in seen_model_keys:
                            seen_model_keys.add(key)
                            models.append(ModelConfig(provider=provider, model=model_name))

        agent_name = bundle.entry_file.stem if bundle.entry_file else bundle.root.name

        return AgentManifest(
            agent=AgentInfo(
                name=agent_name,
                framework="crewai",
            ),
            tools=tools,
            models=models,
            loader=LoaderMeta(detected_by="crewai", warnings=warnings),
        )
