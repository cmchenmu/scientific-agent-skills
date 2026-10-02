from pathlib import Path

from app.services.governance import AuditEvent, GovernanceStore, redact_payload


def event() -> AuditEvent:
    return AuditEvent(
        request_id="req-1",
        trace_id="trace-1",
        actor_id="u1",
        roles=("student",),
        project_id="proj-a",
        action="knowledge.query",
        tool_name="search",
        tool_version="1.0",
        params_digest="digest",
        retrieval_document_ids=("doc-1",),
        approval_decision=None,
        external_request_id=None,
        latency_ms=12,
        estimated_cost_cents=1,
        error_code=None,
        final_state="completed",
    )


def test_redaction_removes_tokens_receipts_and_full_text():
    result = redact_payload(
        {"access_token": "secret", "receipts": ["private.pdf"], "content": "long text"}
    )

    assert result["access_token"] == "[REDACTED]"
    assert result["receipts"] == "[REDACTED]"
    assert result["content"]["sha256"]
    assert "long text" not in str(result)


def test_audit_stores_digest_and_redacted_payload(tmp_path: Path):
    store = GovernanceStore(tmp_path)
    store.record_event(event(), {"access_token": "secret", "content": "private"})
    row = store.audit_events(request_id="req-1")[0]

    assert row["params_digest"] == "digest"
    assert "secret" not in row["payload_redacted"]
    assert "private" not in row["payload_redacted"]
    assert row["retrieval_document_ids"] == '["doc-1"]'


def test_budget_stops_permanently_and_cache_is_acl_scoped(tmp_path: Path):
    store = GovernanceStore(tmp_path)
    store.create_budget("task-1", max_steps=1, max_tokens=5, max_cost_cents=2)
    assert store.consume_budget("task-1", tokens=5, cost_cents=2)
    assert not store.consume_budget("task-1")
    assert not store.consume_budget("task-1")
    store.put_cache(
        "key",
        user_id="u1",
        project_id="proj-a",
        roles=["student"],
        value={"answer": "ok"},
        ttl_seconds=60,
    )
    assert store.get_cache(
        "key", user_id="u1", project_id="proj-a", roles=["student"]
    ) == {"answer": "ok"}
    assert (
        store.get_cache("key", user_id="u2", project_id="proj-a", roles=["student"])
        is None
    )
    assert (
        store.get_cache("key", user_id="u1", project_id="proj-b", roles=["student"])
        is None
    )
    assert (
        store.get_cache("key", user_id="u1", project_id="proj-a", roles=["pi"]) is None
    )
