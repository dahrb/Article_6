"""
envelope_repair.py
------------------
Re-wrap facts-render responses that arrive as a BARE graph, without the
`FactsRenderReport` envelope OntoCast asks for.

WHAT BREAKS
-----------
OntoCast asks the facts renderer for a `FactsRenderReport`:

    {"semantic_graph": {"@context": {...}, "@graph": [...]},
     "ontology_relevance_score": 88, "triples_generation_score": 90, ...}

The OUTPUT INSTRUCTION for jsonld (ontocast/prompt/graph_format.py) describes
in detail how to encode *the graph field*, and says nothing about the envelope
-- the envelope comes from the Pydantic schema in `format_instructions`.
gpt-5-mini reads that emphasis and returns the graph field ALONE:

    {"@context": {...}, "@graph": [...]}

which is valid JSON, valid JSON-LD, and complete -- it simply has no
`semantic_graph` key, so `FactsRenderReport.model_validate` rejects it and the
unit is dropped. Measured 2026-08-24 on Stanev at whole-document: one call,
19,309 output tokens, 26,799 chars of correct JSON-LD carrying every entity in
the document, and `facts_triples_generated: 0`. The log records only
"Parallel facts map failed without usable output for 1/1 unit(s)"; nothing
counts it as a parse failure, because parsing SUCCEEDED -- it was schema
validation that failed.

The failure is size-dependent rather than absolute: the same model at
8000/16000 chunks returned the correct envelope on both chunks of the same
document (380 triples). So it cannot be left to chance -- it silently removes
whole documents from exactly the whole-document arm that the study's
matched-assembly contrast depends on.

WHY THIS IS A HARNESS FIX, NOT A THUMB ON THE SCALE
----------------------------------------------------
This is the same class of fault as `max_completion_tokens` on gpt-5 models and
the malformed-JSON recovery in response_repair.py: the model demonstrably knows
the answer and encodes it correctly, and loses it to a serialization contract.
Reporting that as "gpt-5-mini extracts nothing at whole-document" would be a
measurement artefact, not a finding.

It is also symmetric, which is the important part for the O2 comparison: the
patch is a NO-OP for any response that already carries the envelope, so gemma's
numbers cannot move. It only ever adds a wrapper that was missing.

WHAT THIS DELIBERATELY DOES NOT DO
-----------------------------------
It does not touch any other response model. The rewrap is installed on
`FactsRenderReport` alone, and fires only when the payload has no
`semantic_graph` key AND looks like a JSON-LD document (`@graph` or `@context`).
A response that is merely missing the scores, or malformed in any other way,
is left to fail exactly as before -- those are real results.

Nothing here writes to the ontocast checkout; the patch lives for the lifetime
of the process that calls `enable()`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False

# Count of responses actually re-wrapped, for the run report.
rewrapped = 0


def _looks_like_bare_jsonld(obj: object) -> bool:
    """True for a JSON-LD document handed over without its envelope.

    Deliberately narrow. `semantic_graph` absent is not enough on its own --
    a truncated or hallucinated response is also missing it, and those must
    keep failing. The payload must additionally carry JSON-LD's own markers.
    """
    if not isinstance(obj, dict):
        return False
    if "semantic_graph" in obj:
        return False
    return "@graph" in obj or "@context" in obj


def enable() -> None:
    """Install the rewrap. Idempotent; safe to call once per process."""
    global _INSTALLED
    if _INSTALLED:
        return

    from ontocast.onto.model import FactsRenderReport

    original = FactsRenderReport.model_validate.__func__  # type: ignore[attr-defined]

    def patched_model_validate(cls, obj, *args, **kwargs):
        global rewrapped
        if _looks_like_bare_jsonld(obj):
            rewrapped += 1
            logger.warning(
                "envelope repair: facts response arrived without the "
                "FactsRenderReport envelope; wrapping %d top-level key(s) "
                "into semantic_graph",
                len(obj),
            )
            obj = {"semantic_graph": obj}
        return original(cls, obj, *args, **kwargs)

    FactsRenderReport.model_validate = classmethod(patched_model_validate)  # type: ignore[assignment]
    _INSTALLED = True
