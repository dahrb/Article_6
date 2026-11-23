from pathlib import Path

from art6.simple.extract import to_doc
from art6.simple.model import derived_follows, nodes
from art6.simple.render import render
from art6.simple.score import score

DOC = {
    "case_id": "001-1",
    "sets": [
        {"id": "P1", "kind": "civil", "label": "claim", "pending": False},
        {"id": "P2", "kind": "criminal", "label": "charge", "pending": True},
    ],
    "events": [
        {
            "id": "a",
            "set": "P1",
            "role": "decision",
            "date": "2001-04-12",
            "body": "Court of Appeal",
            "quote": "upheld on appeal",
            "level": "appeal",
            "outcome": "merits",
            "direction": "against_applicant",
        },
        {
            "id": "s",
            "set": "P1",
            "role": "start",
            "date": "2000",
            "body": "court",
            "quote": "lodged a claim",
        },
        {
            "id": "f",
            "set": "P1",
            "role": "decision",
            "date": "2000-11-20",
            "body": "Regional Court",
            "quote": "gave judgment",
            "level": "first_instance",
            "outcome": "merits",
            "direction": "against_applicant",
        },
        {
            "id": "x",
            "set": "P2",
            "role": "decision",
            "date": "2005",
            "body": "prosecutor",
            "quote": "discontinued",
            "level": "investigation",
            "outcome": "procedural",
            "direction": "unknown",
        },
    ],
}


def test_order_is_derived_within_sets_only():
    # start first, then decisions by date; nothing links across sets
    assert derived_follows(DOC) == {"f": "s", "a": "f"}


def test_start_is_a_filing_node_decision_a_decision_node():
    n = {x.key: x for x in nodes(DOC)}
    assert n["s"].is_filing and not n["f"].is_filing


def test_render_conforms_to_simple_shapes():
    from pyshacl import validate
    from rdflib import Graph

    conforms, _, _ = validate(
        render(DOC),
        shacl_graph=Graph().parse("ontology/simple/echr-simple-shapes.ttl"),
        ont_graph=Graph().parse("ontology/simple/echr-simple.ttl"),
        inference="rdfs",
    )
    assert conforms


def test_extractor_reply_is_normalised_and_quotes_checked():
    text = "On 3 May 1999 the applicant lodged a claim. On 4 June 2000 the court dismissed it."
    reply = {
        "sets": [
            {
                "id": "P1",
                "kind": "Civil",
                "start": {
                    "date": "1999-05-03",
                    "quote": "the applicant lodged a claim",
                },
            }
        ],
        "decisions": [
            {
                "set": "P1",
                "date": "2000-06-04",
                "level": "first instance",
                "outcome": "merits",
                "direction": "against applicant",
                "quote": "the court dismissed it.",
            },
            {"set": "P1", "date": "June 2000", "quote": "x"},
        ],
    }  # bad date: dropped
    doc = to_doc("001-1", reply, text)
    assert [e["role"] for e in doc["events"]] == ["start", "decision"]
    assert (
        doc["events"][1]["level"] == "first_instance"
        and doc["events"][1]["direction"] == "against_applicant"
    )
    assert all(e["quote_found"] for e in doc["events"])


def test_gold_scores_perfectly_against_itself():
    t = score(Path("annotation/test_simple"), Path("annotation/test_simple"))
    assert (
        t["c"] == t["g"] == t["a"]
        and t["fo_c"] == t["fo_g"]
        and t["col_start"] == t["col_n"]
    )
