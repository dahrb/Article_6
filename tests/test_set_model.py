"""The set model (art6/model.py, annotation_guide.txt v3.1, ontology 4.1.0) and its pipeline."""

import json
from pathlib import Path

import pytest
import yaml
from rdflib import RDF, Graph, Namespace, URIRef

from art6.evaluation.kg import convert_records
from art6.evaluation.score import score
from art6.model import (
    derived_follows,
    is_review,
    load_doc,
    nodes,
    validate,
)
from art6.stage1.extract import to_doc
from art6.stage1.to_payload import to_payload

ECHR = Namespace("https://growgraph.dev/echr#")
GOLD = Path(__file__).resolve().parents[1] / "annotation" / "test_simple"

DOC = {
    "case_id": "001-1",
    "sets": [
        {
            "id": "P1",
            "kind": "Civil",
            "label": "claim",
            "pending": False,
            "enforces": None,
        },
        {
            "id": "P2",
            "kind": "Criminal",
            "label": "charge",
            "pending": True,
            "enforces": None,
        },
    ],
    "events": [
        {
            "id": "D2",
            "set": "P1",
            "role": "decision",
            "date": "2001-04-12",
            "body": "Court of Appeal",
            "quote": "upheld on appeal",
            "level": "Appeal",
            "outcome": "MeritsDecided",
            "direction": "UnfavourableToApplicant",
            "reviews": "D1",
            "appealed_by": "Applicant",
        },
        {
            "id": "P1.s",
            "set": "P1",
            "role": "start",
            "date": "2000",
            "body": "court",
            "quote": "lodged a claim",
        },
        {
            "id": "D1",
            "set": "P1",
            "role": "decision",
            "date": "2000-11-20",
            "body": "Regional Court",
            "quote": "gave judgment",
            "level": "FirstInstance",
            "outcome": "MeritsDecided",
            "direction": "UnfavourableToApplicant",
        },
        {
            "id": "D3",
            "set": "P2",
            "role": "decision",
            "date": "2005",
            "body": "prosecutor",
            "quote": "discontinued",
            "level": "Investigative",
            "outcome": "SettledOrWithdrawn",
            "direction": "FavourableToApplicant",
        },
    ],
}


def test_order_is_derived_within_sets_only():
    assert derived_follows(DOC) == {"D1": "P1.s", "D2": "D1"}


def test_same_day_decisions_follow_their_review_link():
    doc = json.loads(json.dumps(DOC))
    doc["events"][0]["date"] = (
        "2000-11-20"  # appeal decided the same day as first instance
    )
    doc["events"].insert(0, doc["events"].pop(0))
    assert derived_follows(doc)["D2"] == "D1"


def test_start_is_a_filing_node_decision_a_decision_node():
    by = {n.key: n for n in nodes(DOC)}
    assert by["P1.s"].is_start and by["P1.s"].start_date == "2000"
    assert by["D1"].decision_date == "2000-11-20" and not by["D1"].is_start
    assert by["D2"].edges["reviews"] == {"D1"}


def test_extractor_reply_is_normalised_and_checked():
    text = "On 1 May 2000 the applicant sued. On 2 June 2001 the court dismissed the claim. Later it was upheld."
    reply = {
        "sets": [
            {
                "id": "P1",
                "kind": "civil",
                "label": "claim",
                "pending": False,
                "enforces": "D9",
                "start": {
                    "date": "2000-05-01",
                    "quote": "the applicant sued",
                    "authority_kind": "judicial",
                    "parties": [{"name": "the applicant", "side": "initiating"}],
                },
            },
            {
                "id": "P2",
                "kind": "Other",
                "label": "empty",
                "start": {"date": "unknown"},
            },
        ],
        "decisions": [
            {
                "id": "D1",
                "set": "P1",
                "date": "2001-06-02",
                "level": "first instance",
                "outcome": "Merits Decided",
                "direction": "against",
                "matter": ["damages", "nonsense"],
                "quote": "the court dismissed the claim",
                "reviews": "D1",
                "appealed_by": "applicant",
            },
            {
                "id": "D2",
                "set": "P1",
                "date": "2002",
                "level": "Appeal",
                "outcome": "MeritsDecided",
                "direction": "UnfavourableToApplicant",
                "reviews": "D1",
                "quote": "not in the text",
                "costs": "applicant",
                "communication_date": "2002-03",
            },
        ],
    }
    doc = to_doc("001-1", reply, text)
    assert [s["id"] for s in doc["sets"]] == ["P1"]  # P2 has no dated act
    assert doc["sets"][0]["kind"] == "Civil" and doc["sets"][0]["enforces"] is None
    st, d1, d2 = doc["events"]
    assert (
        st["parties"] == [{"name": "the applicant", "side": "Initiating"}]
        and st["quote_found"]
    )
    assert d1["level"] == "FirstInstance" and d1["outcome"] == "MeritsDecided"
    assert d1["direction"] == "Unknown" and d1["matter"] == ["Damages"]
    assert (
        d1["reviews"] is None and d1["appealed_by"] is None
    )  # a self-review is cleared
    assert (
        d2["reviews"] == "D1"
        and d2["appealed_by"] == "Unknown"
        and not d2["quote_found"]
    )
    assert (
        d2["costs"] == "Applicant"
        and d2["communication_date"] == "2002-03"
        and d1["costs"] is None
    )


