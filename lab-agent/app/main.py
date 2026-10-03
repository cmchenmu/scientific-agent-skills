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

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.demo import DEMO_PROJECT, ensure_demo_data
from app.services.admin_adapter import AdapterError, LocalAdminAdapter, Preview
from app.services.agent import AgentRun, AgentService
from app.services.knowledge import KnowledgeAnswer, KnowledgeService
from app.services.experiment_extraction import ExperimentExtraction, ExperimentExtractionService
from app.services.ingestion import fetch_open_access_abstract
from app.services.literature_import import (
    LiteratureImportResult,
    import_open_access_literature,
    import_selected_open_access_literature,
    import_research_direction,
    search_open_access_catalog,
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


class AgentRequest(BaseModel):
    project_id: str = Field(min_length=1)
    request: str = Field(min_length=1, max_length=4_000)
    mode: str = Field(default="knowledge", pattern="^(knowledge|literature_query)$")


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


class LiteratureImportRequest(BaseModel):
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=100, ge=1, le=100)


class LiteratureCatalogRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    page: int = Field(default=1, ge=1)
    sort_order: str = Field(default="relevance", pattern="^(relevance|year_desc|impact_factor_desc)$")


class SelectedLiteratureRequest(BaseModel):
    project_id: str = Field(min_length=1)
    pmcids: list[str] = Field(min_length=1, max_length=50)


class ResearchDirectionImportRequest(BaseModel):
    project_id: str = Field(min_length=1)
    direction: str = Field(min_length=2, max_length=1_000)
    limit: int = Field(default=100, ge=50, le=100)


class LiteratureSearchRequest(BaseModel):
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=2, max_length=500)


def _data_root() -> Path:
    return Path(os.getenv("LAB_AGENT_DATA_PATH", "data"))


def _archive() -> LocalArchive:
    path = Path(os.getenv("LOCAL_ARCHIVE_PATH", _data_root() / "local-archive"))
    return ensure_demo_data(path)


def _adapter() -> LocalAdminAdapter:
    return LocalAdminAdapter(_data_root() / "admin-adapter")


def _workflow() -> ManuscriptWorkflow:
    return ManuscriptWorkflow(_data_root() / "workflows", _archive())


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
        project: {
            "admin.get_policy", "admin.validate_draft", "admin.create_task",
            "admin.submit_reimbursement", "agent.propose_literature_query",
        }
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


@app.post("/v1/agent/run", response_model=AgentRun)
def run_agent(request: AgentRequest, x_user_id: str | None = Header(default=None)) -> AgentRun:
    user = current_user(x_user_id)
    if request.project_id not in user["project_roles"] and "admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="project is not visible to this user")
    return AgentService(KnowledgeService(_archive())).run(
        user,
        request.project_id,
        request.request,
        prefer_candidate_query=request.mode == "literature_query",
    )


@app.post("/v1/chat/stream")
def chat_stream(request: KnowledgeQuery, x_user_id: str | None = Header(default=None)) -> StreamingResponse:
    user = current_user(x_user_id)
    answer = AgentService(KnowledgeService(_archive())).run(user, request.project_id, request.question)

    def events() -> Generator[str, None, None]:
        yield f"event: answer\ndata: {json.dumps({'answer': answer.answer, 'status': answer.status, 'model_used': answer.model_used}, ensure_ascii=False)}\n\n"
        for citation in answer.citations:
            yield f"event: citation\ndata: {citation.model_dump_json()}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/v1/library/summary")
def library_summary(project_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, int]:
    user = current_user(x_user_id)
    if project_id not in user["project_roles"] and "admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="project is not visible to this user")
    return _archive().project_summary(project_id)


@app.get("/v1/library/documents")
def library_documents(project_id: str, x_user_id: str | None = Header(default=None)) -> list[dict[str, Any]]:
    user = current_user(x_user_id)
    return _archive().library_for_user(user["id"], project_id)


