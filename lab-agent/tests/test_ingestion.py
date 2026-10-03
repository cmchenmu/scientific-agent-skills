from pathlib import Path

from app.services.ingestion import (
    chunk_sections,
    hash_embedding,
    inspect_open_access_article,
    parse_jats_xml,
    select_open_access_results,
    sha256_file,
)

JATS = """<?xml version="1.0"?>
<article><front><article-meta><title-group><article-title>Example Mouse Brain Study</article-title></title-group>
<article-id pub-id-type="pmcid">PMC123</article-id><article-id pub-id-type="doi">10.1/example</article-id>
<permissions><license><license-p>CC BY 4.0</license-p></license></permissions></article-meta></front>
<body><sec><title>Methods</title><p>First paragraph.</p><p>Second paragraph.</p></sec></body></article>"""


def test_parse_jats_retains_provenance_and_sections(tmp_path: Path):
    source = tmp_path / "PMC123.xml"
    source.write_text(JATS, encoding="utf-8")

    parsed = parse_jats_xml(source)

    assert parsed.title == "Example Mouse Brain Study"
    assert parsed.identifiers == {"pmcid": "PMC123", "doi": "10.1/example"}
    assert parsed.license_text == "CC BY 4.0"
    assert parsed.sections[0].title == "Methods"
    assert sha256_file(source) == sha256_file(source)


def test_chunking_preserves_section_metadata(tmp_path: Path):
    source = tmp_path / "PMC123.xml"
    source.write_text(JATS, encoding="utf-8")
    chunks = chunk_sections(parse_jats_xml(source).sections, max_chars=100)

    assert len(chunks) == 1
    assert chunks[0]["metadata"] == {"section": "Methods", "paragraph": 0, "page": None}


def test_hash_embedding_is_deterministic_and_normalized():
    vector = hash_embedding("mouse hippocampus neuron")

    assert vector == hash_embedding("mouse hippocampus neuron")
    assert len(vector) == 256
    assert round(sum(value * value for value in vector), 6) == 1.0


def test_open_access_selection_rejects_abstracts_and_non_open_articles():
    results = [
        {"pmcid": "PMC1", "isOpenAccess": "Y", "pubType": "research article"},
        {"pmcid": "PMC2", "isOpenAccess": "Y", "pubType": "abstract"},
        {"pmcid": "PMC4", "isOpenAccess": "Y", "pubType": "conference abstract"},
        {"pmcid": "PMC3", "isOpenAccess": "N", "pubType": "research article"},
    ]

    assert select_open_access_results(results, 20) == [results[0], results[1], results[2]]


def test_open_access_inspection_uses_pmc_page_cdn_urls(monkeypatch):
    xml = b"""<article><body><sec><title>Methods</title><p>Animals were randomized.</p></sec>
    <table-wrap><caption><p>Table 1. Outcomes</p></caption><table><tr><td>group</td><td>value</td></tr></table></table-wrap>
    <fig><caption><p>Figure 1. Study flow</p></caption><graphic xmlns:xlink=\"http://www.w3.org/1999/xlink\" xlink:href=\"figure-1.jpg\" /></fig></body></article>"""
    page = b'<html><img src="https://cdn.ncbi.nlm.nih.gov/pmc/blobs/a/1/b/figure-1.jpg" alt="Figure 1"></html>'
    responses = iter([xml, page])

    class Response:
        def __init__(self, content: bytes):
            self.content = content
            self.offset = 0

        def read(self, _size: int = -1) -> bytes:
            if _size < 0:
                chunk = self.content[self.offset:]
                self.offset = len(self.content)
                return chunk
            chunk = self.content[self.offset:self.offset + _size]
            self.offset += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("app.services.ingestion.urllib.request.urlopen", lambda *_args, **_kwargs: Response(next(responses)))
    from app.services import ingestion

    ingestion._pmc_figure_urls.cache_clear()

    inspection = inspect_open_access_article("PMC123")

    assert inspection["methods"][0]["section"] == "Methods"
    assert inspection["data_tables"][0]["caption"] == "Table 1. Outcomes"
    assert inspection["data_summary"] == "已从开放全文定位到 1 张表格。表格标题：Table 1. Outcomes。"
    assert inspection["figures"][0]["image_url"].endswith("figure-1.jpg")
