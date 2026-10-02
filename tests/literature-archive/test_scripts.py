from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import skill_contract

SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "literature-archive"
SCRIPT = SKILL_ROOT / "scripts" / "literature_archive.py"
spec = importlib.util.spec_from_file_location("literature_archive", SCRIPT)
archive = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = archive
spec.loader.exec_module(archive)

CliHelpTests = skill_contract.cli.help_test_case(SKILL_ROOT)


class ArchiveTests(unittest.TestCase):
    def config(self, directory: Path) -> archive.Config:
        return archive.Config(directory / "archive.sqlite3", directory / "pdfs", ("openalex", "europepmc"), ({"id": "topic", "query": "test", "max_results_per_source": 2},), False, 0, 1024 * 1024, 10)

    def test_config_rejects_unbounded_query_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps({"database": "a.sqlite3", "pdf_directory": "pdfs", "queries": [{"id": "x", "query": "y", "max_results_per_source": 201}]}))
            with self.assertRaisesRegex(ValueError, "max_results"):
                archive.load_config(path)

    def test_dry_run_makes_no_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = archive.run_archive(self.config(directory), dry_run=True)
            self.assertTrue(result["dry_run"])
            self.assertFalse((directory / "archive.sqlite3").exists())

    def test_deduplicates_identifiers_across_sources(self) -> None:
        openalex = [{"source": "openalex", "source_id": "W1", "doi": "10.1/example", "pmid": "", "pmcid": "", "arxiv_id": "", "title": "Paper", "year": "2025", "journal": "Journal", "abstract": "", "landing_url": "", "oa_pdf_url": "", "oa_license": ""}]
        europepmc = [{"source": "europepmc", "source_id": "MED/1", "doi": "10.1/example", "pmid": "1", "pmcid": "", "arxiv_id": "", "title": "Paper revised", "year": "2025", "journal": "Journal", "abstract": "abstract", "landing_url": "", "oa_pdf_url": "", "oa_license": ""}]
        with tempfile.TemporaryDirectory() as temporary, patch.object(archive, "openalex_records", return_value=(1, openalex)), patch.object(archive, "europepmc_records", return_value=(1, europepmc)):
            config = self.config(Path(temporary))
            summary = archive.run_archive(config)
            self.assertEqual(summary["accepted"], 1)
            self.assertEqual(summary["duplicates"], 1)
            db = sqlite3.connect(config.database)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM papers").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT abstract FROM papers").fetchone()[0], "abstract")
            db.close()

    def test_rejects_non_pdf_response_and_keeps_no_file(self) -> None:
        class Response:
            headers = type("Headers", (), {"get_content_type": lambda self: "text/html"})()
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, _size=-1): return b"<html>not a paper</html>"
        with tempfile.TemporaryDirectory() as temporary, patch.object(archive, "urlopen", return_value=Response()):
            destination = Path(temporary) / "paper.pdf"
            with self.assertRaisesRegex(RuntimeError, "content type"):
                archive.download_pdf("https://example.org/file", destination, 1024, 10)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
