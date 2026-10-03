"""Controlled import of Europe PMC open-access articles into the local archive."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from app.services.ingestion import (
    download_open_access_articles,
    download_open_access_articles_by_ids,
    search_open_access_catalog,
)
from app.services.local_archive import LocalArchive


@dataclass(frozen=True)
class LiteratureImportResult:
    query: str
    requested: int
    downloaded: int
    created: int
    duplicates: int
    direction: str | None = None


# The local demo has no model credential. This limited bilingual map makes the
# research-direction workflow useful without pretending to understand arbitrary text.
DIRECTION_TERMS = (
    ("阿尔茨海默", "Alzheimer"),
    ("帕金森", "Parkinson"),
    ("神经退行", "neurodegeneration"),
    ("神经发生", "neurogenesis"),
    ("小胶质", "microglia"),
    ("神经科学", "neuroscience"),
    ("海马", "hippocampus"),
    ("皮层", "cortex"),
    ("突触", "synapse"),
    ("胶质", "glia"),
    ("神经", "neural"),
    ("大脑", "brain"),
    ("脑", "brain"),
    ("小鼠", "mouse"),
    ("大鼠", "rat"),
    ("炎症", "inflammation"),
    ("脑卒中", "stroke"),
    ("癫痫", "epilepsy"),
    ("抑郁", "depression"),
    ("焦虑", "anxiety"),
    ("记忆", "memory"),
    ("学习", "learning"),
    ("类器官", "organoid"),
)


def research_direction_query(direction: str) -> str:
    """Produce a bounded Europe PMC query from a plain-language research direction."""
    cleaned = " ".join(direction.split())
    if not cleaned:
        raise ValueError("research direction is required")
    english_terms = re.findall(r"[A-Za-z][A-Za-z0-9-]*", cleaned)
    matched_chinese_terms: list[str] = []
    translated_terms: list[str] = []
    for chinese, english in DIRECTION_TERMS:
        if chinese in cleaned and not any(
            chinese in matched for matched in matched_chinese_terms
        ):
            matched_chinese_terms.append(chinese)
            translated_terms.append(english)
    terms = list(dict.fromkeys([*english_terms, *translated_terms]))
    if not terms:
        raise ValueError(
            "the local direction assistant needs an English keyword or a supported biomedical Chinese term"
        )
    return " ".join(terms[:12])


def import_open_access_literature(
    archive: LocalArchive,
    *,
    inbox_root: Path,
    project_id: str,
    query: str,
    limit: int,
    allowed_roles: tuple[str, ...] = ("student", "research-assistant", "pi"),
) -> LiteratureImportResult:
    """Fetch an explicitly bounded OA corpus and apply the local ACL on ingestion."""
    cleaned_query = " ".join(query.split())
    if not cleaned_query:
        raise ValueError("literature query is required")
    if not 1 <= limit <= 100:
        raise ValueError("literature import limit must be between 1 and 100")

    batch_key = hashlib.sha256(f"{project_id}\0{cleaned_query}\0{limit}".encode()).hexdigest()[:16]
    batch_dir = inbox_root / batch_key
    manifest_path = download_open_access_articles(cleaned_query, batch_dir, limit)
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line]
    created = 0
    duplicates = 0
    for row in rows:
        result = archive.ingest(
            Path(row["path"]), project_id, allowed_roles, "open-access-literature"
        )
        if result.state == "created":
            created += 1
        else:
            duplicates += 1
    return LiteratureImportResult(
        query=cleaned_query,
        requested=limit,
        downloaded=len(rows),
        created=created,
        duplicates=duplicates,
    )


def import_selected_open_access_literature(
    archive: LocalArchive, *, inbox_root: Path, project_id: str, pmcids: list[str],
    allowed_roles: tuple[str, ...] = ("student", "research-assistant", "pi"),
) -> LiteratureImportResult:
    """Ingest precisely the articles selected in the catalog UI (max 50)."""
    selected = list(dict.fromkeys(pmcids))
    if not 1 <= len(selected) <= 50:
        raise ValueError("select between 1 and 50 papers")
    selection_key = "\0".join(selected)
    batch_key = hashlib.sha256(f"{project_id}\0{selection_key}".encode()).hexdigest()[:16]
    manifest_path = download_open_access_articles_by_ids(selected, inbox_root / batch_key)
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line]
    created = duplicates = 0
    for row in rows:
        result = archive.ingest(Path(row["path"]), project_id, allowed_roles, "open-access-literature")
        if result.state == "created": created += 1
        else: duplicates += 1
    return LiteratureImportResult(query="selected PMC articles", requested=len(selected), downloaded=len(rows), created=created, duplicates=duplicates)


def import_research_direction(
    archive: LocalArchive,
    *,
    inbox_root: Path,
    project_id: str,
    direction: str,
    limit: int,
    allowed_roles: tuple[str, ...] = ("student", "research-assistant", "pi"),
) -> LiteratureImportResult:
    """Translate a research direction to an auditable query, then import its OA corpus."""
    query = research_direction_query(direction)
    result = import_open_access_literature(
        archive,
        inbox_root=inbox_root,
        project_id=project_id,
        query=query,
        limit=limit,
        allowed_roles=allowed_roles,
    )
    return replace(result, direction=" ".join(direction.split()))
