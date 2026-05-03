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
    """Extract a string literal value from an AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_docstring(func_node: ast.FunctionDef) -> Optional[str]:
    """Extract docstring from a function definition."""
    if (
        func_node.body
        and isinstance(func_node.body[0], ast.Expr)
        and isinstance(func_node.body[0].value, ast.Constant)
        and isinstance(func_node.body[0].value.value, str)
    ):
        return func_node.body[0].value.value
    return None


def _get_class_attr_str(class_node: ast.ClassDef, attr: str) -> Optional[str]:
    """Extract a string class-level attribute assignment from a ClassDef node."""
    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == attr:
                    return _get_string_value(stmt.value)
        if isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.target.id == attr and stmt.value:
                return _get_string_value(stmt.value)
    return None


def _extract_kwarg(call: ast.Call, key: str) -> Optional[str]:
    """Extract a keyword argument string value from a Call node."""
    for kw in call.keywords:
        if kw.arg == key:
            return _get_string_value(kw.value)
    return None


def _extract_secrets_from_call(call: ast.Call) -> list[str]:
    """Extract env var name from os.environ['X'] or os.getenv('X') style call/subscript."""
    secrets: list[str] = []
    # os.getenv("KEY") or os.environ.get("KEY")
    if isinstance(call.func, ast.Attribute):
        if call.func.attr in ("getenv", "get") and call.args:
            val = _get_string_value(call.args[0])
            if val:
                secrets.append(val)
    return secrets


class LangChainLoader(AgentLoader):
    framework = "langchain"

    def detect(self, bundle: UploadBundle) -> bool:
        for py_file in _collect_py_files(bundle):
            try:
                source = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = ""
                    if isinstance(node, ast.ImportFrom) and node.module:
                        module = node.module
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name.startswith("langchain"):
                                return True
                    if module.startswith("langchain"):
                        return True
        return False

    def load(self, bundle: UploadBundle) -> AgentManifest:
        tools: list[ToolDefinition] = []
        models: list[ModelConfig] = []
        secrets: list[str] = []
        warnings: list[str] = []

        # Map of LLM class names to (provider, model_kwarg)
        llm_classes = {
            "ChatOpenAI": ("openai", "model_name"),
            "OpenAI": ("openai", "model_name"),
            "ChatAnthropic": ("anthropic", "model"),
            "ChatGoogleGenerativeAI": ("google", "model"),
            "ChatCohere": ("cohere", "model"),
            "AzureChatOpenAI": ("azure_openai", "deployment_name"),
        }

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
                # @tool decorated functions
                if isinstance(node, ast.FunctionDef):
                    for decorator in node.decorator_list:
                        decorator_name = None
                        if isinstance(decorator, ast.Name):
                            decorator_name = decorator.id
                        elif isinstance(decorator, ast.Attribute):
                            decorator_name = decorator.attr
                        elif isinstance(decorator, ast.Call):
                            if isinstance(decorator.func, ast.Name):
                                decorator_name = decorator.func.id
                            elif isinstance(decorator.func, ast.Attribute):
                                decorator_name = decorator.func.attr

                        if decorator_name == "tool" and node.name not in seen_tool_names:
                            seen_tool_names.add(node.name)
                            description = _get_docstring(node) or ""
                            tools.append(
                                ToolDefinition(
                                    name=node.name,
                                    source="langchain.tool",
                                    args_schema={"description": description} if description else {},
                                )
                            )

                # BaseTool subclasses
                if isinstance(node, ast.ClassDef):
                    base_names = []
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            base_names.append(base.id)
                        elif isinstance(base, ast.Attribute):
                            base_names.append(base.attr)

                    if "BaseTool" in base_names or "StructuredTool" in base_names:
                        cls_name = _get_class_attr_str(node, "name") or node.name
                        if cls_name not in seen_tool_names:
                            seen_tool_names.add(cls_name)
                            description = _get_class_attr_str(node, "description") or ""
                            tools.append(
                                ToolDefinition(
                                    name=cls_name,
                                    source=f"langchain.BaseTool:{node.name}",
                                    args_schema={"description": description} if description else {},
                                )
                            )

                # LLM instantiations
                if isinstance(node, ast.Call):
                    func_name = None
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    if func_name in llm_classes:
                        provider, model_kwarg = llm_classes[func_name]
                        model_name = _extract_kwarg(node, model_kwarg) or _extract_kwarg(node, "model") or "unknown"
                        key = (provider, model_name)
                        if key not in seen_model_keys:
                            seen_model_keys.add(key)
                            models.append(ModelConfig(provider=provider, model=model_name))

                    # os.getenv / os.environ.get secret extraction
                    extracted = _extract_secrets_from_call(node)
                    for s in extracted:
                        if s not in secrets:
                            secrets.append(s)

                # os.environ["KEY"] subscript access
                if isinstance(node, ast.Subscript):
                    if isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
                        key_node = node.slice
                        # Python 3.9+ slice is direct; older wraps in ast.Index
                        val = _get_string_value(key_node)
                        if val and val not in secrets:
                            secrets.append(val)

        if not tools and not models:
            warnings.append("No tools or LLM configs detected — file may use dynamic construction")

        agent_name = bundle.entry_file.stem if bundle.entry_file else bundle.root.name

        return AgentManifest(
            agent=AgentInfo(
                name=agent_name,
                framework="langchain",
            ),
            tools=tools,
            models=models,
            secrets_required=secrets,
            loader=LoaderMeta(detected_by="langchain", warnings=warnings),
        )
