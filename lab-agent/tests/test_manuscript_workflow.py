from pathlib import Path

import pytest

from app.services.local_archive import LocalArchive
from app.services.manuscript_workflow import ManuscriptWorkflow, WorkflowError


def make_workflow(tmp_path: Path) -> ManuscriptWorkflow:
    source = tmp_path / "paper.html"
    source.write_text(
        "<title>Mouse study</title><h1>Methods</h1><p>Mouse tissue was stored on ice.</p>",
        encoding="utf-8",
    )
    archive = LocalArchive(tmp_path / "archive")
    archive.ingest(source, "project-a", ["student"], "internal-research")
    archive.grant_role("user-1", "project-a", "student")
    return ManuscriptWorkflow(tmp_path / "workflow", archive)


def run_to_review(workflow: ManuscriptWorkflow) -> str:
    workflow_id = workflow.create("user-1", "project-a", "mouse tissue storage")
    workflow.set_search_plan(
        workflow_id,
        {
            "query": "mouse tissue storage",
            "sources": ["local"],
            "inclusion": ["authorized"],
            "exclusion": ["unverified"],
        },
    )
    workflow.approve_search_plan(workflow_id, "pi-1")
    workflow.retrieve(workflow_id)
    workflow.ingest_retrieved(workflow_id)
    workflow.create_relevance_cards(workflow_id)
    workflow.build_evidence_table(workflow_id)
    workflow.draft_manuscript(workflow_id)
    return workflow_id


def test_workflow_persists_checkpoints_and_marks_todos(tmp_path: Path):
    workflow = make_workflow(tmp_path)
    workflow_id = run_to_review(workflow)

    assert workflow.resume(workflow_id).state == "human_review"
    assert "TODO" in workflow.draft(workflow_id)
    assert workflow.artifact_for_user(workflow_id, "user-1")["kind"] == "manuscript_draft"
    with pytest.raises(WorkflowError, match="not visible"):
        workflow.artifact_for_user(workflow_id, "other-user")
    assert (
        workflow.complete_human_review(workflow_id, "pi-1", True).state == "completed"
    )


def test_workflow_requires_review_and_rejects_invalid_transition(tmp_path: Path):
    workflow = make_workflow(tmp_path)
    workflow_id = workflow.create("user-1", "project-a", "question")

    with pytest.raises(WorkflowError, match="expected retrieve"):
        workflow.retrieve(workflow_id)


def test_workflow_budget_failure_is_terminal(tmp_path: Path):
    workflow = make_workflow(tmp_path)
    workflow_id = workflow.create("user-1", "project-a", "question", max_steps=0)
    workflow.set_search_plan(
        workflow_id,
        {
            "query": "question",
            "sources": ["local"],
            "inclusion": ["all"],
            "exclusion": [],
        },
    )
    workflow.approve_search_plan(workflow_id, "pi-1")

    with pytest.raises(WorkflowError, match="budget exhausted"):
        workflow.retrieve(workflow_id)
    assert workflow.resume(workflow_id).state == "failed"
