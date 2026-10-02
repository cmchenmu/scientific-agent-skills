"""Deterministic safety gates used by Chapter 9 tests and launch checks."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class UnsafeToolInput(ValueError):
    """Raised when a structured tool argument crosses a safety boundary."""


_DANGEROUS_INPUT = re.compile(
    r"(?:\.\./|\.\\\\|\x00|\$\(|`|\b(?:drop|delete|truncate)\s+(?:table|from)\b)",
    re.IGNORECASE,
)
_SHELL_META = re.compile(r"[;&|<>]")


def validate_tool_arguments(value: Any, *, max_bytes: int = 16_384) -> None:
    """Reject path traversal, shell/SQL fragments and oversized structured input."""
    encoded = repr(value).encode("utf-8")
    if len(encoded) > max_bytes:
        raise UnsafeToolInput("tool arguments exceed the size limit")

    def walk(item: Any) -> None:
        if isinstance(item, str):
            if _DANGEROUS_INPUT.search(item) or _SHELL_META.search(item):
                raise UnsafeToolInput(
                    "tool argument contains a forbidden shell, SQL, or path fragment"
                )
        elif isinstance(item, dict):
            for key, child in item.items():
                walk(key)
                walk(child)
        elif isinstance(item, (list, tuple, set)):
            for child in item:
                walk(child)

    walk(value)


def input_digest(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def validate_approval_commit(
    task: dict[str, Any],
    approvals: list[dict[str, Any]],
    *,
    draft_hash: str,
    now: datetime | None = None,
) -> bool:
    """Require current, non-self approval for the exact draft being committed."""
    if task.get("state") != "pending_approval" or task.get(
        "external_submission_exists"
    ):
        return False
    if task.get("draft_hash") != draft_hash:
        return False
    current = now or datetime.now(UTC)
    valid: set[str] = set()
    for approval in approvals:
        if approval.get("decision") != "approved":
            continue
        if approval.get("approver_id") in {None, task.get("requester_id")}:
            continue
        decided_at = approval.get("decided_at")
        expires_at = approval.get("expires_at")
        if decided_at and _as_datetime(decided_at) > current:
            continue
        if expires_at and _as_datetime(expires_at) <= current:
            continue
        if approval.get("draft_hash", task.get("draft_hash")) != draft_hash:
            continue
        valid.add(approval["approver_id"])
    return len(valid) >= int(task.get("required_approval_count", 1))


def _as_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(value)


@dataclass
class BudgetController:
    max_steps: int
    max_tokens: int
    max_cost_cents: int
    deadline_epoch: float
    steps: int = 0
    tokens: int = 0
    cost_cents: int = 0
    stopped_reason: str | None = None

    def consume(self, *, tokens: int = 0, cost_cents: int = 0) -> bool:
        """Account one model/tool call; false means execution must terminate."""
        if self.stopped_reason:
            return False
        if self.steps + 1 > self.max_steps:
            self.stopped_reason = "max_steps"
        elif self.tokens + tokens > self.max_tokens:
            self.stopped_reason = "max_tokens"
        elif self.cost_cents + cost_cents > self.max_cost_cents:
            self.stopped_reason = "max_cost_cents"
        elif time.time() >= self.deadline_epoch:
            self.stopped_reason = "deadline"
        if self.stopped_reason:
            return False
        self.steps += 1
        self.tokens += tokens
        self.cost_cents += cost_cents
        return True


@dataclass
class CheckpointStore:
    """In-memory checkpoint store used by local workers and deterministic tests."""

    checkpoints: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def save(self, workflow_id: str, state: dict[str, Any]) -> None:
        self.checkpoints.setdefault(workflow_id, []).append(dict(state))

    def resume(self, workflow_id: str) -> dict[str, Any] | None:
        states = self.checkpoints.get(workflow_id, [])
        return dict(states[-1]) if states else None
