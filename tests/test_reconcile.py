"""Offline tests for stage 3's diff-and-transcribe against stage 1."""

import json
from types import SimpleNamespace

from rdflib import RDF, RDFS, Graph, Literal, Namespace

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


# -- final critic -> graph fixer ----------------------------------------------

SOURCE = (
    "the applicant instituted civil proceedings against her employer. "
    "the District Court dismissed the claim in its entirety. "
    "the Regional Court of Appeal upheld that judgment on appeal."
)


def _final(tmp_path, monkeypatch, answer, patch, *, capacity=None, violations=None):
    from art6.ontology import agentic_stage1, new_repair

    calls = []
    monkeypatch.setattr(
        agentic_stage1, "ask_with_flags", lambda *a, **k: (answer, "raw", [])
    )

    def fake_call_model(*a, **k):
        calls.append("fix")
        return patch

    monkeypatch.setattr(new_repair, "call_model", fake_call_model)
    if violations is not None:
        monkeypatch.setattr(r, "_violation_count", violations)
    ttl = tmp_path / "bundles.L1.facts.ttl"
    ttl.write_text(_graph().serialize(format="turtle"), encoding="utf-8")
    payload = {**PAYLOAD, "events": PAYLOAD["events"][:4]}
    report = r.reconcile_document(
        ttl,
        payload,
        client=SimpleNamespace(base_url="http://x/v1", api_key=""),
        model="m",
        source_text=SOURCE,
        temperature=0,
        max_tokens=10,
        max_rounds=0,
        final_critic=True,
        capacity=capacity,
    )
    g = Graph()
    g.parse(ttl)
    return report["final_critic"], g, calls


def _label_op(node, text):
    return {
        "action": "add",
        "subject": f"doc:{node}",
        "predicate": "rdfs:label",
        "object": text,
        "object_is_literal": True,
        "lang": "en",
    }


def test_final_critic_locks_stage1_and_gates_each_group(tmp_path, monkeypatch):
    from art6.ontology.repair_facts import MergeOp

    answer = {
        "needs_fix": "yes",
        "stage1_dispute": "no",
        "findings": [
            {
                "kind": "wrong_court",
                "nodes": ["doc:appeal"],
                "evidence_span": "upheld that judgment",
                "issue": "x",
            },
            {
                "kind": "other",
                "nodes": ["doc:nowhere"],
                "evidence_span": "upheld that judgment",
            },
            {
                "kind": "other",
                "nodes": ["doc:appeal"],
                "evidence_span": "words not in the judgment",
            },
        ],
    }
    patch = _patch(
        [
            ("g1", [_label_op("appeal", "Appeal ruling")]),
            (
                "g1",
                [
                    {
                        "action": "remove",
                        "subject": "doc:decision",
                        "predicate": "echr:hasOutcome",
                        "object": "",
                        "object_is_literal": False,
                    }
                ],
            ),
            ("g1", [_label_op("filing", "BAD")]),
        ]
    )
    patch.merges = [MergeOp(keep="doc:filing", drop="doc:decision", rationale="r")]

    def violations(g, ns):  # a "BAD" label is a violation
        return sum(1 for o in g.objects(None, RDFS.label) if str(o) == "BAD")

    report, g, calls = _final(
        tmp_path, monkeypatch, answer, patch, violations=violations
    )
    assert [f["id"] for f in report["findings"]] == ["g1"]
    assert len(report["findings_refused"]) == 2
    assert calls == ["fix"]
    # the label landed; the locked outcome and the stage-1 merge did not
    assert (DOC.appeal, RDFS.label, Literal("Appeal ruling", lang="en")) in g
    assert set(g.objects(DOC.decision, ECHR.hasOutcome)) == {ECHR.OutcomeMeritsDecided}
    assert (DOC.decision, RDF.type, ECHR.DomesticProceeding) in g
    reasons = sorted(d["reason"] for d in report["stage1_disputes"])
    assert reasons == ["locked by stage 1", "merges a stage-1 event"]
    # the violating group was gated
    assert report["fix"]["groups_kept"] == 1
    assert [x["finding"] for x in report["fix"]["groups_gated"]] == ["g1"]
    assert not any(str(o) == "BAD" for o in g.objects(None, RDFS.label))


def test_final_critic_without_findings_skips_the_fixer(tmp_path, monkeypatch):
    answer = {
        "needs_fix": "no",
        "stage1_dispute": "yes",
        "findings": [],
        "disputes": [{"nodes": ["doc:decision"], "issue": "outcome looks wrong"}],
    }
    report, _, calls = _final(tmp_path, monkeypatch, answer, None)
    assert calls == [] and report["fix"] == {"skipped": "no findings"}
    assert report["stage1_disputes"][0]["issue"] == "outcome looks wrong"


