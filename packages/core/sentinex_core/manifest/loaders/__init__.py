from .base import AgentLoader
from .langchain import LangChainLoader
from .raw_python import RawPythonLoader
from .mcp import MCPLoader
from .crewai import CrewAILoader
from .autogen import AutoGenLoader
from .openai_assistants import OpenAIAssistantsLoader

# Detection order matters: the first loader whose detect() matches wins, and
# RawPythonLoader always matches, so it must stay last.
LOADERS: list[type[AgentLoader]] = [
    LangChainLoader,
    CrewAILoader,
    AutoGenLoader,
    OpenAIAssistantsLoader,
    MCPLoader,
    RawPythonLoader,
]
