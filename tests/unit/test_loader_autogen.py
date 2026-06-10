from sentinex_core.manifest.loaders.autogen import AutoGenLoader
from sentinex_core.manifest.loaders.base import UploadBundle

AUTOGEN_SOURCE = '''
import autogen
from autogen import AssistantAgent, UserProxyAgent, GroupChat, GroupChatManager, register_function

assistant = AssistantAgent(
    name="coder",
    llm_config={"config_list": [{"model": "gpt-4o", "api_key": "x"}]},
)
critic = AssistantAgent(name="critic", llm_config={"model": "claude-sonnet-4-5"})
user = UserProxyAgent(name="user")


def run_tests(path: str) -> str:
    return "ok"


register_function(run_tests, caller=assistant, executor=user)


@user.register_for_execution()
@assistant.register_for_llm(description="read a file")
def read_file(path: str) -> str:
    return ""


chat = GroupChat(agents=[assistant, critic, user], messages=[])
manager = GroupChatManager(groupchat=chat)

user.initiate_chat(manager, message="go")
'''


def _bundle(tmp_path, source=AUTOGEN_SOURCE):
    f = tmp_path / "team.py"
    f.write_text(source)
    return UploadBundle(root=tmp_path, entry_file=f)


def test_detects_autogen(tmp_path):
    assert AutoGenLoader().detect(_bundle(tmp_path))


def test_agent_roster(tmp_path):
    manifest = AutoGenLoader().load(_bundle(tmp_path))
    graph = manifest.agents_graph
    assert graph is not None
    ids = {n.id for n in graph.nodes}
    assert {"assistant", "critic", "user", "manager"} <= ids
    roles = {n.id: n.role for n in graph.nodes}
    assert roles["assistant"] == "coder"  # name= kwarg wins over class role


def test_handoff_and_broadcast_edges(tmp_path):
    manifest = AutoGenLoader().load(_bundle(tmp_path))
    edges = {(e.from_, e.to, e.kind) for e in manifest.agents_graph.edges}
    assert ("user", "manager", "handoff") in edges  # initiate_chat
    # manager broadcasts to every group chat member
    assert ("manager", "assistant", "broadcast") in edges
    assert ("manager", "critic", "broadcast") in edges
    assert ("manager", "user", "broadcast") in edges


def test_tools_extracted(tmp_path):
    manifest = AutoGenLoader().load(_bundle(tmp_path))
    names = {t.name for t in manifest.tools}
    assert "run_tests" in names  # register_function
    assert "read_file" in names  # register_for_llm decorator


def test_models_extracted(tmp_path):
    manifest = AutoGenLoader().load(_bundle(tmp_path))
    models = {(m.provider, m.model) for m in manifest.models}
    assert ("openai", "gpt-4o") in models
    assert ("anthropic", "claude-sonnet-4-5") in models


def test_no_stub_warning(tmp_path):
    manifest = AutoGenLoader().load(_bundle(tmp_path))
    assert not any("stub" in w.lower() for w in manifest.loader.warnings)
