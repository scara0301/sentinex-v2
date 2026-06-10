"""
OpenAI Assistants loader (Sprint 5).

An Assistants "bundle" is configuration, not code. Two sources:

- upload metadata containing ``assistant_id`` (passed as a form field), or
- an ``assistant.json`` file in the bundle — the JSON shape returned by
  ``GET /v1/assistants/{id}`` — from which model and function-tool
  schemas are extracted statically.

The sandbox wraps the assistant at runtime via OPENAI_BASE_URL, so every
run/tool-call passes through the proxy like any other agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .base import AgentLoader, UploadBundle
from ..schema import (
    AgentInfo,
    AgentManifest,
    LoaderMeta,
    ModelConfig,
    ToolDefinition,
)

_CONFIG_FILENAMES = ("assistant.json", "openai_assistant.json")


def _find_config_file(bundle: UploadBundle) -> Optional[Path]:
    root = bundle.root
    if root.is_file():
        return root if root.name in _CONFIG_FILENAMES else None
    for name in _CONFIG_FILENAMES:
        for candidate in root.rglob(name):
            return candidate
    return None


def _load_config(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class OpenAIAssistantsLoader(AgentLoader):
    framework = "openai_assistants"

    def detect(self, bundle: UploadBundle) -> bool:
        if bundle.metadata.get("assistant_id"):
            return True
        config_path = _find_config_file(bundle)
        if config_path is None:
            return False
        config = _load_config(config_path)
        if config is None:
            return False
        assistant_id = config.get("assistant_id") or config.get("id") or ""
        return isinstance(assistant_id, str) and assistant_id.startswith("asst_")

    def load(self, bundle: UploadBundle) -> AgentManifest:
        warnings: list[str] = []
        tools: list[ToolDefinition] = []
        models: list[ModelConfig] = []

        config: dict[str, Any] = {}
        config_path = _find_config_file(bundle)
        if config_path is not None:
            config = _load_config(config_path) or {}

        assistant_id = (
            bundle.metadata.get("assistant_id")
            or config.get("assistant_id")
            or config.get("id")
            or "unknown"
        )
        agent_name = (
            bundle.metadata.get("name") or config.get("name") or assistant_id
        )

        if model := config.get("model"):
            models.append(ModelConfig(provider="openai", model=str(model)))

        for entry in config.get("tools", []):
            if not isinstance(entry, dict):
                continue
            tool_type = entry.get("type")
            if tool_type == "function":
                fn = entry.get("function") or {}
                tools.append(
                    ToolDefinition(
                        name=str(fn.get("name", "unnamed_function")),
                        source="openai_assistants.function",
                        args_schema=fn.get("parameters") or {},
                    )
                )
            elif tool_type in ("code_interpreter", "file_search", "retrieval"):
                tools.append(
                    ToolDefinition(
                        name=str(tool_type),
                        source="openai_assistants.builtin",
                    )
                )

        if not config:
            warnings.append(
                "No assistant.json in bundle — tool schemas will only be "
                "observable at runtime through the proxy"
            )

        return AgentManifest(
            agent=AgentInfo(name=str(agent_name), framework="openai_assistants"),
            models=models,
            tools=tools,
            secrets_required=["OPENAI_API_KEY"],
            loader=LoaderMeta(detected_by="openai_assistants", warnings=warnings),
        )
