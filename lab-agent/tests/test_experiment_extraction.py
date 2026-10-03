from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.experiment_extraction import ExperimentExtractionService
from app.services.local_archive import LocalArchive


def _archive(tmp_path: Path) -> tuple[LocalArchive, str]:
    source = tmp_path / "study.html"
    source.write_text(
        "<title>Hippocampus study</title>"
        "<h1>Methods</h1><p>Mice were randomized, fixed, stained with antibody and imaged on a microscope.</p>"
        "<h1>Statistical analysis</h1><p>Groups were compared using a two-sample t-test.</p>"
        "<h1>Results</h1><p>Treatment increased cell counts.</p>"
        "<h1>Conclusion</h1><p>These results suggest treatment improved neurogenesis.</p>",
        encoding="utf-8",
    )
    archive = LocalArchive(tmp_path / "archive")
    result = archive.ingest(source, "project-a", ["research-assistant"], "literature")
    archive.grant_role("researcher", "project-a", "research-assistant")
    return archive, result.document_id


def test_extraction_returns_grounded_fields_and_t_test_template(tmp_path: Path):
    archive, document_id = _archive(tmp_path)

    result = ExperimentExtractionService(archive).extract("researcher", "project-a", document_id)

    assert result.experimental_workflow
    assert result.materials_and_equipment
    assert result.data_analysis_methods
    assert result.expected_results
    assert result.conclusions
    assert result.automations[0].script_filename == "two_group_t_test.py"
    assert "group,value" in result.automations[0].input_template
    assert result.data_analysis_methods[0].chunk_id


def test_document_search_is_limited_to_five_and_extract_requires_acl(tmp_path: Path):
    archive, document_id = _archive(tmp_path)
    for number in range(6):
        source = tmp_path / f"extra-{number}.html"
        source.write_text(f"<title>Study {number}</title><p>hippocampus mouse study {number}</p>", encoding="utf-8")
        archive.ingest(source, "project-a", ["research-assistant"], "literature")
    hits = archive.search_documents_for_user("researcher", "project-a", "hippocampus mouse", limit=5)
    assert len(hits) == 5
    assert all(hit["document_id"] for hit in hits)
    with pytest.raises(LookupError):
        ExperimentExtractionService(archive).extract("researcher", "other-project", document_id)


def test_experiment_api_obeys_acl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    archive, document_id = _archive(tmp_path)
    monkeypatch.setenv("LOCAL_ARCHIVE_PATH", str(archive.root))
    client = TestClient(app)
    denied = client.get(
        f"/v1/literature/{document_id}/experiment?project_id=project-a",
        headers={"X-User-Id": "student-demo"},
    )
    assert denied.status_code == 404
    response = client.get(
        f"/v1/literature/{document_id}/experiment?project_id=project-a",
        headers={"X-User-Id": "researcher"},
    )
    assert response.status_code == 200
    assert response.json()["automations"][0]["input_template_filename"] == "two_group_input.csv"
