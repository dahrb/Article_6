"""
repair_facts.py
----------------
Final consistency pass over an already-extracted facts graph, run once per
document after `ontocast process` has finished.

OntoCast's own pipeline processes content units independently and in parallel
(see the "chunking" investigation in this project's history) so it cannot
notice that two nodes it minted in separate calls should be linked, and even
within one call it does not reliably apply relations it has full context for.
This script re-reads the merged graph as a whole and asks one LLM call per
document to fix exactly the defects that shape of mistake produces:

  1. missing echr:followsProceeding links between domestic proceedings,
     flagged deterministically wherever hasOutcome is one of the "reviews a
     decision below" outcomes (echr:OutcomeUpheldOnAppeal,
     echr:OutcomeQuashedAndRemitted) and no link is present;
  2. echr:isFinalDomesticDecision asserted true on more than one proceeding
     in the same document;
  3. entities that describe a domestic authority's decision but were typed
     outside echr:DomesticProceeding (heuristically surfaced by keyword, not
     assumed -- the model decides whether each candidate is really one);
  4. whatever the SHACL-lite validator already flagged in the sibling
     `<stem>.facts.validation.json` (dangling_reference, suspect_multi_value),
     PLUS whatever ontology/echr-shapes.ttl flags on the graph directly --
     the multi-label false-merge shape in particular exists specifically so
     this pass can fix what it names (added 2026-08-19, see
     `find_shape_violations` below);
  5. DUPLICATE ENTITIES -- one real proceeding or authority extracted as two
     nodes, surfaced deterministically (same court + same decision date; same
     authority name) and confirmed by the model.

DUPLICATES ARE MERGED AND DELETED, NOT LEFT ORPHANED. The model only names the
pair and which node survives; `merge_nodes` does the work -- it re-points every
inbound edge onto the survivor, moves across any property the survivor lacks
(the survivor's own value wins for anything the ontology declares
owl:FunctionalProperty, so a merge can never manufacture the multi-value
contradiction it exists to remove), and then deletes every triple mentioning
the duplicate. Nothing is left behind for a later pass to clean up.

A final `sweep_stub_orphans` deletes typed nodes that carry nothing but
rdf:type/rdfs:label and have no inbound reference. It is deliberately narrow:
an unreferenced node that has real properties is CONTENT, not litter, and is
kept for the graph build to connect.

The model returns a flat patch of add/remove triple operations, not prose or
raw Turtle, so every change is auditable. Every subject a patch operation
touches must already exist under the document's own `doc:` namespace or be
a brand new `doc:`-namespaced node -- the ontology (`echr:`) and standard
(`schema:`, ...) namespaces are read-only, exactly as in the extraction
prompt's own two-namespace contract.

Safety: the original `<stem>.facts.ttl` is copied to a `backup/` directory
next to it before anything is written, and the patch is written back to the
original filename only after that backup succeeds. A `<stem>.facts.repairs.json`
audit log records every operation applied (or skipped, and why) plus the
model's rationale.

Usage:
  uv run python -m art6.ontology.repair_facts --facts-dir results/20260817_run
  uv run python -m art6.ontology.repair_facts --facts-dir results/20260817_run --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field, model_validator
from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef
from rdflib import Literal as RDFLiteral

from art6.paths import REPO_ROOT, relative

# Overridable so a run can be pinned to the same snapshot it extracted against:
#   ART6_ONTOLOGY_TTL=results/<run>/echr.ttl.snapshot uv run python -m art6.ontology.repair_facts ...
ONTOLOGY_TTL = Path(
    os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr_2.ttl")
)
KEYS_FILE = REPO_ROOT / "keys.env"

ECHR = Namespace("https://growgraph.dev/echr#")
RDFS_NS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
SH = Namespace("http://www.w3.org/ns/shacl#")


@lru_cache(maxsize=1)
def functional_properties() -> frozenset[URIRef]:
    """Properties the ontology declares owl:FunctionalProperty.

    Read from echr_2.ttl rather than hardcoded, so a schema edit cannot leave
    the merge logic silently applying the previous version's cardinalities.
    Merging two nodes has to know which predicates take exactly one value: for
    those the surviving node's value wins and the duplicate's is dropped, and
    for everything else (quotes, source paragraphs, notes) both are kept.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    return frozenset(g.subjects(RDF.type, OWL.FunctionalProperty))


