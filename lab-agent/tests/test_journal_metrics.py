import json

from app.services.journal_metrics import impact_factor


def test_impact_factor_reads_normalized_local_jcr_mapping(monkeypatch, tmp_path):
    metrics = tmp_path / "jcr.json"
    metrics.write_text(json.dumps({"nature medicine": {"jif": 58.7, "year": "2024"}}), encoding="utf-8")
    monkeypatch.setenv("LAB_AGENT_JCR_METRICS_PATH", str(metrics))

    assert impact_factor("Nature   Medicine") == {
        "value": 58.7, "year": "2024", "source": "JCR local import"
    }


def test_impact_factor_is_unknown_without_a_configured_mapping(monkeypatch):
    monkeypatch.delenv("LAB_AGENT_JCR_METRICS_PATH", raising=False)
    assert impact_factor("Nature Medicine") is None
