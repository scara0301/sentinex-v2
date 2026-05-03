from __future__ import annotations
import uuid
from typing import Literal, Optional, Any
from pydantic import BaseModel, Field

FrameworkType = Literal["langchain", "crewai", "autogen", "openai_assistants", "mcp", "raw_python"]
SideEffect = Literal["network", "db:read", "db:write", "fs:read", "fs:write", "email", "sms", "payment"]
AuthScope = Literal["admin", "user", "public"]
EntrypointKind = Literal["python_callable", "http", "cli", "mcp_stdio"]
MemoryKind = Literal["buffer", "vector", "kv"]


class EntrypointConfig(BaseModel):
    kind: EntrypointKind
    module: Optional[str] = None
    callable: Optional[str] = None
    args_schema: Optional[dict[str, Any]] = None


class ModelConfig(BaseModel):
    provider: str
    model: str
    endpoint_env: Optional[str] = None


class ToolDefinition(BaseModel):
    name: str
    source: str
    args_schema: dict[str, Any] = Field(default_factory=dict)
    returns_schema: dict[str, Any] = Field(default_factory=dict)
    side_effects: list[SideEffect] = Field(default_factory=list)
    auth_scope: AuthScope = "user"


class MemoryConfig(BaseModel):
    kind: MemoryKind
    backend: str
    shared_with: list[str] = Field(default_factory=list)


class GraphNode(BaseModel):
    id: str
    role: str


class GraphEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    kind: str  # handoff | delegate | broadcast


class AgentGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class NetworkPolicy(BaseModel):
    allow_hosts: list[str] = Field(default_factory=list)
    deny_hosts: list[str] = Field(default_factory=lambda: ["*"])


class InstrumentationHints(BaseModel):
    http_proxy_compatible: bool = True
    openai_base_url_overridable: bool = True
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)


class LoaderMeta(BaseModel):
    detected_by: FrameworkType
    warnings: list[str] = Field(default_factory=list)


class AgentInfo(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    name: str
    framework: FrameworkType
    framework_version: Optional[str] = None
    entrypoint: Optional[EntrypointConfig] = None


class AgentManifest(BaseModel):
    manifest_version: int = 1
    agent: AgentInfo
    models: list[ModelConfig] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    memory: list[MemoryConfig] = Field(default_factory=list)
    agents_graph: Optional[AgentGraph] = None
    secrets_required: list[str] = Field(default_factory=list)
    network_policy: NetworkPolicy = Field(default_factory=NetworkPolicy)
    instrumentation_hints: InstrumentationHints = Field(default_factory=InstrumentationHints)
    loader: Optional[LoaderMeta] = None