@lru_cache(maxsize=1)
def ontology_terms() -> frozenset[URIRef]:
    """Every echr: term the ontology actually defines.

    Classes, properties, and the named individuals inside every owl:oneOf
    enumeration. Anything else in the echr: namespace is invented vocabulary.

    This exists because the extraction prompt's closed-vocabulary discipline
    was being enforced on the models but NOT on the repair pass: a patch could
    "correct" echr:OutcomeQuashedAndRemitted to echr:OutcomeQuashed -- a term
    that does not exist -- and the two-namespace guard waved it through,
    because it only ever checked that SUBJECTS were doc:-namespaced. Observed
    in the 2026-08-19 echr_2 run: extraction produced 0 invented terms and the
    repair pass introduced 1.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    terms = set()
    for kind in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty):
        terms |= {s for s in g.subjects(RDF.type, kind) if isinstance(s, URIRef)}
    for lst in g.objects(None, OWL.oneOf):
        cur = lst
        while cur and cur != RDF.nil:
            for first in g.objects(cur, RDF.first):
                if isinstance(first, URIRef):
                    terms.add(first)
            cur = next(g.objects(cur, RDF.rest), None)
    return frozenset(terms)


def unknown_echr_terms(*candidates: str) -> list[str]:
    """Which of ``candidates`` (CURIEs) name an echr: term the ontology lacks."""
    known = ontology_terms()
    bad = []
    for curie in candidates:
        prefix, _, local = curie.partition(":")
        if prefix != "echr" or not local:
            continue
        if ECHR[local] not in known:
            bad.append(curie)
    return bad


# Outcomes that presuppose a decision below, per the ontology's own
# scope note on echr:DomesticProceeding (see the 2026-08-18 edit).
APPEAL_SHAPED_OUTCOMES = {
    ECHR.OutcomeUpheldOnAppeal,
    ECHR.OutcomeQuashedAndRemitted,
}

# Heuristic only -- surfaces candidates for the model to judge, never applied
# automatically. A schema:*-typed node whose label/description reads like an
# authority's decision is worth a second look against echr:DomesticProceeding.
DECISION_LANGUAGE_KEYWORDS = (
    "refus",
    "uph",
    "dismiss",
    "grant",
    "reject",
    "quash",
    "remit",
    "decision",
    "judgment",
    "ruling",
    "order",
    "appeal",
    "review",
)


def load_openai_api_key() -> str:
    if KEYS_FILE.exists():
        for line in KEYS_FILE.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit(
            f"OPENAI_API_KEY not found in {relative(KEYS_FILE)} or environment"
        )
    return key


# ---------------------------------------------------------------------------
# Patch schema -- what the model is allowed to say, nothing looser.
# ---------------------------------------------------------------------------


class TripleOp(BaseModel):
    action: Literal["add", "remove"]
    subject: str = Field(
        description="CURIE, e.g. 'doc:appellate_prosecutor_upheld_2005_10_11'"
    )
    predicate: str = Field(description="CURIE, e.g. 'echr:followsProceeding'")
    object: str = Field(
        description=(
            "CURIE for a URI object (e.g. 'doc:refusal_2005_08_10' or "
            "'echr:DomesticProceeding'), or the literal value as plain text "
            "for a datatype property."
        )
    )
    object_is_literal: bool
    datatype: str | None = Field(
        default=None,
        description=(
            "CURIE of the literal's datatype, e.g. 'xsd:date'. Omit for URI objects "
            "AND omit whenever lang is set -- a literal takes lang or datatype, never both."
        ),
    )
    lang: str | None = Field(
        default=None, description="Language tag for a plain-text literal, e.g. 'en'."
    )

    @model_validator(mode="after")
    def _lang_and_datatype_are_exclusive(self) -> TripleOp:
        if self.lang and self.datatype:
            self.datatype = None
        return self


class RepairGroup(BaseModel):
    finding: str = Field(
        description="Which of the four defect categories this addresses."
    )
    rationale: str = Field(
        description="Why, citing the supporting quote or field that justifies it."
    )
    ops: list[TripleOp]


class MergeOp(BaseModel):
    """Two nodes that are one entity. The code does the merging, not the model.

    Asking a model to hand-write the add/remove triples that fold one node into
    another is how you get half-merged nodes and dangling references. It names
    the pair and the survivor; `merge_nodes` moves the properties and inbound
    edges and deletes the duplicate outright.
    """

    keep: str = Field(
        description="CURIE of the node to survive, e.g. 'doc:appeal_2016'"
    )
    drop: str = Field(
        description="CURIE of the duplicate to fold into `keep` and delete entirely."
    )
    rationale: str = Field(
        description="Why these are the same entity, citing the label/date/quote."
    )


class RepairPatch(BaseModel):
    groups: list[RepairGroup]
    merges: list[MergeOp] = Field(
        default_factory=list,
        description=(
            "Pairs of nodes that denote the same real entity. Only include a "
            "pair when the evidence shows one entity described twice; if in "
            "doubt, leave them separate."
        ),
    )


# ---------------------------------------------------------------------------
# Extraction of what to show the model
# ---------------------------------------------------------------------------


@dataclass
class ProceedingSummary:
    curie: str
    label: str | None
    court_label: str | None
    decision_date: str | None
    start_date: str | None
    outcome: str | None
    is_final: bool | None
    follows: list[str]
    quotes: list[str]


def _curie(graph: Graph, term) -> str:
    if isinstance(term, URIRef):
        try:
            return graph.namespace_manager.qname(term)
        except Exception:  # noqa: BLE001
            return str(term)
    return str(term)


def _label(graph: Graph, subject: URIRef) -> str | None:
    from rdflib.namespace import RDFS

    for o in graph.objects(subject, RDFS.label):
        return str(o)
    return None


def summarize_proceedings(graph: Graph) -> list[ProceedingSummary]:
    out = []
    for s in graph.subjects(RDF.type, ECHR.DomesticProceeding):
        court = next(graph.objects(s, ECHR.hasCourt), None)
        outcome = next(graph.objects(s, ECHR.hasOutcome), None)
        is_final = next(graph.objects(s, ECHR.isFinalDomesticDecision), None)
        out.append(
            ProceedingSummary(
                curie=_curie(graph, s),
                label=_label(graph, s),
                court_label=_label(graph, court) if court is not None else None,
                decision_date=next(
                    (str(o) for o in graph.objects(s, ECHR.hasDecisionDate)), None
                ),
                start_date=next(
                    (str(o) for o in graph.objects(s, ECHR.hasProceedingStartDate)),
                    None,
                ),
                outcome=_curie(graph, outcome) if outcome is not None else None,
                is_final=bool(is_final) if is_final is not None else None,
                follows=[
                    _curie(graph, o) for o in graph.objects(s, ECHR.followsProceeding)
                ],
                quotes=[str(o) for o in graph.objects(s, ECHR.hasSupportingQuote)],
            )
        )
    return out


def find_mistyped_candidates(graph: Graph) -> list[dict]:
    """schema:*-typed doc: nodes whose text reads like an authority's decision."""

    doc_ns = str(next((ns for prefix, ns in graph.namespaces() if prefix == "doc"), ""))
    candidates = []
    seen = set()
    for s, p, o in graph:
        if not str(s).startswith(doc_ns):
            continue
        if p != RDF.type or not str(o).startswith("https://schema.org/"):
            continue
        if s in seen:
            continue
        label = _label(graph, s) or ""
        desc = "\n".join(
            str(x) for x in graph.objects(s, None) if isinstance(x, RDFLiteral)
        )
        blob = f"{label} {desc}".lower()
        if any(kw in blob for kw in DECISION_LANGUAGE_KEYWORDS):
            seen.add(s)
            candidates.append(
                {
                    "curie": _curie(graph, s),
                    "current_type": _curie(graph, o),
                    "label": label,
                    "description": desc[:600],
                }
            )
    return candidates


