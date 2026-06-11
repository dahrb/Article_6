"""
test_bug_entity_fragmentation.py
----------------------------------
Reproduces: The same real-world entity is minted as multiple distinct IRIs by
different document chunks, producing disconnected graph fragments for one person.

Bug observed in: Gemini Flash Lite run on case 001-102617 (Paksas v. Lithuania)
Run timestamp:   2026-06-09T13:51:16Z
Fixture:         fixtures/entity_fragmentation_source.ttl

Root cause:
    Each chunk independently decides the IRI for an entity it encounters.
    Without a shared entity registry across chunks, the same person
    (Rolandas Paksas) was minted as four separate IRIs:

        doc1:applicant_001             rdfs:label "Applicant"
                                       rdfs:label "Former President of Lithuania"
        doc1:applicant_1               rdfs:label "Applicant"
        doc1:applicant_paksas          rdfs:label "Rolandas Paksas"
        doc1:applicant_rolandas_paksas rdfs:label "Rolandas Paksas"

    None of the four are linked with owl:sameAs or any merge triple.
    Queries that aggregate party information will return partial, duplicate
    results depending on which IRI they traverse.

Expected behaviour:
    A single canonical IRI (e.g. doc1:applicant_rolandas_paksas, the most
    specific name) should be used consistently across all chunks.
    OR: the aggregation step should emit owl:sameAs links between aliases and
    the canonical IRI, so downstream queries can traverse the full graph.

Last Updated: 2026-06-09
Version: 1.0.0
Progress: Complete
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, OWL, URIRef
from rdflib.namespace import RDFS, RDF

FIXTURE = Path(__file__).parent / "fixtures" / "entity_fragmentation_source.ttl"

DOC1 = "https://github.com/dahrb/Art_6/tree/main/facts/001-102617#"
SEED = "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#"

# The four fragmented IRIs for the same applicant
ALL_APPLICANT_IRIS = [
    f"{DOC1}applicant_001",
    f"{DOC1}applicant_1",
    f"{DOC1}applicant_paksas",
    f"{DOC1}applicant_rolandas_paksas",
]

CANONICAL_IRI = f"{DOC1}applicant_rolandas_paksas"


def _load_graph() -> Graph:
    g = Graph()
    g.parse(str(FIXTURE), format="turtle")
    return g


class TestEntityFragmentation:
    """Regression tests for the entity-fragmentation bug."""

    def test_fixture_parses(self):
        """Fixture must parse cleanly."""
        g = _load_graph()
        assert len(g) > 0

    def test_four_applicant_iris_exist(self):
        """
        Demonstrates the bug: all four distinct applicant IRIs are present in
        the graph, each with at least one triple asserting something about them.
        """
        g = _load_graph()
        present = [
            iri for iri in ALL_APPLICANT_IRIS
            if (URIRef(iri), None, None) in g
        ]
        assert len(present) == 4, (
            f"Expected 4 fragmented IRIs to be present, found {len(present)}: {present}"
        )

    def test_two_iris_share_same_label_string(self):
        """
        Demonstrates: doc1:applicant_paksas and doc1:applicant_rolandas_paksas
        both carry rdfs:label "Rolandas Paksas" — identical label, different IRI.
        This is a clear sign of unmerged duplicates.
        """
        g = _load_graph()
        paksas_label = "Rolandas Paksas"
        nodes_with_label = [
            str(s)
            for s, _, o in g.triples((None, RDFS.label, None))
            if str(o) == paksas_label and isinstance(s, URIRef)
        ]
        assert len(nodes_with_label) >= 2, (
            f"Expected at least 2 nodes with label '{paksas_label}', "
            f"found {len(nodes_with_label)}: {nodes_with_label}"
        )

    def test_applicant_001_has_multiple_labels(self):
        """
        Demonstrates compound bug: doc1:applicant_001 carries BOTH
        'Applicant' and 'Former President of Lithuania' — two different
        labels from two different chunks on the same generic IRI.
        """
        g = _load_graph()
        node = URIRef(f"{DOC1}applicant_001")
        labels = [str(o) for _, _, o in g.triples((node, RDFS.label, None))]
        assert len(labels) == 2, (
            f"Expected 2 labels on applicant_001 (demonstrating the bug), "
            f"got {len(labels)}: {labels}"
        )

    def test_no_same_as_links_between_fragments(self):
        """
        Demonstrates: none of the four IRIs are linked with owl:sameAs.
        Without these links, a SPARQL query on any single IRI will miss
        facts stored under the other three.
        """
        g = _load_graph()
        same_as_pairs = list(g.triples((None, OWL.sameAs, None)))
        # Filter to applicant IRIs only
        applicant_same_as = [
            (str(s), str(o))
            for s, _, o in same_as_pairs
            if str(s) in ALL_APPLICANT_IRIS or str(o) in ALL_APPLICANT_IRIS
        ]
        assert len(applicant_same_as) == 0, (
            "Unexpectedly found owl:sameAs links — bug may already be partially fixed. "
            f"Links found: {applicant_same_as}"
        )

    @pytest.mark.xfail(reason="Bug not yet fixed: all chunks should use a single canonical IRI")
    def test_single_canonical_applicant_iri(self):
        """
        EXPECTED BEHAVIOUR (currently failing):
        Only one applicant IRI should exist in the graph.
        The canonical IRI should be the most specific name-based one.
        """
        g = _load_graph()
        present = [
            iri for iri in ALL_APPLICANT_IRIS
            if (URIRef(iri), None, None) in g
        ]
        assert len(present) == 1 and present[0] == CANONICAL_IRI, (
            f"Expected only {CANONICAL_IRI}, found: {present}"
        )

    @pytest.mark.xfail(reason="Bug not yet fixed: fragments should be merged via owl:sameAs")
    def test_fragments_linked_by_same_as(self):
        """
        ACCEPTABLE ALTERNATIVE FIX:
        If multiple IRIs must coexist, they should all be linked to the
        canonical IRI via owl:sameAs so queries can traverse the full graph.
        """
        g = _load_graph()
        canonical = URIRef(CANONICAL_IRI)
        for iri in ALL_APPLICANT_IRIS:
            if iri == CANONICAL_IRI:
                continue
            node = URIRef(iri)
            linked = (
                (node, OWL.sameAs, canonical) in g
                or (canonical, OWL.sameAs, node) in g
            )
            assert linked, (
                f"{iri} is not linked to canonical IRI {CANONICAL_IRI} via owl:sameAs"
            )
