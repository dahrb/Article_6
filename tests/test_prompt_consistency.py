"""Stage 1, stage 2 and the ontology agree on what the graph holds.

These tests fail when a prompt drifts: a vocabulary value stage 1 can produce
that the ontology or the stage-2 instruction has no place for, an ontology
value no prompt offers, a field stage 1 collects that the graph cannot hold.
"""

import re
from pathlib import Path

import pytest
from rdflib import RDF, Graph, Namespace

from art6.ontology import agentic_stage1 as a
from art6.ontology import prompt_spec as ps
from art6.ontology import reconcile as r

ROOT = Path(__file__).resolve().parents[1]
ECHR = Namespace("https://growgraph.dev/echr#")
PROMPTS = ROOT / "art6" / "ontology" / "prompts"
EXTRACT = (PROMPTS / "extract.txt").read_text(encoding="utf-8")
FACTS = ps.facts_instruction()

# stage-1 field -> ontology class whose members are its vocabulary
SCHEMES = {
    "instance_level": "InstanceLevel",
    "outcome": "ProceedingOutcome",
    "proceeding_type": "ProceedingType",
    "outcome_direction": "OutcomeDirection",
    "matter": "Matter",
}


def members(cls: str) -> set:
    g = Graph()
    g.parse(ROOT / "ontology" / "echr.ttl")
    return set(g.subjects(RDF.type, ECHR[cls]))


@pytest.mark.parametrize("field", list(SCHEMES))
def test_every_ontology_value_is_reachable_from_stage1(field):
    """Each ontology member (bar the Unknown fallbacks the code never needs
    to emit) is the image of some stage-1 value -- a member no prompt offers
    is a value only one arm could ever produce."""
    reachable = {r.vocabulary_iri(field, v) for v in a.VOCABULARY[field]}
    assert None not in reachable, "a stage-1 value has no ontology member"
    missing = {
        m for m in members(SCHEMES[field]) - reachable if not str(m).endswith("Unknown")
    }
    assert not missing, f"{field}: ontology values no prompt offers: {missing}"


@pytest.mark.parametrize("field", list(SCHEMES))
def test_extract_prompt_offers_every_value(field):
    i = EXTRACT.index(f'"{field}":')
    schema_line = EXTRACT[i : EXTRACT.index('",\n', i)]
    for value in a.VOCABULARY[field]:
        if field == "outcome_direction" and value == "unknown":
            continue
        assert value in schema_line, f"extract.txt {field} omits {value!r}"


@pytest.mark.parametrize("field", list(SCHEMES))
def test_facts_maps_every_value(field):
    """facts.txt tells OntoCast which IRI each value is -- on both arms."""
    for value in a.VOCABULARY[field]:
        iri = r.vocabulary_iri(field, value)
        local = str(iri).removeprefix(str(ECHR))
        assert re.search(rf'"{re.escape(value)}" is echr:{local}\b', FACTS), (
            f"facts.txt has no mapping for {field} {value!r} -> echr:{local}"
        )


def test_only_stage1_is_told_not_to_link():
    assert "Do NOT link" in EXTRACT or "are NOT recorded here" in EXTRACT
    assert "Do NOT link entries" not in FACTS


def test_stage1_asks_only_for_what_the_graph_can_hold():
    for dropped in ('"inactivity"', '"adjournments"'):
        assert dropped not in EXTRACT
    assert "inactivity" not in a.EVENT_KEYS and "adjournments" not in a.EVENT_KEYS


@pytest.mark.parametrize(
    "fact, mapping",
    [
        ("finality", "echr:isFinalDomesticDecision"),
        ("detention", "echr:PreTrialDetention"),
        ("pending", "echr:isPending"),
        ("non-occurring", "echr:isNonOccurring"),
        ("case header", "echr:hasApplicationNumber"),
        ("judges", "NO JUDGE IS A PERSON OR A PARTY"),
    ],
)
def test_facts_maps_what_stage1_collects(fact, mapping):
    assert mapping in FACTS, f"facts.txt never says where {fact} goes"


def test_run_native_checks_against_the_composed_instruction():
    from art6.ontology.run_native import _live_facts_prompt

    assert _live_facts_prompt() == FACTS