APPEAL_SHAPED_OUTCOME_CURIES = {
    "echr:" + str(o).split("#")[-1] for o in APPEAL_SHAPED_OUTCOMES
}


def _normalize_name(value) -> str:
    """Casefold and strip punctuation, for comparing authority names."""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def find_duplicate_candidates(graph: Graph) -> list[dict]:
    """Groups of same-typed doc: nodes that look like one entity split in two.

    Deterministic surfacing only -- the model decides whether a group really is
    one entity. Two keys, both chosen because a false positive is expensive
    (merging two genuinely distinct proceedings asserts a falsehood):

      DomesticProceeding  same hasCourt AND same hasDecisionDate. One court
                          cannot decide the same case twice on one day, so a
                          collision here is a duplicate rather than a coincidence.
      DomesticAuthority   same normalized hasAuthorityName / rdfs:label.

    Each group is returned with enough context -- label, dates, outcome, quotes,
    and how many triples the node carries -- for the model to pick which node to
    keep without needing the source text.
    """
    doc_ns = str(next((ns for prefix, ns in graph.namespaces() if prefix == "doc"), ""))
    groups: list[dict] = []

    def _members(nodes: list[URIRef]) -> list[dict]:
        out = []
        for n in nodes:
            out.append(
                {
                    "curie": _curie(graph, n),
                    "label": _label(graph, n),
                    "triples": sum(1 for _ in graph.predicate_objects(n)),
                    "inbound_links": sum(1 for _ in graph.subjects(None, n)),
                    "properties": sorted(
                        {_curie(graph, p) for p in graph.predicates(n, None)}
                    ),
                    "quotes": [
                        str(o) for o in graph.objects(n, ECHR.hasSupportingQuote)
                    ],
                }
            )
        return out

    by_court_date: dict[tuple, list[URIRef]] = {}
    for s_ in graph.subjects(RDF.type, ECHR.DomesticProceeding):
        if not str(s_).startswith(doc_ns):
            continue
        court = next(graph.objects(s_, ECHR.hasCourt), None)
        date = next(graph.objects(s_, ECHR.hasDecisionDate), None)
        if court is None or date is None:
            continue
        by_court_date.setdefault((str(court), str(date)), []).append(s_)
    for (court, date), nodes in by_court_date.items():
        if len(nodes) > 1:
            groups.append(
                {
                    "class": "echr:DomesticProceeding",
                    "matched_on": f"same hasCourt <{court}> and hasDecisionDate {date}",
                    "members": _members(nodes),
                }
            )

    by_name: dict[str, list[URIRef]] = {}
    for s_ in graph.subjects(RDF.type, ECHR.DomesticAuthority):
        if not str(s_).startswith(doc_ns):
            continue
        name = next(graph.objects(s_, ECHR.hasAuthorityName), None) or next(
            graph.objects(s_, RDFS.label), None
        )
        if name is None:
            continue
        by_name.setdefault(_normalize_name(name), []).append(s_)
    for name, nodes in by_name.items():
        if len(nodes) > 1:
            groups.append(
                {
                    "class": "echr:DomesticAuthority",
                    "matched_on": f"same authority name {name!r}",
                    "members": _members(nodes),
                }
            )
    return groups


