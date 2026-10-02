"""Local development API for the Lab Agent learning application.

The X-User-Id header is a local identity selector, not production authentication.
Production must replace it with verified OIDC claims and server-side policy lookup.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.demo import DEMO_PROJECT, ensure_demo_data
from app.services.admin_adapter import AdapterError, LocalAdminAdapter, Preview
from app.services.knowledge import KnowledgeAnswer, KnowledgeService
from app.services.literature_collection import (
    CollectionError,
    LiteratureCollectionService,
)
from app.services.local_archive import LocalArchive
from app.services.manuscript_workflow import ManuscriptWorkflow, WorkflowError

app = FastAPI(title="Lab Agent Development API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class KnowledgeQuery(BaseModel):
    project_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=4_000)


class ReimbursementPayload(BaseModel):
    project_id: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=1)
    description: str = Field(min_length=1, max_length=1_000)
    receipts: list[str] = Field(min_length=1, max_length=20)


class ActionCreate(BaseModel):
    payload: ReimbursementPayload
    idempotency_key: str = Field(min_length=1, max_length=128)


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")


class LiteratureCollectionRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=120)
    domain: str = Field(min_length=1, max_length=500)
    article_count: int = Field(ge=50, le=100)


def _data_root() -> Path:
    return Path(os.getenv("LAB_AGENT_DATA_PATH", "data"))


def _archive() -> LocalArchive:
    path = Path(os.getenv("LOCAL_ARCHIVE_PATH", _data_root() / "local-archive"))
    return ensure_demo_data(path)


def _adapter() -> LocalAdminAdapter:
    return LocalAdminAdapter(_data_root() / "admin-adapter")


def _workflow() -> ManuscriptWorkflow:
    return ManuscriptWorkflow(_data_root() / "workflows", _archive())


def _literature() -> LiteratureCollectionService:
    return LiteratureCollectionService(_data_root() / "literature-collections", _archive())


def current_user(x_user_id: str | None) -> dict[str, Any]:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="X-User-Id is required in development mode")
    if x_user_id == "finance-demo":
        return {"id": x_user_id, "display_name": "Finance Demo", "active": True, "roles": ["finance-approver"], "project_roles": {}, "allowed_tools": {}}
    if x_user_id == "admin-demo":
        return {"id": x_user_id, "display_name": "Admin Demo", "active": True, "roles": ["admin"], "project_roles": {}, "allowed_tools": {}}
    context = _archive().user_context(x_user_id)
    if not context:
        raise HTTPException(status_code=401, detail="unknown or inactive development user")
    roles = sorted({role for values in context["project_roles"].values() for role in values})
    allowed_tools = {
        project: {"admin.get_policy", "admin.validate_draft", "admin.create_task", "admin.submit_reimbursement"}
        for project, project_roles in context["project_roles"].items()
        if set(project_roles).intersection({"research-assistant", "pi"})
    }
    return {**context, "roles": roles, "allowed_tools": allowed_tools}


def _adapter_error(error: AdapterError) -> HTTPException:
    return HTTPException(status_code=403, detail=str(error))


def _preview_response(preview: Preview) -> dict[str, Any]:
    return {"request_type": preview.request_type, "draft_hash": preview.draft_hash, "payload": preview.normalized_payload, "impact": preview.impact}


@app.get("/health")
def health() -> dict[str, str]:
    _archive()
    return {"status": "ok", "mode": "local-development"}


@app.get("/v1/session")
def session(x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user = current_user(x_user_id)
    return {"user": {key: user[key] for key in ("id", "display_name", "roles", "project_roles")}, "default_project_id": DEMO_PROJECT}


@app.post("/v1/knowledge/query", response_model=KnowledgeAnswer)
def query_knowledge(request: KnowledgeQuery, x_user_id: str | None = Header(default=None)) -> KnowledgeAnswer:
    user = current_user(x_user_id)
    return KnowledgeService(_archive()).answer_question(user["id"], request.question, request.project_id)


@app.post("/v1/literature/collections", status_code=202)
def create_literature_collection(
    request: LiteratureCollectionRequest,
    background_tasks: BackgroundTasks,
    x_user_id: str | None = Header(default=None),
) -> dict[str, str]:
    user = current_user(x_user_id)
    if not set(user["project_roles"].get(request.project_id, [])).intersection(
        {"research-assistant", "pi"}
    ):
        raise HTTPException(status_code=403, detail="only research assistants or PIs can collect literature")
    archive = _archive()
    roles = archive.project_role_names(request.project_id)
    if not roles:
        raise HTTPException(status_code=403, detail="project has no server-owned role assignments")
    task_id = _literature().create(user["id"], request.project_id, request.domain, request.article_count)
    background_tasks.add_task(_literature().run, task_id, roles)
    return {"collection_id": task_id, "state": "queued"}


@app.get("/v1/literature/collections")
def list_literature_collections(x_user_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    user = current_user(x_user_id)
    return _literature().list_for_user(user["id"], is_admin="admin" in user["roles"])


@app.get("/v1/literature/collections/{collection_id}")
def get_literature_collection(collection_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user = current_user(x_user_id)
    try:
        return _literature().get(collection_id, user["id"], is_admin="admin" in user["roles"])
    except CollectionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/chat/stream")
def chat_stream(request: KnowledgeQuery, x_user_id: str | None = Header(default=None)) -> StreamingResponse:
    user = current_user(x_user_id)
    answer = KnowledgeService(_archive()).answer_question(user["id"], request.question, request.project_id)

    def events() -> Generator[str, None, None]:
        yield f"event: answer\ndata: {json.dumps({'answer': answer.answer, 'status': answer.status}, ensure_ascii=False)}\n\n"
        for citation in answer.citations:
            yield f"event: citation\ndata: {citation.model_dump_json()}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/v1/tasks")
def list_tasks(x_user_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    try:
        return _adapter().list_tasks(current_user(x_user_id))
    except AdapterError as error:
        raise _adapter_error(error) from error


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        return _adapter().task(current_user(x_user_id), task_id)
    except AdapterError as error:
        raise _adapter_error(error) from error


@app.post("/v1/actions/reimbursements/preview")
def preview_reimbursement(payload: ReimbursementPayload, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        return _preview_response(_adapter().preview_submission(current_user(x_user_id), payload.model_dump()))
    except AdapterError as error:
        raise _adapter_error(error) from error


@app.post("/v1/actions/reimbursements")
def create_reimbursement_action(request: ActionCreate, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    user = current_user(x_user_id)
    try:
        adapter = _adapter()
        preview = adapter.preview_submission(user, request.payload.model_dump())
        task_id = adapter.create_approval_task(user, preview, request.idempotency_key)
        return {"action_id": task_id, "state": "pending_approval", "preview": _preview_response(preview)}
    except AdapterError as error:
        raise _adapter_error(error) from error


@app.post("/v1/actions/{action_id}/approve")
def approve_action(action_id: str, request: ApprovalDecision, x_user_id: str | None = Header(default=None)) -> dict[str, str]:
    try:
        adapter = _adapter()
        task = adapter.task({"id": "admin-demo", "roles": ["admin"]}, action_id)
        adapter.record_approval(current_user(x_user_id), action_id, adapter._digest(task["payload"]), request.decision)
        return {"action_id": action_id, "decision": request.decision}
    except AdapterError as error:
        raise _adapter_error(error) from error


@app.post("/v1/actions/{action_id}/confirm")
def confirm_action(action_id: str, x_idempotency_key: str | None = Header(default=None), x_user_id: str | None = Header(default=None)) -> dict[str, str]:
    if not x_idempotency_key:
        raise HTTPException(status_code=422, detail="X-Idempotency-Key is required")
    try:
        external_id = _adapter().submit_approved_request(current_user(x_user_id), action_id, x_idempotency_key)
        return {"action_id": action_id, "external_id": external_id, "state": "submitted"}
    except AdapterError as error:
        raise _adapter_error(error) from error


@app.post("/v1/actions/{action_id}/cancel")
def cancel_action(action_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, str]:
    try:
        return _adapter().cancel_task(current_user(x_user_id), action_id)
    except AdapterError as error:
        raise _adapter_error(error) from error


@app.get("/v1/artifacts/{artifact_id}")
def artifact(artifact_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, str]:
    user = current_user(x_user_id)
    try:
        return _workflow().artifact_for_user(artifact_id, user["id"])
    except WorkflowError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


_web_dist = Path(__file__).resolve().parents[1] / "web" / "dist"
if _web_dist.is_dir():
    app.mount("/", StaticFiles(directory=_web_dist, html=True), name="web")
