from fastapi.testclient import TestClient

from app.main import app


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

    stream = client.post(
        "/v1/chat/stream",
        headers=headers("student-demo"),
        json={"project_id": "mouse-neuro-demo", "question": "tissue ice"},
    )
    assert stream.status_code == 200
    assert "event: citation" in stream.text


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
