"""Self-contained, deterministic document archive for offline development."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from pypdf import PdfReader

from app.services.ingestion import (
    chunk_sections,
    hash_embedding,
    parse_jats_xml,
    sha256_file,
)

SUPPORTED_SUFFIXES = {".xml", ".html", ".htm", ".pdf", ".docx"}
MAX_SOURCE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class LocalIngestResult:
    document_id: str
    state: str
    chunk_count: int


class _HtmlSections(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._heading = "Body"
        self._active: str | None = None
        self._buffer: list[str] = []
        self.sections: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"title", "h1", "h2", "h3", "p", "li"}:
            self._active = tag
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._active:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != self._active:
            return
        value = " ".join("".join(self._buffer).split())
        if tag == "title" and value:
            self.title = value
        elif tag in {"h1", "h2", "h3"} and value:
            self._heading = value
        elif tag in {"p", "li"} and value:
            self.sections.append((self._heading, value))
        self._active = None


class LocalArchive:
    """A SQLite-backed archive with local object storage and ACL enforcement."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.objects = root / "objects"
        self.database_path = root / "archive.sqlite3"

    def initialize(self) -> None:
        self.objects.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, display_name TEXT NOT NULL, active INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    role_name TEXT NOT NULL, UNIQUE(user_id, project_id, role_name)
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    type TEXT NOT NULL, title TEXT NOT NULL, version TEXT NOT NULL,
                    effective_at TEXT NOT NULL, status TEXT NOT NULL, object_key TEXT NOT NULL,
                    sha256 TEXT NOT NULL, source_format TEXT NOT NULL, metadata TEXT NOT NULL,
                    UNIQUE(project_id, sha256)
                );
                CREATE TABLE IF NOT EXISTS document_identifiers (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(document_id, kind),
                    UNIQUE(kind, value)
                );
                CREATE TABLE IF NOT EXISTS document_acl (
                    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    role_name TEXT NOT NULL, permission TEXT NOT NULL,
                    UNIQUE(document_id, role_name, permission)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL, embedding TEXT NOT NULL,
                    metadata TEXT NOT NULL, UNIQUE(document_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    action TEXT NOT NULL, resource_id TEXT, payload_redacted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingestion_errors (
                    id TEXT PRIMARY KEY, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    source_path TEXT NOT NULL, error_code TEXT NOT NULL, detail TEXT NOT NULL
                );
                """
            )

    def ingest(
        self,
        source: Path,
        project_name: str,
        allowed_roles: Iterable[str],
        classification: str,
    ) -> LocalIngestResult:
        self.initialize()
        try:
            self._preflight(source)
            title, sections, identifiers = self._extract(source)
            chunks = chunk_sections(sections)
            if not chunks:
                raise ValueError("no readable text chunks")
            roles = sorted(set(allowed_roles))
            if not roles:
                raise ValueError("at least one allowed role is required")
            file_hash = sha256_file(source)
            with self._connect() as connection:
                project_id = self._project(connection, project_name)
                duplicate = self._find_duplicate(
                    connection, project_id, file_hash, identifiers
                )
                if duplicate:
                    return LocalIngestResult(duplicate, "duplicate", 0)
                object_key = self._store_object(source, file_hash)
                document_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO documents VALUES (?, ?, 'article', ?, ?, CURRENT_TIMESTAMP,
                       'effective', ?, ?, ?, ?)""",
                    (
                        document_id,
                        project_id,
                        title,
                        file_hash[:12],
                        object_key,
                        file_hash,
                        source.suffix.lower(),
                        json.dumps({"classification": classification}),
                    ),
                )
                for kind, value in identifiers.items():
                    connection.execute(
                        "INSERT INTO document_identifiers VALUES (?, ?, ?)",
                        (document_id, kind, value),
                    )
                for role in roles:
                    connection.execute(
                        "INSERT INTO document_acl VALUES (?, ?, 'read')",
                        (document_id, role),
                    )
                for ordinal, chunk in enumerate(chunks):
                    metadata = {
                        **chunk["metadata"],
                        "classification": classification,
                        "embedding_model": "hash-v1",
                    }
                    connection.execute(
                        "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid4()),
                            document_id,
                            ordinal,
                            chunk["text"],
                            json.dumps(hash_embedding(chunk["text"])),
                            json.dumps(metadata),
                        ),
                    )
                connection.execute(
                    "INSERT INTO audit_events (id, action, resource_id, payload_redacted) VALUES (?, 'document.ingested', ?, ?)",
                    (
                        str(uuid.uuid4()),
                        document_id,
                        json.dumps({"sha256": file_hash, "chunk_count": len(chunks)}),
                    ),
                )
            return LocalIngestResult(document_id, "created", len(chunks))
        except Exception as error:
            self._record_error(source, error)
            raise

    def list_documents(self, project_name: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """SELECT d.id, d.title, d.status, d.sha256 FROM documents d
                   JOIN projects p ON p.id = d.project_id WHERE p.name = ? ORDER BY d.title""",
                    (project_name,),
                )
            ]

    def grant_role(
        self,
        user_id: str,
        project_name: str,
        role_name: str,
        display_name: str | None = None,
    ) -> None:
        """Create/update a local user and grant a project-scoped role."""
        self.initialize()
        with self._connect() as connection:
            project_id = self._project(connection, project_name)
            connection.execute(
                """INSERT INTO users (id, display_name, active) VALUES (?, ?, 1)
                   ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name""",
                (user_id, display_name or user_id),
            )
            connection.execute(
                "INSERT OR IGNORE INTO user_roles VALUES (?, ?, ?)",
                (user_id, project_id, role_name),
            )

    def deactivate_user(self, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))

    def user_context(self, user_id: str) -> dict[str, Any] | None:
        """Return server-owned local identity data for the development API."""
        self.initialize()
        with self._connect() as connection:
            user = connection.execute(
                "SELECT id, display_name, active FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not user or not user["active"]:
                return None
            rows = connection.execute(
                """SELECT p.name, ur.role_name FROM user_roles ur
                   JOIN projects p ON p.id = ur.project_id WHERE ur.user_id = ?
                   ORDER BY p.name, ur.role_name""",
                (user_id,),
            ).fetchall()
        project_roles: dict[str, list[str]] = {}
        for row in rows:
            project_roles.setdefault(row["name"], []).append(row["role_name"])
        return {
            "id": user["id"],
            "display_name": user["display_name"],
            "active": bool(user["active"]),
            "project_roles": project_roles,
        }

    def project_role_names(self, project_name: str) -> list[str]:
        """Return roles already assigned in a project for inherited document ACLs."""
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT ur.role_name FROM user_roles ur
                   JOIN projects p ON p.id = ur.project_id WHERE p.name = ?
                   ORDER BY ur.role_name""",
                (project_name,),
            ).fetchall()
        return [row["role_name"] for row in rows]

    def search(
        self, project_name: str, roles: Iterable[str], query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return only chunks whose document ACL intersects the caller's role set."""
        role_list = sorted(set(roles))
        if not role_list or not query.strip():
            return []
        placeholders = ",".join("?" for _ in role_list)
        sql = f"""
            SELECT c.id, d.title, c.text, c.metadata FROM chunks c
            JOIN documents d ON d.id = c.document_id JOIN projects p ON p.id = d.project_id
            JOIN document_acl acl ON acl.document_id = d.id
            WHERE p.name = ? AND acl.permission = 'read' AND acl.role_name IN ({placeholders})
              AND c.text LIKE ? ORDER BY d.title, c.ordinal LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(
                sql, (project_name, *role_list, f"%{query}%", limit)
            ).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]

    def search_for_user(
        self, user_id: str, project_name: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Hybrid search with authorization embedded in the candidate SQL query."""
        if not query.strip() or limit < 1:
            return []
        terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", query.lower())
        vector = hash_embedding(query)
        sql = """
            SELECT DISTINCT c.id, d.id AS document_id, d.title, c.text, c.embedding, c.metadata
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN projects p ON p.id = d.project_id
            JOIN document_acl acl ON acl.document_id = d.id AND acl.permission = 'read'
            JOIN user_roles ur ON ur.project_id = p.id AND ur.role_name = acl.role_name
            JOIN users u ON u.id = ur.user_id AND u.active = 1
            WHERE u.id = ? AND p.name = ? AND d.status = 'effective'
        """
        with self._connect() as connection:
            rows = connection.execute(sql, (user_id, project_name)).fetchall()
        ranked: list[dict[str, Any]] = []
        for row in rows:
            text = row["text"]
            keyword_score = sum(text.lower().count(term) for term in terms) / max(
                len(terms), 1
            )
            stored_vector = json.loads(row["embedding"])
            vector_score = sum(
                left * right for left, right in zip(vector, stored_vector)
            )
            score = keyword_score + vector_score
            if score > 0:
                ranked.append(
                    {
                        "id": row["id"],
                        "document_id": row["document_id"],
                        "title": row["title"],
                        "text": text,
                        "metadata": json.loads(row["metadata"]),
                        "score": round(score, 6),
                    }
                )
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]

    def list_errors(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM ingestion_errors ORDER BY occurred_at DESC"
                )
            ]

    def document_identifiers(self, document_id: str) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, value FROM document_identifiers WHERE document_id = ?",
                (document_id,),
            ).fetchall()
        return {row["kind"]: row["value"] for row in rows}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _project(self, connection: sqlite3.Connection, name: str) -> str:
        row = connection.execute(
            "SELECT id FROM projects WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return row["id"]
        project_id = str(uuid.uuid4())
        connection.execute("INSERT INTO projects VALUES (?, ?)", (project_id, name))
        return project_id

    def _find_duplicate(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        file_hash: str,
        identifiers: dict[str, str],
    ) -> str | None:
        row = connection.execute(
            "SELECT id FROM documents WHERE project_id = ? AND sha256 = ?",
            (project_id, file_hash),
        ).fetchone()
        if row:
            return row["id"]
        for kind, value in identifiers.items():
            row = connection.execute(
                "SELECT document_id FROM document_identifiers WHERE kind = ? AND value = ?",
                (kind, value),
            ).fetchone()
            if row:
                return row["document_id"]
        return None

    def _store_object(self, source: Path, file_hash: str) -> str:
        destination = self.objects / file_hash
        if not destination.exists():
            temporary = destination.with_suffix(".tmp")
            shutil.copyfile(source, temporary)
            temporary.replace(destination)
        return str(destination.relative_to(self.root))

    def _preflight(self, source: Path) -> None:
        if not source.is_file() or source.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError("unsupported or missing source file")
        if source.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds 50 MiB local safety limit")
        header = source.read_bytes()[:8]
        if header.startswith((b"MZ", b"\x7fELF")):
            raise ValueError("executable content is not an accepted document")

    def _extract(self, source: Path) -> tuple[str, list[Any], dict[str, str]]:
        suffix = source.suffix.lower()
        if suffix == ".xml":
            parsed = parse_jats_xml(source)
            return parsed.title, list(parsed.sections), parsed.identifiers
        if suffix in {".html", ".htm"}:
            parser = _HtmlSections()
            parser.feed(source.read_text(encoding="utf-8", errors="replace"))
            return parser.title or source.stem, self._sections(parser.sections), {}
        if suffix == ".pdf":
            reader = PdfReader(source)
            sections = [
                (f"Page {number}", page.extract_text() or "")
                for number, page in enumerate(reader.pages, 1)
            ]
            title = (
                str(reader.metadata.title)
                if reader.metadata and reader.metadata.title
                else source.stem
            )
            return title, self._sections(sections), {}
        with zipfile.ZipFile(source) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
        paragraphs = [
            " ".join("".join(node.itertext()).split())
            for node in document.iter()
            if node.tag.endswith("}p")
        ]
        return (
            source.stem,
            self._sections(
                [("Document", paragraph) for paragraph in paragraphs if paragraph]
            ),
            {},
        )

    def _sections(self, pairs: Iterable[tuple[str, str]]) -> list[Any]:
        from app.services.ingestion import Section

        return [
            Section(title, text, ordinal)
            for ordinal, (title, text) in enumerate(pairs)
            if text.strip()
        ]

    def _record_error(self, source: Path, error: Exception) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO ingestion_errors VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    str(source),
                    type(error).__name__,
                    str(error)[:500],
                ),
            )
