"""Local audit, privacy, cache-ACL and cost controls for Chapter 12."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "receipt",
    "receipts",
    "token",
}
TEXT_KEYS = {"text", "content", "full_text", "document_text", "raw_payload"}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def redact_payload(value: Any, *, max_text: int = 160) -> Any:
    """Recursively remove secrets and avoid retaining document/financial content."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in SENSITIVE_KEYS or normalized.endswith("_token"):
                result[str(key)] = "[REDACTED]"
            elif normalized in TEXT_KEYS:
                result[str(key)] = {"sha256": _digest(child), "length": len(str(child))}
            else:
                result[str(key)] = redact_payload(child, max_text=max_text)
        return result
    if isinstance(value, list):
        return [redact_payload(item, max_text=max_text) for item in value]
    if isinstance(value, str) and len(value) > max_text:
        return {"sha256": _digest(value), "length": len(value)}
    return value


@dataclass(frozen=True)
class AuditEvent:
    request_id: str
    trace_id: str
    actor_id: str | None
    roles: tuple[str, ...]
    project_id: str | None
    action: str
    tool_name: str | None
    tool_version: str | None
    params_digest: str
    retrieval_document_ids: tuple[str, ...]
    approval_decision: str | None
    external_request_id: str | None
    latency_ms: int | None
    estimated_cost_cents: int | None
    error_code: str | None
    final_state: str


class GovernanceStore:
    """SQLite governance store; production can map the same contract to PostgreSQL."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.database_path = root / "governance.sqlite3"

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY, occurred_at REAL NOT NULL, request_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL, actor_id TEXT, roles TEXT NOT NULL, project_id TEXT,
                    action TEXT NOT NULL, tool_name TEXT, tool_version TEXT, params_digest TEXT NOT NULL,
                    retrieval_document_ids TEXT NOT NULL, approval_decision TEXT,
                    external_request_id TEXT, latency_ms INTEGER, estimated_cost_cents INTEGER,
                    error_code TEXT, final_state TEXT NOT NULL, payload_redacted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS budgets (
                    task_id TEXT PRIMARY KEY, max_steps INTEGER NOT NULL, max_tokens INTEGER NOT NULL,
                    max_cost_cents INTEGER NOT NULL, steps INTEGER NOT NULL DEFAULT 0,
                    tokens INTEGER NOT NULL DEFAULT 0, cost_cents INTEGER NOT NULL DEFAULT 0,
                    stopped_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    roles TEXT NOT NULL, value TEXT NOT NULL, expires_at REAL NOT NULL
                );
                """
            )

    def record_event(self, event: AuditEvent, payload: Any = None) -> str:
        self.initialize()
        event_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    time.time(),
                    event.request_id,
                    event.trace_id,
                    event.actor_id,
                    json.dumps(event.roles),
                    event.project_id,
                    event.action,
                    event.tool_name,
                    event.tool_version,
                    event.params_digest,
                    json.dumps(event.retrieval_document_ids),
                    event.approval_decision,
                    event.external_request_id,
                    event.latency_ms,
                    event.estimated_cost_cents,
                    event.error_code,
                    event.final_state,
                    json.dumps(redact_payload(payload), ensure_ascii=False),
                ),
            )
        return event_id

    def audit_events(self, *, request_id: str | None = None) -> list[dict[str, Any]]:
        self.initialize()
        query = "SELECT * FROM audit_events"
        values: tuple[Any, ...] = ()
        if request_id:
            query += " WHERE request_id = ?"
            values = (request_id,)
        query += " ORDER BY occurred_at"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values)]

    def create_budget(
        self, task_id: str, *, max_steps: int, max_tokens: int, max_cost_cents: int
    ) -> None:
        self.initialize()
        if min(max_steps, max_tokens, max_cost_cents) < 0:
            raise ValueError("budget limits must be non-negative")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO budgets (task_id, max_steps, max_tokens, max_cost_cents) VALUES (?, ?, ?, ?)",
                (task_id, max_steps, max_tokens, max_cost_cents),
            )

    def consume_budget(
        self, task_id: str, *, tokens: int = 0, cost_cents: int = 0
    ) -> bool:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM budgets WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                raise ValueError("unknown budget task")
            reason = None
            if row["stopped_reason"]:
                return False
            if row["steps"] + 1 > row["max_steps"]:
                reason = "max_steps"
            elif row["tokens"] + tokens > row["max_tokens"]:
                reason = "max_tokens"
            elif row["cost_cents"] + cost_cents > row["max_cost_cents"]:
                reason = "max_cost_cents"
            if reason:
                connection.execute(
                    "UPDATE budgets SET stopped_reason = ? WHERE task_id = ?",
                    (reason, task_id),
                )
                return False
            connection.execute(
                "UPDATE budgets SET steps = steps + 1, tokens = tokens + ?, cost_cents = cost_cents + ? WHERE task_id = ?",
                (tokens, cost_cents, task_id),
            )
            return True

    def put_cache(
        self,
        cache_key: str,
        *,
        user_id: str,
        project_id: str,
        roles: list[str],
        value: Any,
        ttl_seconds: int,
    ) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO cache_entries VALUES (?, ?, ?, ?, ?, ?)",
                (
                    cache_key,
                    user_id,
                    project_id,
                    json.dumps(sorted(set(roles))),
                    json.dumps(value, ensure_ascii=False),
                    time.time() + ttl_seconds,
                ),
            )

    def get_cache(
        self, cache_key: str, *, user_id: str, project_id: str, roles: list[str]
    ) -> Any | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM cache_entries WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            if not row or row["expires_at"] <= time.time():
                return None
            allowed_roles = set(json.loads(row["roles"]))
            if (
                row["project_id"] != project_id
                or row["owner_user_id"] != user_id
                or not allowed_roles.intersection(roles)
            ):
                return None
            return json.loads(row["value"])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
