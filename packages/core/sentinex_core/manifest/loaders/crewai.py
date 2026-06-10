"""
CrewAI loader (Sprint 5).

AST-only extraction — uploaded code is never imported or executed:

- ``Agent(role=..., allow_delegation=..., tools=[...])`` -> graph nodes
- ``Crew(agents=[...], process=Process.sequential|hierarchical)`` -> edges
  (sequential: handoff chain in roster order; hierarchical: a synthetic
  manager node delegating to every member)
- ``allow_delegation=True`` -> delegate edges to every other crew member
- ``@tool`` functions, ``BaseTool`` subclasses, and ``Agent(tools=[...])``
  references -> tool definitions
- LLM class instantiations -> model configs
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from .base import AgentLoader, UploadBundle
from ..schema import (
    AgentGraph,
    AgentInfo,
    AgentManifest,
    GraphEdge,
    GraphNode,
    LoaderMeta,
    ModelConfig,
    ToolDefinition,
)

_LLM_CLASSES = {
    "ChatOpenAI": ("openai", "model_name"),
    "OpenAI": ("openai", "model_name"),
    "ChatAnthropic": ("anthropic", "model"),
    "ChatGroq": ("groq", "model_name"),
    "LLM": ("crewai", "model"),  # crewai.LLM(model="provider/model")
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


def _extract_bool_kwarg(call: ast.Call, key: str) -> Optional[bool]:
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, ast.Constant):
            if isinstance(kw.value.value, bool):
                return kw.value.value
    return None


def _extract_name_list_kwarg(call: ast.Call, key: str) -> list[str]:
    """Return variable names from a ``key=[a, b, c]`` keyword argument."""
    for kw in call.keywords:
        if kw.arg == key and isinstance(kw.value, (ast.List, ast.Tuple)):
            return [
                elt.id for elt in kw.value.elts if isinstance(elt, ast.Name)
            ]
    return []


def _call_name(node: ast.Call) -> Optional[str]:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _decorator_name(decorator: ast.expr) -> Optional[str]:
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
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
        warnings: list[str] = []
        tools: dict[str, ToolDefinition] = {}
        models: list[ModelConfig] = []
        seen_model_keys: set[tuple[str, str]] = set()

        # var name -> {"role": str, "delegation": bool}
        crew_agents: dict[str, dict] = {}
        # each Crew() call: {"agents": [var, ...], "process": "sequential"|"hierarchical"}
        crews: list[dict] = []

        for py_file in _collect_py_files(bundle):
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError as exc:
                warnings.append(f"Syntax error in {py_file.name}: {exc}")
                continue

            for node in ast.walk(tree):
                # var = Agent(role=..., allow_delegation=..., tools=[...])
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    call = node.value
                    if _call_name(call) == "Agent" and node.targets:
                        target = node.targets[0]
                        if isinstance(target, ast.Name):
                            crew_agents[target.id] = {
                                "role": _extract_kwarg(call, "role") or target.id,
                                "delegation": _extract_bool_kwarg(
                                    call, "allow_delegation"
                                )
                                or False,
                            }
                            for tool_ref in _extract_name_list_kwarg(call, "tools"):
                                tools.setdefault(
                                    tool_ref,
                                    ToolDefinition(
                                        name=tool_ref, source="crewai.agent_tools"
                                    ),
                                )

                if isinstance(node, ast.Call):
                    func_name = _call_name(node)

                    # Crew(agents=[...], process=Process.hierarchical)
                    if func_name == "Crew":
                        process = "sequential"
                        for kw in node.keywords:
                            if kw.arg == "process" and isinstance(
                                kw.value, ast.Attribute
                            ):
                                process = kw.value.attr
                        crews.append(
                            {
                                "agents": _extract_name_list_kwarg(node, "agents"),
                                "process": process,
                            }
                        )

                    # LLM instantiations
                    if func_name in _LLM_CLASSES:
                        provider, model_kwarg = _LLM_CLASSES[func_name]
                        model_name = (
                            _extract_kwarg(node, model_kwarg)
                            or _extract_kwarg(node, "model")
                            or "unknown"
                        )
                        if provider == "crewai" and "/" in model_name:
                            provider, _, model_name = model_name.partition("/")
                        key = (provider, model_name)
                        if key not in seen_model_keys:
                            seen_model_keys.add(key)
                            models.append(
                                ModelConfig(provider=provider, model=model_name)
                            )

                # @tool functions
                if isinstance(node, ast.FunctionDef):
                    for decorator in node.decorator_list:
                        if _decorator_name(decorator) == "tool":
                            tools.setdefault(
                                node.name,
                                ToolDefinition(name=node.name, source="crewai.tool"),
                            )

                # class MyTool(BaseTool): name = "..."
                if isinstance(node, ast.ClassDef):
                    base_names = {
                        b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                        for b in node.bases
                    }
                    if "BaseTool" in base_names:
                        tool_name = node.name
                        for stmt in node.body:
                            if (
                                isinstance(stmt, ast.Assign)
                                and stmt.targets
                                and isinstance(stmt.targets[0], ast.Name)
                                and stmt.targets[0].id == "name"
                            ):
                                tool_name = _get_string_value(stmt.value) or tool_name
                            elif (
                                isinstance(stmt, ast.AnnAssign)
                                and isinstance(stmt.target, ast.Name)
                                and stmt.target.id == "name"
                                and stmt.value is not None
                            ):
                                tool_name = _get_string_value(stmt.value) or tool_name
                        tools.setdefault(
                            tool_name,
                            ToolDefinition(name=tool_name, source="crewai.base_tool"),
                        )

        graph = self._build_graph(crew_agents, crews)
        if not crew_agents:
            warnings.append("No Agent() definitions found; roster is empty")

        agent_name = bundle.entry_file.stem if bundle.entry_file else bundle.root.name

        return AgentManifest(
            agent=AgentInfo(name=agent_name, framework="crewai"),
            tools=list(tools.values()),
            models=models,
            agents_graph=graph,
            loader=LoaderMeta(detected_by="crewai", warnings=warnings),
        )

    @staticmethod
    def _build_graph(
        crew_agents: dict[str, dict], crews: list[dict]
    ) -> Optional[AgentGraph]:
        if not crew_agents:
            return None

        nodes = [
            GraphNode(id=var, role=info["role"])
            for var, info in crew_agents.items()
        ]
        edges: list[GraphEdge] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edge(src: str, dst: str, kind: str) -> None:
            key = (src, dst, kind)
            if src != dst and key not in seen:
                seen.add(key)
                edges.append(GraphEdge.model_validate({"from": src, "to": dst, "kind": kind}))

        for crew in crews:
            members = [m for m in crew["agents"] if m in crew_agents]
            if crew["process"] == "hierarchical":
                if not any(n.id == "manager" for n in nodes):
                    nodes.append(GraphNode(id="manager", role="crew manager"))
                for member in members:
                    add_edge("manager", member, "delegate")
            else:  # sequential: tasks hand off down the roster
                for src, dst in zip(members, members[1:]):
                    add_edge(src, dst, "handoff")

        # allow_delegation=True lets an agent delegate to any crew mate
        for var, info in crew_agents.items():
            if info["delegation"]:
                for other in crew_agents:
                    add_edge(var, other, "delegate")

        return AgentGraph(nodes=nodes, edges=edges)
