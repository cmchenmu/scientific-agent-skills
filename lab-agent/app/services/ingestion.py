"""Deterministic admission of authorized literature into the knowledge store."""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_FULL_TEXT = (
    "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
)
SUPPORTED_SUFFIXES = {".xml", ".html", ".htm", ".pdf", ".docx"}
EXCLUDED_PUBLICATION_TYPES = {"abstract", "correction", "editorial", "letter"}
EMBEDDING_DIMENSIONS = 256


@dataclass(frozen=True)
class Section:
    title: str
    text: str
    ordinal: int


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    identifiers: dict[str, str]
    license_text: str | None
    sections: tuple[Section, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def parse_jats_xml(path: Path) -> ParsedDocument:
    """Parse the stable JATS fields needed for provenance and deterministic chunks."""
    root = ET.parse(path).getroot()
    title = _text(
        next((e for e in root.iter() if _local_name(e) == "article-title"), None)
    )
    if not title:
        raise ValueError("JATS article is missing article-title")

    identifiers = {
        element.attrib["pub-id-type"]: _text(element)
        for element in root.iter()
        if _local_name(element) == "article-id"
        and element.attrib.get("pub-id-type") in {"doi", "pmid", "pmcid"}
        and _text(element)
    }
    license_text = (
        _text(next((e for e in root.iter() if _local_name(e) == "license"), None))
        or None
    )
    body = next((e for e in root.iter() if _local_name(e) == "body"), None)
    if body is None:
        raise ValueError("JATS article is missing body")

    sections: list[Section] = []

    def collect(container: ET.Element, heading: str) -> None:
        paragraphs = [
            _text(child)
            for child in container
            if _local_name(child) == "p" and _text(child)
        ]
        if paragraphs:
            sections.append(Section(heading, "\n".join(paragraphs), len(sections)))
        for child in container:
            if _local_name(child) != "sec":
                continue
            child_title = _text(
                next((e for e in child if _local_name(e) == "title"), None)
            )
            collect(child, child_title or heading)

    collect(body, "Body")
    if not sections:
        raise ValueError("JATS article has no readable body paragraphs")
    return ParsedDocument(title, identifiers, license_text, tuple(sections))


def chunk_sections(
    sections: Iterable[Section], max_chars: int = 1_200
) -> list[dict[str, Any]]:
    """Split at paragraph boundaries while retaining a document-relative locator."""
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    chunks: list[dict[str, Any]] = []
    for section in sections:
        buffer: list[str] = []
        size = 0
        for paragraph in section.text.splitlines():
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if buffer and size + len(paragraph) + 1 > max_chars:
                chunks.append(
                    {
                        "text": "\n".join(buffer),
                        "metadata": {
                            "section": section.title,
                            "paragraph": section.ordinal,
                            "page": None,
                        },
                    }
                )
                buffer, size = [], 0
            buffer.append(paragraph)
            size += len(paragraph) + 1
        if buffer:
            chunks.append(
                {
                    "text": "\n".join(buffer),
                    "metadata": {
                        "section": section.title,
                        "paragraph": section.ordinal,
                        "page": None,
                    },
                }
            )
    return chunks


def hash_embedding(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """Create a deterministic baseline vector for local ingestion tests.

    Replace this versioned baseline with a production embedding model before
    using semantic similarity for scientific retrieval.
    """
    if dimensions < 1:
        raise ValueError("dimensions must be positive")
    values = [0.0] * dimensions
    for token in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()):
        index = (
            int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
            % dimensions
        )
        values[index] += 1.0
    magnitude = math.sqrt(sum(value * value for value in values))
    return values if magnitude == 0 else [value / magnitude for value in values]


def select_open_access_results(
    results: Iterable[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Keep research articles with PMC full text; API search hits are not trusted blindly."""
    selected: list[dict[str, Any]] = []
    for result in results:
        publication_types = {
            value.strip().lower() for value in result.get("pubType", "").split(";")
        }
        if (
            result.get("isOpenAccess") != "Y"
            or not result.get("pmcid")
            or publication_types.intersection(EXCLUDED_PUBLICATION_TYPES)
        ):
            continue
        selected.append(result)
        if len(selected) == limit:
            return selected
    return selected


def download_open_access_articles(
    query: str, output_dir: Path, limit: int = 20
) -> Path:
    """Download open-access PMC JATS XML and write an auditable JSONL manifest."""
    if limit < 1:
        raise ValueError("limit must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    params = urllib.parse.urlencode(
        {
            "query": f"OPEN_ACCESS:Y AND HAS_FT:Y AND SRC:PMC AND ({query})",
            "format": "json",
            "pageSize": max(limit * 5, 100),
        }
    )
    with urllib.request.urlopen(
        f"{EUROPE_PMC_SEARCH}?{params}", timeout=30
    ) as response:
        payload = json.load(response)
    candidates = select_open_access_results(
        payload["resultList"]["result"], len(payload["resultList"]["result"])
    )
    if len(candidates) < limit:
        raise RuntimeError(
            f"only found {len(candidates)} eligible open-access research articles"
        )

    manifest_path = output_dir / "manifest.jsonl"
    skipped_path = output_dir / "skipped.jsonl"
    accepted = 0
    with (
        manifest_path.open("w", encoding="utf-8") as manifest,
        skipped_path.open("w", encoding="utf-8") as skipped,
    ):
        for result in candidates:
            if accepted == limit:
                break
            pmcid = result["pmcid"]
            source_url = EUROPE_PMC_FULL_TEXT.format(pmcid=pmcid)
            destination = output_dir / f"{pmcid}.xml"
            try:
                request = urllib.request.Request(
                    source_url, headers={"User-Agent": "lab-agent-learning/0.1"}
                )
                with (
                    urllib.request.urlopen(request, timeout=60) as response,
                    destination.open("wb") as target,
                ):
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        target.write(block)
                parsed = parse_jats_xml(destination)
            except (OSError, ValueError, urllib.error.URLError) as error:
                skipped.write(
                    json.dumps(
                        {"pmcid": pmcid, "source_url": source_url, "reason": str(error)},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                continue
            manifest.write(
                json.dumps(
                    {
                        "pmcid": pmcid,
                        "pmid": result.get("pmid"),
                        "doi": result.get("doi"),
                        "title": parsed.title,
                        "journal": result.get("journalTitle"),
                        "publication_year": result.get("pubYear"),
                        "license": parsed.license_text,
                        "source_url": source_url,
                        "response_sha256": sha256_file(destination),
                        "path": str(destination),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            accepted += 1
    if accepted < limit:
        raise RuntimeError(
            f"downloaded {accepted} usable open-access research articles, expected {limit}; "
            f"see {skipped_path}"
        )
    return manifest_path


def ingest_jats_xml(
    connection: Any,
    source: Path,
    project_id: uuid.UUID,
    allowed_role_names: Iterable[str],
    classification: str,
) -> tuple[uuid.UUID, bool]:
    """Insert one authorized JATS article and chunks. Returns (document_id, created)."""
    if source.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported input type: {source.suffix}")
    if source.suffix.lower() != ".xml":
        raise ValueError(
            "this initial importer supports JATS XML; add a deterministic extractor for other types"
        )
    parsed = parse_jats_xml(source)
    file_hash = sha256_file(source)
    allowed_roles = tuple(allowed_role_names)
    if not allowed_roles:
        raise ValueError("at least one allowed role is required")

    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
        if cursor.fetchone() is None:
            raise ValueError("project does not exist")
        cursor.execute(
            "SELECT id FROM documents WHERE project_id = %s AND sha256 = %s",
            (project_id, file_hash),
        )
        existing = cursor.fetchone()
        if existing:
            return existing[0], False
        cursor.execute(
            "SELECT id, name FROM roles WHERE name = ANY(%s)", (list(allowed_roles),)
        )
        role_ids = {name: role_id for role_id, name in cursor.fetchall()}
        missing_roles = set(allowed_roles).difference(role_ids)
        if missing_roles:
            raise ValueError(f"unknown roles: {', '.join(sorted(missing_roles))}")

        document_id = uuid.uuid4()
        cursor.execute(
            """
            INSERT INTO documents (id, project_id, type, title, version, effective_at, status, object_key, sha256)
            VALUES (%s, %s, 'article', %s, %s, CURRENT_TIMESTAMP, 'effective', %s, %s)
            """,
            (
                document_id,
                project_id,
                parsed.title,
                file_hash[:12],
                str(source),
                file_hash,
            ),
        )
        for role_id in role_ids.values():
            cursor.execute(
                """
                INSERT INTO document_acl (id, document_id, principal_type, principal_id, permission)
                VALUES (%s, %s, 'role', %s, 'read')
                """,
                (uuid.uuid4(), document_id, role_id),
            )
        for ordinal, chunk in enumerate(chunk_sections(parsed.sections)):
            cursor.execute(
                """
                INSERT INTO chunks (id, document_id, ordinal, text, tsv, embedding, metadata)
                VALUES (%s, %s, %s, %s, to_tsvector('english', %s), %s, %s)
                """,
                (
                    uuid.uuid4(),
                    document_id,
                    ordinal,
                    chunk["text"],
                    chunk["text"],
                    Jsonb(hash_embedding(chunk["text"])),
                    Jsonb(
                        {
                            **chunk["metadata"],
                            "classification": classification,
                            "embedding_model": "hash-v1",
                        }
                    ),
                ),
            )
    return document_id, True
