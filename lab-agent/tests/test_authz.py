from app.core.authz import can_commit, can_execute_tool, can_read_document
from app.services.idempotency import IdempotencyGate


def test_cross_project_read_denied():
    user = {"id": "u1", "active": True, "roles": ["student"]}
    doc = {"project_id": "proj-b", "status": "effective", "acl": []}
    memberships = [{"user_id": "u1", "project_id": "proj-a"}]
    assert can_read_document(user, doc, memberships) is False


def test_inactive_user_denied():
    user = {"id": "u1", "active": False}
    doc = {"project_id": "proj-a", "status": "effective", "acl": []}
    memberships = [{"user_id": "u1", "project_id": "proj-a"}]
    assert can_read_document(user, doc, memberships) is False


def test_student_can_read_only_effective_document_in_own_project():
    user = {
        "id": "u1",
        "active": True,
        "roles": ["student"],
        "project_roles": {"proj-a": ["student"]},
    }
    doc = {"project_id": "proj-a", "status": "draft", "acl": []}
    memberships = [{"user_id": "u1", "project_id": "proj-a"}]
    assert can_read_document(user, doc, memberships) is False


def test_role_acl_requires_read_permission():
    user = {
        "id": "u1",
        "active": True,
        "project_roles": {"proj-b": ["research-assistant"]},
    }
    doc = {
        "project_id": "proj-b",
        "status": "draft",
        "acl": [
            {
                "principal_type": "role",
                "principal_id": "research-assistant",
                "permission": "write",
            }
        ],
    }
    assert can_read_document(user, doc, []) is False


def test_student_cannot_submit_reimbursement():
    user = {"id": "u1", "active": True, "roles": ["student"], "allowed_tools": {}}
    assert can_execute_tool(user, "submit_reimbursement", "proj-a") is False


def test_research_assistant_can_run_only_project_whitelist():
    user = {
        "id": "u1",
        "active": True,
        "project_roles": {"proj-a": ["research-assistant"]},
        "allowed_tools": {"proj-a": {"run_analysis_template"}},
    }
    assert can_execute_tool(user, "run_analysis_template", "proj-a") is True
    assert can_execute_tool(user, "submit_reimbursement", "proj-a") is False


def test_no_approval_cannot_commit():
    task = {"state": "pending_approval", "requester_id": "u1"}
    assert can_commit(task, []) is False


def test_self_approval_cannot_commit():
    task = {"state": "pending_approval", "requester_id": "u1"}
    approvals = [{"approver_id": "u1", "decision": "approved"}]
    assert can_commit(task, approvals) is False


def test_valid_approval_can_commit():
    task = {"state": "pending_approval", "requester_id": "u1"}
    approvals = [{"approver_id": "u2", "decision": "approved"}]
    assert can_commit(task, approvals) is True


def test_existing_external_submission_cannot_commit_twice():
    task = {
        "state": "pending_approval",
        "requester_id": "u1",
        "external_submission_exists": True,
    }
    approvals = [{"approver_id": "u2", "decision": "approved"}]
    assert can_commit(task, approvals) is False


def test_idempotency_key_is_claimed_only_once():
    gate = IdempotencyGate()
    calls: list[str] = []

    for _ in range(2):
        if gate.claim("reimbursement:task-1"):
            calls.append("external submit")

    assert calls == ["external submit"]
