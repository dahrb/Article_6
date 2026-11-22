import json

from art6.ontology import stage2_coverage as cov

HEAD = """@prefix echr: <https://growgraph.dev/echr#> .
@prefix doc: <https://example.org/doc#> .
"""


def full(i):
    return f'doc:e{i} a echr:DomesticProceeding ; echr:hasSupportingQuote "q{i}" .\n'


def shell(i):
    return f"doc:e{i} a echr:DomesticProceeding .\n"


def setup(tmp_path, n_events, body):
    bundle = tmp_path / "bundle.jsonl"
    text = "\n".join(f"EVENT e{i}\n  description: x" for i in range(1, n_events + 1))
    bundle.write_text(json.dumps({"case_id": "001-1", "text": text}) + "\n")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "bundle.L1.facts.ttl").write_text(HEAD + body)
    (raw / "bundle.L1.run.json").write_text("{}")
    return bundle, raw


def test_full_graph_is_kept(tmp_path):
    bundle, raw = setup(tmp_path, 5, "".join(full(i) for i in range(1, 5)))  # 4/5
    assert cov.thin(bundle, raw, 0, 0.2) == []
    assert (raw / "bundle.L1.facts.ttl").exists()


def test_type_only_nodes_count_as_dropped_and_are_retried(tmp_path):
    body = full(1) + full(2) + "".join(shell(i) for i in range(3, 11))  # 2 of 10 filled
    bundle, raw = setup(tmp_path, 10, body)
    assert cov.thin(bundle, raw, 0, 0.2) == [1]
    assert not (raw / "bundle.L1.facts.ttl").exists()  # next attempt redoes it
    assert (raw / "thinned/a0/bundle.L1.run.json").exists()
    # No retry did better: settle puts the fullest version back and reports it.
    assert cov.settle(bundle, raw, 0.2) == ["THINNED 001-1 2/10"]
    assert (raw / "bundle.L1.facts.ttl").exists()
