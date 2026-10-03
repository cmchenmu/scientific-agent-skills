from fastapi.testclient import TestClient

from app.main import app
from app.services.literature_import import LiteratureImportResult


def headers(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def test_demo_session_query_and_sse(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_AGENT_DATA_PATH", str(tmp_path / "data"))
    client = TestClient(app)

    session = client.get("/v1/session", headers=headers("student-demo"))
    assert session.status_code == 200
    assert session.json()["default_project_id"] == "mouse-neuro-demo"

    response = client.post(
        "/v1/knowledge/query",
        headers=headers("student-demo"),
        json={"project_id": "mouse-neuro-demo", "question": "tissue ice"},
    )
    assert response.status_code == 200
    assert response.json()["citations"]

    chinese = client.post(
        "/v1/knowledge/query",
        headers=headers("student-demo"),
        json={"project_id": "mouse-neuro-demo", "question": "组织固定前应如何保存？"},
    )
    assert chinese.status_code == 200
    assert chinese.json()["status"] == "answered"

    stream = client.post(
        "/v1/chat/stream",
        headers=headers("student-demo"),
        json={"project_id": "mouse-neuro-demo", "question": "tissue ice"},
    )
    assert stream.status_code == 200
    assert "event: citation" in stream.text

    agent = client.post(
        "/v1/agent/run",
        headers=headers("research-demo"),
        json={
            "project_id": "mouse-neuro-demo",
            "request": "小鼠海马神经发生与阿尔茨海默病",
            "mode": "literature_query",
        },
    )
    assert agent.status_code == 200
    assert agent.json()["candidate_query"] == "Alzheimer neurogenesis hippocampus mouse"
    assert agent.json()["model_used"] is False


def test_reimbursement_preview_approval_submit_and_visibility(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_AGENT_DATA_PATH", str(tmp_path / "data"))
    client = TestClient(app)
    body = {
        "idempotency_key": "api-reimburse-1",
        "payload": {
            "project_id": "mouse-neuro-demo",
            "amount_cents": 1200,
            "currency": "CNY",
            "description": "taxi",
            "receipts": ["receipt-1"],
        },
    }
    created = client.post("/v1/actions/reimbursements", headers=headers("research-demo"), json=body)
    assert created.status_code == 200
    action_id = created.json()["action_id"]

    denied = client.post(
        f"/v1/actions/{action_id}/confirm",
        headers={**headers("research-demo"), "X-Idempotency-Key": "api-reimburse-1"},
    )
    assert denied.status_code == 403
    approved = client.post(
        f"/v1/actions/{action_id}/approve",
        headers=headers("finance-demo"),
        json={"decision": "approved"},
    )
    assert approved.status_code == 200
    committed = client.post(
        f"/v1/actions/{action_id}/confirm",
        headers={**headers("research-demo"), "X-Idempotency-Key": "api-reimburse-1"},
    )
    assert committed.status_code == 200
    assert committed.json()["external_id"].startswith("SIM-")
    assert client.get(f"/v1/tasks/{action_id}", headers=headers("student-demo")).status_code == 403


def test_library_summary_and_import_access_control(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_AGENT_DATA_PATH", str(tmp_path / "data"))
    client = TestClient(app)

    summary = client.get(
        "/v1/library/summary?project_id=mouse-neuro-demo",
        headers=headers("student-demo"),
    )
    assert summary.status_code == 200
    assert summary.json()["documents"] >= 1

    denied = client.post(
        "/v1/literature/import",
        headers=headers("student-demo"),
        json={"project_id": "mouse-neuro-demo", "query": "mouse brain", "limit": 1},
    )
    assert denied.status_code == 403

    monkeypatch.setattr(
        "app.main.import_open_access_literature",
        lambda *args, **kwargs: LiteratureImportResult(
            query="mouse brain", requested=1, downloaded=1, created=1, duplicates=0
        ),
    )
    imported = client.post(
        "/v1/literature/import",
        headers=headers("research-demo"),
        json={"project_id": "mouse-neuro-demo", "query": "mouse brain", "limit": 1},
    )
    assert imported.status_code == 200
    assert imported.json()["created"] == 1

    monkeypatch.setattr(
        "app.main.import_research_direction",
        lambda *args, **kwargs: LiteratureImportResult(
            query="Alzheimer hippocampus mouse",
            requested=50,
            downloaded=50,
            created=50,
            duplicates=0,
            direction="小鼠海马与阿尔茨海默病",
        ),
    )
    direction = client.post(
        "/v1/literature/direction-import",
        headers=headers("research-demo"),
        json={
            "project_id": "mouse-neuro-demo",
            "direction": "小鼠海马与阿尔茨海默病",
            "limit": 50,
        },
    )
    assert direction.status_code == 200
    assert direction.json()["query"] == "Alzheimer hippocampus mouse"


def test_catalog_and_selected_import_are_bounded_and_role_protected(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_AGENT_DATA_PATH", str(tmp_path / "data"))
    client = TestClient(app)
    monkeypatch.setattr("app.main.search_open_access_catalog", lambda query, cursor, sort: {"total": 101, "papers": [{"pmcid": "PMC1", "title": "Paper", "journal": None, "year": "2026", "authors": None, "abstract": "Abstract"}], "next_cursor_mark": "next"})
    catalog = client.post("/v1/literature/catalog", headers=headers("student-demo"), json={"query": "mouse brain"})
    assert catalog.status_code == 200
    assert catalog.json()["total"] == 101
    assert catalog.json()["papers"][0]["abstract"] == "Abstract"
    denied = client.post("/v1/literature/selected-import", headers=headers("student-demo"), json={"project_id": "mouse-neuro-demo", "pmcids": ["PMC1"]})
    assert denied.status_code == 403
    selected = client.post("/v1/literature/selected-import", headers=headers("research-demo"), json={"project_id": "mouse-neuro-demo", "pmcids": [f"PMC{number}" for number in range(51)]})
    assert selected.status_code == 422


def test_library_documents_download_and_delete_access(monkeypatch, tmp_path):
    monkeypatch.setenv("LAB_AGENT_DATA_PATH", str(tmp_path / "data"))
    client = TestClient(app)
    documents = client.get("/v1/library/documents?project_id=mouse-neuro-demo", headers=headers("student-demo"))
    assert documents.status_code == 200
    document_id = documents.json()[0]["document_id"]
    original = client.get(f"/v1/library/documents/{document_id}/original?project_id=mouse-neuro-demo", headers=headers("student-demo"))
    assert original.status_code == 200
    assert original.content
    denied = client.delete(f"/v1/library/documents/{document_id}?project_id=mouse-neuro-demo", headers=headers("student-demo"))
    assert denied.status_code == 403
    deleted = client.delete(f"/v1/library/documents/{document_id}?project_id=mouse-neuro-demo", headers=headers("research-demo"))
    assert deleted.status_code == 200