def find_appeal_shaped_gaps(summaries: list[ProceedingSummary]) -> list[str]:
    return [
        p.curie
        for p in summaries
        if p.outcome in APPEAL_SHAPED_OUTCOME_CURIES and not p.follows
    ]


def find_final_decision_conflicts(summaries: list[ProceedingSummary]) -> list[str]:
    finals = [p.curie for p in summaries if p.is_final]
    return finals if len(finals) > 1 else []


def load_validator_findings(facts_ttl: Path) -> list[dict]:
    # facts_ttl is "<stem>.facts.ttl"; the validator's sibling output is
    # "<stem>.facts.validation.json".
    validation_path = facts_ttl.parent / (
        facts_ttl.name.removesuffix(".ttl") + ".validation.json"
    )
    if not validation_path.exists():
        return []
    data = json.loads(validation_path.read_text())
    return data.get("findings", [])


@lru_cache(maxsize=1)
def _shapes_graph() -> Graph:
    from art6.ontology.validate_shapes import SHAPES_PATH

    g = Graph()
    g.parse(SHAPES_PATH)
    return g


def find_shape_violations(graph: Graph) -> list[dict]:
    """Static SHACL violations from ontology/echr-shapes.ttl, in the graph
    alone -- no source text, no LLM call.

    Returned in the same shape as load_validator_findings, so both feed the
    model identically. This is what makes the shapes file load-bearing rather
    than a standalone report card: SingleLabelShape names exactly the
    false-merge defect (a node with two rdfs:label values) that neither the
    functional-property nor the vocabulary checks can see, and this function
    is what gets that finding in front of the model that can fix it.
    """
    from pyshacl import validate as shacl_validate

    conforms, results_graph, _ = shacl_validate(
        graph,
        shacl_graph=_shapes_graph(),
        advanced=True,
        inference="none",
        abort_on_first=False,
        meta_shacl=False,
    )
    if conforms:
        return []

    findings = []
    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        severity = next(results_graph.objects(result, SH.resultSeverity), None)
        message = next(results_graph.objects(result, SH.resultMessage), None)
        focus = next(results_graph.objects(result, SH.focusNode), None)
        path = next(results_graph.objects(result, SH.resultPath), None)
        findings.append(
            {
                "kind": "shacl_violation",
                "severity": "error" if severity == SH.Violation else "warning",
                "message": str(message) if message is not None else "",
                "subject": _curie(graph, focus) if focus is not None else "",
                "predicate": _curie(graph, path) if path is not None else "",
                "values": [],
            }
        )
    return findings


def load_ontology_context() -> str:
    """The DomesticProceeding-relevant fragment of echr.ttl, read live so this
    script can never drift out of sync with the ontology's own definitions."""
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    relevant_classes = [
        ECHR.DomesticProceeding,
        ECHR.ProceedingOutcome,
        ECHR.ProceedingType,
        ECHR.InstanceLevel,
    ]
    lines: list[str] = []
    for cls in relevant_classes:
        for s, p, o in g.triples((cls, None, None)):
            lines.append(
                f"{g.namespace_manager.qname(s)} {g.namespace_manager.qname(p)} {_render(g, o)} ."
            )
    for prop_s in g.subjects(None, None):
        for domain in g.objects(
            prop_s, URIRef("http://www.w3.org/2000/01/rdf-schema#domain")
        ):
            if domain == ECHR.DomesticProceeding:
                for p, o in g.predicate_objects(prop_s):
                    lines.append(
                        f"{g.namespace_manager.qname(prop_s)} {g.namespace_manager.qname(p)} {_render(g, o)} ."
                    )
                break
    return "\n".join(sorted(set(lines)))


def _render(g: Graph, o) -> str:
    if isinstance(o, URIRef):
        try:
            return g.namespace_manager.qname(o)
        except Exception:  # noqa: BLE001
            return f"<{o}>"
    return f'"{o}"'


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are doing a final consistency pass over an already-extracted RDF facts \
graph for one ECHR judgment, under the echr: ontology (definitions provided \
below, read live from the ontology file so they cannot drift).

