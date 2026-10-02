#!/usr/bin/env python3
"""Maintain a bounded, auditable local archive of open scholarly literature.

The script intentionally uses only the standard library. It queries OpenAlex
and Europe PMC for metadata, resolves only stated OA PDF URLs, and stores data
in a local SQLite database. It is a collection tool, not a paywall bypasser.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

OPENALEX_URL = "https://api.openalex.org/works"
EUROPE_PMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
UNPAYWALL_URL = "https://api.unpaywall.org/v2/"
USER_AGENT = "scientific-agent-skills-literature-archive/1.0"
VALID_SOURCES = frozenset({"openalex", "europepmc"})
DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)\s*", re.I)
ARXIV_ID = re.compile(r"(?:arxiv:)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)", re.I)


@dataclass(frozen=True)
class Config:
    database: Path
    pdf_directory: Path
    sources: tuple[str, ...]
    queries: tuple[dict[str, Any], ...]
    download_enabled: bool
    max_pdfs_per_run: int
    max_pdf_bytes: int
    timeout_seconds: int


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    return DOI_PREFIX.sub("", value.strip()).rstrip(" .;,)").lower()


def normalize_arxiv(value: str | None) -> str:
    if not value:
        return ""
    match = ARXIV_ID.search(value.strip())
    return match.group(1).lower() if match else ""


def text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"configuration file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"configuration is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("configuration must be a JSON object")

    def required_path(name: str) -> Path:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"configuration.{name} must be a non-empty path string")
        return Path(value).expanduser()

    sources = raw.get("sources", ["openalex", "europepmc"])
    if (
        not isinstance(sources, list)
        or not sources
        or any(not isinstance(source, str) or source not in VALID_SOURCES for source in sources)
        or len(set(sources)) != len(sources)
    ):
        raise ValueError("configuration.sources must be a non-empty list of openalex and/or europepmc")
    queries = raw.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("configuration.queries must be a non-empty list")
    seen_ids: set[str] = set()
    for item in queries:
        if not isinstance(item, dict) or not text(item.get("id")) or not text(item.get("query")):
            raise ValueError("each query needs non-empty string id and query fields")
        identifier = text(item["id"])
        if identifier in seen_ids:
            raise ValueError(f"query id is repeated: {identifier}")
        seen_ids.add(identifier)
        if not isinstance(item.get("max_results_per_source", 50), int) or not 1 <= item.get("max_results_per_source", 50) <= 200:
            raise ValueError("query.max_results_per_source must be an integer from 1 to 200")
        if "from_year" in item and (not isinstance(item["from_year"], int) or not 1800 <= item["from_year"] <= 2100):
            raise ValueError("query.from_year must be a year from 1800 to 2100")
    download = raw.get("download", {})
    if not isinstance(download, dict):
        raise ValueError("configuration.download must be an object")
    enabled = download.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("download.enabled must be true or false")
    limits = {
        "max_pdfs_per_run": (download.get("max_pdfs_per_run", 10), 0, 100),
        "max_pdf_bytes": (download.get("max_pdf_bytes", 52_428_800), 1_024, 524_288_000),
        "timeout_seconds": (download.get("timeout_seconds", 30), 1, 300),
    }
    for name, (value, minimum, maximum) in limits.items():
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"download.{name} must be an integer from {minimum} to {maximum}")
    return Config(
        database=required_path("database"),
        pdf_directory=required_path("pdf_directory"),
        sources=tuple(sources), queries=tuple(queries), download_enabled=enabled,
        max_pdfs_per_run=limits["max_pdfs_per_run"][0], max_pdf_bytes=limits["max_pdf_bytes"][0],
        timeout_seconds=limits["timeout_seconds"][0],
    )


def request_json(url: str, params: dict[str, str], timeout: int) -> dict[str, Any]:
    request = Request(f"{url}?{urlencode(params)}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"request failed for {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"request returned a non-object JSON payload: {url}")
    return payload


def paper_key(record: dict[str, str]) -> str:
    for field in ("doi", "pmid", "pmcid", "arxiv_id", "source_id"):
        value = record.get(field, "")
        if value:
            return f"{field}:{value.lower()}"
    digest = hashlib.sha256((record.get("title", "") + "|" + record.get("year", "")).lower().encode()).hexdigest()
    return f"title:{digest}"


def openalex_records(query: dict[str, Any], timeout: int) -> tuple[int, list[dict[str, str]]]:
    limit = query.get("max_results_per_source", 50)
    params = {"search": query["query"], "per-page": str(limit), "cursor": "*"}
    if "from_year" in query:
        params["filter"] = f"from_publication_date:{query['from_year']}-01-01"
    payload = request_json(OPENALEX_URL, params, timeout)
    records = []
    for work in payload.get("results", []):
        if not isinstance(work, dict):
            continue
        location = work.get("best_oa_location") or work.get("primary_location") or {}
        if not isinstance(location, dict):
            location = {}
        records.append({
            "source": "openalex", "source_id": text(work.get("id")), "doi": normalize_doi(text(work.get("doi"))),
            "pmid": normalize_doi(text((work.get("ids") or {}).get("pmid"))), "pmcid": text((work.get("ids") or {}).get("pmcid")).upper(),
            "arxiv_id": normalize_arxiv(text((work.get("ids") or {}).get("arxiv"))), "title": text(work.get("title")),
            "year": str(work.get("publication_year") or ""), "journal": text(((location.get("source") or {}).get("display_name"))),
            "abstract": "", "landing_url": text(location.get("landing_page_url")), "oa_pdf_url": text(location.get("pdf_url")),
            "oa_license": text(location.get("license")),
        })
    return int((payload.get("meta") or {}).get("count") or 0), records


def europepmc_records(query: dict[str, Any], timeout: int) -> tuple[int, list[dict[str, str]]]:
    limit = query.get("max_results_per_source", 50)
    term = query["query"]
    if "from_year" in query:
        term = f"({term}) AND FIRST_PDATE:[{query['from_year']}-01-01 TO 2100-12-31]"
    payload = request_json(EUROPE_PMC_URL, {"query": term, "format": "json", "pageSize": str(limit), "resultType": "core"}, timeout)
    records = []
    for item in payload.get("resultList", {}).get("result", []):
        if not isinstance(item, dict):
            continue
        author_string = text(item.get("authorString"))
        records.append({
            "source": "europepmc", "source_id": f"{text(item.get('source'))}/{text(item.get('id'))}", "doi": normalize_doi(text(item.get("doi"))),
            "pmid": text(item.get("pmid")), "pmcid": text(item.get("pmcid")).upper(), "arxiv_id": normalize_arxiv(text(item.get("arxivId"))),
            "title": text(item.get("title")), "year": text(item.get("pubYear")), "journal": text(item.get("journalTitle")),
            "abstract": text(item.get("abstractText")), "landing_url": text(item.get("journalInfo")), "oa_pdf_url": "", "oa_license": "",
        })
        if author_string:
            records[-1]["authors"] = author_string
    return int(payload.get("hitCount") or 0), records


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, summary_json TEXT);
CREATE TABLE IF NOT EXISTS queries (run_id INTEGER NOT NULL, query_id TEXT NOT NULL, source TEXT NOT NULL, reported_count INTEGER, retrieved_count INTEGER NOT NULL, error TEXT, FOREIGN KEY(run_id) REFERENCES runs(id));
CREATE TABLE IF NOT EXISTS papers (
 id INTEGER PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE, doi TEXT, pmid TEXT, pmcid TEXT, arxiv_id TEXT, title TEXT NOT NULL,
 year TEXT, journal TEXT, abstract TEXT, landing_url TEXT, oa_pdf_url TEXT, oa_license TEXT, pdf_path TEXT, pdf_sha256 TEXT,
 download_status TEXT NOT NULL DEFAULT 'not_requested', download_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_sources (paper_id INTEGER NOT NULL, source TEXT NOT NULL, source_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, UNIQUE(source, source_id), FOREIGN KEY(paper_id) REFERENCES papers(id));
"""


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def find_existing(db: sqlite3.Connection, record: dict[str, str]) -> int | None:
    clauses, values = [], []
    for field in ("doi", "pmid", "pmcid", "arxiv_id"):
        if record.get(field):
            clauses.append(f"{field} = ?")
            values.append(record[field])
    if not clauses:
        return None
    row = db.execute(f"SELECT id FROM papers WHERE {' OR '.join(clauses)} LIMIT 1", values).fetchone()
    return int(row[0]) if row else None


