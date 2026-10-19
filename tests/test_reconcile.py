"""Offline tests for stage 3's diff-and-transcribe against stage 1."""

import json

from rdflib import RDF, Graph, Literal, Namespace

from art6.ontology import reconcile as r

DOC = Namespace("https://growgraph.dev/doc/t/")
ECHR = Namespace("https://growgraph.dev/echr#")


def _graph():
    g = Graph()
    g.bind("doc", DOC)
    g.bind("echr", ECHR)

    def event(name, quote, level, ptype="TypeCivil"):
        g.add((DOC[name], RDF.type, ECHR.DomesticProceeding))
        g.add((DOC[name], ECHR.hasSupportingQuote, Literal(quote, lang="en")))
        g.add((DOC[name], ECHR.hasInstanceLevel, ECHR[level]))
        g.add((DOC[name], ECHR.hasProceedingType, ECHR[ptype]))

    event(
        "filing",
        "the applicant instituted civil proceedings against her employer",
        "LevelFirstInstance",
    )
    event(
        "decision",
        "the District Court dismissed the claim in its entirety",
        "LevelFirstInstance",
    )
    event(
        "appeal",
        "the Regional Court of Appeal upheld that judgment on appeal",
        "LevelAppeal",
    )
    event(
        "crim",
        "the applicant was arrested by the police on suspicion of fraud",
        "LevelInvestigative",
        "TypeCriminal",
    )
    # stage 2's mistakes: a filing/decision pair chained with follows, the
    # appeal hung off the decision, and the parallel criminal track chained in.
    g.add((DOC.decision, ECHR.followsProceeding, DOC.filing))
    g.add((DOC.appeal, ECHR.followsProceeding, DOC.decision))
    g.add((DOC.crim, ECHR.followsProceeding, DOC.appeal))
    g.add((DOC.decision, ECHR.hasOutcome, ECHR.OutcomeRemitted))
    return g


def _s(text):
    return {"text": text, "start": 0, "end": len(text)}


PAYLOAD = {
    "tracks": [{"id": "T1", "label": "civil"}, {"id": "T2", "label": "criminal"}],
    "events": [
        {
            "id": "e1",
            "track": "T1",
            "what_happened_span": _s(
                "instituted civil proceedings against her employer"
            ),
            "instance_level": "first instance",
            "proceeding_type": "civil",
        },
        {
            "id": "e2",
            "track": "T1",
            "what_happened_span": _s("the District Court dismissed the claim"),
            "instance_level": "first instance",
            "proceeding_type": "civil",
            "outcome": "merits decided",
            "matter": ["employment"],
            "part_of": "e1",
        },
        {
            "id": "e3",
            "track": "T1",
            "what_happened_span": _s("upheld that judgment on appeal"),
            "instance_level": "appeal",
            "proceeding_type": "civil",
            "follows": "e1",
        },
        {
            "id": "e4",
            "track": "T2",
            "what_happened_span": _s(
                "was arrested by the police on suspicion of fraud"
            ),
            "instance_level": "investigative",
            "proceeding_type": "criminal",
        },
        {
            "id": "e5",
            "track": "T2",
            "what_happened_span": _s("a hearing that stage 2 dropped entirely"),
        },
    ],
}


def test_match_by_quote_and_report_missing():
    g = _graph()
    m = r.match_events(g, PAYLOAD["events"])
    assert m.nodes["e2"] == [DOC.decision]
    assert m.missing(PAYLOAD["events"]) == ["e5"]


def test_transcribe_makes_the_graph_agree_with_stage1():
    g = _graph()
    m = r.match_events(g, PAYLOAD["events"])
    audit = r.transcribe(g, PAYLOAD, m)
    # filing/decision retyped to partOf, appeal moved to the proceeding root,
    # the cross-track chain removed
    assert (DOC.decision, ECHR.partOfProceeding, DOC.filing) in g
    assert (DOC.decision, ECHR.followsProceeding, DOC.filing) not in g
    assert (DOC.appeal, ECHR.followsProceeding, DOC.filing) in g
    assert (DOC.appeal, ECHR.followsProceeding, DOC.decision) not in g
    assert (DOC.crim, ECHR.followsProceeding, DOC.appeal) not in g
    # stage 1 wins the outcome conflict and fills the missing matter
    assert set(g.objects(DOC.decision, ECHR.hasOutcome)) == {ECHR.OutcomeMeritsDecided}
    assert (DOC.decision, ECHR.hasMatter, ECHR.MatterEmployment) in g
    kinds = sorted(a["kind"] for a in audit)
    assert kinds.count("link_removed") == 3 and kinds.count("link_added") == 2
    assert "field_conflict" in kinds and "field_missing" in kinds
    # a second pass finds nothing left to do
    assert r.transcribe(g, PAYLOAD, r.match_events(g, PAYLOAD["events"])) == []


def test_untyped_payload_leaves_links_alone():
    g = _graph()
    legacy = {"events": [dict(e) for e in PAYLOAD["events"]]}
    r.transcribe(g, legacy, r.match_events(g, legacy["events"]))
    assert (DOC.crim, ECHR.followsProceeding, DOC.appeal) in g


