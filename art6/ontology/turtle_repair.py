"""
turtle_repair.py
-----------------
Recover three specific classes of malformed-Turtle facts-render failure that
ontocast's own recovery heuristics don't cover, WITHOUT editing the ontocast
checkout.

BACKGROUND -- what was being lost
-----------------------------------
Distinct from the JSON-envelope bracket mismatch `response_repair.py` fixes,
this is a defect in the TURTLE TEXT ITSELF, inside an otherwise well-formed
JSON envelope: the model ends a class-typing triple with a full stop where it
meant to continue the SAME subject's property list, e.g.

    cd:doc a echr:CaseDocument .
        echr:hasCaseName "Scholz AG v. Armenia" .

instead of

    cd:doc a echr:CaseDocument ;
        echr:hasCaseName "Scholz AG v. Armenia" .

The second line has no subject, so rdflib rejects it ("Bad syntax (objectList
expected)" / "expected '.' or '}' or ']' at end of statement", depending on
exactly where parsing gives up). Observed 2026-08-20 on the rolling-forward
arm: 2 of 5 chunks in one document (input.L6) lost outright -- 30% of that
document silently dropped, because after three retries all producing the same
mistake, `carry_forward.py` gives up on the chunk.

ontocast's OWN Turtle repair (`ontocast.onto.rdfgraph.RDFGraph`) already has
several pattern-matched fixes keyed to specific rdflib error messages --
`_repair_truncated_turtle` for dangling `;`/`,` at EOF,
`_repair_repeated_subject_after_semicolon` for the OPPOSITE mistake (the
model needlessly repeats the subject after a `;` it didn't need to), and a
couple of others. None of them recognize a full stop that should have been a
semicolon before a bare predicate continuation -- confirmed by reading
`_repair_common_turtle_issues` end to end: the "objectList expected" message
this defect raises matches none of its trigger substrings.

WHY THIS IS SAFE TO AUTO-FIX
-----------------------------
This ontology's own naming convention is the signal: classes are
UpperCamelCase (`echr:CaseDocument`), properties are lowerCamelCase
(`echr:hasCaseName`), and every document-local subject lives under the
document's OWN namespace (`cd:`/`doc:`), never under an ontology-vocabulary
prefix. So a line immediately following "a echr:SomeClass ." that starts with
a KNOWN VOCABULARY prefix (echr:, rdfs:, schema:, owl:, xsd:, dcterms:,
prov:, skos:) and a lowercase-initial local name cannot legitimately be a new
subject -- new subjects are always `cd:`/`doc:`-prefixed. That is what keeps
this narrow: a genuinely new triple starting `cd:next_subject a echr:Party ;`
right after a "." is left untouched (verified against a real capture), while
the two real failures from the 2026-08-20 run are both fixed correctly.

A SECOND, RELATED DEFECT
-------------------------
The same failures also show the model repeating a PREDICATE after a ","
continuation, e.g. (verbatim from the driver.log for input.L10, nochunk arm):

    echr:hasApplicant cd:applicant_couple ,
        echr:hasApplicant cd:applicant_child_1 ,
        echr:hasApplicant cd:applicant_child_2 .

"," lists another OBJECT for the same predicate -- the predicate token can
never legitimately reappear after one. `_repair_repeated_predicate_after_comma`
fixes this the same way ontocast's own `_repair_repeated_subject_after_semicolon`
fixes the mirror-image mistake (a needlessly repeated SUBJECT after a `;`):
track whether the previous line ended in a bare "," continuation and, if the
next line starts with the SAME predicate that was active, strip it back down
to just the object.

A THIRD DEFECT: AN UNESCAPED QUOTE INSIDE A LITERAL
-----------------------------------------------------
Also observed 2026-08-20, still on the rolling-forward arm: the model copies
a quoted phrase from the source text verbatim into hasSupportingQuote without
escaping the phrase's own internal quote marks, e.g.

    echr:hasSupportingQuote "a German corporation, Scholz AG (
        "the applicant company")" .

Turtle's string ends at the FIRST unescaped '"', so the parser reads a
complete (wrong) literal, then chokes on the bare word after it. See
`_repair_unescaped_quote_in_literal` for how this is told apart from a
genuinely valid multi-value object list (`"Foo", "Bar"`), which looks
superficially similar (more than one quoted segment on a line) but is a
different, legitimate structure that must not be touched.

WHAT THIS DOES
---------------
`RDFGraph._repair_common_turtle_issues` is a classmethod ontocast's own
`_from_turtle_str` always calls in its except-block, regardless of which
error triggered it, before deciding whether anything changed. That is the
hook: wrap it so the original runs first (untouched, so the ~existing
recovery paths behave exactly as before), and only when it made no change do
we additionally try all three patterns above, in sequence. If any of them
changes the text, the SAME retry-and-reparse machinery in `_from_turtle_str`
picks it up automatically -- nothing else needs to change.

Nothing here writes to the ontocast checkout; the patch lives for the
lifetime of the process that calls `enable()`.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("turtle_repair")

# Prefixes that can legitimately appear as a bare PREDICATE continuation.
# Document-local subject prefixes (cd:, doc:, ...) are deliberately absent --
# a token under one of THOSE prefixes right after a "." is always a new
# subject, never a mistaken continuation, and must never be touched.
_KNOWN_PREDICATE_PREFIXES = "echr|rdfs|schema|owl|xsd|dcterms|prov|skos"

# The left context is deliberately just "any '.'" -- an earlier version
# required the preceding line to be "a echr:SomeClass .", but the real
# failures showed the SAME mistake recurring several times within one
# triple block (e.g. after a class declaration, then AGAIN after the next
# property's value), so anchoring to only the first occurrence left the
# later ones unfixed and the reparse still failed. Widening it to match
# unconditionally is still safe: only a `cd:`/`doc:`-prefixed token can
# legitimately open a new subject, so a bare known-vocabulary predicate
# right after a "." is never a genuine new statement in this pipeline's
# output (facts extraction only ever asserts cd:/doc: instance data, never
# metadata about the echr: vocabulary itself).
_PREMATURE_PERIOD_RE = re.compile(
    rf"\.\s*\n(\s*)((?:{_KNOWN_PREDICATE_PREFIXES}):[a-z]\w*\s)"
)

_enabled = False


def _repair_premature_period_before_property(turtle_str: str) -> str:
    """Turn "... .\\n  echr:someProperty ..." into "... ;\\n  echr:someProperty ...".

    Only fires where the following line's predicate is under a known
    ontology/vocabulary prefix -- see the module docstring for why that
    keeps a legitimate new `cd:`/`doc:`-prefixed subject untouched.
    """
    return _PREMATURE_PERIOD_RE.sub(r" ;\n\1\2", turtle_str)


_PRED_TOKEN_RE = re.compile(rf"((?:{_KNOWN_PREDICATE_PREFIXES}):[a-zA-Z]\w*)\s+(.*)")


def _repair_repeated_predicate_after_comma(turtle_str: str) -> str:
    """Strip a predicate the model repeated right after a ',' continuation.

    "," lists another object for the SAME predicate; the predicate token can
    never legitimately reappear there. Tracked line-by-line rather than with
    a single regex because the mistake can repeat across several
    consecutive "," lines (see the module docstring's real example) and each
    line's fix depends on which predicate is currently active, not just the
    line immediately before it.
    """
    lines = turtle_str.split("\n")
    out: list[str] = []
    pending_predicate: str | None = None
    for line in lines:
        stripped = line.strip()
        match = _PRED_TOKEN_RE.match(stripped)
        if (
            pending_predicate is not None
            and match
            and match.group(1) == pending_predicate
        ):
            indent = line[: len(line) - len(line.lstrip())]
            line = f"{indent}{match.group(2)}"
            stripped = line.strip()
        if stripped.endswith(","):
            active_match = _PRED_TOKEN_RE.match(stripped)
            pending_predicate = (
                active_match.group(1) if active_match else pending_predicate
            )
        else:
            pending_predicate = None
        out.append(line)
    return "\n".join(out)


# Matches from an opening '"' greedily through to the LAST '"' that sits
# right before a genuine statement terminator (.;,]}) -- i.e. the span a
# human reading the Turtle would recognise as "one literal, however many
# stray quotes are inside it". Greedy .* is what lets this span an
# arbitrary number of embedded quote marks in one match, not just one.
_LITERAL_SPAN_RE = re.compile(r'"(.*)"(?=\s*[.;,\]}])')


def _repair_unescaped_quote_in_literal(turtle_str: str) -> str:
    """Escape a quote mark the model left unescaped inside a literal.

    Observed 2026-08-20 (rolling-forward, input.L6): the model copies a
    quoted phrase from the source text verbatim into hasSupportingQuote
    without escaping the phrase's own internal quote marks, e.g.

        echr:hasSupportingQuote "a German corporation, Scholz AG (
            "the applicant company")" .

    Turtle's string literal ends at the FIRST unescaped '"', so the parser
    reads a complete (wrong) literal, then chokes on the bare word that
    follows it -- "expected '.' or '}' or ']' at end of statement".

    Escaping (`\\"`), not deleting, the stray marks is deliberate:
    hasSupportingQuote must stay an exact verbatim substring of the source,
    so the nested quote characters the source text actually had need to
    survive, just made syntactically legal.

    THE HARD PART is telling this apart from a genuinely valid multi-value
    object list -- `rdfs:label "Foo", "Bar" .` also has more than one
    quoted segment on one line. The distinguishing signal: in a valid list,
    every embedded quote sits immediately next to a ',' or ';' separator on
    at least one side (closing quote followed by ", or opening quote
    preceded by ", ). A quote with a plain word directly on BOTH sides is
    never a valid list boundary -- it can only be a stray mark inside what
    was meant to be one literal. Verified against both shapes plus a list
    item that itself legitimately contains a comma.
    """

    def fix_span(match: re.Match) -> str:
        body = match.group(1)
        if '"' not in body:
            return match.group(0)
        pos = 0
        is_defect = False
        while True:
            quote_pos = body.find('"', pos)
            if quote_pos == -1:
                break
            before = body[:quote_pos].rstrip()
            after = body[quote_pos + 1 :].lstrip()
            legit_reopen = before.endswith((",", ";"))
            legit_close = after.startswith((",", ";"))
            if not (legit_reopen or legit_close):
                is_defect = True
                break
            pos = quote_pos + 1
        if not is_defect:
            return match.group(0)
        return '"' + body.replace('"', '\\"') + '"'

    return _LITERAL_SPAN_RE.sub(fix_span, turtle_str)


def enable() -> None:
    """Install the patch. Idempotent; safe to call once per process."""
    global _enabled
    if _enabled:
        return

    from ontocast.onto.rdfgraph import RDFGraph

    original = RDFGraph._repair_common_turtle_issues.__func__

    def patched(cls, turtle_str: str, parse_error_message: str) -> str:
        # Every step here is applied UNCONDITIONALLY on top of whatever the
        # previous step produced -- no "return early because something
        # changed". `_from_turtle_str` only reparses the final result ONCE,
        # so any short-circuit here risks leaving a second, different defect
        # behind and the reparse still fails, uncaught, exactly as if this
        # patch had never run. Caught twice already at smaller scope (this
        # module's own two fixes composed the same way after the first
        # attempt trusted whichever fired first); this is that same mistake
        # one level up -- trusting ontocast's OWN repair just because it
        # changed something, instead of always layering ours on top too.
        repaired = original(cls, turtle_str, parse_error_message=parse_error_message)
        after_extra = _repair_repeated_predicate_after_comma(
            _repair_premature_period_before_property(repaired)
        )
        after_extra = _repair_unescaped_quote_in_literal(after_extra)
        if after_extra != repaired:
            logger.info(
                "turtle repair: rewrote a premature '.' before a bare "
                "property continuation, and/or stripped a predicate "
                "repeated after a ',' continuation, and/or escaped an "
                "unescaped quote inside a literal"
            )
        return after_extra

    RDFGraph._repair_common_turtle_issues = classmethod(patched)
    _enabled = True
    logger.info(
        "turtle repair ENABLED: premature-period-before-property and "
        "repeated-predicate-after-comma are now recovered in addition to "
        "ontocast's own Turtle repairs"
    )
