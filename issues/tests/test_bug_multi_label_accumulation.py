"""
test_bug_multi_label_accumulation.py
--------------------------------------
Reproduces: Multiple rdfs:label triples accumulate on the same IRI when the
same entity appears across more than one document chunk and OntoCast's
aggregator performs a graph union without label deduplication.

Bug observed in: Gemini Flash Lite run on case 001-102617 (Paksas v. Lithuania)
Run timestamp:   2026-06-09T13:51:16Z

Strategy:
    We use OntoCast's real EmbeddingBasedAggregator with the actual cached JSON
    outputs from the gemini-3.1-flash-lite facts extraction run. This avoids
    needing raw text files, as the actual prompts and semantic graphs are stored
    inside the local JSONs in `fixtures/` to make the test self-sufficient.

Last Updated: 2026-06-10
Version: 1.2.1
Progress: Complete

Version History:
    1.0.0: Initial version using synthetic TTL snippets and raw .txt files.
    1.1.0: Refactored to load cached LLM response JSONs directly, removing raw text chunk fixtures.
    1.2.0: Localized the cached LLM response JSONs to the local fixtures directory for isolation.
    1.2.1: Fixed undefined name cache_dir -> fixtures_dir in pytest.fail error message.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest
from rdflib import URIRef
from rdflib.namespace import RDFS

from ontocast.onto.content_unit import ContentUnit, OutputType
from ontocast.onto.rdfgraph import RDFGraph
from ontocast.tool.agg.aggregate import EmbeddingBasedAggregator

# ---------------------------------------------------------------------------
# Paths & IRI constants
# ---------------------------------------------------------------------------
SEED_TTL  = Path(__file__).parent.parent / "ontologies" / "seed.ttl"

DOC1     = "https://github.com/dahrb/Art_6/tree/main/facts/001-102617#"
PROC_05_25 = URIRef(f"{DOC1}proc_2004_05_25")
PROC_05_28 = URIRef(f"{DOC1}proc_2004_05_28")
PROC_04_06 = URIRef(f"{DOC1}proc_2004_04_06")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_ontology() -> RDFGraph:
    g = RDFGraph()
    g.parse(str(SEED_TTL), format="turtle")
    return g


def _labels_per_subject(g: RDFGraph) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for s, _, o in g.triples((None, RDFS.label, None)):
        if isinstance(s, URIRef):
            result[str(s)].append(str(o))
    return dict(result)


# ---------------------------------------------------------------------------
# Fixture: aggregated graph produced by OntoCast's real aggregator
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def aggregated_graph() -> RDFGraph:
    """Run the real EmbeddingBasedAggregator on the actual cached Paksas chunks."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    if not fixtures_dir.exists():
        pytest.fail(f"OntoCast fixtures directory does not exist: {fixtures_dir}")
        
    units = []
    idx = 0
    # Load all cached JSON files matching the criteria
    for p in sorted(fixtures_dir.glob("*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            config = data.get("config", {})
            if config.get("model_name") != "gemini-3.1-flash-lite":
                continue
            content_str = data.get("result", {}).get("content", "")
            if "semantic_graph" not in content_str:
                continue
            prompt = data.get("result", {}).get("prompt", "")
            if "102617" not in prompt and "Paksas" not in prompt:
                continue
                
            # Parse text excerpt from prompt
            excerpt = ""
            if "Here is the text excerpt:" in prompt:
                excerpt = prompt.split("Here is the text excerpt:")[1].split("#")[0].split("Additional user instruction")[0].strip()
            elif "# TEXT" in prompt:
                excerpt = prompt.split("# TEXT")[1].split("#")[0].split("OUTPUT INSTRUCTION")[0].strip()
            elif "text excerpt:" in prompt:
                excerpt = prompt.split("text excerpt:")[1].split("#")[0].strip()
            excerpt_clean = excerpt.replace("```", "").strip()
            
            content = json.loads(content_str)
            graph_data = content.get("semantic_graph", {})
            
            rdf_graph = RDFGraph()
            rdf_graph.parse(data=json.dumps(graph_data), format="json-ld")
            
            unit = ContentUnit(
                text=excerpt_clean,
                index=idx,
                doc_iri=URIRef("https://github.com/dahrb/Art_6/tree/main/facts/001-102617"),
                type=OutputType.FACTS,
                graph=rdf_graph,
            )
            units.append(unit)
            idx += 1
        except Exception:
            pass

    if not units:
        pytest.fail(f"No gemini-3.1-flash-lite facts files found in {fixtures_dir}")
        
    ontology = _load_ontology()
    aggregator = EmbeddingBasedAggregator(
        embedding_model="paraphrase-multilingual-MiniLM-L12-v2",
        similarity_threshold=0.80,
    )
    return aggregator.aggregate_graphs(units, ontology)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMultiLabelAccumulation:
    """
    Regression tests for the multi-label accumulation bug.

    Tests marked PASSES confirm the bug is reproducible.
    Tests marked @pytest.mark.xfail document the desired fixed behaviour.
    """

    def test_aggregator_returns_a_graph(self, aggregated_graph):
        """Sanity check: the aggregator should return a non-empty graph."""
        assert len(aggregated_graph) > 0

    def test_proc_2004_05_25_has_multiple_labels(self, aggregated_graph):
        """
        DEMONSTRATES BUG: proc_2004_05_25 carries 4 rdfs:label triples after
        aggregation — one from each chunk that independently described the node.
        This test PASSES, confirming the bug is present.
        """
        labels = _labels_per_subject(aggregated_graph)
        node_labels = labels.get(str(PROC_05_25), [])
        assert len(node_labels) >= 3, (
            f"Expected >= 3 labels (demonstrating multi-label bug), "
            f"got {len(node_labels)}: {node_labels}"
        )

    def test_proc_2004_05_28_has_multiple_labels(self, aggregated_graph):
        """DEMONSTRATES BUG: proc_2004_05_28 carries 3 labels after aggregation."""
        labels = _labels_per_subject(aggregated_graph)
        node_labels = labels.get(str(PROC_05_28), [])
        assert len(node_labels) >= 2, (
            f"Expected >= 2 labels on proc_2004_05_28, got {len(node_labels)}: {node_labels}"
        )

    def test_proc_2004_04_06_has_multiple_labels(self, aggregated_graph):
        """DEMONSTRATES BUG: proc_2004_04_06 gets 2 different labels from 2 chunks."""
        labels = _labels_per_subject(aggregated_graph)
        node_labels = labels.get(str(PROC_04_06), [])
        assert len(node_labels) >= 2, (
            f"Expected >= 2 labels on proc_2004_04_06, got {len(node_labels)}: {node_labels}"
        )

    def test_capitalisation_duplicate_present(self, aggregated_graph):
        """
        DEMONSTRATES BUG: chunk C emits two labels for proc_2004_05_25 that
        differ only by capitalisation ('ruling' vs 'Ruling'). Both survive
        aggregation — a within-chunk dedup failure.
        """
        labels = _labels_per_subject(aggregated_graph)
        node_labels = labels.get(str(PROC_05_25), [])
        lower = [l.lower() for l in node_labels]
        assert len(lower) > len(set(lower)), (
            f"Expected at least one case-only duplicate on proc_2004_05_25, "
            f"but none found. Labels: {node_labels}"
        )

    def test_applicant_001_has_multiple_labels(self, aggregated_graph):
        """
        DEMONSTRATES BUG: applicant_001 accumulates both 'Applicant' and
        'Former President of Lithuania'.
        """
        labels = _labels_per_subject(aggregated_graph)
        applicant_iri = f"{DOC1}applicant_001"
        node_labels = labels.get(applicant_iri, [])
        assert len(node_labels) >= 2, (
            f"Expected >= 2 labels on applicant_001 (demonstrating bug), "
            f"got {len(node_labels)}: {node_labels}"
        )

    @pytest.mark.xfail(
        reason="Bug not yet fixed: aggregator should keep at most one rdfs:label per subject"
    )
    def test_each_node_has_at_most_one_label(self, aggregated_graph):
        """
        EXPECTED BEHAVIOUR (currently failing):
        After aggregation, every IRI should carry at most one rdfs:label.
        The aggregator should deduplicate or elect a canonical label.
        """
        labels = _labels_per_subject(aggregated_graph)
        multi = {s: lbls for s, lbls in labels.items() if len(lbls) > 1}
        assert not multi, (
            f"{len(multi)} node(s) still have multiple rdfs:label after aggregation:\n"
            + "\n".join(f"  {s}: {lbls}" for s, lbls in sorted(multi.items()))
        )

    @pytest.mark.xfail(
        reason="Bug not yet fixed: case-duplicate labels should be normalised before union"
    )
    def test_labels_are_case_normalised_before_union(self, aggregated_graph):
        """
        EXPECTED BEHAVIOUR (currently failing):
        No two labels on the same node should differ only by capitalisation.
        The aggregator (or the per-chunk critic) should normalise before merging.
        """
        labels = _labels_per_subject(aggregated_graph)
        for subj, lbls in labels.items():
            lower = [l.lower() for l in lbls]
            assert len(set(lower)) == len(lower), (
                f"Case-duplicate labels on {subj}: {lbls}"
            )
