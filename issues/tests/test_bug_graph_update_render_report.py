"""
test_bug_graph_update_render_report.py
=============================================================
Reproducible bug report for ontocast — GraphUpdateRenderReport parse
failure (attempt 1/3) despite the LLM returning valid JSON content.

Last updated : 2026-06-09
Progress     : Fixed cp1252 print character bug
Version history:
  0.1.0 – Initial report
  0.2.0 – Relocated cache file to local fixtures directory
  0.2.1 – Fixed cp1252 unicode character prints

ISSUE
-----
call_llm_with_retry (ontocast/agent/common.py) raises:

    1 validation error for GraphUpdateRenderReport
    Input should be a valid dictionary or instance of GraphUpdateRenderReport
    [type=model_type, input_value=None, input_type=NoneType]

on attempt 1/3, even though the cached LLM response contains a structurally
valid GraphUpdateRenderReport JSON.

ROOT CAUSE
----------
The code path for llm_graph_format is not None is:

    json_object = parse_json_markdown(content_to_parse)
    model_cls.model_validate(json_object, context={...})

parse_json_markdown internally calls parse_json which does:

    try:
        return json.loads(text)
    except JSONDecodeError:
        ...
    return None            # <─ returns None on failure

The failure happens because gpt-5-mini appended explanatory prose after
the closing "}" of the JSON object.  For example:

    {
      "graph_update": { ... }
    }
    I have added the following classes to declare the missing parent
    classes referenced by existing subclasses: ...

json.loads() correctly rejects this as invalid JSON.  parse_json()
then returns None.  model_validate(None) raises the pydantic error.

Unlike the OntologyCritiqueReport bug (test_bug_parse_json_markdown.py),
this failure does NOT involve strip_trailing_commas corruption — the
JSON itself is fine; it is the trailing prose that breaks parsing.

EXPECTED BEHAVIOUR
------------------
ontocast should extract the leading JSON object from the response before
calling json.loads, e.g. by:
  (a) finding the first balanced { ... } block (brace counting), OR
  (b) wrapping parse_json_markdown in a fallback that uses a regex to
      find the outermost JSON object before pure-string parsing.

This is complementary to the strip_trailing_commas fix — both
corruptions must be addressed independently.

REPRODUCTION
------------
Run with the ontocast venv active:
    uv run python issues/tests/test_bug_graph_update_render_report.py
"""

import json
import sys
import re
from pathlib import Path

CACHE_FILE = Path(__file__).parent / "fixtures" / "a893e5ab01cebfa689a504cabae4d9742e701baa9e7eab89b4790d46245c6df7.json"

# Synthetic reproducer: valid JSON followed by explanatory prose.
# This is the pattern produced by gpt-5-mini when it forgets it should
# output ONLY JSON (particularly on retry attempts with large prompts).
_VALID_GRAPH_UPDATE = {
    "graph_update": {
        "triple_operations": [
            {
                "type": "insert",
                "graph": {
                    "@context": {
                        "owl": "http://www.w3.org/2002/07/owl#",
                        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
                        "seed": "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#",
                    },
                    "@graph": [
                        {
                            "@id": "seed:CaseDocument",
                            "@type": "owl:Class",
                            "rdfs:label": {"@value": "Case Document", "@language": "en"},
                            "rdfs:comment": {
                                "@value": "Document produced within judicial proceedings.",
                                "@language": "en",
                            },
                        }
                    ],
                },
            }
        ]
    }
}

MINIMAL_GOOD_CONTENT = json.dumps(_VALID_GRAPH_UPDATE, indent=2)

# This is the bug: the LLM appended prose after the JSON.
MINIMAL_BAD_CONTENT = (
    MINIMAL_GOOD_CONTENT
    + "\n\nI have added the following classes to address the missing parent "
    "class declarations referenced by existing subclasses in the ontology. "
    "Each class has been given an appropriate rdfs:label and rdfs:comment "
    "consistent with existing naming conventions."
)


def load_cached_content() -> str:
    """Return cached LLM content string, or fall back to synthetic."""
    try:
        data = json.loads(Path(CACHE_FILE).read_text(encoding="utf-8"))
        content = data["result"]["content"]
        print(f"[INFO] Loaded from cache file ({len(content):,} chars)")
        return content
    except Exception as exc:
        print(f"[INFO] Cache file not readable ({exc}); using synthetic reproducer.")
        return MINIMAL_BAD_CONTENT


