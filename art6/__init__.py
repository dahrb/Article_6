"""Art. 6 ECHR: domestic proceedings extraction.

- :mod:`art6.model` -- the set model (vocabularies, file format, validation, order)
- :mod:`art6.stage1` -- stage 1: extraction, vote, payloads
- :mod:`art6.ontology` -- stages 2-3: OntoCast graph construction and reconciliation
- :mod:`art6.evaluation` -- alignment, scoring, agreement, the reasonable-time rule
- :mod:`art6.baselines` -- the non-LLM baseline
- :mod:`art6.data` -- HUDOC collection, metadata and sampling
- :mod:`art6.utils` -- helpers

Every stage runs as a module (``uv run python -m art6.stage1.extract`` ...);
shared locations live in :mod:`art6.paths`.
"""

__all__ = [
    "baselines",
    "data",
    "evaluation",
    "model",
    "ontology",
    "paths",
    "stage1",
    "utils",
]
