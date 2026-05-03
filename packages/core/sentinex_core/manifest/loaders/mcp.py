from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from .base import AgentLoader, UploadBundle
from ..schema import AgentManifest, AgentInfo, ToolDefinition, LoaderMeta, InstrumentationHints

_MCP_CONFIG_NAMES = {"mcp.json", "claude_desktop_config.json", ".mcp.json"}


def _find_mcp_config(bundle: UploadBundle) -> tuple[Path, dict[str, Any]] | None:
    root = bundle.root
    candidates: list[Path] = []
    if root.is_file():
        if root.name in _MCP_CONFIG_NAMES:
            candidates.append(root)
    else:
        for name in _MCP_CONFIG_NAMES:
            p = root / name
            if p.exists():
                candidates.append(p)

    for config_path in candidates:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if "mcpServers" in data:
                return config_path, data
        except (json.JSONDecodeError, OSError):
            continue
    return None


class MCPLoader(AgentLoader):
    framework = "mcp"

    def detect(self, bundle: UploadBundle) -> bool:
        return _find_mcp_config(bundle) is not None

    def load(self, bundle: UploadBundle) -> AgentManifest:
        result = _find_mcp_config(bundle)
        warnings: list[str] = []
        tools: list[ToolDefinition] = []
        mcp_servers_raw: list[dict[str, Any]] = []

        if result is None:
            warnings.append("MCP config file not found during load — bundle may have changed")
        else:
            config_path, data = result
            mcp_servers: dict[str, Any] = data.get("mcpServers", {})

            for server_name, server_cfg in mcp_servers.items():
                mcp_servers_raw.append({"name": server_name, **server_cfg})
                tools.append(
                    ToolDefinition(
                        name=server_name,
                        source=f"mcp:{server_name}",
                        side_effects=["network"],
                        args_schema=server_cfg if isinstance(server_cfg, dict) else {},
                    )
                )

        agent_name = bundle.entry_file.stem if bundle.entry_file else bundle.root.name

        return AgentManifest(
            agent=AgentInfo(
                name=agent_name,
                framework="mcp",
            ),
            tools=tools,
            instrumentation_hints=InstrumentationHints(
                mcp_servers=mcp_servers_raw,
            ),
            loader=LoaderMeta(detected_by="mcp", warnings=warnings),
        )
