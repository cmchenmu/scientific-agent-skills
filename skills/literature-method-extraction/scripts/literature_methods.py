#!/usr/bin/env python3
"""Archive a topic and extract auditable data-analysis methods from JATS XML."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EUROPE_PMC_FULL_TEXT = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
USER_AGENT = "scientific-agent-skills-literature-method-extraction/1.0"
METHOD_TITLES = re.compile(r"\b(methods?|methodology|materials? and methods?|experimental procedures?)\b", re.I)
FIELD_PATTERNS = {
    "data_and_design": r"\b(dataset|cohort|participants?|samples?|cross-sectional|randomi[sz]ed|retrospective)\b",
    "preprocessing": r"\b(preprocess\w*|normaliz\w*|quality control|filter\w*|imput\w*|batch effect)\b",
    "analysis": r"\b(regression|classification|clustering|differential expression|principal component|pca|umap|model(?:ling)?|machine learning)\b",
    "statistics": r"\b(statistical|p\s*[<=>]|confidence interval|anova|t-test|wilcoxon|chi-square|multiple testing|fdr)\b",
    "validation": r"\b(validation|cross-validation|holdout|test set|replication|sensitivity analysis|external cohort)\b",
    "software": r"\b(R|Python|SPSS|SAS|Stata|MATLAB|Scanpy|Seurat|TensorFlow|PyTorch)\b",
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_archive_module():
    script = Path(__file__).resolve().parents[2] / "literature-archive" / "scripts" / "literature_archive.py"
    spec = importlib.util.spec_from_file_location("literature_archive_runtime", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load literature-archive runtime")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def methods_text(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"Europe PMC returned invalid JATS XML: {exc}") from exc
    sections = []
    for section in root.findall(".//sec"):
        title = " ".join(section.findtext("title", "").split())
        if METHOD_TITLES.search(title):
            value = " ".join(" ".join(section.itertext()).split())
            if value:
                sections.append(value)
    if not sections:
        raise RuntimeError("JATS full text did not contain a labelled Methods section")
    return "\n\n".join(sections)[:80_000]


def split_sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]


def summarize(text: str) -> dict[str, list[str]]:
    sentences = split_sentences(text)
    result: dict[str, list[str]] = {}
    for field, pattern in FIELD_PATTERNS.items():
        selected = [sentence for sentence in sentences if re.search(pattern, sentence, re.I)]
        result[field] = selected[:3]
    return result


SCHEMA = """
CREATE TABLE IF NOT EXISTS method_extractions (
 paper_id INTEGER PRIMARY KEY, source_url TEXT, methods_text TEXT, summary_json TEXT,
 status TEXT NOT NULL, error TEXT, reviewer_status TEXT NOT NULL DEFAULT 'pending',
 reviewer_note TEXT, updated_at TEXT NOT NULL,
 FOREIGN KEY(paper_id) REFERENCES papers(id)
);
"""


def fetch_jats(pmcid: str, timeout: int = 30) -> tuple[bytes, str]:
    url = EUROPE_PMC_FULL_TEXT.format(pmcid=pmcid)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read(), url
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"JATS retrieval failed: {exc}") from exc


def extract(database: Path, limit: int | None, refresh: bool) -> dict[str, int]:
    db = sqlite3.connect(database)
    db.execute(SCHEMA)
    query = """SELECT p.id, p.pmcid FROM papers p
      LEFT JOIN method_extractions m ON m.paper_id=p.id
      WHERE m.paper_id IS NULL OR ?
      ORDER BY p.updated_at DESC"""
    rows = db.execute(query, (1 if refresh else 0,)).fetchall()
    if limit is not None:
        rows = rows[:limit]
    summary = {"attempted": len(rows), "ready_for_review": 0, "unavailable": 0}
    try:
        for paper_id, pmcid in rows:
            if not pmcid:
                db.execute("""INSERT INTO method_extractions (paper_id, status, error, updated_at)
                   VALUES (?, 'unavailable', 'No PMCID: open JATS full text is unavailable', ?)
                   ON CONFLICT(paper_id) DO UPDATE SET status='unavailable', error=excluded.error, updated_at=excluded.updated_at""", (paper_id, now()))
                summary["unavailable"] += 1
                continue
            try:
                xml, source_url = fetch_jats(pmcid)
                text = methods_text(xml)
                payload = json.dumps(summarize(text), ensure_ascii=False)
                db.execute("""INSERT INTO method_extractions (paper_id, source_url, methods_text, summary_json, status, error, updated_at)
                   VALUES (?, ?, ?, ?, 'ready_for_review', NULL, ?)
                   ON CONFLICT(paper_id) DO UPDATE SET source_url=excluded.source_url, methods_text=excluded.methods_text, summary_json=excluded.summary_json, status='ready_for_review', error=NULL, updated_at=excluded.updated_at""", (paper_id, source_url, text, payload, now()))
                summary["ready_for_review"] += 1
            except RuntimeError as exc:
                db.execute("""INSERT INTO method_extractions (paper_id, status, error, updated_at)
                   VALUES (?, 'unavailable', ?, ?)
                   ON CONFLICT(paper_id) DO UPDATE SET status='unavailable', error=excluded.error, updated_at=excluded.updated_at""", (paper_id, str(exc), now()))
                summary["unavailable"] += 1
        db.commit()
        return summary
    finally:
        db.close()


def export_csv(database: Path, output: Path) -> int:
    db = sqlite3.connect(database)
    rows = db.execute("""SELECT p.id, p.title, p.year, p.doi, p.pmid, p.pmcid, m.status, m.reviewer_status, m.reviewer_note, m.source_url, m.summary_json, m.methods_text
        FROM papers p LEFT JOIN method_extractions m ON m.paper_id=p.id ORDER BY p.updated_at DESC""").fetchall()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["paper_id", "title", "year", "doi", "pmid", "pmcid", "extraction_status", "reviewer_status", "reviewer_note", "source_url", "summary_json", "methods_excerpt"])
        writer.writerows(rows)
    db.close()
    return len(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    archive = load_archive_module()
    config = archive.load_config(args.archive_config)
    archive_summary: dict[str, Any] | None = None
    if not args.skip_archive:
        archive_summary = archive.run_archive(config)
    extraction = extract(config.database, args.limit, args.refresh)
    exported = export_csv(config.database, args.output)
    return {"archive": archive_summary, "extraction": extraction, "exported_rows": exported, "output": str(args.output)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("run", help="archive papers, extract Methods sections, and export a CSV")
    command.add_argument("--archive-config", required=True, type=Path)
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--limit", type=int, default=None, help="maximum papers to fetch from the local archive")
    command.add_argument("--skip-archive", action="store_true", help="do not run new literature discovery")
    command.add_argument("--refresh", action="store_true", help="re-fetch JATS for previously extracted papers")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        result = run(args)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"literature methods error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
