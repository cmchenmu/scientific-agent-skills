from pathlib import Path

import pytest

from app.services.admin_adapter import AdapterError, LocalAdminAdapter


def user(user_id: str, roles: list[str]) -> dict:
    return {
        "id": user_id,
        "active": True,
        "roles": roles,
        "project_roles": {"proj-a": roles},
        "allowed_tools": {
            "proj-a": {
                "admin.validate_draft",
                "admin.get_policy",
                "admin.create_task",
                "admin.submit_reimbursement",
            }
        },
    }


def payload() -> dict:
    return {
        "project_id": "proj-a",
        "amount_cents": 1200,
        "currency": "CNY",
        "description": "taxi",
        "receipts": ["receipt-1"],
    }


def test_preview_approval_submit_and_status_are_idempotent(tmp_path: Path):
    adapter = LocalAdminAdapter(tmp_path)
    requester = user("student", ["research-assistant"])
    approver = user("finance", ["finance-approver"])
    preview = adapter.preview_submission(requester, payload())
    task_id = adapter.create_approval_task(requester, preview, "reimburse-1")
    adapter.record_approval(approver, task_id, preview.draft_hash, "approved")

    external_id = adapter.submit_approved_request(requester, task_id, "reimburse-1")
    assert (
        adapter.submit_approved_request(requester, task_id, "reimburse-1")
        == external_id
    )
    assert adapter.get_request_status(requester, external_id)["status"] == "submitted"


def test_invalid_amount_and_self_approval_are_rejected(tmp_path: Path):
    adapter = LocalAdminAdapter(tmp_path)
    requester = user("student", ["research-assistant"])
    bad = {**payload(), "amount_cents": 999_999}
    with pytest.raises(AdapterError, match="threshold"):
        adapter.validate_draft(requester, bad)
    preview = adapter.preview_submission(requester, payload())
    task_id = adapter.create_approval_task(requester, preview, "reimburse-2")
    with pytest.raises(AdapterError, match="approver"):
        adapter.record_approval(requester, task_id, preview.draft_hash, "approved")


def test_changed_draft_cannot_use_old_approval(tmp_path: Path):
    adapter = LocalAdminAdapter(tmp_path)
    requester = user("student", ["research-assistant"])
    approver = user("finance", ["finance-approver"])
    first = adapter.preview_submission(requester, payload())
    task_id = adapter.create_approval_task(requester, first, "reimburse-3")
    adapter.record_approval(approver, task_id, first.draft_hash, "approved")
    with pytest.raises(AdapterError, match="task or idempotency"):
        adapter.submit_approved_request(requester, task_id, "reimburse-3-changed")
