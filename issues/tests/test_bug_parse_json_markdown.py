"""
test_bug_parse_json_markdown.py
=============================================================
Reproducible bug report for ontocast — OntologyCritiqueReport parse
failure (attempt 3/3) even when the LLM produced valid JSON.

Last updated : 2026-06-09
Progress     : Fixed unicode characters to prevent cp1252 error
Version history:
  0.1.0 – Initial report
  0.2.0 – Relocated cache file to local fixtures directory
  0.2.1 – Fixed unicode characters in documentation

ISSUE
-----
call_llm_with_retry (ontocast/agent/common.py) fails to parse a
structurally valid OntologyCritiqueReport JSON on all 3 attempts.

The root error reported is:
    1 validation error for OntologyCritiqueReport
    Input should be a valid dictionary or instance of OntologyCritiqueReport
    [type=model_type, input_value=None, input_type=NoneType]

input_value=None means parse_json_markdown() returned None even though
the cached content IS valid JSON.

ROOT CAUSE
----------
strip_trailing_commas() uses the regex:
    r",(\s*[}\]])"
applied to the RAW string content.

Some actionable_ontology_fixes entries carry a correct_value field that
is a JSON-LD object serialized as a JSON string — i.e. doubly-escaped
JSON embedded inside a string literal in the outer JSON.

Example (unescaped to show the inner JSON):
    "correct_value": "{\"@context\": {...}, \"@id\": \"seed:Judgment\",
                       \"rdfs:comment\": {\"@value\": \"...\", \"@language\": \"en\"}}"

After json.dumps, the outer JSON string contains the literal text:
    ,\\\"@language\\\": \\\"en\\\"}}\\"

The regex sees , followed by a literal } (from \\}) and strips it,
corrupting the string value.  After corruption parse_json_markdown can
no longer find valid JSON -> returns None.

EXPECTED BEHAVIOUR
------------------
strip_trailing_commas should only strip trailing commas that appear
*outside* string literals.  It should not modify content inside
JSON string values.

REPRODUCTION
------------
Run with the ontocast venv active:
    uv run python issues/tests/test_bug_parse_json_markdown.py
"""

import json
import sys

# ---------------------------------------------------------------------------
# The exact content string returned by the LLM (attempt 3/3 for chunk 1 of
# case 001-104726, ontology critique for the Schelling v. Austria header).
# Extracted from cache key:
#   0c165913194aeec377752d3d56830b7bf95d1fd1c7503253bda67de483298478.json
#
# This is what _content_to_str(response.content) returns.
# We load the cache file if present; otherwise fall back to a synthetic
# minimal that triggers the same failure path.
# ---------------------------------------------------------------------------
import pathlib
CACHE_FILE = pathlib.Path(__file__).parent / "fixtures" / "0c165913194aeec377752d3d56830b7bf95d1fd1c7503253bda67de483298478.json"

# Minimal synthetic reproducer — triggers the SAME regex corruption
# without needing the full cache file.
MINIMAL_VALID_JSON = json.dumps(
    {
        "success": True,
        "score": 84,
        "actionable_ontology_fixes": [
            {
                "text_fragment": "the Court's judgment of 10 November 2005",
                "action": "ADD",
                "severity": "critical",
                "target": "entity",
                "incorrect_value": None,
                # The inner JSON-LD string — this is what breaks the regex.
                # After json.dumps the outer JSON contains literal ,\"}}" which
                # the strip_trailing_commas regex matches and strips.
                "correct_value": json.dumps(
                    {
                        "@context": {
                            "seed": "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#",
                            "owl": "http://www.w3.org/2002/07/owl#",
                            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                        },
                        "@id": "seed:Judgment",
                        "rdf:type": {"@id": "owl:Class"},
                        "rdfs:label": {"@value": "Judgment", "@language": "en"},
                        "rdfs:comment": {
                            "@value": "ECHR Judgment class reserved for supranational records.",
                            "@language": "en",
                        },
                    }
                ),
                "explanation": "The ontology lacks a declared class for ECHR Judgments.",
            }
        ],
        "systemic_critique_summary": "Missing ECHR-level declarations.",
        "external_evidence_request": {
            "initiate_search": False,
            "rationale": "",
            "query_hints": [],
            "confidence": 0.0,
        },
    }
)


