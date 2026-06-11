"""
test_bug_jsonld_type_array.py
=============================================================
Reproducible bug report for ontocast — JSON-LD normalization failure.

Last updated : 2026-06-09
Progress     : Fixed cp1252 print character bug
Version history:
  0.1.0 – Initial report extracted from production run on case 001-104726
  0.1.1 – Fixed cp1252 unicode character prints

ISSUE
-----
When the LLM produces a JSON-LD graph where a node has *multiple* OWL
property characteristics (e.g. both owl:DatatypeProperty and
owl:FunctionalProperty), it emits:

    "@type": [
        {"@id": "owl:DatatypeProperty"},
        {"@id": "owl:FunctionalProperty"}
    ]

pyld's URDNA2015 normalizer rejects this because the JSON-LD spec
requires @type values to be plain strings (compact IRIs), not
{"@id": ...} node-objects.  The error thrown is:

    JsonLdError: Invalid JSON-LD syntax; "@type" value must be a
    string, an array of strings, or an empty object.
    Code: invalid type value
    Details: {'value': [{'@id': 'owl:DatatypeProperty'},
                        {'@id': 'owl:FunctionalProperty'}]}

ontocast catches this in RDFGraph._from_jsonld_str (rdfgraph.py:1043)
and falls back to rdflib's json-ld parser, which *is* tolerant.
The fallback works but silently drops the WARNING log; the caller never
learns which triple was affected.

EXPECTED BEHAVIOUR
------------------
Either:
  (a) pyld should accept {"@id": ...} objects as @type values (upstream fix),
  OR
  (b) ontocast should pre-process the graph and flatten
      `"@type": [{"@id": "x"}, {"@id": "y"}]`
      into `"@type": ["x", "y"]` before calling jsonld.normalize().

REPRODUCTION
------------
Run this file with the ontocast venv active:
    uv run python issues/tests/test_bug_jsonld_type_array.py
"""

import json
import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal JSON-LD that reproduces the issue — extracted from the live
# LLM response cached at:
#   .cache/ontocast/art_6/llm/c2ea96d1...f1.json
# (graph_update -> triple_operations[0] -> graph -> @graph)
# The two properties seed:hasPecuniaryDamageAmount and
# seed:hasNonPecuniaryDamageAmount both carry:
#   "@type": [{"@id": "owl:DatatypeProperty"}, {"@id": "owl:FunctionalProperty"}]
# ---------------------------------------------------------------------------

MINIMAL_JSONLD = {
    "@context": {
        "owl": "http://www.w3.org/2002/07/owl#",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "schema": "https://schema.org/",
        "seed": "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#",
    },
    "@graph": [
        {
            # ── This is the problematic node ────────────────────────────────
            "@id": "seed:hasPecuniaryDamageAmount",
            # BUG: @type is an array of node-objects, not plain strings.
            # The JSON-LD spec requires: "@type": ["owl:DatatypeProperty",
            #                                      "owl:FunctionalProperty"]
            "@type": [
                {"@id": "owl:DatatypeProperty"},
                {"@id": "owl:FunctionalProperty"},
            ],
            "rdfs:label": {"@value": "has Pecuniary Damage Amount", "@language": "en"},
            "rdfs:domain": {"@id": "seed:PecuniaryDamage"},
            "rdfs:range": {"@id": "xsd:decimal"},
            "schema:unitCode": "EUR",
        },
    ],
}

# The corrected form that pyld *can* handle:
CORRECTED_JSONLD = {
    "@context": MINIMAL_JSONLD["@context"],
    "@graph": [
        {
            "@id": "seed:hasPecuniaryDamageAmount",
            "@type": ["owl:DatatypeProperty", "owl:FunctionalProperty"],  # plain strings
            "rdfs:label": {"@value": "has Pecuniary Damage Amount", "@language": "en"},
            "rdfs:domain": {"@id": "seed:PecuniaryDamage"},
            "rdfs:range": {"@id": "xsd:decimal"},
            "schema:unitCode": "EUR",
        },
    ],
}


def run_tests():
    try:
        import pyld.jsonld as jsonld
    except ImportError:
        sys.exit("pyld not found — run inside the ontocast venv")

    try:
        from ontocast.onto.rdfgraph import RDFGraph
    except ImportError:
        sys.exit("ontocast not importable — run inside the ontocast venv")

    opts = {"algorithm": "URDNA2015", "format": "application/n-quads"}

    # ── Test 1: confirm pyld rejects the buggy form ────────────────────────
    print("=" * 60)
    print("Test 1 — pyld rejects @type as array-of-node-objects")
    print("=" * 60)
    try:
        jsonld.normalize(MINIMAL_JSONLD, opts)
        print("UNEXPECTED PASS — pyld did NOT raise; bug may be fixed upstream.")
    except Exception as exc:
        print(f"[CONFIRMED BUG] pyld raised: {type(exc).__name__}")
        print(f"  Message : {exc}")
        print()

    # ── Test 2: confirm pyld accepts the corrected form ───────────────────
    print("=" * 60)
    print("Test 2 — pyld accepts @type as array-of-strings (correct form)")
    print("=" * 60)
    try:
        result = jsonld.normalize(CORRECTED_JSONLD, opts)
        print(f"[PASS] Normalized to {len(result.splitlines())} n-quad line(s).")
        print()
    except Exception as exc:
        print(f"[UNEXPECTED FAIL] {type(exc).__name__}: {exc}")
        print()

    # ── Test 3: confirm RDFGraph._from_jsonld_str falls back gracefully ───
    print("=" * 60)
    print("Test 3 — RDFGraph._from_jsonld_str fallback (rdflib parser)")
    print("=" * 60)
    import logging
    logging.basicConfig(level=logging.WARNING)
    try:
        g = RDFGraph._from_jsonld_str(json.dumps(MINIMAL_JSONLD))
        triple_count = len(list(g))
        print(f"[PASS — fallback] rdflib parsed {triple_count} triple(s).")
        print("  WARNING: the fallback silently swallows the pyld error.")
        print("  A pre-processing step should flatten @type before calling pyld.")
    except Exception as exc:
        print(f"[FAIL] Even fallback raised: {type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ── Test 4: demonstrate the proposed fix (pre-processing) ─────────────
    print()
    print("=" * 60)
    print("Test 4 — proposed fix: flatten @type node-objects -> strings")
    print("=" * 60)

    def flatten_type_values(data):
        """Recursively flatten @type: [{@id: x}] -> @type: [x] in JSON-LD."""
        if isinstance(data, dict):
            if "@type" in data:
                t = data["@type"]
                if isinstance(t, list):
                    flat = []
                    for v in t:
                        if isinstance(v, dict) and "@id" in v:
                            flat.append(v["@id"])
                        else:
                            flat.append(v)
                    data = {**data, "@type": flat}
                elif isinstance(t, dict) and "@id" in t:
                    data = {**data, "@type": t["@id"]}
            return {k: flatten_type_values(v) for k, v in data.items()}
        if isinstance(data, list):
            return [flatten_type_values(item) for item in data]
        return data

    fixed = flatten_type_values(MINIMAL_JSONLD)
    try:
        result = jsonld.normalize(fixed, opts)
        print(f"[PASS] After pre-processing, pyld normalizes to "
              f"{len(result.splitlines())} n-quad line(s).")
        print("  Suggested fix: apply flatten_type_values() in "
              "RDFGraph._from_jsonld_str before calling jsonld.normalize().")
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    run_tests()
