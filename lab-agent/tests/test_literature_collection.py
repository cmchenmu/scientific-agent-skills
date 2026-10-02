from pathlib import Path

from app.services.literature_collection import LiteratureCollectionService
from app.services.local_archive import LocalArchive


def test_collection_imports_open_access_batch_and_preserves_acl(tmp_path, monkeypatch):
    archive = LocalArchive(tmp_path / "archive")
    archive.grant_role("researcher", "neuro", "research-assistant")
    archive.grant_role("student", "neuro", "student")
    service = LiteratureCollectionService(tmp_path / "collections", archive)
    candidates = [{"pmcid": f"PMC{index}", "doi": f"10.1/{index}"} for index in range(50)]
    monkeypatch.setattr(
        "app.services.literature_collection.search_open_access_articles",
        lambda domain, count: candidates,
    )

    def download(_: str, destination: Path) -> None:
        number = destination.stem.removeprefix("PMC")
        destination.write_text(
            "<article><front><article-meta><title-group><article-title>Study "
            f"{number}</article-title></title-group><article-id pub-id-type=\"pmcid\">"
            f"PMC{number}</article-id></article-meta></front><body><sec><title>Methods</title>"
            f"<p>Neural method {number}.</p></sec></body></article>",
            encoding="utf-8",
        )

    monkeypatch.setattr("app.services.literature_collection.download_full_text_xml", download)
    task_id = service.create("researcher", "neuro", "hippocampus", 50)
    service.run(task_id, archive.project_role_names("neuro"))

    task = service.get(task_id, "researcher")
    assert task["state"] == "completed"
    assert task["imported_count"] == 50
    assert len(archive.search_for_user("student", "neuro", "Neural method")) == 10
