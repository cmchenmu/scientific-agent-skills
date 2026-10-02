"""Durable, evidence-bounded manuscript workflow for local teaching use."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.local_archive import LocalArchive

STATES = (
    "draft",
    "search_plan_review",
    "retrieve",
    "ingest",
    "relevance_review",
    "evidence_table",
    "manuscript_draft",
    "human_review",
    "completed",
    "failed",
    "cancelled",
)
NEXT_STATE = {
    "draft": "search_plan_review",
    "search_plan_review": "retrieve",
    "retrieve": "ingest",
    "ingest": "relevance_review",
    "relevance_review": "evidence_table",
    "evidence_table": "manuscript_draft",
    "manuscript_draft": "human_review",
}


class WorkflowError(ValueError):
    """Raised for invalid state transitions and workflow budget limits."""


@dataclass(frozen=True)
class WorkflowSnapshot:
    workflow_id: str
    state: str
    budget_reason: str | None


class ManuscriptWorkflow:
    """A local state machine whose evidence always comes from authorized chunks."""

    def __init__(self, root: Path, archive: LocalArchive) -> None:
        self.root = root
        self.database_path = root / "manuscript_workflows.sqlite3"
        self.archive = archive

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    question TEXT NOT NULL, state TEXT NOT NULL, search_plan TEXT,
                    max_steps INTEGER NOT NULL, max_tokens INTEGER NOT NULL,
                    max_cost_cents INTEGER NOT NULL, steps INTEGER NOT NULL DEFAULT 0,
                    tokens INTEGER NOT NULL DEFAULT 0, cost_cents INTEGER NOT NULL DEFAULT 0,
                    deadline_epoch REAL NOT NULL, budget_reason TEXT
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    state TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relevance_cards (
                    id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, document_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL, title TEXT NOT NULL, card TEXT NOT NULL,
                    UNIQUE(workflow_id, chunk_id)
                );
                CREATE TABLE IF NOT EXISTS evidence_rows (
                    id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, claim TEXT NOT NULL,
                    document_id TEXT NOT NULL, chunk_id TEXT NOT NULL, evidence TEXT NOT NULL,
                    UNIQUE(workflow_id, chunk_id)
                );
                CREATE TABLE IF NOT EXISTS manuscript_drafts (
                    workflow_id TEXT PRIMARY KEY, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def create(
        self,
        user_id: str,
        project_id: str,
        question: str,
        *,
        max_steps: int = 10,
        max_tokens: int = 10_000,
        max_cost_cents: int = 100,
        deadline_epoch: float | None = None,
    ) -> str:
        if not question.strip():
            raise WorkflowError("question is required")
        self.initialize()
        workflow_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO workflows VALUES (?, ?, ?, ?, 'draft', NULL, ?, ?, ?, 0, 0, 0, ?, NULL)",
                (
                    workflow_id,
                    user_id,
                    project_id,
                    question,
                    max_steps,
                    max_tokens,
                    max_cost_cents,
                    deadline_epoch or time.time() + 3_600,
                ),
            )
            self._checkpoint(connection, workflow_id, "draft", {"question": question})
        return workflow_id

    def set_search_plan(
        self, workflow_id: str, plan: dict[str, Any]
    ) -> WorkflowSnapshot:
        if not {"query", "sources", "inclusion", "exclusion"}.issubset(plan):
            raise WorkflowError(
                "search plan requires query, sources, inclusion, and exclusion"
            )
        return self._transition(
            workflow_id,
            "draft",
            "search_plan_review",
            {"search_plan": plan},
            search_plan=plan,
        )

    def approve_search_plan(
        self, workflow_id: str, reviewer_id: str
    ) -> WorkflowSnapshot:
        if not reviewer_id:
            raise WorkflowError("reviewer is required")
        return self._transition(
            workflow_id, "search_plan_review", "retrieve", {"reviewer_id": reviewer_id}
        )

    def retrieve(self, workflow_id: str, limit: int = 8) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        self._require_state(row, "retrieve")
        self._consume(row, tokens=0, cost_cents=0)
        plan = json.loads(row["search_plan"])
        chunks = self.archive.search_for_user(
            row["user_id"], row["project_id"], plan["query"], limit
        )
        return self._transition(
            workflow_id, "retrieve", "ingest", {"retrieved": chunks}
        )

    def ingest_retrieved(self, workflow_id: str) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        self._require_state(row, "ingest")
        checkpoint = self._last_checkpoint(workflow_id)
        retrieved = checkpoint["payload"].get("retrieved", [])
        if not retrieved:
            raise WorkflowError("no authorized retrieved documents to ingest")
        return self._transition(
            workflow_id,
            "ingest",
            "relevance_review",
            {"ingested_chunk_ids": [item["id"] for item in retrieved]},
        )

    def create_relevance_cards(self, workflow_id: str) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        self._require_state(row, "relevance_review")
        retrieved = self._find_retrieved(workflow_id)
        if not retrieved:
            raise WorkflowError("no retrieved evidence")
        with self._connect() as connection:
            for item in retrieved:
                identifiers = self.archive.document_identifiers(item["document_id"])
                card = {
                    "research_object": "not reported",
                    "model": "not reported",
                    "sample_size": "not reported",
                    "method": item["metadata"].get("section", "not reported"),
                    "main_result": item["text"],
                    "limitations": "not assessed by deterministic workflow",
                    "relation_to_question": "retrieved by approved search plan",
                    "evidence_location": {
                        "document_id": item["document_id"],
                        "chunk_id": item["id"],
                        "section": item["metadata"].get("section"),
                    },
                    "identifiers": identifiers,
                }
                connection.execute(
                    "INSERT OR REPLACE INTO relevance_cards VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        workflow_id,
                        item["document_id"],
                        item["id"],
                        item["title"],
                        json.dumps(card),
                    ),
                )
        return self._transition(
            workflow_id,
            "relevance_review",
            "evidence_table",
            {"card_count": len(retrieved)},
        )

    def build_evidence_table(self, workflow_id: str) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        self._require_state(row, "evidence_table")
        with self._connect() as connection:
            cards = connection.execute(
                "SELECT * FROM relevance_cards WHERE workflow_id = ?", (workflow_id,)
            ).fetchall()
            if not cards:
                raise WorkflowError("no relevance cards")
            for card in cards:
                payload = json.loads(card["card"])
                connection.execute(
                    "INSERT OR REPLACE INTO evidence_rows VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        workflow_id,
                        "retrieved evidence",
                        card["document_id"],
                        card["chunk_id"],
                        payload["main_result"],
                    ),
                )
        return self._transition(
            workflow_id,
            "evidence_table",
            "manuscript_draft",
            {"evidence_count": len(cards)},
        )

    def draft_manuscript(self, workflow_id: str) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        self._require_state(row, "manuscript_draft")
        with self._connect() as connection:
            evidence = connection.execute(
                "SELECT * FROM evidence_rows WHERE workflow_id = ?", (workflow_id,)
            ).fetchall()
            if not evidence:
                raise WorkflowError("no evidence table rows")
            citations = "\n".join(
                f"- [{item['document_id']}:{item['chunk_id']}] {item['evidence']}"
                for item in evidence
            )
            content = f"# Draft\n\n## Background\nTODO: add reviewed background with citations.\n\n## Evidence\n{citations}\n\n## Methods\nTODO: add human-verified methods.\n\n## Results\nTODO: synthesize only supported evidence.\n\n## Discussion\nTODO: add limitations and interpretation after review.\n"
            connection.execute(
                "INSERT OR REPLACE INTO manuscript_drafts VALUES (?, ?, CURRENT_TIMESTAMP)",
                (workflow_id, content),
            )
        return self._transition(
            workflow_id, "manuscript_draft", "human_review", {"draft_created": True}
        )

    def complete_human_review(
        self, workflow_id: str, reviewer_id: str, approved: bool
    ) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        self._require_state(row, "human_review")
        if not reviewer_id:
            raise WorkflowError("reviewer is required")
        target = "completed" if approved else "failed"
        return self._transition(
            workflow_id,
            "human_review",
            target,
            {"reviewer_id": reviewer_id, "approved": approved},
        )

    def cancel(self, workflow_id: str, reason: str) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        if row["state"] in {"completed", "failed", "cancelled"}:
            raise WorkflowError("workflow is already terminal")
        return self._transition(
            workflow_id, row["state"], "cancelled", {"reason": reason}
        )

    def resume(self, workflow_id: str) -> WorkflowSnapshot:
        row = self._workflow(workflow_id)
        return WorkflowSnapshot(workflow_id, row["state"], row["budget_reason"])

    def draft(self, workflow_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT content FROM manuscript_drafts WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
        return row["content"] if row else None

    def artifact_for_user(self, workflow_id: str, user_id: str) -> dict[str, str]:
        """Return a draft only to the workflow owner for the local API."""
        workflow = self._workflow(workflow_id)
        if workflow["user_id"] != user_id:
            raise WorkflowError("workflow is not visible to this user")
        content = self.draft(workflow_id)
        if content is None:
            raise WorkflowError("workflow has no manuscript artifact yet")
        return {"id": workflow_id, "kind": "manuscript_draft", "content": content}

    def _transition(
        self,
        workflow_id: str,
        expected: str,
        target: str,
        payload: dict[str, Any],
        *,
        search_plan: dict[str, Any] | None = None,
    ) -> WorkflowSnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
            self._require_state(row, expected)
            if search_plan is None:
                connection.execute(
                    "UPDATE workflows SET state = ? WHERE id = ?", (target, workflow_id)
                )
            else:
                connection.execute(
                    "UPDATE workflows SET state = ?, search_plan = ? WHERE id = ?",
                    (target, json.dumps(search_plan), workflow_id),
                )
            self._checkpoint(connection, workflow_id, target, payload)
        return WorkflowSnapshot(workflow_id, target, None)

    def _consume(self, row: sqlite3.Row, *, tokens: int, cost_cents: int) -> None:
        reason = None
        if row["steps"] + 1 > row["max_steps"]:
            reason = "max_steps"
        elif row["tokens"] + tokens > row["max_tokens"]:
            reason = "max_tokens"
        elif row["cost_cents"] + cost_cents > row["max_cost_cents"]:
            reason = "max_cost_cents"
        elif time.time() >= row["deadline_epoch"]:
            reason = "deadline"
        with self._connect() as connection:
            if reason:
                connection.execute(
                    "UPDATE workflows SET state = 'failed', budget_reason = ? WHERE id = ?",
                    (reason, row["id"]),
                )
                self._checkpoint(connection, row["id"], "failed", {"reason": reason})
                connection.commit()
                raise WorkflowError(f"workflow budget exhausted: {reason}")
            connection.execute(
                "UPDATE workflows SET steps = steps + 1, tokens = tokens + ?, cost_cents = cost_cents + ? WHERE id = ?",
                (tokens, cost_cents, row["id"]),
            )

    def _workflow(self, workflow_id: str) -> sqlite3.Row:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
            ).fetchone()
        if not row:
            raise WorkflowError("unknown workflow")
        return row

    def _last_checkpoint(self, workflow_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE workflow_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
        if not row:
            raise WorkflowError("workflow has no checkpoint")
        return {**dict(row), "payload": json.loads(row["payload"])}  # type: ignore[return-value]

    def _find_retrieved(self, workflow_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM checkpoints WHERE workflow_id = ? AND state = 'ingest' ORDER BY rowid DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
        return json.loads(rows["payload"]).get("retrieved", []) if rows else []

    def _checkpoint(
        self,
        connection: sqlite3.Connection,
        workflow_id: str,
        state: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO checkpoints VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?)",
            (str(uuid.uuid4()), workflow_id, state, json.dumps(payload)),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _require_state(row: sqlite3.Row, expected: str) -> None:
        if row["state"] != expected:
            raise WorkflowError(f"expected {expected}, found {row['state']}")
