"""Small process-local idempotency gate used by an external-submit worker.

Production workers must claim the same key transactionally through the unique
``tasks.idempotency_key`` constraint before invoking an external system.  This
object makes the single-process behavior explicit and testable.
"""

from __future__ import annotations

from threading import Lock


class IdempotencyGate:
    """Allows exactly one caller to claim a non-empty idempotency key."""

    def __init__(self) -> None:
        self._claimed_keys: set[str] = set()
        self._lock = Lock()

    def claim(self, key: str) -> bool:
        """Return true only for the first claim of ``key``."""
        if not key:
            raise ValueError("idempotency_key must not be empty")
        with self._lock:
            if key in self._claimed_keys:
                return False
            self._claimed_keys.add(key)
            return True
