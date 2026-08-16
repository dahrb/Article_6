"""Art. 6 ECHR corpus pipeline.

Subpackages mirror the stages of the work:

- :mod:`art6.data` — collection and processing of HUDOC metadata and case text
- :mod:`art6.ontology` — ontology-extraction inputs built on top of that corpus
- :mod:`art6.utils` — helpers shared across both

Every stage is runnable as a module, from any working directory::

    uv run python -m art6.data.collection
    uv run python -m art6.data.metadata_processing
    uv run python -m art6.data.text_processing --corpus judgments
    uv run python -m art6.ontology.build_ontocast_test_set

Shared locations live in :mod:`art6.paths`, so the repo layout is defined in
exactly one place.
"""

__all__ = ["data", "ontology", "paths", "utils"]