def upsert_paper(db: sqlite3.Connection, record: dict[str, str]) -> tuple[int, bool]:
    now = utc_now()
    existing = find_existing(db, record)
    if existing is None:
        cursor = db.execute(
            """INSERT INTO papers (canonical_key, doi, pmid, pmcid, arxiv_id, title, year, journal, abstract, landing_url, oa_pdf_url, oa_license, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (paper_key(record), record.get("doi"), record.get("pmid"), record.get("pmcid"), record.get("arxiv_id"), record.get("title") or "Untitled", record.get("year"), record.get("journal"), record.get("abstract"), record.get("landing_url"), record.get("oa_pdf_url"), record.get("oa_license"), now, now),
        )
        paper_id, created = int(cursor.lastrowid), True
    else:
        paper_id, created = existing, False
        db.execute(
            """UPDATE papers SET doi=COALESCE(NULLIF(?, ''), doi), pmid=COALESCE(NULLIF(?, ''), pmid), pmcid=COALESCE(NULLIF(?, ''), pmcid), arxiv_id=COALESCE(NULLIF(?, ''), arxiv_id),
               title=COALESCE(NULLIF(?, ''), title), year=COALESCE(NULLIF(?, ''), year), journal=COALESCE(NULLIF(?, ''), journal), abstract=COALESCE(NULLIF(?, ''), abstract),
               landing_url=COALESCE(NULLIF(?, ''), landing_url), oa_pdf_url=COALESCE(NULLIF(?, ''), oa_pdf_url), oa_license=COALESCE(NULLIF(?, ''), oa_license), updated_at=? WHERE id=?""",
            (record.get("doi"), record.get("pmid"), record.get("pmcid"), record.get("arxiv_id"), record.get("title"), record.get("year"), record.get("journal"), record.get("abstract"), record.get("landing_url"), record.get("oa_pdf_url"), record.get("oa_license"), now, paper_id),
        )
    db.execute("INSERT INTO paper_sources (paper_id, source, source_id, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(source, source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at", (paper_id, record["source"], record["source_id"], now, now))
    return paper_id, created


def unpaywall_pdf_url(doi: str, timeout: int) -> tuple[str, str]:
    email = os.getenv("UNPAYWALL_EMAIL", "").strip()
    if not doi or not email:
        return "", ""
    payload = request_json(f"{UNPAYWALL_URL}{quote(doi, safe='/')}", {"email": email}, timeout)
    location = payload.get("best_oa_location") or {}
    return text(location.get("url_for_pdf")), text(location.get("license"))


def download_pdf(url: str, destination: Path, max_bytes: int, timeout: int) -> str:
    if not url.startswith(("https://", "http://")):
        raise RuntimeError("OA PDF URL is not HTTP(S)")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"})
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type().lower()
            if content_type not in {"application/pdf", "application/octet-stream"}:
                raise RuntimeError(f"OA URL did not return a PDF content type: {content_type}")
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
                temp_path = Path(temporary.name)
                size = 0
                while chunk := response.read(65_536):
                    size += len(chunk)
                    if size > max_bytes:
                        raise RuntimeError(f"PDF exceeds configured size limit ({max_bytes} bytes)")
                    temporary.write(chunk)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"PDF download failed: {exc}") from exc
    try:
        data = temp_path.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise RuntimeError("OA URL response did not have a PDF file signature")
        digest = hashlib.sha256(data).hexdigest()
        os.replace(temp_path, destination)
        return digest
    finally:
        temp_path = locals().get("temp_path")
        if isinstance(temp_path, Path):
            temp_path.unlink(missing_ok=True)


def run_archive(config: Config, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"dry_run": True, "sources": list(config.sources), "queries": [q["id"] for q in config.queries], "database": str(config.database), "pdf_directory": str(config.pdf_directory)}
    lock_path = config.database.with_suffix(config.database.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another literature archive run already holds the lock") from exc
        db = init_db(config.database)
        started = utc_now()
        run_id = int(db.execute("INSERT INTO runs (started_at) VALUES (?)", (started,)).lastrowid)
        summary: dict[str, Any] = {"run_id": run_id, "started_at": started, "accepted": 0, "duplicates": 0, "downloaded": 0, "source_errors": []}
        download_budget = config.max_pdfs_per_run
        try:
            for query in config.queries:
                for source in config.sources:
                    try:
                        reported, records = (openalex_records(query, config.timeout_seconds) if source == "openalex" else europepmc_records(query, config.timeout_seconds))
                        db.execute("INSERT INTO queries (run_id, query_id, source, reported_count, retrieved_count) VALUES (?, ?, ?, ?, ?)", (run_id, query["id"], source, reported, len(records)))
                    except RuntimeError as exc:
                        summary["source_errors"].append({"query": query["id"], "source": source, "error": str(exc)})
                        db.execute("INSERT INTO queries (run_id, query_id, source, retrieved_count, error) VALUES (?, ?, ?, 0, ?)", (run_id, query["id"], source, str(exc)))
                        continue
                    for record in records:
                        paper_id, created = upsert_paper(db, record)
                        summary["accepted" if created else "duplicates"] += 1
                        if not config.download_enabled or download_budget <= 0:
                            continue
                        row = db.execute("SELECT pdf_path, oa_pdf_url, doi FROM papers WHERE id=?", (paper_id,)).fetchone()
                        if row is None or row[0]:
                            continue
                        pdf_url, license_name = text(row[1]), ""
                        if not pdf_url:
                            try:
                                pdf_url, license_name = unpaywall_pdf_url(text(row[2]), config.timeout_seconds)
                            except RuntimeError as exc:
                                db.execute("UPDATE papers SET download_status='resolution_failed', download_error=?, updated_at=? WHERE id=?", (str(exc), utc_now(), paper_id))
                                continue
                        if not pdf_url:
                            db.execute("UPDATE papers SET download_status='not_available', updated_at=? WHERE id=?", (utc_now(), paper_id))
                            continue
                        destination = config.pdf_directory / f"paper-{paper_id}.pdf"
                        try:
                            digest = download_pdf(pdf_url, destination, config.max_pdf_bytes, config.timeout_seconds)
                        except RuntimeError as exc:
                            db.execute("UPDATE papers SET download_status='failed', download_error=?, updated_at=? WHERE id=?", (str(exc), utc_now(), paper_id))
                            continue
                        db.execute("UPDATE papers SET oa_pdf_url=?, oa_license=COALESCE(NULLIF(?, ''), oa_license), pdf_path=?, pdf_sha256=?, download_status='downloaded', download_error=NULL, updated_at=? WHERE id=?", (pdf_url, license_name, str(destination), digest, utc_now(), paper_id))
                        download_budget -= 1
                        summary["downloaded"] += 1
            if summary["source_errors"] and summary["accepted"] + summary["duplicates"] == 0:
                raise RuntimeError("every configured source failed; see source_errors in the run summary")
            summary["finished_at"] = utc_now()
            db.execute("UPDATE runs SET finished_at=?, summary_json=? WHERE id=?", (summary["finished_at"], json.dumps(summary, ensure_ascii=False), run_id))
            db.commit()
            return summary
        finally:
            db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="path to a JSON archive configuration")
    parser.add_argument("--dry-run", action="store_true", help="validate configuration and print the plan without network or writes")
    args = parser.parse_args(argv)
    try:
        summary = run_archive(load_config(args.config), dry_run=args.dry_run)
    except (ValueError, RuntimeError) as exc:
        print(f"literature archive error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
