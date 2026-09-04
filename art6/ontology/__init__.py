"""Ontology-extraction work built on top of the Art. 6 corpus.

Stage 4 of the pipeline lives here: it consumes the processed corpus and emits
inputs for extraction experiments rather than corpus data itself. Building
that input sample is :mod:`art6.data.build_ontocast_test_set`; everything
under here consumes the sample it produces.
"""

__all__ = []
