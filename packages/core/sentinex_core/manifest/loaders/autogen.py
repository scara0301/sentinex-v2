"""
AutoGen loader (Sprint 5).

AST-only extraction — uploaded code is never imported or executed:

- ``AssistantAgent / UserProxyAgent / ConversableAgent / GroupChatManager``
  instantiations -> graph nodes
- ``x.initiate_chat(y, ...)`` calls -> handoff edges
- ``GroupChat(agents=[...])`` (+ manager) -> broadcast edges
- ``register_function(fn, caller=..., executor=...)`` and
  ``@agent.register_for_llm()`` / ``@agent.register_for_execution()``
  decorators -> tool definitions
- ``llm_config={"model": ...}`` / ``config_list=[{"model": ...}]`` -> models
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

_AGENT_CLASSES = {
    "AssistantAgent": "assistant",
    "UserProxyAgent": "user proxy",
    "ConversableAgent": "conversable",
    "GroupChatManager": "group chat manager",
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


def _call_name(node: ast.Call) -> Optional[str]:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _extract_kwarg_node(call: ast.Call, key: str) -> Optional[ast.expr]:
    for kw in call.keywords:
        if kw.arg == key:
            return kw.value
    return None


def _models_from_llm_config(node: ast.expr) -> list[str]:
    """Pull model name strings out of an llm_config dict / config_list."""
    found: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Dict):
            for k, v in zip(sub.keys, sub.values):
                if (
                    k is not None
                    and _get_string_value(k) == "model"
                    and (model := _get_string_value(v))
                ):
                    found.append(model)
    return found


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
        warnings: list[str] = []
        tools: dict[str, ToolDefinition] = {}
        models: list[ModelConfig] = []
        seen_models: set[str] = set()

        # var name -> role
        agents: dict[str, str] = {}
        # var name -> member var names
        group_chats: dict[str, list[str]] = {}
        # (caller var, recipient var) handoffs from initiate_chat
        handoffs: list[tuple[str, str]] = []
        # group chat var -> manager var
        managers: dict[str, str] = {}

        for py_file in _collect_py_files(bundle):
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError as exc:
                warnings.append(f"Syntax error in {py_file.name}: {exc}")
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    call = node.value
                    func_name = _call_name(call)
                    target = node.targets[0] if node.targets else None
                    if not isinstance(target, ast.Name):
                        continue

                    if func_name in _AGENT_CLASSES:
                        name_kw = _extract_kwarg_node(call, "name")
                        display = (
                            _get_string_value(name_kw) if name_kw is not None else None
                        )
                        agents[target.id] = display or _AGENT_CLASSES[func_name]
                        if func_name == "GroupChatManager":
                            gc = _extract_kwarg_node(call, "groupchat")
                            if isinstance(gc, ast.Name):
                                managers[gc.id] = target.id

                    elif func_name == "GroupChat":
                        members_kw = _extract_kwarg_node(call, "agents")
                        members = []
                        if isinstance(members_kw, (ast.List, ast.Tuple)):
                            members = [
                                e.id for e in members_kw.elts if isinstance(e, ast.Name)
                            ]
                        group_chats[target.id] = members

                if isinstance(node, ast.Call):
                    func_name = _call_name(node)

                    # x.initiate_chat(y, ...) -> handoff x -> y
                    if (
                        func_name == "initiate_chat"
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                    ):
                        caller = node.func.value.id
                        recipient = None
                        if node.args and isinstance(node.args[0], ast.Name):
                            recipient = node.args[0].id
                        else:
                            rec_kw = _extract_kwarg_node(node, "recipient")
                            if isinstance(rec_kw, ast.Name):
                                recipient = rec_kw.id
                        if recipient:
                            handoffs.append((caller, recipient))

                    # register_function(fn, caller=..., executor=...)
                    if func_name == "register_function" and node.args:
                        fn = node.args[0]
                        if isinstance(fn, ast.Name):
                            tools.setdefault(
                                fn.id,
                                ToolDefinition(
                                    name=fn.id, source="autogen.register_function"
                                ),
                            )

                    # llm_config / config_list models
                    for key in ("llm_config", "config_list"):
                        cfg = _extract_kwarg_node(node, key)
                        if cfg is not None:
                            for model in _models_from_llm_config(cfg):
                                if model not in seen_models:
                                    seen_models.add(model)
                                    provider = (
                                        "anthropic" if "claude" in model else "openai"
                                    )
                                    models.append(
                                        ModelConfig(provider=provider, model=model)
                                    )

                # @agent.register_for_llm(...) / @agent.register_for_execution(...)
                if isinstance(node, ast.FunctionDef):
                    for decorator in node.decorator_list:
                        dec = decorator
                        if isinstance(dec, ast.Call):
                            dec = dec.func
                        if (
                            isinstance(dec, ast.Attribute)
                            and dec.attr in ("register_for_llm", "register_for_execution")
                        ):
                            tools.setdefault(
                                node.name,
                                ToolDefinition(
                                    name=node.name, source=f"autogen.{dec.attr}"
                                ),
                            )

        graph = self._build_graph(agents, group_chats, managers, handoffs)
        if not agents:
            warnings.append("No AutoGen agent instantiations found; roster is empty")

        agent_name = bundle.entry_file.stem if bundle.entry_file else bundle.root.name

        return AgentManifest(
            agent=AgentInfo(name=agent_name, framework="autogen"),
            tools=list(tools.values()),
            models=models,
            agents_graph=graph,
            loader=LoaderMeta(detected_by="autogen", warnings=warnings),
        )

    @staticmethod
    def _build_graph(
        agents: dict[str, str],
        group_chats: dict[str, list[str]],
        managers: dict[str, str],
        handoffs: list[tuple[str, str]],
    ) -> Optional[AgentGraph]:
        if not agents:
            return None

        nodes = [GraphNode(id=var, role=role) for var, role in agents.items()]
        edges: list[GraphEdge] = []
        seen: set[tuple[str, str, str]] = set()

        def add_edge(src: str, dst: str, kind: str) -> None:
            key = (src, dst, kind)
            if src != dst and key not in seen:
                seen.add(key)
                edges.append(
                    GraphEdge.model_validate({"from": src, "to": dst, "kind": kind})
                )

        for src, dst in handoffs:
            if src in agents and dst in agents:
                add_edge(src, dst, "handoff")

        for gc_var, members in group_chats.items():
            hub = managers.get(gc_var)
            if hub is None:
                # No manager: synthesize a hub node for the chat itself.
                hub = gc_var
                if not any(n.id == hub for n in nodes):
                    nodes.append(GraphNode(id=hub, role="group chat"))
            for member in members:
                if member in agents:
                    add_edge(hub, member, "broadcast")

        return AgentGraph(nodes=nodes, edges=edges)