def test_reconcile_document_without_model(tmp_path):
    ttl = tmp_path / "bundles.L1.facts.ttl"
    ttl.write_text(_graph().serialize(format="turtle"), encoding="utf-8")
    report = r.reconcile_document(
        ttl,
        PAYLOAD,
        client=None,
        model="m",
        source_text=None,
        temperature=0,
        max_tokens=10,
    )
    assert report["status"] == "reconciled"
    assert report["missing_before"] == 1 and report["missing_after"] == 1
    assert report["mint"]["status"] == "no model or no source text"
    assert (tmp_path / "backup" / ttl.name).exists()
    saved = json.loads((tmp_path / "bundles.L1.reconcile.json").read_text())
    assert saved["changes"]["link_removed"] == 3

    # the reconciled graph is now consistent with stage 1 apart from e5
    again = r.reconcile_document(
        ttl,
        {**PAYLOAD, "events": PAYLOAD["events"][:4]},
        client=None,
        model="m",
        source_text=None,
        temperature=0,
        max_tokens=10,
    )
    assert again["status"] == "consistent" and again["changes"] == {}


def _patch(groups):
    from art6.ontology.repair_facts import RepairGroup, RepairPatch, TripleOp

    return RepairPatch(
        groups=[
            RepairGroup(finding=f, rationale="r", ops=[TripleOp(**op) for op in ops])
            for f, ops in groups
        ]
    )


def _mint_ops(name, quote):
    return [
        {
            "action": "add",
            "subject": f"doc:{name}",
            "predicate": "rdf:type",
            "object": "echr:DomesticProceeding",
            "object_is_literal": False,
        },
        {
            "action": "add",
            "subject": f"doc:{name}",
            "predicate": "echr:hasSupportingQuote",
            "object": quote,
            "object_is_literal": True,
            "lang": "en",
        },
    ]


def test_mint_is_gated_and_anchored(tmp_path, monkeypatch):
    from art6.ontology import new_repair

    source = "Later, a hearing that stage 2 dropped entirely took place."
    patch = _patch(
        [
            ("e5", _mint_ops("hearing", "a hearing that stage 2 dropped entirely")),
            ("e9", [_mint_ops("ghost", "x")[0]]),  # no quote: refused whole
        ]
    )
    monkeypatch.setattr(new_repair, "call_model", lambda *a, **k: patch)
    ttl = tmp_path / "bundles.L1.facts.ttl"
    ttl.write_text(_graph().serialize(format="turtle"), encoding="utf-8")
    report = r.reconcile_document(
        ttl,
        PAYLOAD,
        client=object(),
        model="m",
        source_text=source,
        temperature=0,
        max_tokens=10,
    )
    assert report["mint"]["accepted"] is True
    assert report["mint"]["groups_refused_unanchored"] == ["e9"]
    assert report["missing_after"] == 0
    g = Graph()
    g.parse(ttl)
    assert (DOC.hearing, RDF.type, ECHR.DomesticProceeding) in g
    assert (DOC.ghost, RDF.type, ECHR.DomesticProceeding) not in g


def test_mint_reverted_when_it_places_nothing(tmp_path, monkeypatch):
    from art6.ontology import new_repair

    patch = _patch([("e5", _mint_ops("other", "words not in the judgment"))])
    monkeypatch.setattr(new_repair, "call_model", lambda *a, **k: patch)
    ttl = tmp_path / "bundles.L1.facts.ttl"
    ttl.write_text(_graph().serialize(format="turtle"), encoding="utf-8")
    report = r.reconcile_document(
        ttl,
        PAYLOAD,
        client=object(),
        model="m",
        source_text="unrelated",
        temperature=0,
        max_tokens=10,
    )
    assert report["mint"]["status"] == "reverted by gate"
    assert report["mint"]["nodes_removed_unanchored"] == ["doc:other"]
    g = Graph()
    g.parse(ttl)
    assert (DOC.other, RDF.type, ECHR.DomesticProceeding) not in g


def test_shacl_loop_runs_only_on_violations_and_cannot_undo_stage1(
    tmp_path, monkeypatch
):
    from art6.ontology import new_repair

    calls = []

    def fake_loop(graph, **kwargs):
        calls.append(kwargs["max_rounds"])
        # a "repair" that clears a shape by deleting a stage-1 decision
        graph.remove((DOC.decision, ECHR.partOfProceeding, DOC.filing))
        return graph, [{"status": "applied"}]

    monkeypatch.setattr(new_repair, "run_loop", fake_loop)
    counts = iter([2, 0, 0])
    monkeypatch.setattr(r, "_violation_count", lambda g, ns: next(counts))
    ttl = tmp_path / "bundles.L1.facts.ttl"
    ttl.write_text(_graph().serialize(format="turtle"), encoding="utf-8")
    report = r.reconcile_document(
        ttl,
        PAYLOAD,
        client=object(),
        model="m",
        source_text=None,
        temperature=0,
        max_tokens=10,
        max_rounds=2,
    )
    assert calls == [2]
    loop = report["shacl_loop"]
    assert loop["violations_before"] == 2 and loop["violations_after"] == 0
    assert loop["stage1_decisions_restored"] == 1
    g = Graph()
    g.parse(ttl)
    assert (DOC.decision, ECHR.partOfProceeding, DOC.filing) in g

    # no violations -> no loop call
    calls.clear()
    monkeypatch.setattr(r, "_violation_count", lambda g, ns: 0)
    ttl.write_text(_graph().serialize(format="turtle"), encoding="utf-8")
    (tmp_path / "bundles.L1.reconcile.json").unlink()
    report = r.reconcile_document(
        ttl,
        PAYLOAD,
        client=object(),
        model="m",
        source_text=None,
        temperature=0,
        max_tokens=10,
    )
    assert calls == [] and report["shacl_loop"]["called"] is False
