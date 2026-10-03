import json
from pathlib import Path

import pytest

from app.services.literature_import import (
    import_open_access_literature,
    import_selected_open_access_literature,
    research_direction_query,
)
from app.services.local_archive import LocalArchive


def test_import_open_access_literature_ingests_downloaded_manifest(
    monkeypatch, tmp_path: Path
):
    source = tmp_path / "PMC1.xml"
    source.write_text(
        "<article><front><article-meta><title-group><article-title>Study</article-title>"
        "</title-group></article-meta></front><body><sec><title>Results</title>"
        "<p>Mouse cortex result.</p></sec></body></article>",
        encoding="utf-8",
    )

    def fake_download(query: str, output: Path, limit: int) -> Path:
        output.mkdir(parents=True)
        manifest = output / "manifest.jsonl"
        manifest.write_text(json.dumps({"path": str(source)}) + "\n", encoding="utf-8")
        return manifest

    monkeypatch.setattr("app.services.literature_import.download_open_access_articles", fake_download)
    archive = LocalArchive(tmp_path / "archive")
    result = import_open_access_literature(
        archive,
        inbox_root=tmp_path / "inbox",
        project_id="project-a",
        query="mouse cortex",
        limit=1,
    )

    assert result.downloaded == 1
    assert result.created == 1
    assert archive.project_summary("project-a") == {"documents": 1, "chunks": 1}


def test_import_open_access_literature_rejects_unbounded_limit(tmp_path: Path):
    with pytest.raises(ValueError, match="between 1 and 100"):
        import_open_access_literature(
            LocalArchive(tmp_path / "archive"),
            inbox_root=tmp_path / "inbox",
            project_id="project-a",
            query="mouse cortex",
            limit=101,
        )


def test_research_direction_query_expands_supported_chinese_terms():
    assert research_direction_query("小鼠海马神经发生与阿尔茨海默病") == (
        "Alzheimer neurogenesis hippocampus mouse"
    )


def test_selected_import_uses_only_selected_ids_and_caps_at_fifty(monkeypatch, tmp_path: Path):
    source = tmp_path / "PMC1.xml"
    source.write_text(
        "<article><front><article-meta><title-group><article-title>Study</article-title>"
        "</title-group></article-meta></front><body><sec><title>Methods</title><p>Text.</p>"
        "</sec></body></article>", encoding="utf-8"
    )
    received: list[str] = []

    def fake_download(pmcids: list[str], output: Path) -> Path:
        received.extend(pmcids)
        output.mkdir(parents=True)
        manifest = output / "manifest.jsonl"
        manifest.write_text(json.dumps({"path": str(source)}) + "\n", encoding="utf-8")
        return manifest

    monkeypatch.setattr("app.services.literature_import.download_open_access_articles_by_ids", fake_download)
    result = import_selected_open_access_literature(LocalArchive(tmp_path / "archive"), inbox_root=tmp_path / "inbox", project_id="project-a", pmcids=["PMC1", "PMC1"])
    assert received == ["PMC1"]
    assert result.requested == 1
    with pytest.raises(ValueError, match="between 1 and 50"):
        import_selected_open_access_literature(LocalArchive(tmp_path / "other"), inbox_root=tmp_path / "inbox", project_id="project-a", pmcids=[f"PMC{number}" for number in range(51)])
