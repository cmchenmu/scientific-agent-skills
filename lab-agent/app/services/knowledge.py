"""ACL-first retrieval and evidence-bounded answers for the local archive."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.local_archive import LocalArchive


class Citation(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    section: str | None = None
    page: int | None = None


class KnowledgeAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    missing_information: list[str] = Field(default_factory=list)
    status: str


def validate_citations(
    citations: list[Citation], retrieved: list[dict[str, Any]]
) -> list[Citation]:
    """Reject citations not present in the authorized retrieval result set."""
    permitted = {(item["document_id"], item["id"]) for item in retrieved}
    invalid = [
        citation
        for citation in citations
        if (citation.document_id, citation.chunk_id) not in permitted
    ]
    if invalid:
        raise ValueError("citation does not reference an authorized retrieved chunk")
    return citations


class KnowledgeService:
    """Produces extractive answers only; an LLM may be added behind citation validation."""

    def __init__(self, archive: LocalArchive) -> None:
        self.archive = archive

    def search_documents(
        self, user_id: str, query: str, project_id: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        return self.archive.search_for_user(user_id, project_id, query, limit)

    def answer_question(
        self, user_id: str, question: str, project_id: str
    ) -> KnowledgeAnswer:
        retrieved = self.search_documents(user_id, question, project_id)
        if not retrieved:
            return KnowledgeAnswer(
                answer="没有找到可授权且足以支持该问题的证据。",
                confidence=0.0,
                missing_information=["authorized evidence"],
                status="insufficient_evidence",
            )
        evidence = retrieved[0]
        metadata = evidence["metadata"]
        citation = Citation(
            document_id=evidence["document_id"],
            chunk_id=evidence["id"],
            title=evidence["title"],
            section=metadata.get("section"),
            page=metadata.get("page"),
        )
        validate_citations([citation], retrieved)
        return KnowledgeAnswer(
            answer=evidence["text"],
            citations=[citation],
            confidence=min(0.9, max(0.2, evidence["score"] / 3)),
            status="answered",
        )
