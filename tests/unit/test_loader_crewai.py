from sentinex_core.manifest.loaders.base import UploadBundle
from sentinex_core.manifest.loaders.crewai import CrewAILoader

CREW_SOURCE = '''
from crewai import Agent, Task, Crew, Process
from crewai.tools import tool, BaseTool
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model_name="gpt-4o")


@tool
def search_web(query: str) -> str:
    """Search the web."""
    return ""


class RefundTool(BaseTool):
    name = "issue_refund"
    description = "Issue a Stripe refund."


researcher = Agent(role="Researcher", goal="find facts", allow_delegation=False, tools=[search_web])
writer = Agent(role="Writer", goal="write report", allow_delegation=True)
reviewer = Agent(role="Reviewer", goal="review report")

crew = Crew(agents=[researcher, writer, reviewer], tasks=[], process=Process.sequential)
'''

HIERARCHICAL_SOURCE = '''
from crewai import Agent, Crew, Process

a = Agent(role="Analyst")
b = Agent(role="Builder")
crew = Crew(agents=[a, b], process=Process.hierarchical)
'''


def _bundle(tmp_path, source):
    f = tmp_path / "crew.py"
    f.write_text(source)
    return UploadBundle(root=tmp_path, entry_file=f)


def test_detects_crewai(tmp_path):
    assert CrewAILoader().detect(_bundle(tmp_path, CREW_SOURCE))


def test_roster_and_sequential_handoffs(tmp_path):
    manifest = CrewAILoader().load(_bundle(tmp_path, CREW_SOURCE))
    graph = manifest.agents_graph
    assert graph is not None
    assert {n.id for n in graph.nodes} == {"researcher", "writer", "reviewer"}
    roles = {n.id: n.role for n in graph.nodes}
    assert roles["researcher"] == "Researcher"

    handoffs = {(e.from_, e.to) for e in graph.edges if e.kind == "handoff"}
    assert handoffs == {("researcher", "writer"), ("writer", "reviewer")}

    # writer has allow_delegation=True -> delegate edges to crew mates
    delegates = {(e.from_, e.to) for e in graph.edges if e.kind == "delegate"}
    assert delegates == {("writer", "researcher"), ("writer", "reviewer")}


def test_tools_extracted(tmp_path):
    manifest = CrewAILoader().load(_bundle(tmp_path, CREW_SOURCE))
    names = {t.name for t in manifest.tools}
    assert "search_web" in names      # @tool decorator + Agent(tools=[...])
    assert "issue_refund" in names    # BaseTool subclass with name attr


def test_models_extracted(tmp_path):
    manifest = CrewAILoader().load(_bundle(tmp_path, CREW_SOURCE))
    assert [(m.provider, m.model) for m in manifest.models] == [("openai", "gpt-4o")]


def test_no_stub_warning(tmp_path):
    manifest = CrewAILoader().load(_bundle(tmp_path, CREW_SOURCE))
    assert not any("stub" in w.lower() for w in manifest.loader.warnings)


def test_hierarchical_manager_delegation(tmp_path):
    manifest = CrewAILoader().load(_bundle(tmp_path, HIERARCHICAL_SOURCE))
    graph = manifest.agents_graph
    assert any(n.id == "manager" for n in graph.nodes)
    delegates = {(e.from_, e.to) for e in graph.edges if e.kind == "delegate"}
    assert delegates == {("manager", "a"), ("manager", "b")}
