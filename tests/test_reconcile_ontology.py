"""Stage 3 reads the ontology the graph was built against, not always echr.ttl."""

from pathlib import Path

from art6.ontology import reconcile


def test_ontology_ttl_defaults_to_echr(monkeypatch):
    monkeypatch.delenv("ART6_ONTOLOGY_TTL", raising=False)
    assert reconcile.ontology_ttl().name == "echr.ttl"


def test_ontology_ttl_follows_env(monkeypatch, tmp_path):
    alt = tmp_path / "echr_timeline.ttl"
    monkeypatch.setenv("ART6_ONTOLOGY_TTL", str(alt))
    assert reconcile.ontology_ttl() == Path(alt)
