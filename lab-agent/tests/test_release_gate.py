from datetime import UTC, datetime, timedelta

import pytest

from app.services.release_gate import (
    BudgetController,
    CheckpointStore,
    UnsafeToolInput,
    validate_approval_commit,
    validate_tool_arguments,
)


@pytest.mark.parametrize(
    "value",
    [
        {"path": "../../other-project/sop.pdf"},
        {"query": "DROP TABLE documents"},
        {"command": "safe; rm -rf /"},
        {"path": "$(whoami)"},
    ],
)
def test_tool_arguments_reject_red_team_fragments(value):
    with pytest.raises(UnsafeToolInput):
        validate_tool_arguments(value)


def test_tool_arguments_reject_oversized_input():
    with pytest.raises(UnsafeToolInput, match="size"):
        validate_tool_arguments({"question": "a" * 17_000})


def test_approval_must_be_current_non_self_and_match_draft():
    now = datetime.now(UTC)
    task = {"state": "pending_approval", "requester_id": "student", "draft_hash": "v1"}
    approved = [
        {
            "approver_id": "pi",
            "decision": "approved",
            "draft_hash": "v1",
            "expires_at": now + timedelta(hours=1),
        }
    ]

    assert validate_approval_commit(task, approved, draft_hash="v1", now=now)
    assert not validate_approval_commit(task, approved, draft_hash="changed", now=now)
    assert not validate_approval_commit(
        task,
        [{"approver_id": "student", "decision": "approved"}],
        draft_hash="v1",
        now=now,
    )
    assert not validate_approval_commit(
        task,
        [{"approver_id": "pi", "decision": "approved", "expires_at": now}],
        draft_hash="v1",
        now=now,
    )


def test_budget_exhaustion_stops_future_calls():
    controller = BudgetController(
        max_steps=2, max_tokens=10, max_cost_cents=5, deadline_epoch=4_000_000_000
    )
    assert controller.consume(tokens=4, cost_cents=2)
    assert not controller.consume(tokens=7, cost_cents=1)
    assert controller.stopped_reason == "max_tokens"
    assert not controller.consume()


def test_checkpoint_resume_is_last_durable_state():
    store = CheckpointStore()
    store.save("workflow-1", {"state": "retrieve", "attempt": 1})
    store.save("workflow-1", {"state": "ingest", "attempt": 2})

    assert store.resume("workflow-1") == {"state": "ingest", "attempt": 2}
