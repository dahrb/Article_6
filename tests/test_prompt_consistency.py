"""Every mode asks for the same knowledge-graph content.

C0 (flat baseline), C1/C2 (OntoCast on the judgment) and C3/C4 (the agentic
evidence stage, then OntoCast) differ in method only. These tests fail when a
mode's prompt drifts: a vocabulary value one mode can produce and another
cannot, a shared definition one mode never sees, a field stage 1 collects that
the graph has nowhere to put.
"""

import re
from pathlib import Path

import pytest
from rdflib import RDF, Graph, Namespace

from art6.conditions.run_conditions import load_condition_prompt
from art6.conditions.schema import NormalisedDocument
from art6.ontology import agentic_stage1 as a
from art6.ontology import prompt_spec as ps
from art6.ontology import reconcile as r

ROOT = Path(__file__).resolve().parents[1]
ECHR = Namespace("https://growgraph.dev/echr#")
PROMPTS = ROOT / "art6" / "ontology" / "prompts"
EXTRACT = (PROMPTS / "extract.txt").read_text(encoding="utf-8")
FACTS = (PROMPTS / "facts.txt").read_text(encoding="utf-8")
C0 = load_condition_prompt("o1")

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


@pytest.mark.parametrize(
    "field", ["instance_level", "outcome", "proceeding_type", "outcome_direction"]
)
def test_c0_offers_the_same_values(field):
    i = C0.index(f'"{field}"')
    block = re.sub(r"\s+", " ", C0[i : C0.index('\n  "', i + 1)])
    for value in a.VOCABULARY[field]:
        if field == "outcome" and value == "other":
            assert "other" in block
            continue
        assert value in block, f"C0 {field} omits {value!r}"


def test_c0_authority_kinds_match_the_ontology():
    i = C0.index('"authority_kind"')
    block = C0[i : C0.index("\n", i)]
    offered = {
        v.strip() for v in block.split('"authority_kind"')[1].strip(" .").split("|")
    }
    kinds = {
        str(m).removeprefix(str(ECHR) + "Authority").lower()
        for m in members("AuthorityKind")
    }
    assert offered == kinds


def test_every_mode_gets_the_shared_definitions():
    rules = ps.shared_rules()
    facts = ps.facts_instruction()
    assert facts.startswith(FACTS.strip()[:200]) and rules.strip() in facts
    assert rules.strip() in C0
    for section in ps.SHARED_SECTIONS:
        assert f"## {section}" in facts and f"## {section}" in C0
    # stage 1's critic and fixer are held to the same sections
    for section in a.RULE_SECTIONS:
        assert section in ps.SHARED_SECTIONS


def test_no_mode_but_stage1_is_told_not_to_link():
    assert "Do NOT link entries" in EXTRACT
    assert "Do NOT link entries" not in ps.facts_instruction()
    assert "Do NOT link entries" not in C0


def test_c0_no_longer_has_its_own_entry_definition():
    assert "ONE ENTRY PER INSTANCE" not in C0
    assert "COVERAGE AND GRANULARITY" in C0


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

    assert _live_facts_prompt() == ps.facts_instruction().strip()


def test_c0_schema_accepts_old_and_new_records():
    NormalisedDocument.model_validate({"proceedings": [{"order": 1}]})
    doc = NormalisedDocument.model_validate(
        {
            "case": {"case_name": "X v. Y"},
            "applicants": [{"name": "X", "birth_year": "1970"}],
            "persons": [{"name": "Mr A.", "role": "lawyer", "represents": "X"}],
            "proceedings": [
                {"order": 1, "matter": ["requests"], "is_final": True, "part_of": None}
            ],
        }
    )
    assert doc.proceedings[0].matter == ["requests"]


def test_a_renamed_section_fails_loudly():
    with pytest.raises(KeyError):
        ps.load_sections(ps.EXTRACT, ("NO SUCH SECTION",))
