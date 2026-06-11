import json

from sentinex_core.manifest.loaders.base import UploadBundle
from sentinex_core.manifest.loaders.mcp import MCPLoader

CONFIG = {
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/work"],
        },
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "ghp_supersecrettoken123456789"},
        },
    }
}


def _bundle(tmp_path):
    (tmp_path / "mcp.json").write_text(json.dumps(CONFIG))
    return UploadBundle(root=tmp_path)


def test_detects_mcp(tmp_path):
    assert MCPLoader().detect(_bundle(tmp_path))


def test_tools_extracted(tmp_path):
    manifest = MCPLoader().load(_bundle(tmp_path))
    names = {t.name for t in manifest.tools}
    assert names == {"filesystem", "github"}


def test_env_secret_values_are_redacted(tmp_path):
    manifest = MCPLoader().load(_bundle(tmp_path))
    blob = json.dumps(manifest.model_dump(mode="json"))
    # The secret value must never be persisted; the key name may remain.
    assert "ghp_supersecrettoken123456789" not in blob
    assert "GITHUB_TOKEN" in blob
    assert "***redacted***" in blob
