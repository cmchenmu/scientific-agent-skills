"""服务端授权策略。

这些函数刻意只接收普通映射，以便 HTTP 层在加载 ORM 数据后调用，并能
独立地进行单元测试。调用方不得把客户端传入的 ``roles`` 或
``allowed_tools`` 直接传给这些函数；它们必须由服务端的角色和策略记录构造。
"""

from __future__ import annotations

from collections.abc import Iterable

READ_PERMISSIONS = {"read", "admin"}


def _project_roles(user: dict, project_id: str) -> set[str]:
    """取得用户在项目内的角色，管理员角色可跨项目生效。"""
    roles = set(user.get("roles", []))
    if "admin" in roles:
        return roles

    project_roles = user.get("project_roles", {})
    return set(project_roles.get(project_id, []))


def can_read_document(user: dict, document: dict, memberships: Iterable[dict]) -> bool:
    """用户能否读取某文档。

    规则（示例）：
    - 用户必须 active；
    - 管理员可读取全部文档；
    - PI 可读取其项目的任意状态文档；
    - student 和 research-assistant 只能读取其项目的 effective 文档；
    - 显式 ACL 仅在 permission 为 read 或 admin 时授予访问。
    """
    if not user.get("active"):
        return False

    if "admin" in user.get("roles", []):
        return True

    project_id = document.get("project_id")
    project_ids = {
        membership.get("project_id")
        for membership in memberships
        if membership.get("user_id") == user.get("id")
    }
    roles = _project_roles(user, project_id)
    if project_id in project_ids and (
        "pi" in roles or document.get("status") == "effective"
    ):
        return True

    for acl in document.get("acl", []):
        if acl.get("permission") not in READ_PERMISSIONS:
            continue
        if acl.get("principal_type") == "user" and acl.get("principal_id") == user.get(
            "id"
        ):
            return True
        if acl.get("principal_type") == "role" and acl.get("principal_id") in roles:
            return True
    return False


def can_execute_tool(user: dict, tool_name: str, project_id: str) -> bool:
    """用户能否对某项目执行一个已在服务端允许的工具模板。"""
    if not user.get("active"):
        return False

    if "admin" in user.get("roles", []):
        return True

    roles = _project_roles(user, project_id)
    if not roles.intersection({"research-assistant", "pi"}):
        return False

    allowed = user.get("allowed_tools", {})
    return tool_name in allowed.get(project_id, set())


def can_commit(task: dict, approvals: Iterable[dict]) -> bool:
    """任务能否提交（外部写入）。

    必须处于待审批状态、从未提交外部操作，且存在足够多的非申请人审批。
    任一拒绝会阻止提交。
    """
    if task.get("state") != "pending_approval" or task.get(
        "external_submission_exists"
    ):
        return False

    approval_list = list(approvals)
    if any(approval.get("decision") == "rejected" for approval in approval_list):
        return False

    approvers = {
        approval.get("approver_id")
        for approval in approval_list
        if approval.get("decision") == "approved"
        and approval.get("approver_id") != task.get("requester_id")
    }
    required_count = task.get("required_approval_count", 1)
    return len(approvers) >= required_count
