from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import skill_contract

SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "literature-method-extraction"
SCRIPT = SKILL_ROOT / "scripts" / "literature_methods.py"
spec = importlib.util.spec_from_file_location("literature_methods", SCRIPT)
methods = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = methods
spec.loader.exec_module(methods)

gui_spec = importlib.util.spec_from_file_location("literature_methods_gui", SKILL_ROOT / "scripts" / "literature_methods_gui.py")
gui = importlib.util.module_from_spec(gui_spec)
assert gui_spec and gui_spec.loader
sys.modules[gui_spec.name] = gui
gui_spec.loader.exec_module(gui)

CliHelpTests = skill_contract.cli.help_test_case(SKILL_ROOT)


class MethodExtractionTests(unittest.TestCase):
    def test_extracts_and_labels_methods_section(self) -> None:
        xml = b"<article><body><sec><title>Methods</title><p>Data were normalized before analysis in R.</p><p>We used cross-validation and regression.</p></sec></body></article>"
        result = methods.methods_text(xml)
        summary = methods.summarize(result)
        self.assertIn("normalized", result)
        self.assertTrue(summary["preprocessing"])
        self.assertTrue(summary["validation"])
        self.assertTrue(summary["software"])

    def test_rejects_jats_without_methods_heading(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Methods"):
            methods.methods_text(b"<article><body><sec><title>Results</title><p>Text.</p></sec></body></article>")

    def test_persists_unavailable_and_ready_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "archive.sqlite3"
            db = sqlite3.connect(database)
            db.executescript("CREATE TABLE papers (id INTEGER PRIMARY KEY, pmcid TEXT, updated_at TEXT); INSERT INTO papers VALUES (1, 'PMC1', '2026'); INSERT INTO papers VALUES (2, 'PMC2', '2026');")
            db.close()
            xml = b"<article><body><sec><title>Materials and Methods</title><p>Quality control and clustering were performed with Python.</p></sec></body></article>"
            with patch.object(methods, "fetch_jats", side_effect=[(xml, "https://example/P1"), RuntimeError("not found")]):
                summary = methods.extract(database, None, False)
            self.assertEqual(summary, {"attempted": 2, "ready_for_review": 1, "unavailable": 1})
            db = sqlite3.connect(database)
            values = db.execute("SELECT status, reviewer_status FROM method_extractions ORDER BY paper_id").fetchall()
            self.assertEqual(values, [("ready_for_review", "pending"), ("unavailable", "pending")])
            db.close()

    def test_gui_reads_review_queue_from_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database = directory / "archive.sqlite3"
            config = directory / "archive.json"
            config.write_text('{"database": "' + str(database) + '"}', encoding="utf-8")
            db = sqlite3.connect(database)
            db.executescript("""CREATE TABLE papers (id INTEGER PRIMARY KEY, title TEXT, year TEXT, doi TEXT, pmcid TEXT, updated_at TEXT);
            CREATE TABLE method_extractions (paper_id INTEGER PRIMARY KEY, status TEXT, error TEXT, summary_json TEXT, reviewer_status TEXT);
            INSERT INTO papers VALUES (1, 'Example', '2026', '10/example', 'PMC1', '2026');
            INSERT INTO method_extractions VALUES (1, 'ready_for_review', NULL, '{"software": ["Python"]}', 'pending');""")
            db.close()
            result = gui.App(config, directory / "methods.csv").results()
            self.assertEqual(result["stats"]["论文"], 1)
            self.assertEqual(result["rows"][0]["summary"]["software"], ["Python"])


if __name__ == "__main__":
    unittest.main()