def test_payload_carries_set_model_links_with_offsets():
    text = "lodged a claim; gave judgment; upheld on appeal; discontinued"
    p = to_payload(DOC, text)
    ev = {e["set_model_id"]: e for e in p["events"]}
    assert p["set_model"] and [t["id"] for t in p["tracks"]] == ["P1", "P2"]
    assert (
        ev["D2"]["reviews"] == ev["D1"]["id"] and ev["D2"]["follows"] == ev["D1"]["id"]
    )
    assert (
        ev["D1"]["follows"] == ev["P1.s"]["id"]
        and "decision_date_iso" not in ev["P1.s"]
    )
    assert (
        ev["D2"]["appealed_by"] == "applicant"
        and ev["D3"]["outcome"] == "settled or withdrawn"
    )
    span = ev["D1"]["what_happened_span"]
    assert text[span["start"] : span["end"]] == "gave judgment"


def test_v1_chain_gives_reviews_and_appealed_by():
    records = [
        {
            "id": 1,
            "start_date": "2000",
            "quote": "q1",
            "parties": [{"name": "the applicant", "side": "Initiating"}],
        },
        {
            "id": 2,
            "decision_date": "2001",
            "part_of": 1,
            "level": "FirstInstance",
            "quote": "q2",
        },
        {
            "id": 3,
            "start_date": "2001-05",
            "follows": 1,
            "quote": "q3",
            "parties": [{"name": "the prosecutor", "side": "Initiating"}],
        },
        {
            "id": 4,
            "decision_date": "2002",
            "part_of": 3,
            "level": "Appeal",
            "quote": "q4",
        },
    ]
    doc = convert_records(
        "x",
        records,
        [
            {
                "id": "P1",
                "kind": "Criminal",
                "label": "",
                "start": 1,
                "decisions": [2, 4],
            }
        ],
    )
    d = {e["gold_id"]: e for e in doc["events"]}
    assert d[2]["reviews"] is None
    assert d[4]["reviews"] == d[2]["id"] and d[4]["appealed_by"] == "OpposingParty"
    assert d[4]["appeal_date"] == "2001-05"


def test_gold_scores_perfectly_against_itself(tmp_path):
    for f in GOLD.glob("0*.yaml"):
        d = load_doc(f)
        (tmp_path / f"{d['case_id']}.json").write_text(json.dumps(d))
    t = score(tmp_path, GOLD)
    assert t["c"] == t["a"] == t["g"] > 0
    assert t["reviews_c"] == t["reviews_g"] and t["follows_c"] == t["follows_g"]
    assert t["direction_ok"] == t["direction_n"] and t["side_ok"] == t["side_n"]


def test_reconcile_builds_the_sets():
    from art6.ontology import reconcile

    g = Graph()
    g.bind("doc", "https://growgraph.dev/doc/x/")
    doc = Namespace("https://growgraph.dev/doc/x/")
    g.add((doc.case, RDF.type, ECHR.CaseDocument))
    for n in ("e1", "e2"):
        g.add((doc[n], RDF.type, ECHR.DomesticProceeding))
    m = reconcile.Match(
        nodes={"e1": [doc.e1], "e2": [doc.e2]}, entry_of={doc.e1: "e1", doc.e2: "e2"}
    )
    payload = {
        "set_model": True,
        "tracks": [
            {
                "id": "P1",
                "label": "claim",
                "kind": "civil",
                "pending": True,
                "enforces": None,
            }
        ],
        "events": [
            {"id": "e1", "track": "P1"},
            {
                "id": "e2",
                "track": "P1",
                "reviews": "e1",
                "appealed_by": "applicant",
                "appeal_date_iso": "2001-05",
            },
        ],
    }
    reconcile.transcribe(g, payload, m)
    sets = list(g.subjects(RDF.type, ECHR.ProceedingSet))
    assert sets == [URIRef(str(doc) + "set_P1")]
    assert (doc.e1, ECHR.inProceedingSet, sets[0]) in g and (
        doc.e2,
        ECHR.reviewsDecision,
        doc.e1,
    ) in g
    assert (doc.e2, ECHR.appealedBy, ECHR.AppealedByApplicant) in g
    assert str(g.value(doc.e2, ECHR.hasAppealDate)) == "2001-05"
    assert (doc.case, ECHR.hasProceedingSet, sets[0]) in g


GOLD_FILES = sorted(Path("annotation/test_simple").glob("0*.yaml"))


@pytest.mark.parametrize("path", GOLD_FILES, ids=lambda p: p.stem)
def test_gold_files_follow_the_guide_schema(path):
    assert validate(yaml.safe_load(path.read_text(encoding="utf-8"))) == []


def test_validate_reports_schema_errors():
    bad = {
        "case_id": "001-1",
        "sets": [
            {
                "id": "P1",
                "kind": "Civil",
                "label": "x",
                "decisions": [
                    {
                        "id": "D1",
                        "date": "2001-06-02",
                        "level": "FirstInstnace",
                        "outcome": "MeritsDecided",
                        "direction": "Unknown",
                        "quote": "q",
                        "costs": "Shared",
                        "service_date": "2001",
                        "parties": [{"name": "the applicant", "side": "Initiator"}],
                    },
                    {
                        "id": "D2",
                        "date": "2 June 2002",
                        "level": "Appeal",
                        "outcome": "MeritsDecided",
                        "direction": "Unknown",
                        "quote": "q",
                        "reviews": "D9",
                        "appealed_by": "Applicant",
                    },
                ],
            }
        ],
    }
    errs = "\n".join(validate(bad))
    for needle in (
        "FirstInstnace",
        "'Shared'",
        "unknown field 'service_date'",
        "Initiator",
        "'2 June 2002'",
        "reviews 'D9'",
    ):
        assert needle in errs


def test_inspector_on_appeal_reviews_the_administrative_decision():
    council = {"level": "AdministrativeReview", "outcome": "MeritsDecided"}
    assert is_review("AdministrativeReview", council, appealed=True)
    assert not is_review("AdministrativeReview", council)  # two steps of one procedure
    assert is_review("FirstInstance", council)