def extract_leading_json_object(text: str) -> str | None:
    """Extract the first balanced {...} block from text (proposed fix).

    Uses a simple brace-counter that is aware of JSON string boundaries
    so embedded braces inside string values don't confuse the count.
    """
    depth = 0
    in_string = False
    escape_next = False
    start = None

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            if in_string:
                escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start : i + 1]
    return None


def run_tests():
    try:
        from langchain_core.utils.json import parse_json_markdown
        from ontocast.agent.common import strip_json_comments, strip_trailing_commas
        from ontocast.onto.model import GraphUpdateRenderReport
    except ImportError as exc:
        sys.exit(f"Import failed (run inside ontocast venv): {exc}")

    content = load_cached_content()

    # ── Detect whether the content has trailing prose ─────────────────────
    print()
    print("=" * 60)
    print("Step 1 — detect trailing prose after JSON")
    print("=" * 60)
    stripped = content.strip()
    try:
        json.loads(stripped)
        print("[OK] Content is pure JSON — no trailing prose detected.")
        print("     (The synthetic reproducer will be used to demonstrate the bug.)")
        # Use synthetic for demonstration
        content = MINIMAL_BAD_CONTENT
        stripped = content.strip()
    except json.JSONDecodeError as exc:
        print(f"[CONFIRMED] Content has trailing text: {exc}")
        # Find where JSON ends
        json_part = extract_leading_json_object(stripped)
        if json_part:
            trailing = stripped[len(json_part):].strip()
            print(f"  JSON object: {len(json_part):,} chars")
            print(f"  Trailing text snippet: {trailing[:120]!r}")

    # ── Step 2: show parse_json_markdown returns None ─────────────────────
    print()
    print("=" * 60)
    print("Step 2 — parse_json_markdown returns None  [THE REPORTED FAILURE]")
    print("=" * 60)
    processed = strip_trailing_commas(strip_json_comments(stripped))
    result = parse_json_markdown(processed)
    if result is None:
        print("[CONFIRMED BUG] parse_json_markdown returned None for content")
        print("  that contains valid JSON followed by explanatory prose.")
    else:
        print(f"[PASS] parse_json_markdown returned a {type(result).__name__}.")

    # ── Step 3: reproduce the exact pydantic error ────────────────────────
    print()
    print("=" * 60)
    print("Step 3 — model_validate(None) -> the logged pydantic error")
    print("=" * 60)
    try:
        GraphUpdateRenderReport.model_validate(result)
        print("[UNEXPECTED PASS]")
    except Exception as exc:
        print(f"[CONFIRMED] model_validate({result!r}) raises: {type(exc).__name__}")
        print(f"  {str(exc)[:200]}")

    # ── Step 4: proposed fix — extract leading JSON object ────────────────
    print()
    print("=" * 60)
    print("Step 4 — proposed fix: brace-balanced extraction before parse")
    print("=" * 60)
    json_only = extract_leading_json_object(processed)
    if json_only is None:
        print("[FAIL] Could not find a balanced JSON object in the content.")
    else:
        try:
            parsed_dict = json.loads(json_only)
            report = GraphUpdateRenderReport.model_validate(parsed_dict)
            ops = report.graph_update.triple_operations
            print(f"[PASS] Parsed GraphUpdateRenderReport successfully.")
            print(f"  triple_operations count : {len(ops)}")
            print()
            print("SUGGESTED FIX: in call_llm_with_retry (agent/common.py), before")
            print("  calling parse_json_markdown(), apply a brace-balanced extractor")
            print("  when the response content doesn't parse as pure JSON:")
            print()
            print("    try:")
            print("        json.loads(content_to_parse)  # fast-path: already pure JSON")
            print("    except json.JSONDecodeError:")
            print("        content_to_parse = extract_leading_json_object(content_to_parse)")
            print("                           or content_to_parse")
        except Exception as exc:
            print(f"[FAIL] Even after extraction, parsing raised: {exc}")

    # ── Step 5: confirm pure JSON path is fine ────────────────────────────
    print()
    print("=" * 60)
    print("Step 5 — baseline: pure JSON (no trailing prose) parses fine")
    print("=" * 60)
    pure = strip_trailing_commas(strip_json_comments(MINIMAL_GOOD_CONTENT))
    result2 = parse_json_markdown(pure)
    if result2 is not None:
        try:
            r2 = GraphUpdateRenderReport.model_validate(result2)
            print(f"[PASS] Pure JSON parses fine: {len(r2.graph_update.triple_operations)} op(s).")
        except Exception as exc:
            print(f"[FAIL] Validation failed: {exc}")
    else:
        print("[UNEXPECTED FAIL] parse_json_markdown returned None for pure JSON.")


if __name__ == "__main__":
    run_tests()