def test_final_critic_skipped_when_the_prompt_does_not_fit(tmp_path, monkeypatch):
    from art6.ontology import compress

    monkeypatch.setattr(compress, "count_prompt_tokens", lambda *a: 50_000)
    report, _, calls = _final(tmp_path, monkeypatch, {}, None, capacity=40_000)
    assert report["status"].startswith("skipped") and calls == []


def test_final_fixer_cannot_delete_a_stage1_event(tmp_path, monkeypatch):
    answer = {
        "needs_fix": "yes",
        "stage1_dispute": "no",
        "findings": [
            {
                "kind": "spurious_event",
                "nodes": ["doc:crim"],
                "evidence_span": "the applicant was arrested by the police on suspicion of fraud",
            }
        ],
    }
    patch = _patch(
        [
            (
                "g1",
                [
                    {
                        "action": "remove",
                        "subject": "doc:crim",
                        "predicate": "rdf:type",
                        "object": "echr:DomesticProceeding",
                        "object_is_literal": False,
                    }
                ],
            )
        ]
    )
    report, g, _ = _final(tmp_path, monkeypatch, answer, patch)
    assert (DOC.crim, RDF.type, ECHR.DomesticProceeding) in g
    assert report["stage1_disputes"][0]["reason"] == "locked by stage 1"


# -- stage-2 sharding --------------------------------------------------------


def test_stage2_shards_cover_only_missing_and_keep_names(tmp_path):
    from art6.ontology import stage2_shards as s2

    src = tmp_path / "input.jsonl"
    src.write_text(
        "".join(f'{{"case_id": "c{i}"}}\n' for i in range(1, 6)), encoding="utf-8"
    )
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "input.L2.facts.ttl").write_text("done")
    assert s2.missing(src, raw) == [1, 3, 4, 5]

    shards = s2.plan(src, raw, 3, tmp_path / "shards")
    covered = sorted(n for sh in shards for n in s2.record_lines(sh / "input.jsonl"))
    assert covered == [1, 3, 4, 5]
    # every shard file is full length, so OntoCast's line numbers are unchanged
    assert all(len((sh / "input.jsonl").read_text().splitlines()) == 5 for sh in shards)

    # fake OntoCast output: a multi-record shard names by line, a single-record
    # shard does not
    for sh in shards:
        lines = s2.record_lines(sh / "input.jsonl")
        (sh / "raw").mkdir()
        if len(lines) == 1:
            (sh / "raw" / "input.facts.ttl").write_text("x")
            (sh / "raw" / "input.run.json").write_text("{}")
        else:
            for n in lines:
                (sh / "raw" / f"input.L{n}.facts.ttl").write_text("x")
        (sh / "raw" / "extract.log").write_text(f"log {sh.name}\n")
    assert s2.merge(src, raw, tmp_path / "shards") == 4
    assert s2.missing(src, raw) == []
    assert (raw / "input.L2.facts.ttl").read_text() == "done"  # never overwritten
    assert sorted(p.name for p in raw.glob("*.run.json"))  # renamed with its line
    assert "log" in (raw / "extract.log").read_text()


def test_events_deleted_by_the_shacl_loop_are_revived():
    from rdflib import RDF, Graph, Literal, Namespace, URIRef

    E = Namespace("https://growgraph.dev/echr#")
    doc = "https://growgraph.dev/doc/abc/"
    event, other, gone = URIRef(doc + "e1"), URIRef(doc + "e0"), URIRef(doc + "p9")
    g = Graph()
    g.add((event, RDF.type, E.DomesticProceeding))
    g.add((event, E.hasDecisionDate, Literal("2001-01-01")))
    g.add((event, E.followsProceeding, other))
    g.add((event, E.hasParticipation, gone))
    g.add((other, RDF.type, E.DomesticProceeding))
    kept = {event: list(g.triples((event, None, None)))}
    g.remove((event, None, None))  # what the loop did
    audit = r.restore_deleted_events(g, kept)
    assert audit == [{"kind": "event_revived", "node": str(event), "triples": 3}]
    assert (event, E.followsProceeding, other) in g
    assert (event, E.hasParticipation, gone) not in g  # its target no longer exists
    assert r.restore_deleted_events(g, kept) == []  # present nodes are left alone