@app.get("/v1/library/documents/{document_id}/original")
def download_original(document_id: str, project_id: str, x_user_id: str | None = Header(default=None)) -> FileResponse:
    user = current_user(x_user_id)
    original = _archive().original_for_user(user["id"], project_id, document_id)
    if not original:
        raise HTTPException(status_code=404, detail="authorized original was not found")
    path, suffix = original
    return FileResponse(path, filename=f"{document_id}{suffix}")


@app.delete("/v1/library/documents/{document_id}")
def delete_library_document(document_id: str, project_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, str]:
    user = current_user(x_user_id)
    if not set(user["project_roles"].get(project_id, [])).intersection({"research-assistant", "pi"}):
        raise HTTPException(status_code=403, detail="document deletion requires research-assistant or pi role")
    if not _archive().authorized_document(user["id"], project_id, document_id):
        raise HTTPException(status_code=404, detail="authorized document was not found")
    _archive().delete_document(project_id, document_id)
    return {"document_id": document_id, "state": "deleted"}


@app.post("/v1/literature/search")
def search_literature(
    request: LiteratureSearchRequest, x_user_id: str | None = Header(default=None)
) -> list[dict[str, Any]]:
    user = current_user(x_user_id)
    if request.project_id not in user["project_roles"] and "admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="project is not visible to this user")
    return _archive().search_documents_for_user(user["id"], request.project_id, request.query, limit=5)


@app.get("/v1/literature/{document_id}/experiment", response_model=ExperimentExtraction)
def extract_experiment(
    document_id: str, project_id: str, x_user_id: str | None = Header(default=None)
) -> ExperimentExtraction:
    user = current_user(x_user_id)
    try:
        return ExperimentExtractionService(_archive()).extract(user["id"], project_id, document_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/v1/literature/import", response_model=LiteratureImportResult)
def import_literature(
    request: LiteratureImportRequest, x_user_id: str | None = Header(default=None)
) -> LiteratureImportResult:
    user = current_user(x_user_id)
    if not set(user["project_roles"].get(request.project_id, [])).intersection(
        {"research-assistant", "pi"}
    ):
        raise HTTPException(status_code=403, detail="literature import requires research-assistant or pi role")
    try:
        return import_open_access_literature(
            _archive(),
            inbox_root=_data_root() / "inbox",
            project_id=request.project_id,
            query=request.query,
            limit=request.limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.post("/v1/literature/catalog")
def literature_catalog(request: LiteratureCatalogRequest, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
    current_user(x_user_id)
    try:
        return search_open_access_catalog(request.query, request.page, request.sort_order)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/v1/literature/catalog/{pmcid}/abstract")
def literature_abstract(pmcid: str, x_user_id: str | None = Header(default=None)) -> dict[str, str | None]:
    current_user(x_user_id)
    try:
        return {"abstract": fetch_open_access_abstract(pmcid)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/v1/literature/selected-import", response_model=LiteratureImportResult)
def import_selected_literature(request: SelectedLiteratureRequest, x_user_id: str | None = Header(default=None)) -> LiteratureImportResult:
    user = current_user(x_user_id)
    if not set(user["project_roles"].get(request.project_id, [])).intersection({"research-assistant", "pi"}):
        raise HTTPException(status_code=403, detail="literature import requires research-assistant or pi role")
    try:
        return import_selected_open_access_literature(_archive(), inbox_root=_data_root() / "inbox", project_id=request.project_id, pmcids=request.pmcids)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/v1/literature/direction-import", response_model=LiteratureImportResult)
def import_research_direction_endpoint(
    request: ResearchDirectionImportRequest,
    x_user_id: str | None = Header(default=None),
) -> LiteratureImportResult:
    user = current_user(x_user_id)
    if not set(user["project_roles"].get(request.project_id, [])).intersection(
        {"research-assistant", "pi"}
    ):
        raise HTTPException(
            status_code=403,
            detail="research direction import requires research-assistant or pi role",
        )
    try:
        return import_research_direction(
            _archive(),
            inbox_root=_data_root() / "inbox",
            project_id=request.project_id,
            direction=request.direction,
            limit=request.limit,
        )
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


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