def load_cached_content() -> str:
    """Return the cached LLM content string if available, else use synthetic."""
    try:
        import pathlib
        data = json.loads(pathlib.Path(CACHE_FILE).read_text(encoding="utf-8"))
        content = data["result"]["content"]
        print(f"[INFO] Loaded from cache file ({len(content)} chars)")
        return content
    except Exception as exc:
        print(f"[INFO] Cache file not readable ({exc}); using synthetic reproducer.")
        return MINIMAL_VALID_JSON


def run_tests():
    try:
        from langchain_core.utils.json import parse_json_markdown
        from ontocast.agent.common import strip_json_comments, strip_trailing_commas
        from ontocast.onto.model import OntologyCritiqueReport
        from langchain_core.output_parsers import PydanticOutputParser
    except ImportError as exc:
        sys.exit(f"Import failed (run inside ontocast venv): {exc}")

    content = load_cached_content()

    # ── Step 1: baseline — can we parse the raw content? ──────────────────
    print()
    print("=" * 60)
    print("Step 1 — raw content is valid JSON (baseline)")
    print("=" * 60)
    try:
        raw_parsed = json.loads(content)
        print(f"[PASS] json.loads succeeded. Keys: {list(raw_parsed.keys())}")
    except json.JSONDecodeError as exc:
        print(f"[FAIL] Raw content is not valid JSON: {exc}")
        print("  Content snippet:", content[:200])

    # ── Step 2: after strip_json_comments ─────────────────────────────────
    print()
    print("=" * 60)
    print("Step 2 — after strip_json_comments")
    print("=" * 60)
    after_comments = strip_json_comments(content)
    try:
        json.loads(after_comments)
        print("[PASS] Still valid JSON after strip_json_comments.")
    except json.JSONDecodeError as exc:
        print(f"[FAIL] strip_json_comments corrupted the JSON: {exc}")

    # ── Step 3: after strip_trailing_commas ───────────────────────────────
    print()
    print("=" * 60)
    print("Step 3 — after strip_trailing_commas  [LIKELY FAILS]")
    print("=" * 60)
    after_commas = strip_trailing_commas(after_comments)
    try:
        json.loads(after_commas)
        print("[PASS] Still valid JSON after strip_trailing_commas.")
    except json.JSONDecodeError as exc:
        print(f"[CONFIRMED BUG] strip_trailing_commas corrupted the JSON!")
        print(f"  JSONDecodeError: {exc}")
        # Find which character was changed
        for i, (a, b) in enumerate(zip(after_comments, after_commas)):
            if a != b:
                print(f"  First diff at char {i}: original={repr(a)}, after={repr(b)}")
                print(f"  Context: ...{repr(after_commas[max(0,i-30):i+30])}...")
                break

    # ── Step 4: parse_json_markdown returns None ──────────────────────────
    print()
    print("=" * 60)
    print("Step 4 — parse_json_markdown returns None  [THE REPORTED FAILURE]")
    print("=" * 60)
    final_content = strip_trailing_commas(strip_json_comments(content))
    result = parse_json_markdown(final_content)
    if result is None:
        print("[CONFIRMED BUG] parse_json_markdown returned None.")
        print("  This causes model_validate(None) which raises the pydantic error")
        print("  seen in the WARNING log.")
    else:
        print(f"[PASS — may be fixed] parse_json_markdown returned: {type(result)}")

    # ── Step 5: pydantic model_validate(None) reproduces the log error ────
    print()
    print("=" * 60)
    print("Step 5 — pydantic model_validate(None) reproduces the warning")
    print("=" * 60)
    try:
        OntologyCritiqueReport.model_validate(None)
        print("[UNEXPECTED PASS]")
    except Exception as exc:
        print(f"[CONFIRMED] model_validate(None) raises: {type(exc).__name__}")
        print(f"  {str(exc)[:200]}")

    # ── Step 6: workaround — parse directly with json.loads ───────────────
    print()
    print("=" * 60)
    print("Step 6 — workaround: json.loads on un-stripped content")
    print("=" * 60)
    try:
        raw = json.loads(content)  # skip strip_trailing_commas
        report = OntologyCritiqueReport.model_validate(raw)
        print(f"[PASS] Parsed successfully without strip_trailing_commas.")
        print(f"  success={report.success}, score={report.score}, "
              f"n_fixes={len(report.actionable_ontology_fixes)}")
        print()
        print("SUGGESTED FIX: in call_llm_with_retry (common.py), apply")
        print("  strip_trailing_commas only when parse_json_markdown fails,")
        print("  OR limit strip_trailing_commas to content OUTSIDE string literals.")
    except Exception as exc:
        print(f"[FAIL] Even direct parse failed: {exc}")


if __name__ == "__main__":
    run_tests()
