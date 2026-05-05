import pytest

pytest_plugins = ("anyio",)

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as requiring real DB/Redis")
    config.addinivalue_line("markers", "e2e: mark test as full end-to-end (requires Docker)")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def test_db_url():
    return "postgresql+asyncpg://sentinex:sentinex@localhost:5433/sentinex_test"


@pytest.fixture(scope="session")
def test_redis_url():
    return "redis://localhost:6380/1"


@pytest.fixture(scope="session")
def docker_client():
    try:
        import docker
        client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        client.ping()
        return client
    except Exception:
        pytest.skip("Docker not available")


@pytest.fixture
def sample_langchain_agent_dir(tmp_path):
    """Create a minimal LangChain agent bundle for testing."""
    agent_file = tmp_path / "agent.py"
    agent_file.write_text(
        """
from langchain.tools import tool
from langchain_openai import ChatOpenAI
import os

llm = ChatOpenAI(model="gpt-4o", api_key=os.environ.get("OPENAI_API_KEY"))

@tool
def search_customer(customer_id: int) -> dict:
    \"\"\"Look up a customer by ID.\"\"\"
    return {"id": customer_id, "name": "Test User"}

@tool
def send_email(to: str, subject: str, body: str) -> bool:
    \"\"\"Send an email to a customer.\"\"\"
    return True
"""
    )
    return tmp_path


@pytest.fixture
def sample_mcp_config(tmp_path):
    """Create a minimal MCP config bundle for testing."""
    import json
    config = {
        "mcpServers": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/work"],
            },
            "fetch": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-fetch"],
            },
        }
    }
    (tmp_path / "mcp.json").write_text(json.dumps(config, indent=2))
    return tmp_path
