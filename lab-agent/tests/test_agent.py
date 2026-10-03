from types import SimpleNamespace

from app.services.agent import AgentService
from app.services.knowledge import KnowledgeService
from app.services.local_archive import LocalArchive


def _archive(tmp_path):
    source = tmp_path / "sop.html"
    source.write_text("<title>SOP</title><p>Keep tissue on ice.</p>", encoding="utf-8")
    archive = LocalArchive(tmp_path / "archive")
    archive.ingest(source, "project-a", ["student", "research-assistant"], "internal")
    archive.grant_role("researcher", "project-a", "research-assistant")
    return archive


def _user():
    return {
        "id": "researcher",
        "active": True,
        "roles": ["research-assistant"],
        "project_roles": {"project-a": ["research-assistant"]},
        "allowed_tools": {"project-a": {"agent.propose_literature_query"}},
    }


def test_agent_falls_back_to_authorized_deterministic_retrieval(tmp_path, monkeypatch):
    monkeypatch.delenv("LAB_AGENT_OPENAI_MODEL", raising=False)
    result = AgentService(KnowledgeService(_archive(tmp_path))).run(
        _user(), "project-a", "tissue ice"
    )
    assert result.model_used is False
    assert result.citations
    assert result.executed_tools == ["search_authorized_evidence"]


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            call = SimpleNamespace(
                id="call-1",
                function=SimpleNamespace(name="search_authorized_evidence", arguments='{"query":"tissue ice"}'),
            )
            message = SimpleNamespace(tool_calls=[call], content=None)
            message.model_dump = lambda **_: {"role": "assistant", "tool_calls": []}
        else:
            message = SimpleNamespace(tool_calls=[], content="Keep the tissue on ice before fixation.")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_model_can_select_only_server_registered_evidence_tool(tmp_path):
    completions = _FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    result = AgentService(KnowledgeService(_archive(tmp_path)), model_client=client).run(
        _user(), "project-a", "How should tissue be stored?"
    )
    assert result.model_used is True
    assert result.executed_tools == ["search_authorized_evidence"]
    assert result.citations
    names = {tool["function"]["name"] for tool in completions.calls[0]["tools"]}
    assert names == {"search_authorized_evidence", "propose_literature_query"}
    assert "approve" not in names
