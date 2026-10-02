"""Safe, local administrative adapter used to teach preview/approve/commit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from app.core.authz import can_execute_tool
from app.services.release_gate import validate_approval_commit, validate_tool_arguments


class AdapterError(ValueError):
    """Expected business or authorization failure in an administrative adapter."""


@dataclass(frozen=True)
class Preview:
    request_type: str
    draft_hash: str
    normalized_payload: dict[str, Any]
    impact: dict[str, Any]


class LocalAdminAdapter:
    """A SQLite simulation of a low-risk reimbursement draft workflow.

    No real finance API is called. The ``external_requests`` table is the
    deterministic stand-in used to test authorization, approval and retries.
    """

    POLICY: ClassVar[dict[str, Any]] = {
        "request_type": "reimbursement",
        "currency": "CNY",
        "max_amount_cents": 500_000,
        "required_fields": [
            "project_id",
            "amount_cents",
            "currency",
            "description",
            "receipts",
        ],
        "required_approver_role": "finance-approver",
    }

    def __init__(self, root: Path) -> None:
        self.root = root
        self.database_path = root / "admin_adapter.sqlite3"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_tasks (
                    id TEXT PRIMARY KEY, requester_id TEXT NOT NULL, request_type TEXT NOT NULL,
                    draft_hash TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS admin_approvals (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, approver_id TEXT NOT NULL,
                    decision TEXT NOT NULL, draft_hash TEXT NOT NULL, expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS external_requests (
                    external_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
                    payload_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS adapter_audit (
                    id TEXT PRIMARY KEY, action TEXT NOT NULL, actor_id TEXT,
                    task_id TEXT, payload_redacted TEXT NOT NULL
                );
                """
            )

    def get_policy(self, user: dict[str, Any], request_type: str) -> dict[str, Any]:
        self._authorize(user, "admin.get_policy", "")
        if request_type != self.POLICY["request_type"]:
            raise AdapterError("unsupported request type")
        return dict(self.POLICY)

    def validate_draft(
        self, user: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._authorize(
            user, "admin.validate_draft", str(payload.get("project_id", ""))
        )
        validate_tool_arguments(payload)
        if set(payload) != set(self.POLICY["required_fields"]):
            raise AdapterError("payload fields do not match the reimbursement policy")
        if not isinstance(payload["amount_cents"], int) or payload["amount_cents"] <= 0:
            raise AdapterError("amount_cents must be a positive integer")
        if payload["amount_cents"] > self.POLICY["max_amount_cents"]:
            raise AdapterError("amount exceeds policy threshold")
        if payload["currency"] != self.POLICY["currency"]:
            raise AdapterError("currency is not allowed by policy")
        if (
            not isinstance(payload["description"], str)
            or not payload["description"].strip()
        ):
            raise AdapterError("description is required")
        if not isinstance(payload["receipts"], list) or not payload["receipts"]:
            raise AdapterError("at least one receipt reference is required")
        if any(
            not isinstance(receipt, str) or not receipt.strip()
            for receipt in payload["receipts"]
        ):
            raise AdapterError("receipt references must be non-empty strings")
        return {
            "valid": True,
            "request_type": "reimbursement",
            "field_count": len(payload),
        }

    def preview_submission(
        self, user: dict[str, Any], payload: dict[str, Any]
    ) -> Preview:
        self.validate_draft(user, payload)
        normalized = {
            **payload,
            "description": payload["description"].strip(),
            "receipts": sorted(set(payload["receipts"])),
        }
        draft_hash = self._digest(normalized)
        return Preview(
            request_type="reimbursement",
            draft_hash=draft_hash,
            normalized_payload=normalized,
            impact={
                "amount_cents": normalized["amount_cents"],
                "currency": normalized["currency"],
                "external_write": True,
            },
        )

    def create_approval_task(
        self, user: dict[str, Any], preview: Preview, idempotency_key: str
    ) -> str:
        self._authorize(
            user, "admin.create_task", str(preview.normalized_payload["project_id"])
        )
        if not idempotency_key:
            raise AdapterError("idempotency_key is required")
        task_id = str(uuid.uuid4())
        self.initialize()
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO admin_tasks VALUES (?, ?, 'reimbursement', ?, ?, 'pending_approval', ?)",
                    (
                        task_id,
                        user["id"],
                        preview.draft_hash,
                        json.dumps(preview.normalized_payload),
                        idempotency_key,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise AdapterError("idempotency key already has a task") from error
            self._audit(
                connection,
                "preview.created",
                user["id"],
                task_id,
                {"draft_hash": preview.draft_hash},
            )
        return task_id

    def record_approval(
        self,
        approver: dict[str, Any],
        task_id: str,
        draft_hash: str,
        decision: str,
        expires_at: str | None = None,
    ) -> None:
        if "finance-approver" not in approver.get(
            "roles", []
        ) and "admin" not in approver.get("roles", []):
            raise AdapterError("approver lacks finance-approver role")
        if decision not in {"approved", "rejected"}:
            raise AdapterError("decision must be approved or rejected")
        self.initialize()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT draft_hash FROM admin_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task or task["draft_hash"] != draft_hash:
                raise AdapterError("task or draft hash is invalid")
            connection.execute(
                "INSERT INTO admin_approvals VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    task_id,
                    approver["id"],
                    decision,
                    draft_hash,
                    expires_at,
                ),
            )
            self._audit(
                connection,
                "approval.recorded",
                approver["id"],
                task_id,
                {"decision": decision},
            )

    def submit_approved_request(
        self, user: dict[str, Any], task_id: str, idempotency_key: str
    ) -> str:
        self.initialize()
        with self._connect() as connection:
            task = connection.execute(
                "SELECT * FROM admin_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if not task or task["idempotency_key"] != idempotency_key:
                raise AdapterError("task or idempotency key is invalid")
            existing = connection.execute(
                "SELECT external_id FROM external_requests WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return existing["external_id"]
            payload = json.loads(task["payload"])
            self._authorize(user, "admin.submit_reimbursement", payload["project_id"])
            approvals = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM admin_approvals WHERE task_id = ?", (task_id,)
                )
            ]
            if not validate_approval_commit(
                dict(task), approvals, draft_hash=task["draft_hash"]
            ):
                raise AdapterError("valid approval for the exact draft is required")
            external_id = f"SIM-{uuid.uuid4()}"
            connection.execute(
                "INSERT INTO external_requests VALUES (?, ?, ?, 'submitted', ?)",
                (external_id, task_id, idempotency_key, self._digest(payload)),
            )
            connection.execute(
                "UPDATE admin_tasks SET state = 'submitted' WHERE id = ?", (task_id,)
            )
            self._audit(
                connection,
                "submission.committed",
                user["id"],
                task_id,
                {"external_id": external_id},
            )
            return external_id

    def get_request_status(
        self, user: dict[str, Any], external_request_id: str
    ) -> dict[str, str]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT external_id, task_id, status FROM external_requests WHERE external_id = ?",
                (external_request_id,),
            ).fetchone()
            if not row:
                raise AdapterError("request not found")
            task = connection.execute(
                "SELECT requester_id, payload FROM admin_tasks WHERE id = ?",
                (row["task_id"],),
            ).fetchone()
            payload = json.loads(task["payload"])
            if user["id"] != task["requester_id"] and "admin" not in user.get(
                "roles", []
            ):
                raise AdapterError("request is not visible to this user")
            return {
                "external_id": row["external_id"],
                "task_id": row["task_id"],
                "status": row["status"],
                "project_id": payload["project_id"],
            }

    def list_tasks(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        """List only a requester's tasks, unless the caller is an administrator."""
        self.initialize()
        with self._connect() as connection:
            if "admin" in user.get("roles", []):
                rows = connection.execute(
                    "SELECT id, requester_id, state, request_type FROM admin_tasks ORDER BY rowid DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT id, requester_id, state, request_type FROM admin_tasks
                       WHERE requester_id = ? ORDER BY rowid DESC""",
                    (user["id"],),
                ).fetchall()
        return [dict(row) for row in rows]

    def task(self, user: dict[str, Any], task_id: str) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, requester_id, state, request_type, payload FROM admin_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if not row or (
            row["requester_id"] != user["id"] and "admin" not in user.get("roles", [])
        ):
            raise AdapterError("task is not visible to this user")
        task = dict(row)
        task["payload"] = json.loads(task["payload"])
        return task

    def cancel_task(self, user: dict[str, Any], task_id: str) -> dict[str, str]:
        task = self.task(user, task_id)
        if task["state"] != "pending_approval":
            raise AdapterError("only pending approval tasks can be cancelled")
        with self._connect() as connection:
            connection.execute("UPDATE admin_tasks SET state = 'cancelled' WHERE id = ?", (task_id,))
            self._audit(connection, "task.cancelled", user["id"], task_id, {})
        return {"id": task_id, "state": "cancelled"}

    def _authorize(self, user: dict[str, Any], tool_name: str, project_id: str) -> None:
        if not can_execute_tool(user, tool_name, project_id):
            raise AdapterError("user is not authorized for this adapter operation")

    @staticmethod
    def _digest(value: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        action: str,
        actor_id: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO adapter_audit VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), action, actor_id, task_id, json.dumps(payload)),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
