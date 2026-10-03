from pathlib import Path

from app.services.local_archive import LocalArchive


def test_local_archive_ingests_html_deduplicates_and_enforces_acl(tmp_path: Path):
    source = tmp_path / "study.html"
    source.write_text(
        "<html><head><title>Local Study</title></head><body><h1>Methods</h1>"
        "<p>Mouse hippocampus sample.</p><p>Control group.</p></body></html>",
        encoding="utf-8",
    )
    archive = LocalArchive(tmp_path / "archive")

    created = archive.ingest(source, "demo", ["student"], "internal-research")
    duplicate = archive.ingest(source, "demo", ["student"], "internal-research")

    assert created.state == "created"
    assert created.chunk_count == 2
    assert duplicate.state == "duplicate"
    assert archive.search("demo", ["student"], "hippocampus")
    assert archive.search("demo", ["finance-approver"], "hippocampus") == []


def test_local_archive_records_rejected_files(tmp_path: Path):
    source = tmp_path / "bad.bin"
    source.write_bytes(b"MZ executable")
    archive = LocalArchive(tmp_path / "archive")

    try:
        archive.ingest(source, "demo", ["student"], "internal-research")
    except ValueError:
        pass
    else:
        raise AssertionError("executable input should be rejected")
    assert archive.list_errors()[0]["error_code"] == "ValueError"


def test_library_listing_original_access_and_deletion(tmp_path: Path):
    source = tmp_path / "study.html"
    source.write_text("<title>Study</title><p>Readable evidence.</p>", encoding="utf-8")
    archive = LocalArchive(tmp_path / "archive")
    result = archive.ingest(source, "demo", ["research-assistant"], "literature")
    archive.grant_role("researcher", "demo", "research-assistant")

    items = archive.library_for_user("researcher", "demo")
    assert items[0]["document_id"] == result.document_id
    original = archive.original_for_user("researcher", "demo", result.document_id)
    assert original and original[0].is_file()
    assert archive.delete_document("demo", result.document_id)
    assert archive.library_for_user("researcher", "demo") == []
    assert not original[0].exists()
