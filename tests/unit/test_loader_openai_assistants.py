import json

from sentinex_core.manifest.loaders.base import UploadBundle
from sentinex_core.manifest.loaders.openai_assistants import OpenAIAssistantsLoader

ASSISTANT_CONFIG = {
    "id": "asst_abc123",
    "name": "Support Bot",
    "model": "gpt-4o",
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "lookup_order",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                },
            },
        },
        {"type": "code_interpreter"},
    ],
}


def test_detect_via_metadata(tmp_path):
    bundle = UploadBundle(root=tmp_path, metadata={"assistant_id": "asst_xyz"})
    assert OpenAIAssistantsLoader().detect(bundle)


def test_detect_via_config_file(tmp_path):
    (tmp_path / "assistant.json").write_text(json.dumps(ASSISTANT_CONFIG))
    assert OpenAIAssistantsLoader().detect(UploadBundle(root=tmp_path))


def test_detect_rejects_other_json(tmp_path):
    (tmp_path / "assistant.json").write_text(json.dumps({"id": "not-an-assistant"}))
    assert not OpenAIAssistantsLoader().detect(UploadBundle(root=tmp_path))


def test_load_extracts_model_and_tools(tmp_path):
    (tmp_path / "assistant.json").write_text(json.dumps(ASSISTANT_CONFIG))
    manifest = OpenAIAssistantsLoader().load(UploadBundle(root=tmp_path))

    assert manifest.agent.name == "Support Bot"
    assert manifest.agent.framework == "openai_assistants"
    assert [(m.provider, m.model) for m in manifest.models] == [("openai", "gpt-4o")]

    by_name = {t.name: t for t in manifest.tools}
    assert "lookup_order" in by_name
    assert by_name["lookup_order"].args_schema["properties"]["order_id"]
    assert "code_interpreter" in by_name
    assert manifest.secrets_required == ["OPENAI_API_KEY"]


def test_load_metadata_only(tmp_path):
    bundle = UploadBundle(
        root=tmp_path, metadata={"assistant_id": "asst_xyz", "name": "Meta Bot"}
    )
    manifest = OpenAIAssistantsLoader().load(bundle)
    assert manifest.agent.name == "Meta Bot"
    assert manifest.loader.warnings  # no config file -> runtime-only warning
