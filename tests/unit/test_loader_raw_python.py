"""Regression coverage for the raw-Python loader after the dead-branch removal."""

from sentinex_core.manifest.loaders.base import UploadBundle
from sentinex_core.manifest.loaders.raw_python import RawPythonLoader

SOURCE = '''
import requests
from openai import OpenAI

client = OpenAI(model="gpt-4o")


def run_tool(url: str) -> str:
    resp = requests.get(url)
    return resp.text


def write_handler(path: str) -> None:
    with open(path, "w") as fh:
        fh.write("done")
'''


def _bundle(tmp_path):
    f = tmp_path / "agent.py"
    f.write_text(SOURCE)
    return UploadBundle(root=tmp_path, entry_file=f)


def test_detects_any_python(tmp_path):
    assert RawPythonLoader().detect(_bundle(tmp_path))


def test_tool_side_effects_still_extracted(tmp_path):
    manifest = RawPythonLoader().load(_bundle(tmp_path))
    tools = {t.name: t for t in manifest.tools}
    assert "run_tool" in tools
    assert "network" in tools["run_tool"].side_effects
    assert "write_handler" in tools
    assert "fs:write" in tools["write_handler"].side_effects


def test_llm_client_detected(tmp_path):
    manifest = RawPythonLoader().load(_bundle(tmp_path))
    assert ("openai", "gpt-4o") in {(m.provider, m.model) for m in manifest.models}
