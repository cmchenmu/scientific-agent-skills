"""ACL-first retrieval and evidence-bounded answers for the local archive."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.literature_import import research_direction_query
from app.services.local_archive import LocalArchive


class Citation(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    section: str | None = None
    page: int | None = None


class KeyInformation(BaseModel):
    chunk_id: str
    section: str | None = None
    page: int | None = None
    text: str


class RelatedPaper(BaseModel):
    document_id: str
    title: str
    key_information: list[KeyInformation] = Field(default_factory=list)


class KnowledgeAnswer(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    related_papers: list[RelatedPaper] = Field(default_factory=list)
    literature_query: str | None = None
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
        papers: dict[str, RelatedPaper] = {}
        citations: list[Citation] = []
        summary_parts: list[str] = []
        for evidence in retrieved:
            metadata = evidence["metadata"]
            citation = Citation(
                document_id=evidence["document_id"], chunk_id=evidence["id"],
                title=evidence["title"], section=metadata.get("section"), page=metadata.get("page"),
            )
            paper = papers.setdefault(
                evidence["document_id"],
                RelatedPaper(document_id=evidence["document_id"], title=evidence["title"]),
            )
            if len(paper.key_information) < 3:
                paper.key_information.append(KeyInformation(
                    chunk_id=evidence["id"], section=metadata.get("section"),
                    page=metadata.get("page"), text=evidence["text"],
                ))
            if len(summary_parts) < 3 and len(citations) < 3:
                summary_parts.append(evidence["text"])
                citations.append(citation)
        validate_citations(citations, retrieved)
        try:
            literature_query = research_direction_query(
                question + " " + " ".join(summary_parts)
            )
        except ValueError:
            literature_query = None
        return KnowledgeAnswer(
            answer="基于已授权原文证据的总结：" + " ".join(summary_parts),
            citations=citations,
            related_papers=list(papers.values())[:5],
            literature_query=literature_query,
            confidence=min(0.9, max(0.2, retrieved[0]["score"] / 3)),
            status="answered",
        )
