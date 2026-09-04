"""Stages 1-3: HUDOC collection, metadata processing, and case-text processing.

The data files themselves live in the repo's top-level `data/` directory; this
package holds only the code that produces them. :mod:`build_ontocast_test_set`
draws the ontology-extraction sample from that processed corpus -- output, not
an input stage, but it belongs here rather than in :mod:`art6.ontology` since
it reads the corpus directly and produces no ontology-extraction code itself.
"""

__all__ = [
    "build_ontocast_test_set",
    "collection",
    "metadata_processing",
    "text_processing",
]
