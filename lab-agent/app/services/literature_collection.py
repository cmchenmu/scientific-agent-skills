"""Durable collection of open-access PMC articles into a local ACL archive."""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from app.services.ingestion import (
    EUROPE_PMC_FULL_TEXT,
    EUROPE_PMC_SEARCH,
    parse_jats_xml,
    select_open_access_results,
    sha256_file,
)
from app.services.local_archive import LocalArchive


class CollectionError(ValueError):
    """Raised for invalid collection requests and task visibility errors."""


class LiteratureCollectionService:
    """Stores task state before downloading, so a browser can poll progress safely."""

    def __init__(self, root: Path, archive: LocalArchive) -> None:
        self.root = root
        self.database_path = root / "literature_collections.sqlite3"
        self.archive = archive

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS literature_collections (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    domain TEXT NOT NULL, requested_count INTEGER NOT NULL, state TEXT NOT NULL,
                    discovered_count INTEGER NOT NULL DEFAULT 0, imported_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def create(self, user_id: str, project_id: str, domain: str, count: int) -> str:
        if not domain.strip() or len(domain) > 500:
            raise CollectionError("domain description must contain 1 to 500 characters")
        if not 50 <= count <= 100:
            raise CollectionError("article count must be between 50 and 100")
        self.initialize()
        task_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO literature_collections
                (id, user_id, project_id, domain, requested_count, state)
                VALUES (?, ?, ?, ?, ?, 'queued')""",
                (task_id, user_id, project_id, domain.strip(), count),
            )
        return task_id

    def get(self, task_id: str, user_id: str, *, is_admin: bool = False) -> dict[str, Any]:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM literature_collections WHERE id = ?", (task_id,)
            ).fetchone()
        if not row or (row["user_id"] != user_id and not is_admin):
            raise CollectionError("collection is not visible to this user")
        return dict(row)

    def list_for_user(self, user_id: str, *, is_admin: bool = False) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            if is_admin:
                rows = connection.execute(
                    "SELECT * FROM literature_collections ORDER BY created_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM literature_collections WHERE user_id = ?
                       ORDER BY created_at DESC""",
                    (user_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def run(self, task_id: str, allowed_roles: list[str]) -> None:
        """Download and ingest a queued task. Safe to run in a FastAPI background task."""
        self.initialize()
        task = self._task(task_id)
        if task["state"] not in {"queued", "failed"}:
            return
        self._update(task_id, state="running", error=None)
        task_dir = self.root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        try:
            candidates = search_open_access_articles(task["domain"], task["requested_count"])
            if len(candidates) < task["requested_count"]:
                raise CollectionError(
                    f"only found {len(candidates)} eligible open-access PMC articles"
                )
            self._update(task_id, discovered_count=len(candidates))
            manifest_path = task_dir / "manifest.jsonl"
            with manifest_path.open("w", encoding="utf-8") as manifest:
                for candidate in candidates:
                    pmcid = candidate["pmcid"]
                    source = task_dir / f"{pmcid}.xml"
                    source_url = EUROPE_PMC_FULL_TEXT.format(pmcid=pmcid)
                    try:
                        download_full_text_xml(source_url, source)
                        parsed = parse_jats_xml(source)
                        result = self.archive.ingest(
                            source,
                            task["project_id"],
                            allowed_roles,
                            "open-access-literature",
                        )
                        self._increment(task_id, "duplicate_count" if result.state == "duplicate" else "imported_count")
                        record = {
                            "pmcid": pmcid,
                            "pmid": candidate.get("pmid"),
                            "doi": candidate.get("doi"),
                            "title": parsed.title,
                            "license": parsed.license_text,
                            "source_url": source_url,
                            "sha256": sha256_file(source),
                            "state": result.state,
                        }
                    except Exception as error:  # noqa: BLE001 - each article must not stop the batch.
                        self._increment(task_id, "failed_count")
                        record = {"pmcid": pmcid, "source_url": source_url, "state": "failed", "error": str(error)[:500]}
                    manifest.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._update(task_id, state="completed")
        except Exception as error:  # noqa: BLE001 - persist every task-level failure.
            self._update(task_id, state="failed", error=str(error)[:500])

    def _task(self, task_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM literature_collections WHERE id = ?", (task_id,)
            ).fetchone()
        if not row:
            raise CollectionError("unknown collection task")
        return row

    def _update(self, task_id: str, **values: Any) -> None:
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE literature_collections SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*values.values(), task_id),
            )

    def _increment(self, task_id: str, column: str) -> None:
        if column not in {"imported_count", "duplicate_count", "failed_count"}:
            raise ValueError("unsupported collection counter")
        with self._connect() as connection:
            connection.execute(
                f"UPDATE literature_collections SET {column} = {column} + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (task_id,),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def search_open_access_articles(domain: str, count: int) -> list[dict[str, Any]]:
    """Use a fixed source filter; user text never controls a URL or arbitrary host."""
    query = urllib.parse.urlencode(
        {
            "query": f"OPEN_ACCESS:Y AND SRC:PMC AND ({domain})",
            "format": "json",
            "pageSize": max(count * 3, 150),
        }
    )
    request = urllib.request.Request(
        f"{EUROPE_PMC_SEARCH}?{query}", headers={"User-Agent": "lab-agent-learning/0.2"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return select_open_access_results(payload["resultList"]["result"], count)


def download_full_text_xml(source_url: str, destination: Path) -> None:
    request = urllib.request.Request(source_url, headers={"User-Agent": "lab-agent-learning/0.2"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as target:
        while block := response.read(1024 * 1024):
            target.write(block)
