from .langchain import LangChainLoader
from .raw_python import RawPythonLoader
from .mcp import MCPLoader
from .crewai import CrewAILoader
from .autogen import AutoGenLoader
from .openai_assistants import OpenAIAssistantsLoader

LOADERS = [LangChainLoader, CrewAILoader, AutoGenLoader, OpenAIAssistantsLoader, MCPLoader, RawPythonLoader]