You will be given, for one document:
- every echr:DomesticProceeding node currently in the graph, with its court, \
dates, outcome, isFinalDomesticDecision flag, existing followsProceeding \
links, and supporting quotes;
- candidate nodes typed outside echr:DomesticProceeding that may actually \
describe a domestic authority's decision and belong under it instead;
- deterministically-flagged gaps: proceedings whose outcome presupposes a \
decision below but have no followsProceeding link, and any case where more \
than one proceeding is marked isFinalDomesticDecision true;
- structural findings a separate validator already raised on this graph.

Fix only what the evidence in the quotes and fields supports. Do not invent \
a link between two proceedings that merely happened around the same time -- \
two separately-initiated avenues on the same underlying grievance (e.g. a \
complaint to a prosecutor and a parallel court application) are not a chain \
unless one decision is actually about reviewing the other. Where you retype \
a candidate node into echr:DomesticProceeding, also add whatever standard \
properties (hasCourt, hasDecisionDate, hasOutcome, ...) its existing label/ \
description already supports -- do not leave it a bare rdf:type change.

echr:DomesticProceeding is scoped to proceedings IN THIS APPLICANT'S OWN \
case, as narrated in the facts. A court decision from a different, unrelated \
case that this judgment's facts mention only as case-law authority -- \
"cited as a precedent", "referred to in support of", and similar -- is NOT a \
domestic proceeding in this case and must NOT be retyped into it, no matter \
how decision-like its own label reads. If a candidate's description marks it \
as a citation rather than a step in this applicant's own proceedings, leave \
it exactly as it is and do not include it in your patch.

Every subject you touch must be an existing doc: node, or (only for a newly \
minted echr:DomesticProceeding standing in for a decision the facts mention \
but no node yet exists for) a brand-new doc: node with a lowercase_snake_case \
local name. Never write a triple whose subject is in the echr:, schema:, \
rdf:, or rdfs: namespace -- those are read-only vocabulary.

DUPLICATE ENTITIES. You are also given groups of nodes that share a \
distinguishing key -- two proceedings with the same court AND the same \
decision date, or two authorities with the same name. One court does not \
decide the same case twice on one day, so such a group is usually one entity \
the extraction split in two. For each group that really is one entity, emit a \
`merges` entry naming which node to KEEP and which to DROP. Keep the node \
carrying the most evidence -- more properties, a fuller supporting quote, a \
more specific label. Do NOT hand-write add/remove triples to do the merging \
yourself: naming the pair is enough, and the pipeline moves the properties \
and the inbound links and deletes the duplicate for you. If a group turns out \
to be two genuinely distinct steps that happen to share a date, leave it out \
of `merges` entirely.

