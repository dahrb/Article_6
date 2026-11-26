"""Stage 1, stage 2 and the ontology agree on what the graph holds.

These tests fail when a prompt drifts: a closed-list value stage 1 can
produce that the ontology or the stage-2 instruction has no place for, an
ontology value stage 1 never offers, a field stage 1 collects that the graph
cannot hold.
"""

import re
from pathlib import Path

import pytest
from rdflib import RDF, Graph, Namespace

from art6 import model
from art6.ontology import prompt_spec as ps
from art6.ontology import reconcile as r
from art6.stage1.to_payload import LEVEL, words

ROOT = Path(__file__).resolve().parents[1]
ECHR = Namespace("https://growgraph.dev/echr#")
EXTRACT = (ROOT / "art6" / "stage1" / "prompts" / "extract.txt").read_text(
    encoding="utf-8"
)
FACTS = ps.facts_instruction()

# payload field -> (set-model values, payload spelling, ontology class)
FIELDS = {
    "instance_level": (model.LEVELS, LEVEL.get, "InstanceLevel"),
    "outcome": (model.OUTCOMES, words, "ProceedingOutcome"),
    "proceeding_type": (model.KINDS, str.lower, "ProceedingType"),
    "outcome_direction": (model.DIRECTIONS, words, "OutcomeDirection"),
    "matter": (model.MATTERS, words, "Matter"),
}


def members(cls: str) -> set:
    g = Graph()
    g.parse(ROOT / "ontology" / "echr.ttl")
    return set(g.subjects(RDF.type, ECHR[cls]))


@pytest.mark.parametrize("field", list(FIELDS))
def test_every_stage1_value_has_an_ontology_member_and_back(field):
    values, spell, cls = FIELDS[field]
    reachable = {r.vocabulary_iri(field, spell(v)) for v in values}
    assert None not in reachable, f"{field}: a stage-1 value has no ontology member"
    missing = {m for m in members(cls) - reachable if not str(m).endswith("Unknown")}
    assert not missing, f"{field}: ontology values stage 1 never offers: {missing}"


@pytest.mark.parametrize("field", list(FIELDS))
def test_facts_maps_every_value(field):
    """facts.txt tells OntoCast which IRI each payload value is."""
    values, spell, _ = FIELDS[field]
    for v in values:
        value = spell(v)
        local = str(r.vocabulary_iri(field, value)).removeprefix(str(ECHR))
        assert re.search(rf'"{re.escape(value)}" is echr:{local}\b', FACTS), (
            f"facts.txt has no mapping for {field} {value!r} -> echr:{local}"
        )


@pytest.mark.parametrize(
    "values",
    [
        model.KINDS,
        model.LEVELS,
        model.OUTCOMES,
        model.DIRECTIONS,
        model.AUTHORITY_KINDS,
        model.SIDES,
        model.APPEALED_BY,
        model.MATTERS,
    ],
)
def test_extract_prompt_offers_every_closed_value(values):
    for v in values:
        assert re.search(rf"\b{v}\b", EXTRACT), (
            f"prompts/extract.txt never offers {v!r}"
        )


@pytest.mark.parametrize("field", model.DECISION_FIELDS)
def test_extract_prompt_asks_for_every_decision_field(field):
    assert f'"{field}"' in EXTRACT


@pytest.mark.parametrize(
    "fact, mapping",
    [
        ("pending", "echr:isPending"),
        ("case header", "echr:hasApplicationNumber"),
        ("judges", "NO JUDGE IS A PERSON OR A PARTY"),
        ("appealed_by", "echr:appealedBy"),
        ("costs", "echr:costsBorneBy"),
        ("communication date", "echr:hasCommunicationDate"),
    ],
)
def test_facts_maps_what_stage1_collects(fact, mapping):
    assert mapping in FACTS, f"facts.txt never says where {fact} goes"


def test_run_native_checks_against_the_composed_instruction():
    from art6.ontology.run_native import _live_facts_prompt

    assert _live_facts_prompt() == FACTS