Return a patch: one RepairGroup per finding you act on, each carrying the \
rationale and the exact add/remove operations, plus a `merges` list for \
duplicate entities. If a candidate, gap or duplicate group does not hold up \
under the quotes, leave it out rather than forcing an edge.
"""


def build_user_prompt(
    *,
    doc_curie_prefix: str,
    ontology_context: str,
    summaries: list[ProceedingSummary],
    candidates: list[dict],
    duplicate_groups: list[dict],
    appeal_gaps: list[str],
    final_conflicts: list[str],
    validator_findings: list[dict],
) -> str:
    payload = {
        "domestic_proceedings": [vars(p) for p in summaries],
        "mistyped_candidates": candidates,
        "duplicate_candidate_groups": duplicate_groups,
        "flagged_missing_followsProceeding_for": appeal_gaps,
        "flagged_isFinalDomesticDecision_conflict": final_conflicts,
        "validator_findings": validator_findings,
    }
    return (
        f"Document namespace prefix: {doc_curie_prefix}\n\n"
        f"Relevant ontology fragment (echr.ttl, DomesticProceeding-related):\n"
        f"{ontology_context}\n\n"
        f"Current graph state and flagged findings:\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n"
    )


def call_repair_model(
    client: OpenAI, model: str, user_prompt: str, *, temperature: float = 1.0
) -> RepairPatch:
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format=RepairPatch,
        temperature=temperature,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError("model refused or returned no parseable patch")
    return parsed


# ---------------------------------------------------------------------------
# Applying the patch
# ---------------------------------------------------------------------------


def resolve_term(
    graph: Graph,
    curie_or_literal: str,
    *,
    is_literal: bool,
    datatype: str | None,
    lang: str | None,
):
    if not is_literal:
        prefix, _, local = curie_or_literal.partition(":")
        ns = dict(graph.namespaces()).get(prefix)
        if ns is None:
            raise ValueError(f"unknown prefix in CURIE: {curie_or_literal!r}")
        return URIRef(str(ns) + local)
    dt = None
    # RDF forbids a literal from carrying both a language tag and a datatype
    # (rdf:langString implies the language tag alone); lang wins if the model
    # supplied both.
    if datatype and not lang:
        prefix, _, local = datatype.partition(":")
        ns = dict(graph.namespaces()).get(prefix)
        if ns is not None:
            dt = URIRef(str(ns) + local)
    return RDFLiteral(curie_or_literal, datatype=dt, lang=lang)


def merge_nodes(graph: Graph, keep: URIRef, drop: URIRef) -> dict:
    """Fold ``drop`` into ``keep`` and delete it. Returns a summary of the move.

    Three moves, in order:

    1. INBOUND. Every ``(s, p, drop)`` becomes ``(s, p, keep)``. This is what
       repairs the chain: a followsProceeding edge aimed at the duplicate ends
       up aimed at the survivor without the model having to name the edge.
       A rewrite that would produce a self-loop (``s`` is ``keep``) is dropped
       instead -- echr:followsProceeding is owl:AsymmetricProperty and
       owl:IrreflexiveProperty, so a self-loop is a schema violation, and the
       identity that made the pair a duplicate is exactly what creates one.
    2. OUTBOUND. Every ``(drop, p, o)`` is added as ``(keep, p, o)``, EXCEPT
       where ``p`` is owl:FunctionalProperty and ``keep`` already has a value --
       the survivor's own value wins, otherwise the merge would manufacture the
       multi-value contradiction it exists to remove. rdf:type and rdfs:label
       are treated the same way: the survivor keeps its own label.
    3. DELETE. Every remaining triple with ``drop`` as subject or object goes.
       Nothing is left behind to be post-processed away later.
    """
    functional = functional_properties() | {RDFS.label}
    moved_in = rewritten_selfloop = moved_out = kept_own = 0

    for s_, p_ in list(graph.subject_predicates(drop)):
        graph.remove((s_, p_, drop))
        if s_ == keep:
            rewritten_selfloop += 1
            continue
        graph.add((s_, p_, keep))
        moved_in += 1

    for p_, o_ in list(graph.predicate_objects(drop)):
        if p_ == RDF.type:
            graph.add((keep, p_, o_))
            continue
        # Mirror of the inbound self-loop guard, for the other direction: a
        # `drop -> keep` edge would become `keep -> keep`. Both directions have
        # to be checked -- guarding only the inbound one silently minted a
        # followsProceeding self-loop on the survivor (observed on gpt5mini L5,
        # 2026-08-19), violating owl:IrreflexiveProperty.
        if o_ == keep:
            rewritten_selfloop += 1
            continue
        if p_ in functional and (keep, p_, None) in graph:
            kept_own += 1
            continue
        graph.add((keep, p_, o_))
        moved_out += 1

    graph.remove((drop, None, None))
    graph.remove((None, None, drop))
    return {
        "inbound_edges_repointed": moved_in,
        "self_loops_dropped": rewritten_selfloop,
        "properties_moved": moved_out,
        "survivor_value_kept": kept_own,
    }


def sweep_stub_orphans(graph: Graph, doc_ns: str) -> list[str]:
    """Delete typed doc: nodes that carry no content AND nothing points at them.

    Deliberately narrow. A node with real properties -- a date, an outcome, a
    quote -- is CONTENT even when nothing links to it, and deleting it would
    lose an extraction rather than clean one up; those are left for the graph
    build to connect. Only a bare ``rdf:type`` (optionally plus a label), with
    zero inbound references, is swept: that is a stub the model minted and
    never populated, which no post-processing step can do anything with.
    """
    removed: list[str] = []
    for node in sorted(set(graph.subjects(RDF.type, None)), key=str):
        if not str(node).startswith(doc_ns):
            continue
        if any(True for _ in graph.subjects(None, node)):
            continue
        if {p for p in graph.predicates(node, None)} - {RDF.type, RDFS.label}:
            continue
        removed.append(_curie(graph, node))
        graph.remove((node, None, None))
    return removed


def apply_patch(
    graph: Graph, patch: RepairPatch, doc_ns: str
) -> tuple[Graph, list[dict]]:
    """Returns a NEW graph with the patch applied, plus an audit trail. Every
    op is checked against the two-namespace contract and, for removes,
    against what actually exists before it is applied.

    Also guards referential integrity for `add` ops: a doc:-namespaced URI
    object must already be a real node -- either present in the input graph
    or minted by an `add rdf:type` op elsewhere in this same patch. Without
    this, a retype op can point echr:hasCourt (or similar) at a node that
    was never created, silently introducing exactly the dangling-reference
    defect this pass exists to remove.
    """
    working = Graph()
    for t in graph:
        working.add(t)
    for prefix, ns in graph.namespaces():
        working.bind(prefix, ns)

    known_doc_nodes = {str(s) for s in working.subjects() if str(s).startswith(doc_ns)}
    for group in patch.groups:
        for op in group.ops:
            if (
                op.action == "add"
                and op.predicate == "rdf:type"
                and not op.object_is_literal
            ):
                subj_prefix, _, subj_local = op.subject.partition(":")
                if subj_prefix == "doc":
                    known_doc_nodes.add(doc_ns + subj_local)

    audit: list[dict] = []
    for group in patch.groups:
        for op in group.ops:
            record = {
                "finding": group.finding,
                "rationale": group.rationale,
                **op.model_dump(),
            }
            try:
                subj_prefix, _, _ = op.subject.partition(":")
                subj_ns = dict(working.namespaces()).get(subj_prefix)
                if subj_ns is None or str(subj_ns) != doc_ns:
                    record["status"] = "skipped: subject not in doc: namespace"
                    audit.append(record)
                    continue
                if op.action == "add":
                    # Closed-vocabulary discipline applies to the repair pass
                    # too. Without this a patch can "correct" a valid term to
                    # an invented one and the namespace guard waves it through.
                    unknown = unknown_echr_terms(
                        op.predicate, *([] if op.object_is_literal else [op.object])
                    )
                    if unknown:
                        record["status"] = (
                            "skipped: not defined in the ontology: "
                            + ", ".join(unknown)
                        )
                        audit.append(record)
                        continue
                if op.action == "add" and not op.object_is_literal:
                    obj_prefix, _, obj_local = op.object.partition(":")
                    if (
                        obj_prefix == "doc"
                        and (doc_ns + obj_local) not in known_doc_nodes
                    ):
                        record["status"] = (
                            "skipped: object node does not exist in the graph or this patch"
                        )
                        audit.append(record)
                        continue
                s = resolve_term(
                    working, op.subject, is_literal=False, datatype=None, lang=None
                )
                p = resolve_term(
                    working, op.predicate, is_literal=False, datatype=None, lang=None
                )
                o = resolve_term(
                    working,
                    op.object,
                    is_literal=op.object_is_literal,
                    datatype=op.datatype,
                    lang=op.lang,
                )
            except ValueError as exc:
                record["status"] = f"skipped: {exc}"
                audit.append(record)
                continue

            if op.action == "add":
                working.add((s, p, o))
                record["status"] = "applied"
            else:
                if (s, p, o) in working:
                    working.remove((s, p, o))
                    record["status"] = "applied"
                else:
                    record["status"] = "skipped: triple not present"
            audit.append(record)

    # Merges run AFTER the add/remove ops, never before: an op names nodes by
    # the CURIE the model was shown, and a merge that ran first would have
    # already deleted one of them, turning a valid op into a spurious skip.
    for merge in patch.merges:
        record = {
            "finding": "duplicate_entity",
            "rationale": merge.rationale,
            "action": "merge",
            "keep": merge.keep,
            "drop": merge.drop,
        }
        try:
            keep = resolve_term(
                working, merge.keep, is_literal=False, datatype=None, lang=None
            )
            drop = resolve_term(
                working, merge.drop, is_literal=False, datatype=None, lang=None
            )
        except ValueError as exc:
            record["status"] = f"skipped: {exc}"
            audit.append(record)
            continue

        if not (str(keep).startswith(doc_ns) and str(drop).startswith(doc_ns)):
            record["status"] = "skipped: merge outside the doc: namespace"
        elif keep == drop:
            record["status"] = "skipped: keep and drop are the same node"
        elif (keep, RDF.type, None) not in working:
            record["status"] = f"skipped: {merge.keep} is not a node in the graph"
        elif (drop, RDF.type, None) not in working:
            record["status"] = f"skipped: {merge.drop} is not a node in the graph"
        elif set(working.objects(keep, RDF.type)) != set(
            working.objects(drop, RDF.type)
        ):
            # Different classes means the model is asserting a retype, not a
            # duplicate. That is a legitimate change but it must come through an
            # explicit rdf:type op that the two-namespace guard can see.
            record["status"] = "skipped: nodes have different rdf:type"
        else:
            record["status"] = "applied"
            record["effect"] = merge_nodes(working, keep, drop)
        audit.append(record)

    return working, audit


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def repair_one(
    facts_ttl: Path,
    client: OpenAI,
    model: str,
    *,
    dry_run: bool,
    temperature: float = 1.0,
) -> None:
    graph = Graph()
    graph.parse(facts_ttl)
    doc_ns = str(next((ns for prefix, ns in graph.namespaces() if prefix == "doc"), ""))
    if not doc_ns:
        print(f"  skip {relative(facts_ttl)}: no doc: namespace bound")
        return

    summaries = summarize_proceedings(graph)
    if len(summaries) < 2:
        print(f"  skip {relative(facts_ttl)}: fewer than 2 DomesticProceeding nodes")
        return

    candidates = find_mistyped_candidates(graph)
    duplicate_groups = find_duplicate_candidates(graph)
    appeal_gaps = find_appeal_shaped_gaps(summaries)
    final_conflicts = find_final_decision_conflicts(summaries)
    validator_findings = load_validator_findings(facts_ttl) + find_shape_violations(
        graph
    )

    if not (
        candidates
        or duplicate_groups
        or appeal_gaps
        or final_conflicts
        or validator_findings
    ):
        print(f"  clean {relative(facts_ttl)}: nothing flagged")
        return

    ontology_context = load_ontology_context()
    prompt = build_user_prompt(
        doc_curie_prefix="doc",
        ontology_context=ontology_context,
        summaries=summaries,
        candidates=candidates,
        duplicate_groups=duplicate_groups,
        appeal_gaps=appeal_gaps,
        final_conflicts=final_conflicts,
        validator_findings=validator_findings,
    )
    patch = call_repair_model(client, model, prompt, temperature=temperature)

    applied_ops = sum(1 for g in patch.groups for op in g.ops)
    print(
        f"  {relative(facts_ttl)}: model proposed {len(patch.groups)} group(s), "
        f"{applied_ops} op(s), {len(patch.merges)} merge(s)"
    )

    repaired_graph, audit = apply_patch(graph, patch, doc_ns)
    swept = sweep_stub_orphans(repaired_graph, doc_ns)
    if swept:
        audit.append(
            {
                "finding": "stub_orphan_sweep",
                "rationale": (
                    "Typed nodes carrying nothing but rdf:type/rdfs:label with no "
                    "inbound reference; nothing downstream can use them."
                ),
                "action": "delete_node",
                "nodes": swept,
                "status": "applied",
            }
        )
    applied = sum(1 for a in audit if a["status"] == "applied")
    skipped = [a for a in audit if a["status"] != "applied"]
    merged = sum(
        1 for a in audit if a["action"] == "merge" and a["status"] == "applied"
    )
    print(
        f"    applied {applied} (of which {merged} merge(s)), skipped {len(skipped)}, "
        f"stub orphans swept {len(swept)}"
    )
    for a in skipped:
        if a["action"] == "merge":
            print(f"    SKIPPED merge {a['keep']} <- {a['drop']}: {a['status']}")
        else:
            print(
                f"    SKIPPED [{a['finding']}] {a['action']} {a['subject']} "
                f"{a['predicate']} {a['object']}: {a['status']}"
            )

    audit_path = facts_ttl.parent / (
        facts_ttl.name.removesuffix(".ttl") + ".repairs.json"
    )
    audit_path.write_text(
        json.dumps({"source": str(relative(facts_ttl)), "operations": audit}, indent=2)
    )

    if dry_run:
        print(
            f"    dry-run: not writing {relative(facts_ttl)} or {relative(audit_path)} contents applied"
        )
        return

    backup_dir = facts_ttl.parent / "backup"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / facts_ttl.name
    if not backup_path.exists():
        shutil.copy2(facts_ttl, backup_path)
    elif backup_path.read_bytes() != facts_ttl.read_bytes():
        raise RuntimeError(
            f"{relative(backup_path)} already exists and differs from the current "
            f"{relative(facts_ttl)} -- refusing to overwrite an earlier backup"
        )

    repaired_graph.serialize(destination=str(facts_ttl), format="turtle")
    print(f"    wrote {relative(facts_ttl)} (backup at {relative(backup_path)})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--facts-dir",
        type=Path,
        required=True,
        help="Directory of <stem>.facts.ttl files",
    )
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "OpenAI-compatible endpoint, e.g. http://localhost:8000/v1 for a vLLM "
            "server. Omit to use the OpenAI API. The repair pass should normally "
            "run on the SAME model that produced the facts."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key. Defaults to OPENAI_API_KEY from keys.env; vLLM servers accept any placeholder.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help=(
            "Per-request timeout in seconds. vLLM guided decoding can hang "
            "indefinitely on a schema it cannot satisfy; without a bound the "
            "SDK default plus retries stalls a batch run for ~30 min per "
            "document. A timed-out document is reported and skipped."
        ),
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="Sampling temperature. gpt-5 reasoning models only accept 1.0.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Propose and audit-log, but do not write .ttl files",
    )
    args = parser.parse_args()

    api_key = args.api_key or (
        "token-abc123" if args.base_url else load_openai_api_key()
    )
    client = OpenAI(
        api_key=api_key, base_url=args.base_url, timeout=args.timeout, max_retries=1
    )
    facts_files = sorted(args.facts_dir.glob("*.facts.ttl"))
    if not facts_files:
        raise SystemExit(f"no *.facts.ttl files under {relative(args.facts_dir)}")

    endpoint = args.base_url or "api.openai.com"
    print(f"repairing {len(facts_files)} file(s) under {relative(args.facts_dir)}")
    print(
        f"  model: {args.model} @ {endpoint} "
        f"(temperature {args.temperature}, timeout {args.timeout}s)"
    )
    failures = 0
    for facts_ttl in facts_files:
        try:
            repair_one(
                facts_ttl,
                client,
                args.model,
                dry_run=args.dry_run,
                temperature=args.temperature,
            )
        except Exception as exc:  # noqa: BLE001
            # One document failing must not abandon the remaining nine: the
            # experiment needs whatever repaired output is obtainable, and the
            # failure itself is a result worth recording per model.
            failures += 1
            print(
                f"  FAILED {relative(facts_ttl)}: {type(exc).__name__}: {str(exc)[:300]}"
            )
    if failures:
        print(f"  {failures}/{len(facts_files)} document(s) failed to repair")


if __name__ == "__main__":
    main()
