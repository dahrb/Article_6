"""
repair_facts.py
----------------
Final consistency pass over an already-extracted facts graph, run once per
document after `ontocast process` has finished.

OntoCast's own pipeline processes content units independently and in parallel
(see the "chunking" investigation in this project's history) so it cannot
notice that two nodes it minted in separate calls should be linked, and even
within one call it does not reliably apply relations it has full context for.
This script re-reads the merged graph as a whole and repairs it in SEQUENTIAL
STAGES, one LLM call per stage per document, each scoped to one region of the
ontology so every call only sees the classes, properties and findings that are
actually relevant to it (see `STAGES` below). A later stage sees whatever an
earlier stage already fixed, since they share one graph in memory and the
patch from each stage is applied before the next stage's findings are derived.

  STAGE 1: domestic proceedings
    1. missing echr:followsProceeding links between domestic proceedings,
       flagged deterministically wherever hasInstanceLevel places the
       proceeding at an appeal-shaped level (echr:LevelAppeal,
       echr:LevelCassation, echr:LevelSupervisoryReview, echr:LevelReopening)
       or hasOutcome is echr:OutcomeRemitted, and no link is present;
    2. echr:isFinalDomesticDecision asserted true on more than one proceeding
       in the same document;
    3. entities that describe a domestic authority's decision but were typed
       outside echr:DomesticProceeding (heuristically surfaced by keyword,
       not assumed -- the model decides whether each candidate is really
       one);
    4. duplicate proceedings (same court + same decision date).

  STAGE 2: persons and participation
    1. duplicate echr:NaturalPerson nodes (same normalized name);
    2. domestic-event nodes (proceedings, administrative actions, enforcement
       actions, prosecutorial reviews) with no echr:hasParticipation link to
       any party at all;
    3. echr:NaturalPerson nodes never connected to any event through an
       echr:Participation node.

  STAGE 3: domestic authorities
    1. duplicate echr:DomesticAuthority nodes (same normalized name).

  EVERY STAGE also gets, scoped to its own classes:
    - whatever the SHACL-lite validator already flagged in the sibling
      `<stem>.facts.validation.json` (dangling_reference, suspect_multi_value)
      on its first pass, PLUS whatever ontology/echr-shapes.ttl flags on the
      graph directly on every pass -- the multi-label false-merge shape in
      particular exists specifically so this pass can fix what it names
      (added 2026-08-19, see `find_shape_violations` below);
    - the full, unfiltered set of triples on every doc: instance of its
      classes (`entity_dump` below) -- not a curated subset, so nothing in
      that region of the graph is hidden from the stage responsible for it.

DUPLICATE ENTITIES ARE MERGED AND DELETED, NOT LEFT ORPHANED. The model only
names the pair and which node survives; `merge_nodes` does the work -- it
re-points every inbound edge onto the survivor, moves across any property the
survivor lacks (the survivor's own value wins for anything the ontology
declares owl:FunctionalProperty, so a merge can never manufacture the
multi-value contradiction it exists to remove), and then deletes every triple
mentioning the duplicate. Nothing is left behind for a later pass to clean up.

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
import datetime
import json
import os
import re
import shutil
import sys
import time
from collections.abc import Iterator
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
    os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr.ttl")
)
KEYS_FILE = REPO_ROOT / "keys.env"

ECHR = Namespace("https://growgraph.dev/echr#")
SH = Namespace("http://www.w3.org/ns/shacl#")


@lru_cache(maxsize=1)
def functional_properties() -> frozenset[URIRef]:
    """Properties the ontology declares owl:FunctionalProperty.

    Read from the ontology file rather than hardcoded, so a schema edit cannot leave
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


APPEAL_SHAPED_LEVELS = {
    ECHR.LevelAppeal,
    ECHR.LevelCassation,
    ECHR.LevelSupervisoryReview,
    ECHR.LevelReopening,
}
APPEAL_SHAPED_OUTCOMES = {
    ECHR.OutcomeRemitted,
}

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
# Patch schema
# ---------------------------------------------------------------------------


# NOT a grammar cap. Three attempts to stop the output-cap truncations by
# constraining the decoding grammar were made on 2026-08-24 and all three
# failed; this comment exists so the fourth is not attempted from scratch.
#
#   maxLength on the rationale     truncations went 0 -> 2 on nochunk_ttl_mv1
#   + maxItems on groups and ops   truncations went 2 -> 5
#   removing the quote_index field no effect, stalls identically
#
# vLLM does enforce both keywords -- a prompt demanding a 200-group patch came
# back terminated at the ceiling with finish_reason "stop" -- so the constraints
# work and simply do not address the failure.
#
# What the failure actually is, from dumping a raw L10 proceedings generation:
# the model emits ONE correct operation, stalls immediately after the `object`
# value, and then pads with whitespace for the entire remaining budget. The
# output was 56,018 characters of which 55,731 -- 99.5% -- were whitespace. The
# JSON grammar permits arbitrary whitespace between tokens, so a stalled model
# has a legal token to emit forever, and neither a shorter string field nor a
# shorter list can take that away. vLLM's disable_any_whitespace option is the
# right shape of fix but was silently ignored by this server version.
#
# This is the same pathology response_repair.py documents for facts-render.
# It is reliably reproducible on L10's proceedings prompt and is a decoding
# bug, not a prompt or schema defect. Leave --max-tokens as the backstop.

# How many entries of any one structural-gap list go into a single pass.
#
# The multi-pass loop re-derives every finding from the working graph before
# each pass, so a list longer than this is deferred rather than dropped and the
# next pass picks up what is outstanding. Batching keeps any single patch to a
# reviewable size.
#
# Honest about what this does NOT do: it was added to fix L10, whose
# proceedings stage must add nineteen hasCourt links and truncates every time,
# and it did not. Cutting that stage's input to eight gaps produced exactly the
# same whitespace stall (see the block above). Keep it for the bounded patches,
# not as a truncation remedy.
MAX_GAP_ENTRIES_PER_PASS = 8


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
            "for a datatype property. On a 'remove', an empty string means "
            "every value the subject holds under this predicate."
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
    quote_index: int | None = Field(
        default=None,
        description=(
            "QUOTE-FIDELITY STAGE ONLY. The `index` of the `unverified_quotes` "
            "entry this operation targets. Set it on a `remove` of "
            "echr:hasSupportingQuote and leave `object` empty: the pipeline "
            "looks the exact literal up by index and deletes that one value. "
            "Do NOT retype the quote text -- naming the index is both shorter "
            "and exact. Omit this field on every other kind of operation."
        ),
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
        description=(
            "Why, citing the supporting quote or field that justifies it. "
            "Two sentences at most."
        ),
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
        description=(
            "Why these are the same entity, citing the label/date/quote. "
            "Two sentences at most."
        ),
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
    instance_level: str | None
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
        instance_level = next(graph.objects(s, ECHR.hasInstanceLevel), None)
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
                instance_level=(
                    _curie(graph, instance_level)
                    if instance_level is not None
                    else None
                ),
                is_final=bool(is_final) if is_final is not None else None,
                follows=[
                    _curie(graph, o) for o in graph.objects(s, ECHR.followsProceeding)
                ],
                quotes=[str(o) for o in graph.objects(s, ECHR.hasSupportingQuote)],
            )
        )
    return out


def entity_dump(graph: Graph, classes: tuple[URIRef, ...], doc_ns: str) -> list[dict]:
    """Every triple on every doc: instance of `classes`, in full.

    Unlike ProceedingSummary above, this is not a curated subset of fields --
    it is whatever the extraction actually put on the node, rendered as one
    dict per instance with every predicate (CURIE) mapped to its list of
    values (CURIE for a URI object, plain text for a literal). That is the
    point: a repair stage scoped to a class should see everything about its
    instances, not just the properties someone thought to add a field for.
    """
    out = []
    for s in sorted(
        {n for cls in classes for n in graph.subjects(RDF.type, cls)}, key=str
    ):
        if not str(s).startswith(doc_ns):
            continue
        props: dict[str, list[str]] = {}
        for p, o in graph.predicate_objects(s):
            if p == RDF.type:
                continue
            props.setdefault(_curie(graph, p), []).append(_render(graph, o))
        out.append({"curie": _curie(graph, s), **props})
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
APPEAL_SHAPED_LEVEL_CURIES = {
    "echr:" + str(o).split("#")[-1] for o in APPEAL_SHAPED_LEVELS
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
      NaturalPerson       same normalized hasPersonName / rdfs:label. Weaker
                          than the other two keys -- two distinct people can
                          share a name (a father and son, two co-accused) --
                          so this one leans harder on the model actually
                          checking the quotes before merging.

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

    by_person_name: dict[str, list[URIRef]] = {}
    for s_ in graph.subjects(RDF.type, ECHR.NaturalPerson):
        if not str(s_).startswith(doc_ns):
            continue
        name = next(graph.objects(s_, ECHR.hasPersonName), None) or next(
            graph.objects(s_, RDFS.label), None
        )
        if name is None:
            continue
        by_person_name.setdefault(_normalize_name(name), []).append(s_)
    for name, nodes in by_person_name.items():
        if len(nodes) > 1:
            groups.append(
                {
                    "class": "echr:NaturalPerson",
                    "matched_on": f"same person name {name!r}",
                    "members": _members(nodes),
                }
            )
    return groups


@lru_cache(maxsize=1)
def domestic_event_classes() -> frozenset[URIRef]:
    """echr:DomesticEvent and every class transitively subClassOf it.

    Read from the ontology rather than hardcoded, for the same reason as
    `functional_properties` above: a new DomesticEvent subclass should not
    need this file edited to be covered by the missing-participation check.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    return frozenset(g.transitive_subjects(RDFS.subClassOf, ECHR.DomesticEvent)) | {
        ECHR.DomesticEvent
    }


def find_missing_participation(graph: Graph, doc_ns: str) -> list[str]:
    """DomesticEvent-shaped nodes with no echr:hasParticipation link at all.

    A proceeding, administrative action, enforcement action or prosecutorial
    review with zero recorded participants is very likely missing an
    echr:Participation node the source text supports, not an event nobody
    took part in.
    """
    out = []
    for cls in domestic_event_classes():
        for s in graph.subjects(RDF.type, cls):
            if not str(s).startswith(doc_ns):
                continue
            if (s, ECHR.hasParticipation, None) not in graph:
                out.append(_curie(graph, s))
    return sorted(set(out))


def find_proceedings_missing_court(graph: Graph, doc_ns: str) -> list[str]:
    """DomesticEvent-shaped nodes with no echr:hasCourt link at all.

    This finder exists because the defect it names was invisible to the entire
    repair pass. Measured 2026-08-24 on the nochunk_ttl_mv1 arm: 19 of 60
    proceedings carried no deciding authority, and the number was UNCHANGED by
    repair -- not because the model declined to fix it, but because nothing
    ever told it. The proceedings stage reported "nothing flagged" on 8 of 10
    documents and made 2 model calls in the whole run.

    Nor would SHACL have caught it. `echr-shapes.ttl` constrains hasCourt with
    sh:maxCount 1 and no sh:minCount, so a courtless proceeding is perfectly
    conformant -- deliberately, because some events genuinely have no deciding
    authority. That makes this exactly the kind of finding that has to be
    SURFACED for a model to judge rather than asserted as an error: an
    administrative action taken by a mayor has no court and should keep none.

    Scoped to DomesticEvent subclasses for the same reason
    `find_missing_participation` is: they share the property vocabulary, and a
    new subclass should be covered without editing this file.
    """
    out = []
    for cls in domestic_event_classes():
        for s in graph.subjects(RDF.type, cls):
            if not str(s).startswith(doc_ns):
                continue
            if (s, ECHR.hasCourt, None) not in graph:
                out.append(_curie(graph, s))
    return sorted(set(out))


def find_unlinked_persons(graph: Graph, doc_ns: str) -> list[str]:
    """echr:NaturalPerson nodes never named as an echr:Participation's party.

    Surfaced, not assumed wrong: a legal representative is linked via
    echr:isRepresentedBy on the party they act for, not a Participation node,
    so this is deterministic surfacing for the model to judge, exactly like
    every other finding here -- someone mentioned only in passing, with no
    procedural role, legitimately has no link to add.
    """
    linked = set(graph.objects(None, ECHR.participatingParty))
    out = []
    for s in graph.subjects(RDF.type, ECHR.NaturalPerson):
        if not str(s).startswith(doc_ns):
            continue
        if s not in linked:
            out.append(_curie(graph, s))
    return sorted(set(out))


@lru_cache(maxsize=64)
def load_source_text(input_path: Path, line_number: int | None) -> str | None:
    """The source text one facts file was extracted from, normalized.

    `input_path` is the run's `input.jsonl` (or `.json`); `line_number` is the N
    in `<stem>.L<N>.facts.ttl`, 1-based, matching how run_data.sh and
    carry_forward.py name their outputs. A single-record run has no `.L<N>` and
    takes the only record.
    """
    if not input_path.is_file():
        return None
    text = input_path.read_text(encoding="utf-8")
    if input_path.suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        records = json.loads(text)
    if not isinstance(records, list) or not records:
        return None
    if line_number is None:
        record = records[0] if len(records) == 1 else None
    else:
        record = records[line_number - 1] if line_number <= len(records) else None
    if record is None:
        return None
    return record.get("text")


def find_unverified_quotes(graph: Graph, source_text: str) -> list[dict]:
    """Every echr:hasSupportingQuote whose text is not in the source document.

    Uses the SAME check the standalone validator applies
    (`diagnostics.validate_source_quotes.quote_verifies`), imported rather than
    reimplemented so the repair pass can never disagree with the report about
    what counts as verified: curly quotes folded to straight, whitespace runs
    collapsed, and each ellipsis-delimited segment required to appear.

    Each finding carries the FULL triple, because the fix is a remove of that
    exact literal and the model cannot reproduce a long quote from memory --
    the 2026-08-23 sweep skipped repair ops for precisely that reason.
    """
    from art6.ontology.diagnostics.validate_source_quotes import (
        normalize,
        quote_verifies,
    )

    normalized = normalize(source_text)
    out: list[dict] = []
    for s, _, o in graph.triples((None, ECHR.hasSupportingQuote, None)):
        quote = str(o)
        if quote_verifies(quote, normalized):
            continue
        out.append(
            {
                # Stable handle for this exact literal, so a `remove` can name it
                # by number instead of reproducing it. See TripleOp.quote_index.
                "index": len(out),
                "subject": _curie(graph, s),
                "predicate": "echr:hasSupportingQuote",
                "quote": quote,
                "subject_types": sorted(
                    _curie(graph, t) for t in graph.objects(s, RDF.type)
                ),
                "subject_label": _label(graph, s),
            }
        )
    return out


def find_appeal_shaped_gaps(summaries: list[ProceedingSummary]) -> list[str]:
    return [
        p.curie
        for p in summaries
        if not p.follows
        and (
            p.instance_level in APPEAL_SHAPED_LEVEL_CURIES
            or p.outcome in APPEAL_SHAPED_OUTCOME_CURIES
        )
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


@lru_cache(maxsize=2)
def _shapes_graph(*, include_undefined_term_shape: bool = True) -> Graph:
    """The static shapes plus the generated undefined-term shape.

    Goes through validate_shapes.load_shapes() rather than parsing SHAPES_PATH
    directly, so the closed-vocabulary check travels with the shapes: invented
    vocabulary now arrives as a shacl_violation finding and the repair model is
    asked to fix it, instead of it only ever being counted in a report. The
    predicate half of that check is not expressible as a static shape at all --
    see undefined_term_shape() -- so without this, an invented PROPERTY was
    invisible to every stage of the pipeline.
    """
    from art6.ontology.validate_shapes import load_shapes

    return load_shapes(include_undefined_term_shape=include_undefined_term_shape)


def _graph_fingerprint(graph: Graph) -> str:
    """Content hash of a graph, for the SHACL cache below.

    Hashes every triple, not just `len(graph)`: a patch that removes one triple
    and adds another leaves the count identical while changing what validates.
    Cost is linear and tiny next to a SHACL run -- microseconds against seconds.
    """
    import hashlib

    h = hashlib.blake2b(digest_size=16)
    for triple in sorted(graph, key=lambda t: (str(t[0]), str(t[1]), str(t[2]))):
        h.update(repr(triple).encode("utf-8"))
    return h.hexdigest()


_SHAPE_VIOLATION_CACHE: dict[str, list[dict]] = {}


def find_shape_violations(graph: Graph) -> list[dict]:
    """Static SHACL violations from ontology/echr-shapes.ttl, in the graph
    alone -- no source text, no LLM call.

    Returned in the same shape as load_validator_findings, so both feed the
    model identically. This is what makes the shapes file load-bearing rather
    than a standalone report card: SingleLabelShape names exactly the
    false-merge defect (a node with two rdfs:label values) that neither the
    functional-property nor the vocabulary checks can see, and this function
    is what gets that finding in front of the model that can fix it.

    RUNS AS CORE SHACL, with the vocabulary check done in Python beside it.
    This is what stopped SHACL being the repair pass's dominant cost. Every
    shape in echr-shapes.ttl is Core; the only SHACL-AF in the shapes graph is
    the pair of SPARQL constraints in the generated undefined-term shape, and
    `advanced=True` -- needed solely to reach those two -- multiplied the cost
    of the whole validation by 360x. Measured 2026-08-24 over 20 documents of
    the sweep: 115.5s with it on, 0.2s with it off, identical Core findings.
    `validate_shapes.find_undefined_terms` recovers the two SPARQL constraints
    as a set-membership loop, so nothing is given up: verified on every
    document of the 2026-08-23 sweep to produce exactly the same rows.

    Before this the pass was dominated by validation rather than inference --
    92% of repair wall clock on the worst arm (492.9s of 535.6s, against 42.7s
    of actual inference), and 946.1s of rolling_3k6k's 1090.8s.

    STILL MEMOIZED ON GRAPH CONTENT, because the loop calls this TWICE per pass
    (once to derive findings, once for the no-progress check) x passes x stages
    -- up to 12 runs per document -- and most of those validate a byte-identical
    graph: the no-progress check at the end of pass N sees exactly what pass N+1
    re-derives, and stage N+1 opens on exactly what stage N closed with. Keyed
    on content rather than object identity because apply_patch returns a NEW
    Graph every time.
    """
    from pyshacl import validate as shacl_validate

    from art6.ontology.validate_shapes import find_undefined_terms

    fingerprint = _graph_fingerprint(graph)
    if (cached := _SHAPE_VIOLATION_CACHE.get(fingerprint)) is not None:
        return list(cached)

    conforms, results_graph, _ = shacl_validate(
        graph,
        shacl_graph=_shapes_graph(include_undefined_term_shape=False),
        advanced=False,
        inference="none",
        abort_on_first=False,
        meta_shacl=False,
    )

    findings = [
        {
            "kind": "shacl_violation",
            "severity": "error",
            "message": message,
            "subject": _curie(graph, URIRef(focus)) if focus else "",
            "predicate": _curie(graph, URIRef(path)) if path else "",
            "values": [],
        }
        for focus, path, message in find_undefined_terms(graph)
    ]
    if conforms:
        _SHAPE_VIOLATION_CACHE[fingerprint] = list(findings)
        return findings

    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        severity = next(results_graph.objects(result, SH.resultSeverity), None)
        message = next(results_graph.objects(result, SH.resultMessage), None)
        focus = next(results_graph.objects(result, SH.focusNode), None)
        path = next(results_graph.objects(result, SH.resultPath), None)
        # sh:value carries the offending term. Core constraints set it to the
        # bad value; the generated undefined-term constraints bind ?value, which
        # SHACL surfaces the same way. Without this the model is told only that
        # "something" is invented and has to parse the IRI back out of the
        # message -- and for a SPARQL constraint sh:resultPath is absent, so
        # the predicate field is empty and sh:value is the ONLY handle on it.
        value = next(results_graph.objects(result, SH.value), None)
        findings.append(
            {
                "kind": "shacl_violation",
                "severity": "error" if severity == SH.Violation else "warning",
                "message": str(message) if message is not None else "",
                "subject": _curie(graph, focus) if focus is not None else "",
                "predicate": _curie(graph, path) if path is not None else "",
                "values": [_curie(graph, value)] if value is not None else [],
            }
        )
    _SHAPE_VIOLATION_CACHE[fingerprint] = list(findings)
    return findings


def load_ontology_fragment(classes: tuple[URIRef, ...]) -> str:
    """The fragment of echr.ttl relevant to `classes`, read live so this
    script can never drift out of sync with the ontology's own definitions.

    Three things go in, and the third one is load-bearing:

    1. each class's own definition;
    2. every property whose rdfs:domain is one of `classes` -- so a stage
       scoped to echr:NaturalPerson sees echr:hasGender and echr:hasPersonName
       without being handed the whole ontology;
    3. THE MEMBERS of every closed vocabulary reachable from those properties'
       rdfs:range, e.g. echr:GenderMale / echr:GenderFemale / echr:GenderOther
       for echr:hasGender.

    Point 3 exists because leaving it out is actively destructive, not merely
    unhelpful. The system prompt tells the model the ontology is CLOSED and
    that anything absent from this fragment "does not exist, however sensible
    its name looks", with instructions to remove it. A fragment that names
    echr:Gender as a class but never lists its individuals therefore reads as
    proof that echr:GenderMale is invented -- and the correct, evidence-backed
    triple gets deleted. Measured on the 2026-08-23 smoke test: the model spent
    its entire 8,000-token budget reasoning in circles about whether
    echr:GenderMale was real, truncating every one of three draws, and the
    patch it did produce removed the valid gender triple. Both symptoms are
    this omission.

    Note the range expansion is one hop, deliberately. A vocabulary member's
    own type is a vocabulary class, so following ranges transitively would
    walk most of the ontology back into every stage and undo the scoping.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    nsm = g.namespace_manager
    lines: list[str] = []

    def emit(subject: URIRef) -> None:
        for p, o in g.predicate_objects(subject):
            lines.append(f"{nsm.qname(subject)} {nsm.qname(p)} {_render(g, o)} .")

    in_scope = set(classes)
    for cls in classes:
        emit(cls)

    # Properties whose domain is in scope, plus the range classes they reach.
    for prop_s in set(g.subjects(RDFS.domain, None)):
        domains = set(g.objects(prop_s, RDFS.domain))
        if not domains & set(classes):
            continue
        emit(prop_s)
        for rng in g.objects(prop_s, RDFS.range):
            if isinstance(rng, URIRef):
                in_scope.add(rng)

    # Every named individual of an in-scope class: the closed vocabularies'
    # actual members, which is what makes the fragment usable as the closed
    # world the system prompt claims it is.
    for cls in in_scope:
        for member in g.subjects(RDF.type, cls):
            if isinstance(member, URIRef) and member != cls:
                emit(member)

    return "\n".join(sorted(set(lines)))


def _render(g: Graph, o) -> str:
    if isinstance(o, URIRef):
        try:
            return g.namespace_manager.qname(o)
        except Exception:  # noqa: BLE001
            return f"<{o}>"
    return f'"{o}"'


def _resolve_subject(graph: Graph, subject: str) -> URIRef | None:
    """A finding's `subject` field, which is either a full URI (from the
    extraction-time validator's validation.json) or a CURIE (from
    `find_shape_violations`), back to a node -- so both finding sources can
    be filtered by class the same way."""
    if not subject:
        return None
    if subject.startswith("http"):
        return URIRef(subject)
    try:
        return graph.namespace_manager.expand_curie(subject)
    except Exception:  # noqa: BLE001
        return None


def _finding_in_classes(
    graph: Graph, finding: dict, classes: tuple[URIRef, ...]
) -> bool:
    """Whether `finding["subject"]` is rdf:type one of `classes` in `graph`.

    Scopes a graph-wide finding (SHACL violation, extraction-time validator
    entry) to one repair stage. A subject the resolver cannot place -- an
    unresolvable CURIE, or a bare property-only SPARQL finding with no
    focusNode -- is kept rather than dropped, so a stage never silently loses
    a finding it has no way to attribute elsewhere.
    """
    node = _resolve_subject(graph, finding.get("subject", ""))
    if node is None:
        return True
    types = set(graph.objects(node, RDF.type))
    if not types:
        return True
    return bool(types & set(classes))


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts" / "repair_system_prompt.txt"
)
STAGE_GUIDANCE_DIR = Path(__file__).resolve().parent / "prompts"


def load_system_prompt() -> str:
    """The current contents of prompts/repair_system_prompt.txt.

    Read fresh on every call, not cached: this is the model's instructions,
    edited by hand far more often than the code around it, and a stale
    in-process copy would mean a mid-session edit silently not taking effect.
    """
    if not SYSTEM_PROMPT_PATH.is_file():
        raise SystemExit(f"repair system prompt not found: {SYSTEM_PROMPT_PATH}")
    text = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"repair system prompt is empty: {SYSTEM_PROMPT_PATH}")
    return text


def load_stage_guidance(filename: str) -> str:
    """The current contents of one prompts/repair_stage_*.txt file.

    Read fresh on every call, for the same reason as `load_system_prompt`.
    """
    path = STAGE_GUIDANCE_DIR / filename
    if not path.is_file():
        raise SystemExit(f"repair stage guidance not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"repair stage guidance is empty: {path}")
    return text


@dataclass(frozen=True)
class RepairStage:
    """One scoped region of the ontology, checked by its own LLM call.

    `ontology_classes` bounds what `load_ontology_fragment` shows the model;
    `dump_classes` bounds what `entity_dump` shows it about the actual graph;
    `filter_classes` bounds which graph-wide findings (SHACL violations,
    extraction-time validator entries, duplicate groups) get shown at all --
    kept separate from the other two because a finding can legitimately
    attach to a class this stage cares about without that class needing its
    own ontology fragment or entity dump (e.g. echr:Party for stage 2).
    """

    key: str
    name: str
    guidance_file: str
    ontology_classes: tuple[URIRef, ...]
    dump_classes: tuple[URIRef, ...]
    filter_classes: tuple[URIRef, ...]
    duplicate_classes: frozenset[str]
    check_mistyped: bool = False
    # When true the stage is handed the document's full source text and the list
    # of quotes that do not appear in it. Only the quotes stage sets this: source
    # text is large, and a stage that cannot act on it should not pay for it.
    needs_source_text: bool = False


# ORDER: authorities, then proceedings, then persons, then quotes.
#
# The original reason given for putting authorities first was that the
# proceedings stage could then link hasCourt to authorities that already exist,
# closing the measured 19 -> 19 gap in `proceedings without hasCourt`. THAT
# EXPLANATION WAS WRONG and is recorded here so it is not re-derived. Tested
# 2026-08-24 on nochunk_ttl_mv1 with identical raw input and only the order
# changed: proc_no_court stayed at 19, singletons improved by exactly one node.
#
# The real cause of that gap was that nothing ever told the model about it --
# the proceedings stage had no missing-court finder and reported "nothing
# flagged" on 8 of 10 documents, and echr-shapes.ttl constrains hasCourt with
# sh:maxCount and no sh:minCount, so SHACL is silent too. See
# find_proceedings_missing_court, which is the actual fix.
#
# Authorities-first is kept anyway, on the weaker but sound ground that a stage
# which mints authority nodes should run before the stage that wants to point
# at them: apply_patch's referential-integrity guard skips an add whose doc:
# object does not exist yet.
STAGES: tuple[RepairStage, ...] = (
    RepairStage(
        key="authorities",
        name="domestic authorities",
        guidance_file="repair_stage_authorities.txt",
        ontology_classes=(ECHR.DomesticAuthority, ECHR.AuthorityKind),
        dump_classes=(ECHR.DomesticAuthority,),
        filter_classes=(ECHR.DomesticAuthority,),
        duplicate_classes=frozenset({"echr:DomesticAuthority"}),
    ),
    RepairStage(
        key="proceedings",
        name="domestic proceedings",
        guidance_file="repair_stage_proceedings.txt",
        ontology_classes=(
            ECHR.DomesticProceeding,
            ECHR.ProceedingOutcome,
            ECHR.ProceedingType,
            ECHR.InstanceLevel,
            # In scope so the stage can SEE the authorities the previous stage
            # just minted, and link hasCourt to them.
            ECHR.DomesticAuthority,
        ),
        dump_classes=(ECHR.DomesticProceeding, ECHR.DomesticAuthority),
        filter_classes=(ECHR.DomesticProceeding,),
        duplicate_classes=frozenset({"echr:DomesticProceeding"}),
        check_mistyped=True,
    ),
    RepairStage(
        key="persons",
        name="persons and participation",
        guidance_file="repair_stage_persons.txt",
        ontology_classes=(
            ECHR.NaturalPerson,
            ECHR.LegalRepresentative,
            ECHR.Participation,
            ECHR.Party,
            ECHR.PartySide,
            ECHR.Gender,
        ),
        dump_classes=(ECHR.NaturalPerson, ECHR.LegalRepresentative, ECHR.Participation),
        filter_classes=(
            ECHR.NaturalPerson,
            ECHR.LegalRepresentative,
            ECHR.Participation,
            ECHR.Party,
        ),
        duplicate_classes=frozenset({"echr:NaturalPerson"}),
    ),
    RepairStage(
        key="quotes",
        name="source quote fidelity",
        guidance_file="repair_stage_quotes.txt",
        # Quote repair is not scoped to a class: echr:hasSupportingQuote hangs off
        # anything. The finder below supplies the affected triples directly, and
        # this stage is the only one handed the document's FULL SOURCE TEXT --
        # every other stage works from the graph alone, and a quote can only be
        # checked against the text it claims to come from.
        ontology_classes=(),
        dump_classes=(),
        filter_classes=(),
        duplicate_classes=frozenset(),
        needs_source_text=True,
    ),
)


def build_user_prompt(
    *,
    doc_curie_prefix: str,
    stage: RepairStage,
    ontology_context: str,
    entities: list[dict],
    candidates: list[dict],
    duplicate_groups: list[dict],
    structural_gaps: dict[str, list[str]],
    validator_findings: list[dict],
    unverified_quotes: list[dict] | None = None,
    source_text: str | None = None,
) -> str:
    payload = {
        "entities": entities,
        "mistyped_candidates": candidates,
        "duplicate_candidate_groups": duplicate_groups,
        **structural_gaps,
        "validator_findings": validator_findings,
    }
    if unverified_quotes is not None:
        payload["unverified_quotes"] = unverified_quotes

    parts = [
        f"Document namespace prefix: {doc_curie_prefix}\n",
        f"Repair stage: {stage.name}\n{load_stage_guidance(stage.guidance_file)}\n",
    ]
    if ontology_context:
        parts.append(
            f"Relevant ontology fragment (echr.ttl, {stage.name}-related):\n"
            f"{ontology_context}\n"
        )
    # The source text goes LAST, immediately before the findings that reference
    # it, so the model reads the evidence and the claims about it together
    # rather than with the whole ontology wedged in between.
    if source_text:
        parts.append(
            "FULL SOURCE TEXT of the document these facts were extracted from. "
            "A supporting quote is verified only if it appears here verbatim:\n"
            f"<<<SOURCE\n{source_text}\nSOURCE\n"
        )
    parts.append(
        "Current graph state and flagged findings:\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n"
    )
    return "\n".join(parts)


def warm_up_grammar(client: OpenAI, model: str) -> None:
    """One throwaway constrained call, to pay the grammar compile up front.

    A local vLLM server compiles the JSON schema into a decoding grammar the
    first time it sees one, and RepairPatch is big enough (nested $defs, a
    recursive op list) that the compile alone ran past the 300s client timeout
    on a cold server. Every subsequent call reuses the cached grammar and
    returns in ~30s. Without this the FIRST document of a run reliably timed
    out while the other nine were fine -- which read as "the model can't do
    repair" rather than "the server was still compiling".

    Deliberately non-fatal: a hosted endpoint has no such cost and a warm-up
    failure is not a reason to abandon the run.
    """
    try:
        client.chat.completions.parse(
            model=model,
            messages=[{"role": "user", "content": "Return an empty patch."}],
            response_format=RepairPatch,
            temperature=1.0,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  grammar warm-up skipped: {type(exc).__name__}: {str(exc)[:150]}")


_REASONING_TOKEN_PARAM_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _token_limit_kwargs(model: str, max_tokens: int) -> dict[str, int]:
    """The output-cap kwarg this model actually accepts."""
    name = model.lower().lstrip()
    if name.startswith(_REASONING_TOKEN_PARAM_PREFIXES):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def _is_wrong_token_param_error(exc: Exception) -> bool:
    """True for the 400 that says we picked the wrong spelling of the cap."""
    text = str(exc)
    return "max_tokens" in text and "max_completion_tokens" in text


class RepairTruncated(RuntimeError):
    """The model hit the output cap without closing the patch.

    A distinct type because this failure means something different from every
    other one, and the difference is actionable: a timeout or a refusal says
    "this document could not be repaired", whereas truncation says "the cap is
    too low for this document" -- the model was working and got cut off. The
    2026-08-20 experiment lost 14 of 50 gemma documents this way at a 3,000
    cap, and because the driver counted them alongside genuine failures the
    signal that the CAP was the problem never surfaced. main() now tallies
    these separately and names the fix in its summary line.
    """

    def __init__(self, max_tokens: int) -> None:
        super().__init__(
            f"generation hit the {max_tokens}-token output cap without closing "
            "the patch -- raise --max-tokens for this document"
        )
        self.max_tokens = max_tokens


# A stalled generation emits legal JSON whitespace forever, so this is the
# length of an unbroken whitespace run that is taken as proof the model has
# stopped producing content. Sized off the measured failure: a stalled L10
# proceedings draw was 56,018 characters of which 55,731 were whitespace, and a
# healthy patch never contains a whitespace run longer than an indent. 400 is
# far beyond any pretty-printer and reached within a second of the stall.
STALL_WHITESPACE_RUN = 400

# Cut points to try when salvaging a truncated patch, newest first. Bounded so
# a 56k-character stall does not turn into 56k validation attempts.
MAX_SALVAGE_CANDIDATES = 400

_CURIE_RE = re.compile(r"^[A-Za-z][\w.-]*:\S+$")


def _stream_patch_text(
    client: OpenAI,
    model: str,
    messages: list[dict],
    *,
    temperature: float,
    token_kwargs: dict,
) -> tuple[str, str | None, bool]:
    """Stream one patch, aborting the moment the generation stalls.

    Returns (raw_text, finish_reason, stalled).

    Streaming exists here purely to make the stall CHEAP. The pathology cannot
    be prevented from the client -- see the note above the patch schema for the
    three grammar-level attempts that failed, and disable_any_whitespace is
    silently ignored by vLLM 0.27.1 -- but it can be detected the instant it
    starts. Measured 2026-08-24: a stalled draw runs 88s to the 8,000-token cap
    and 173s to 16,000, and on one 10-document run roughly 500 of 921 seconds
    were spent generating whitespace. Cutting the request at the stall turns
    that into about a second and hands back the prefix, which is what the
    salvage below needs anyway.
    """
    parts: list[str] = []
    finish_reason: str | None = None
    stalled = False
    trailing_ws = 0

    with client.chat.completions.stream(
        model=model,
        messages=messages,
        response_format=RepairPatch,
        temperature=temperature,
        **token_kwargs,
    ) as stream:
        for event in stream:
            if event.type == "chunk":
                for choice in event.chunk.choices:
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                continue
            if event.type != "content.delta":
                continue
            delta = event.delta
            parts.append(delta)
            stripped = delta.strip()
            if stripped:
                # Only whitespace AFTER the last real character counts, so an
                # indented pretty-printed patch never accumulates a run.
                trailing_ws = len(delta) - len(delta.rstrip())
            else:
                trailing_ws += len(delta)
            if trailing_ws >= STALL_WHITESPACE_RUN:
                stalled = True
                break

    return "".join(parts), finish_reason, stalled


def _salvage_candidates(raw: str) -> Iterator[str]:
    """Progressively shorter prefixes of `raw`, each closed into valid JSON.

    Walks the text once, tracking bracket depth and string state, and records
    every position where a value is complete: after a closing bracket, and
    after the closing quote of a string. Each such position becomes a candidate
    -- the prefix up to it, plus the brackets needed to close whatever is still
    open. Newest first, because the longest salvageable prefix keeps the most
    work.

    One extra candidate is offered per position: the same prefix with
    `object_is_literal` supplied. That field is required on every TripleOp and
    is exactly where the observed stall lands -- the model wrote action,
    subject, predicate and object, which is the entire triple, and stopped
    before the one boolean that makes it parseable. Its value is inferred from
    the object rather than guessed: a CURIE is a URI reference, anything else
    is a literal. Without this the L10 stall salvages nothing at all, because
    its single operation never closed.
    """
    stack: list[str] = []
    in_string = escaped = False
    cuts: list[tuple[int, tuple[str, ...], str | None]] = []
    last_string: str | None = None
    string_start = 0

    for i, ch in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                last_string = raw[string_start:i]
                cuts.append((i + 1, tuple(stack), last_string))
            continue
        if ch == '"':
            in_string = True
            string_start = i + 1
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
            cuts.append((i + 1, tuple(stack), None))

    for index, open_stack, string_value in reversed(cuts[-MAX_SALVAGE_CANDIDATES:]):
        closers = "".join("}" if b == "{" else "]" for b in reversed(open_stack))
        prefix = raw[:index]
        yield prefix + closers
        if string_value is not None and open_stack and open_stack[-1] == "{":
            is_literal = "false" if _CURIE_RE.match(string_value) else "true"
            yield f'{prefix},"object_is_literal":{is_literal}{closers}'


def salvage_truncated_patch(raw: str) -> RepairPatch | None:
    """The largest well-formed patch recoverable from a truncated generation.

    A stalled draw is not garbage: it contains complete, correct operations
    before the padding starts, and discarding the whole stage throws them away.
    That mattered most on the one document that needed repair most -- L10 holds
    all 19 of its arm's courtless proceedings and stalls on every attempt, so
    without salvage it is the single document guaranteed to get nothing.

    Returns None when nothing parses, which is a real outcome: a draw that
    stalls inside the very first key has nothing to recover.
    """
    raw = raw.strip()
    if not raw:
        return None
    for candidate in _salvage_candidates(raw):
        try:
            patch = RepairPatch.model_validate_json(candidate)
        except Exception:  # noqa: BLE001,S112 - most candidates fail by design
            continue
        if patch.groups or patch.merges:
            return patch
    return None


def call_repair_model(
    client: OpenAI,
    model: str,
    user_prompt: str,
    *,
    temperature: float = 0.4,
    max_tokens: int = 8000,
    max_attempts: int = 2,
) -> RepairPatch:
    """One repair patch, retried once against a runaway-generation failure.

    vLLM guided decoding (response_format=RepairPatch) occasionally never
    closes the RepairGroup.rationale free-text field and pads with whitespace
    until something stops it -- confirmed on facts-render at a much bigger
    prompt (see response_repair.py), and reproduced here at a MUCH smaller one
    (2026-08-20: a 5.6k-token repair prompt for L3 ran the full 600s client
    timeout with zero output). This is a decoding pathology, not a sign the
    prompt is too much work for the model: the same prompt content produces a
    correct patch in well under a minute on a clean draw.

    Two guards, matched to that failure shape:
    - `max_tokens` bounds a runaway draw to a fast, cheap failure (seconds,
      not the full request timeout) instead of a silent multi-minute hang.
      It is a BACKSTOP and must be set well clear of real work: at 3000 it
      stopped being one. The 2026-08-20 experiment hit it on 4/10, 6/10 and
      4/10 documents in gemma native mv1, native mv2 and rolling mv2 -- 14 of
      50 documents lost their repair pass not to a decoding pathology but to
      a cap set below what a 25-finding document legitimately needs. 8000
      restores the headroom; a runaway draw still fails in seconds rather
      than burning the full --timeout.
    - `max_attempts` retries once on ANY failure (timeout, truncation, empty
      patch) before giving up, because this is drawn from noisy sampling: the
      same prompt produced a good patch on one run and nothing on the next
      with no change to the input. One extra draw is cheap insurance against
      a bad one, and a real inability to help still surfaces after that.

    Truncation raises RepairTruncated rather than a bare RuntimeError, so the
    driver can separate "the cap was too low" from "this document could not be
    repaired" -- see that class.
    """
    last_error: Exception | None = None
    token_kwargs = _token_limit_kwargs(model, max_tokens)
    for attempt in range(1, max_attempts + 1):
        try:
            raw, finish_reason, stalled = _stream_patch_text(
                client,
                model,
                [
                    {"role": "system", "content": load_system_prompt()},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                token_kwargs=token_kwargs,
            )
        except Exception as exc:  # noqa: BLE001
            # LengthFinishReasonError comes out of .parse() itself, BEFORE the
            # finish_reason check below can ever run -- which is why that check
            # is unreachable for this client and why RepairTruncated never
            # fired in the 2026-08-20 run. The report there recorded 14 gemma
            # documents lost to "LengthFinishReasonError at exactly 3,000
            # completion tokens" and counted every one as a generic failure, so
            # the signal that the CAP was the problem never reached the
            # summary. Converting here is what makes main()'s truncation tally
            # real rather than decorative.
            last_error = (
                RepairTruncated(max_tokens)
                if type(exc).__name__ == "LengthFinishReasonError"
                else exc
            )
            if _is_wrong_token_param_error(exc):
                # Wrong spelling for this endpoint: swap it and retry WITHOUT
                # consuming a sampling attempt -- nothing was sampled, the
                # request never reached the model.
                other = (
                    {"max_completion_tokens": max_tokens}
                    if "max_tokens" in token_kwargs
                    else {"max_tokens": max_tokens}
                )
                if other != token_kwargs:
                    token_kwargs = other
                    max_attempts += 1
            continue
        if not stalled and finish_reason != "length":
            try:
                return RepairPatch.model_validate_json(raw.strip())
            except Exception as exc:  # noqa: BLE001
                last_error = RuntimeError(
                    f"model returned no parseable patch: {type(exc).__name__}"
                )
                continue

        # Stalled or hit the cap. Retry first -- the pathology is drawn from
        # sampling and a clean draw usually follows -- and only salvage on the
        # LAST attempt, so a recoverable fragment is never preferred over a
        # complete patch that one more draw would have produced.
        last_error = RepairTruncated(max_tokens)
        if attempt < max_attempts:
            continue
        if (salvaged := salvage_truncated_patch(raw)) is not None:
            ops = sum(len(g.ops) for g in salvaged.groups)
            print(
                f"    RECOVERED {len(salvaged.groups)} group(s), {ops} op(s) "
                f"from a {'stalled' if stalled else 'truncated'} generation"
            )
            return salvaged
    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# Applying the patch
# ---------------------------------------------------------------------------


def unwrap_literal(value: str) -> str:
    """Strip Turtle literal syntax the model copied into a plain-text field.

    `TripleOp.object` is documented as "the literal value as plain text", but a
    model reading a Turtle-serialized graph hands back the SYNTAX it was shown --
    `"Ruse Municipal Council"`, quote marks included -- and rdflib then stores
    those marks as part of the string. Measured on the 2026-08-23 five-arm sweep:
    26 literals across the run, all from the authorities stage
    (echr:hasAuthorityName, echr:hasJurisdictionState, rdfs:label), and none in
    the pre-repair graphs, so repair is the only source.

    Only a single matched pair wrapping the WHOLE value is removed, and only
    when the interior holds no straight double quote of its own. A supporting
    quote that legitimately opens and closes on a straight quote is therefore
    left alone -- ECHR texts mark quotations with curly quotes, so the wrapped
    form is the artefact, not the data.
    """
    if len(value) > 1 and value[0] == '"' and value[-1] == '"':
        inner = value[1:-1]
        if '"' not in inner:
            return inner
    return value


def resolve_term(
    graph: Graph,
    curie_or_literal: str,
    *,
    is_literal: bool,
    datatype: str | None,
    lang: str | None,
):
    if not is_literal:
        # Strip first: a trailing space the model tacks onto a CURIE (seen in
        # practice, e.g. "doc:partySdif ") builds a URIRef that looks right
        # but never matches the real node, so every op against it reads as
        # "triple not present" even though the model named the correct triple.
        prefix, _, local = curie_or_literal.strip().partition(":")
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
    return RDFLiteral(unwrap_literal(curie_or_literal), datatype=dt, lang=lang)


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


def _match_unverified_quote(
    subject: str, proposed: str, unverified_quotes: list[dict]
) -> int | None:
    """Which listed unverified quote a retyped `remove` was aiming at.

    Only ever considers quotes already flagged as unverified ON THAT SUBJECT,
    so the worst case is deleting a quote that was going to be reported as
    broken anyway -- never a good anchor on a node the model did not name.

    One candidate resolves outright: if a subject has exactly one bad quote and
    the model asked to remove a quote from that subject, there is nothing else
    it could have meant. With several, the closest normalized match wins and
    only above a high similarity floor, because a near-miss between two
    genuinely different quotes should fail loudly rather than delete the wrong
    evidence.
    """
    import difflib

    from art6.ontology.diagnostics.validate_source_quotes import normalize

    candidates = [q for q in unverified_quotes if q.get("subject") == subject]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]["index"]

    target = normalize(proposed)
    best, best_ratio = None, 0.0
    for candidate in candidates:
        ratio = difflib.SequenceMatcher(
            None, target, normalize(candidate["quote"])
        ).ratio()
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best["index"] if best is not None and best_ratio >= 0.75 else None


def apply_patch(
    graph: Graph,
    patch: RepairPatch,
    doc_ns: str,
    source_text: str | None = None,
    unverified_quotes: list[dict] | None = None,
) -> tuple[Graph, list[dict]]:
    """Returns a NEW graph with the patch applied, plus an audit trail. Every
    op is checked against the two-namespace contract and, for removes,
    against what actually exists before it is applied.

    When `source_text` is supplied, an `add` of echr:hasSupportingQuote is
    additionally checked against it and REJECTED unless the quote appears
    verbatim. Repair must never be able to introduce an evidence anchor that
    does not anchor anything. Observed 2026-08-24 on L10: the quotes stage
    proposed a remove/add pair to correct a paraphrased quote, the remove was
    skipped because the model could not reproduce the original literal exactly,
    the add landed anyway -- and the "corrected" quote did not verify either.
    Unverified quotes went 3 -> 4, so the stage built to fix them made them
    worse. This guard makes that outcome impossible: the stage can now only
    remove bad anchors or add genuinely verifiable ones.

    `unverified_quotes` is the finding list the quote stage was shown. When an
    op carries a `quote_index`, its object is resolved from that list here
    rather than from the text the model retyped. This closes the OTHER half of
    the same L10 failure: a remove could only land if the model reproduced a
    ~107-character literal character for character, and when it paraphrased
    instead the op was skipped as "triple not present". The same skip is
    visible outside this stage -- four consecutive ones on doc:administrativeCourt
    in the rolling_3k6k L3 log. Naming the index makes the remove exact and
    costs the model a couple of tokens instead of a hundred.

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
                subj_prefix, _, subj_local = op.subject.strip().partition(":")
                if subj_prefix == "doc":
                    known_doc_nodes.add(doc_ns + subj_local)

    quotes_by_index = {q["index"]: q for q in (unverified_quotes or []) if "index" in q}

    audit: list[dict] = []
    for group in patch.groups:
        for op in group.ops:
            index_note: str | None = None
            resolved_quote_target: dict | None = None
            if (
                op.quote_index is None
                and op.action == "remove"
                and op.predicate == "echr:hasSupportingQuote"
                and op.object.strip()
            ):
                # The model was told to name the quote by index and did not.
                # Measured 2026-08-24 on L10: it retyped all three literals,
                # every remove missed on a punctuation difference it had itself
                # described as "minor" in the rationale, and the three adds
                # landed on top of the bad anchors they were meant to replace.
                # Recovering the index here rather than relying on compliance
                # is the difference between a stage that works and a stage that
                # depends on the model doing something optional.
                recovered = _match_unverified_quote(
                    op.subject.strip(), op.object, unverified_quotes or []
                )
                if recovered is not None:
                    op.quote_index = recovered

            # SCOPED to hasSupportingQuote, and that scoping is load-bearing.
            # quote_index is optional and meant only for this stage, but the
            # model sets it to 0 on unrelated operations -- measured 2026-08-24,
            # where 19 perfectly good `add echr:hasCourt` ops arrived carrying
            # quote_index 0 and an unscoped guard rejected every one of them,
            # silently discarding the entire output of the missing-court
            # finder. A field the model may fill in spuriously must never be
            # able to veto an operation it does not describe.
            if op.quote_index is not None and op.predicate == "echr:hasSupportingQuote":
                target = quotes_by_index.get(op.quote_index)
                if target is None:
                    index_note = (
                        f"quote_index {op.quote_index} does not name a listed "
                        "unverified quote"
                    )
                elif target["subject"] != op.subject.strip():
                    # Refusing the mismatch rather than trusting the index is
                    # deliberate: silently retargeting the op to the indexed
                    # subject would delete a quote off a node the model never
                    # meant to touch.
                    index_note = (
                        f"quote_index {op.quote_index} belongs to "
                        f"{target['subject']}, not {op.subject.strip()}"
                    )
                else:
                    op.object = target["quote"]
                    op.object_is_literal = True
                    resolved_quote_target = target
            record = {
                "finding": group.finding,
                "rationale": group.rationale,
                **op.model_dump(),
            }
            if index_note is not None:
                record["status"] = f"skipped: {index_note}"
                audit.append(record)
                continue

            if resolved_quote_target is not None and op.action == "remove":
                # Delete the LITERAL TERM ITSELF rather than rebuilding one from
                # its text. Extraction writes these quotes as "..."^^xsd:string,
                # and rdflib does not consider Literal(x) equal to
                # Literal(x, datatype=XSD.string) -- so a remove reconstructed
                # through resolve_term misses a triple that is plainly there.
                # Measured 2026-08-24: every indexed quote remove resolved to
                # the right finding and was then discarded as "triple not
                # present", which looked exactly like the model retyping the
                # quote badly and was nothing of the sort.
                subject_term = resolve_term(
                    working, op.subject, is_literal=False, datatype=None, lang=None
                )
                removed = [
                    obj
                    for obj in working.objects(subject_term, ECHR.hasSupportingQuote)
                    if str(obj) == resolved_quote_target["quote"]
                ]
                for obj in removed:
                    working.remove((subject_term, ECHR.hasSupportingQuote, obj))
                record["status"] = (
                    f"applied (removed {len(removed)} quote(s) by index)"
                    if removed
                    else "skipped: indexed quote no longer in the graph"
                )
                audit.append(record)
                continue
            try:
                subj_prefix, _, _ = op.subject.partition(":")
                subj_ns = dict(working.namespaces()).get(subj_prefix)
                if subj_ns is None or str(subj_ns) != doc_ns:
                    record["status"] = "skipped: subject not in doc: namespace"
                    audit.append(record)
                    continue
                if op.action == "remove" and not op.object.strip():
                    # An empty object on a remove means "every value of this
                    # predicate on this subject". The model reaches for this
                    # constantly when deleting an invented property, because
                    # the object is beside the point there and reproducing a
                    # long literal verbatim is exactly the thing it gets
                    # wrong. Before this, all of those landed as "triple not
                    # present" and the invented predicates survived repair.
                    p = resolve_term(
                        working,
                        op.predicate,
                        is_literal=False,
                        datatype=None,
                        lang=None,
                    )
                    s = resolve_term(
                        working, op.subject, is_literal=False, datatype=None, lang=None
                    )
                    removed = 0
                    for _, _, obj in list(working.triples((s, p, None))):
                        working.remove((s, p, obj))
                        removed += 1
                    record["status"] = (
                        f"applied ({removed} value(s))"
                        if removed
                        else "skipped: predicate not present"
                    )
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
                    # An evidence anchor that is not in the evidence is not an
                    # anchor. See this function's docstring for the L10 case
                    # this exists to prevent.
                    if (
                        source_text
                        and op.predicate == "echr:hasSupportingQuote"
                        and op.object_is_literal
                    ):
                        from art6.ontology.diagnostics.validate_source_quotes import (
                            normalize,
                            quote_verifies,
                        )

                        if not quote_verifies(op.object, normalize(source_text)):
                            record["status"] = (
                                "skipped: quote does not appear verbatim in the "
                                "source document"
                            )
                            audit.append(record)
                            continue
                if op.action == "add" and not op.object_is_literal:
                    obj_prefix, _, obj_local = op.object.strip().partition(":")
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


def _stage_structural_gaps(
    stage: RepairStage, graph: Graph, doc_ns: str
) -> dict[str, list[str]]:
    """The stage-specific deterministic gap lists shown alongside duplicates
    and validator findings -- the checks that need typed access to specific
    properties (follows/outcome/instance level, participation links) rather
    than a generic class/finding filter."""
    if stage.key == "proceedings":
        summaries = summarize_proceedings(graph)
        return {
            "flagged_missing_followsProceeding_for": find_appeal_shaped_gaps(summaries),
            "flagged_isFinalDomesticDecision_conflict": find_final_decision_conflicts(
                summaries
            ),
            "flagged_missing_hasCourt_for": find_proceedings_missing_court(
                graph, doc_ns
            ),
        }
    if stage.key == "persons":
        return {
            "flagged_events_missing_participation": find_missing_participation(
                graph, doc_ns
            ),
            "flagged_unlinked_persons": find_unlinked_persons(graph, doc_ns),
        }
    return {}


def _run_stage(
    graph: Graph,
    stage: RepairStage,
    facts_ttl: Path,
    client: OpenAI,
    model: str,
    *,
    doc_ns: str,
    temperature: float,
    passes: int,
    max_tokens: int,
    source_text: str | None = None,
) -> tuple[Graph, bool, list[dict], list[str], list[dict]]:
    """Run one stage's repair loop against `graph`, optionally over several
    model calls. Returns the (possibly replaced) graph, whether anything
    changed, the audit trail, the curies of any stub nodes swept, and one
    timing record per pass.

    One call is rarely enough. The model reliably fixes a subset of what it is
    shown and leaves the rest, and applying a patch changes the graph, so the
    NEXT round of findings is different -- a merge can expose a functional-
    property collision that was invisible while the two nodes were separate.
    The 2026-08-19 cfcmp run needed four hand-run invocations for this reason.
    So re-derive the findings from the working graph on every pass rather than
    reusing the first pass's list, and stop as soon as this stage's region of
    the graph is clean or a pass changes nothing.

    Timing is recorded per pass, splitting the model call out from the
    deterministic work around it. Staging multiplied the call count by the
    number of stages, so "what does repair cost" stopped being answerable from
    a single run total -- `timings` is what makes the per-stage cost visible.
    """
    ontology_context = load_ontology_fragment(stage.ontology_classes)
    audit: list[dict] = []
    swept_all: list[str] = []
    timings: list[dict] = []
    changed = False

    for pass_no in range(1, passes + 1):
        t_pass = time.perf_counter()
        entities = entity_dump(graph, stage.dump_classes, doc_ns)
        candidates = find_mistyped_candidates(graph) if stage.check_mistyped else []
        duplicate_groups = [
            g
            for g in find_duplicate_candidates(graph)
            if g["class"] in stage.duplicate_classes
        ]
        structural_gaps = {
            key: value[:MAX_GAP_ENTRIES_PER_PASS]
            for key, value in _stage_structural_gaps(stage, graph, doc_ns).items()
        }

        # Graph-derived findings are recomputed every pass; the extraction-time
        # validator report is a fixed artefact of the original file, so it only
        # goes in on pass 1 -- re-showing findings a later pass already fixed
        # invites the model to "fix" them a second time.
        shape_findings = [
            f
            for f in find_shape_violations(graph)
            if _finding_in_classes(graph, f, stage.filter_classes)
        ]
        # Counted before the patch so the no-progress check below compares like
        # with like: shape violations only, never the fixed extraction-time
        # report, which no repair can shrink.
        before_count = len(shape_findings)
        validator_findings = shape_findings
        if pass_no == 1:
            extraction_findings = [
                f
                for f in load_validator_findings(facts_ttl)
                if _finding_in_classes(graph, f, stage.filter_classes)
            ]
            validator_findings = extraction_findings + shape_findings

        unverified_quotes = None
        if stage.needs_source_text:
            if not source_text:
                print(
                    f"  [{stage.name}] {relative(facts_ttl)}: skipped, no source "
                    "text available (pass --input-jsonl)"
                )
                return graph, False, audit, swept_all, timings
            unverified_quotes = find_unverified_quotes(graph, source_text)

        if not (
            candidates
            or duplicate_groups
            or any(structural_gaps.values())
            or validator_findings
            or unverified_quotes
        ):
            if pass_no == 1:
                print(f"  [{stage.name}] {relative(facts_ttl)}: nothing flagged")
                return graph, False, audit, swept_all, timings
            print(f"    [{stage.name}] pass {pass_no}: nothing left to flag - stopping")
            break

        prompt = build_user_prompt(
            doc_curie_prefix="doc",
            stage=stage,
            ontology_context=ontology_context,
            entities=entities,
            candidates=candidates,
            duplicate_groups=duplicate_groups,
            structural_gaps=structural_gaps,
            validator_findings=validator_findings,
            unverified_quotes=unverified_quotes,
            source_text=source_text if stage.needs_source_text else None,
        )
        t_call = time.perf_counter()
        patch = call_repair_model(
            client, model, prompt, temperature=temperature, max_tokens=max_tokens
        )
        call_seconds = time.perf_counter() - t_call

        proposed_ops = sum(1 for g in patch.groups for op in g.ops)
        label = (
            f"  [{stage.name}] {relative(facts_ttl)}"
            if pass_no == 1
            else f"    [{stage.name}] pass {pass_no}"
        )
        print(
            f"{label}: {len(validator_findings) + len(unverified_quotes or [])} "
            f"finding(s) in; model proposed "
            f"{len(patch.groups)} group(s), {proposed_ops} op(s), "
            f"{len(patch.merges)} merge(s)"
        )

        graph, pass_audit = apply_patch(
            graph,
            patch,
            doc_ns,
            source_text if stage.needs_source_text else None,
            unverified_quotes=unverified_quotes,
        )
        swept = sweep_stub_orphans(graph, doc_ns)
        if swept:
            swept_all.extend(swept)
            pass_audit.append(
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
        for entry in pass_audit:
            entry["stage"] = stage.key
            entry["pass"] = pass_no
        audit.extend(pass_audit)

        applied = sum(1 for a in pass_audit if a["status"].startswith("applied"))
        skipped = [a for a in pass_audit if a["status"] != "applied"]
        merged = sum(
            1 for a in pass_audit if a["action"] == "merge" and a["status"] == "applied"
        )
        pass_seconds = time.perf_counter() - t_pass
        # Recorded here, before either early-exit below, so a pass that stops
        # the loop is still costed -- it made the same model call as any other.
        timings.append(
            {
                "stage": stage.key,
                "pass": pass_no,
                "seconds_total": round(pass_seconds, 1),
                "seconds_model_call": round(call_seconds, 1),
                "seconds_deterministic": round(pass_seconds - call_seconds, 1),
                "findings_in": len(validator_findings),
                "ops_proposed": proposed_ops,
                "ops_applied": applied,
                "merges_applied": merged,
            }
        )
        print(
            f"    [{stage.name}] pass {pass_no}: applied {applied} (of which "
            f"{merged} merge(s)), skipped {len(skipped)}, stub orphans swept "
            f"{len(swept)} [{pass_seconds:.1f}s, {call_seconds:.1f}s in the model]"
        )
        for a in skipped:
            if a["action"] == "merge":
                print(f"    SKIPPED merge {a['keep']} <- {a['drop']}: {a['status']}")
            else:
                print(
                    f"    SKIPPED [{a['finding']}] {a['action']} {a['subject']} "
                    f"{a['predicate']} {a['object']}: {a['status']}"
                )

        if not applied:
            # Nothing landed. Another identical call would see the same graph
            # and the same findings, so spending it is pure cost.
            print(
                f"    [{stage.name}] pass {pass_no}: no operations applied - stopping"
            )
            break

        changed = True

        # Operations landed but the finding count did not move: the model is
        # editing around the problem rather than closing it, and a further pass
        # tends to keep doing that. Same signal OntoCast's own facts_gate uses
        # ("repair pass 1 did not reduce merge-signature errors (8 -> 8)").
        #
        # The quotes stage measures progress on UNVERIFIED QUOTES, not shape
        # violations: it targets no class, so the shape count is 0 both sides
        # and every pass would read as "did not fall" regardless of what it
        # actually fixed.
        if stage.needs_source_text:
            before_count = len(unverified_quotes or [])
            remaining = len(find_unverified_quotes(graph, source_text or ""))
        else:
            remaining = len(
                [
                    f
                    for f in find_shape_violations(graph)
                    if _finding_in_classes(graph, f, stage.filter_classes)
                ]
            )
        if pass_no < passes and remaining >= before_count:
            print(
                f"    [{stage.name}] pass {pass_no}: {applied} op(s) applied but "
                f"findings did not fall ({before_count} -> {remaining}) - stopping"
            )
            break

    return graph, changed, audit, swept_all, timings


def repair_one(
    facts_ttl: Path,
    client: OpenAI,
    model: str,
    *,
    dry_run: bool,
    temperature: float = 0.4,
    passes: int = 1,
    max_tokens: int = 8000,
    input_path: Path | None = None,
) -> dict:
    """Repair one facts graph, one LLM call per STAGES entry per pass.

    Each stage runs its own `_run_stage` loop of up to `passes` calls, scoped
    to its own region of the ontology (see the module docstring and `STAGES`).
    Stages run in order and share one graph, so a later stage sees whatever an
    earlier one already fixed. The file is written ONCE at the end, across all
    stages: writing per stage would make the next stage find a backup that
    differs from the file it is about to back up, which the guard below
    (correctly) refuses.

    Returns this document's timing record. Returned rather than only printed
    because a document that ends up UNCHANGED still spent its model calls, and
    that cost has to reach the run total -- the early return below skips the
    audit file, so the audit file cannot be the only place timing lives.
    """
    t_doc = time.perf_counter()
    graph = Graph()
    graph.parse(facts_ttl)
    doc_ns = str(next((ns for prefix, ns in graph.namespaces() if prefix == "doc"), ""))
    if not doc_ns:
        print(f"  skip {relative(facts_ttl)}: no doc: namespace bound")
        return {"source": str(relative(facts_ttl)), "skipped": "no doc: namespace"}

    # `<stem>.L<N>.facts.ttl` -> record N of the run's input file. Resolved once
    # per document; the loader itself is cached across documents.
    source_text = None
    if input_path is not None:
        match = re.search(r"\.L(\d+)\.facts\.ttl$", facts_ttl.name)
        source_text = load_source_text(
            input_path, int(match.group(1)) if match else None
        )

    audit: list[dict] = []
    total_swept: list[str] = []
    stage_timings: list[dict] = []
    stage_failures: list[dict] = []
    changed = False

    for stage in STAGES:
        t_stage = time.perf_counter()
        try:
            graph, stage_changed, stage_audit, stage_swept, stage_times = _run_stage(
                graph,
                stage,
                facts_ttl,
                client,
                model,
                doc_ns=doc_ns,
                temperature=temperature,
                passes=passes,
                max_tokens=max_tokens,
                source_text=source_text,
            )
        except Exception as exc:  # noqa: BLE001
            # Bill the failed stage's wall clock to the MODEL, not to
            # deterministic work. A stage that dies raises out of _run_stage
            # before it can return its timing records, so before this the time
            # vanished from `seconds_model_calls` while still counting in the
            # document total -- and the difference between the two is what gets
            # reported as deterministic. The effect was badly misleading: the
            # 2026-08-24 run showed "347.7s deterministic" for a pass whose
            # per-stage rows added up to 0.7s, because two runaway generations
            # were being reported as if they were SHACL. A truncated draw is
            # the model generating for three minutes; the timing must say so.
            stage_timings.append(
                {
                    "stage": stage.key,
                    "pass": 0,
                    "seconds_total": round(time.perf_counter() - t_stage, 1),
                    "seconds_model_call": round(time.perf_counter() - t_stage, 1),
                    "seconds_deterministic": 0.0,
                    "findings_in": 0,
                    "ops_applied": 0,
                    "failed": type(exc).__name__,
                }
            )
            # One stage failing must not discard the other two. Stages are
            # independent regions of the graph, and the shared `graph` is only
            # rebound on success -- so a truncated persons pass leaves the
            # proceedings fixes that already landed fully intact. Before this,
            # the exception escaped repair_one entirely and the document was
            # written off whole, losing work that had nothing to do with the
            # failure. The 2026-08-23 smoke test hit exactly that: proceedings
            # came back clean, persons hit the token cap, and the document was
            # reported as a total failure.
            stage_failures.append(
                {
                    "stage": stage.key,
                    "error": type(exc).__name__,
                    "message": str(exc)[:300],
                }
            )
            print(
                f"    [{stage.name}] FAILED: {type(exc).__name__}: {str(exc)[:200]}"
                "\n    continuing to the next stage; earlier stages' work is kept"
            )
            continue
        audit.extend(stage_audit)
        total_swept.extend(stage_swept)
        stage_timings.extend(stage_times)
        changed = changed or stage_changed

    timing = _document_timing(
        facts_ttl, stage_timings, t_doc, changed=changed, failures=stage_failures
    )
    print(
        f"    document total: {timing['seconds_total']}s across "
        f"{timing['model_calls']} model call(s) "
        f"({timing['seconds_model_calls']}s in the model)"
    )

    if not changed:
        print("    no net change across all stages; leaving the file untouched")
        return timing

    repaired_graph = graph
    audit_path = facts_ttl.parent / (
        facts_ttl.name.removesuffix(".ttl") + ".repairs.json"
    )
    audit_path.write_text(
        json.dumps(
            {
                "source": str(relative(facts_ttl)),
                "timings": timing,
                "operations": audit,
            },
            indent=2,
        )
    )

    if dry_run:
        print(
            f"    dry-run: not writing {relative(facts_ttl)} or {relative(audit_path)} contents applied"
        )
        return timing

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
    return timing


def _document_timing(
    facts_ttl: Path,
    stage_timings: list[dict],
    t_doc: float,
    *,
    changed: bool,
    failures: list[dict] | None = None,
) -> dict:
    """Roll per-pass records up into one document-level timing summary.

    `failures` carries any stage that raised. A failed stage still consumed
    wall clock and (usually) a model call, so it belongs in the cost record --
    counting only successful stages would understate what the run actually
    spent, which is precisely the mistake the truncation tally used to make.
    """
    model_seconds = sum(t["seconds_model_call"] for t in stage_timings)
    total_seconds = time.perf_counter() - t_doc
    by_stage: dict[str, dict] = {}
    for t in stage_timings:
        entry = by_stage.setdefault(
            t["stage"], {"passes": 0, "seconds": 0.0, "seconds_model_call": 0.0}
        )
        entry["passes"] += 1
        entry["seconds"] = round(entry["seconds"] + t["seconds_total"], 1)
        entry["seconds_model_call"] = round(
            entry["seconds_model_call"] + t["seconds_model_call"], 1
        )
    return {
        "source": str(relative(facts_ttl)),
        "changed": changed,
        "stage_failures": failures or [],
        "seconds_total": round(total_seconds, 1),
        "seconds_model_calls": round(model_seconds, 1),
        # Everything that is not a model call: parsing, SHACL, finding
        # derivation, patch application. Worth separating -- SHACL runs once
        # per pass per stage, so staging multiplied it too, not just the calls.
        "seconds_deterministic": round(total_seconds - model_seconds, 1),
        "model_calls": len(stage_timings),
        "by_stage": by_stage,
        "passes": stage_timings,
    }


def main() -> int:
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
        default=0.4,
        help=(
            "Sampling temperature. Default 0.4: at 1.0, gemma-4-31b showed "
            "wide response variance on the SAME repair prompt (2026-08-20, "
            "5 draws: a 9-op real answer, a 1-op real answer, an empty "
            '\'{"groups": [], "merges": []}\' decline, and a runaway '
            "generation that never closed and hit the token cap). 5 draws "
            "at 0.4 against the same prompt were all clean, substantive "
            "answers -- no declines, no runaways. gpt-5 reasoning models "
            "reject any value except 1.0 and must pass --temperature 1.0 "
            "explicitly."
        ),
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=1,
        help=(
            "Maximum repair calls PER STAGE per file (default 1; see STAGES "
            "in the module docstring for what runs). Findings are re-derived "
            "from the working graph before each pass, and each stage's loop "
            "stops early as soon as its region of the graph is clean or a "
            "pass applies nothing -- so N passes is a per-stage ceiling, not "
            "a fixed cost. The file is written once, after every stage."
        ),
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8000,
        help=(
            "Completion token cap per repair call. Guided decoding "
            "(response_format=RepairPatch) occasionally never closes the "
            "rationale field and pads with whitespace until something stops "
            "it -- observed 2026-08-20 burning the full --timeout on a 5.6k- "
            "token prompt for zero output. This turns that into a fast, "
            "cheap failure (a few seconds) instead of a multi-minute hang. "
            "It is a BACKSTOP, not a working limit: at the previous default "
            "of 3000 it became one, costing 14 of 50 gemma documents their "
            "repair pass across the 2026-08-20 arms. Documents truncated at "
            "this cap are reported separately at the end of the run."
        ),
    )
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        default=None,
        help=(
            "The run's input.jsonl (or .json), so the quote-fidelity stage can "
            "check echr:hasSupportingQuote against the text it claims to come "
            "from. Defaults to input.jsonl / input.json beside --facts-dir or "
            "its parent. Without it that stage is skipped and every other "
            "stage runs unchanged."
        ),
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
    # Look beside the facts dir first (run_arms.sh copies it there), then in the
    # experiment root above it, matching where the standalone quote and date
    # validators already look for their own input.
    input_path = args.input_jsonl
    if input_path is None:
        for base in (args.facts_dir, args.facts_dir.parent):
            for name in ("input.jsonl", "input.json"):
                if (candidate := base / name).is_file():
                    input_path = candidate
                    break
            if input_path is not None:
                break
    if input_path is not None:
        print(f"  source text: {relative(input_path)} (quote stage enabled)")
    else:
        print("  source text: not found - the quote-fidelity stage will be skipped")

    warm_up_grammar(client, args.model)

    t_run = time.perf_counter()
    run_started = datetime.datetime.now(datetime.UTC)
    failures = 0
    truncated: list[Path] = []
    doc_timings: list[dict] = []
    for facts_ttl in facts_files:
        try:
            doc_timings.append(
                repair_one(
                    facts_ttl,
                    client,
                    args.model,
                    passes=max(1, args.passes),
                    dry_run=args.dry_run,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    input_path=input_path,
                )
            )
        except Exception as exc:  # noqa: BLE001
            # One document failing must not abandon the remaining nine: the
            # experiment needs whatever repaired output is obtainable, and the
            # failure itself is a result worth recording per model.
            failures += 1
            if isinstance(exc, RepairTruncated):
                truncated.append(facts_ttl)
            print(
                f"  FAILED {relative(facts_ttl)}: {type(exc).__name__}: {str(exc)[:300]}"
            )

    # A stage that failed no longer raises out of repair_one -- it is recorded
    # and the document keeps going -- so PARTIAL failures have to be counted
    # from the timing records rather than from an exception that never arrives.
    partial = [t for t in doc_timings if t.get("stage_failures")]
    for t in partial:
        if any(f["error"] == "RepairTruncated" for f in t["stage_failures"]):
            truncated.append(Path(t["source"]))

    if failures:
        print(f"  {failures}/{len(facts_files)} document(s) failed to repair")
    if partial:
        print(
            f"  {len(partial)}/{len(facts_files)} document(s) lost at least one "
            f"stage but kept the others:"
        )
        for t in partial:
            for f in t["stage_failures"]:
                print(f"    {t['source']}  [{f['stage']}] {f['error']}")
    if truncated:
        print(
            f"  {len(truncated)} document/stage(s) hit the {args.max_tokens}-token "
            f"output cap mid-patch -- re-run these with a higher --max-tokens:"
        )
        for path in truncated:
            print(f"    {path}")

    _print_and_write_run_timings(
        args.facts_dir, doc_timings, t_run, run_started, args=args
    )
    return 1 if failures else 0


def _print_and_write_run_timings(
    facts_dir: Path,
    doc_timings: list[dict],
    t_run: float,
    run_started: datetime.datetime,
    *,
    args: argparse.Namespace,
) -> None:
    """Summarise the run's cost, and drop it beside the repaired files.

    Written to `repair_timings.json` in the facts directory rather than only
    printed, so an experiment driver can read the cost of a repair pass without
    having to parse the log. Staging made this worth doing: repair used to be
    one call per document and is now up to `3 * passes`, which is a large
    enough share of a run's total that it has to be measurable on its own.
    """
    run_seconds = time.perf_counter() - t_run
    timed = [t for t in doc_timings if "model_calls" in t]
    calls = sum(t["model_calls"] for t in timed)
    model_seconds = sum(t["seconds_model_calls"] for t in timed)
    by_stage: dict[str, dict] = {}
    for t in timed:
        for stage_key, entry in t.get("by_stage", {}).items():
            agg = by_stage.setdefault(
                stage_key, {"passes": 0, "seconds": 0.0, "seconds_model_call": 0.0}
            )
            agg["passes"] += entry["passes"]
            agg["seconds"] = round(agg["seconds"] + entry["seconds"], 1)
            agg["seconds_model_call"] = round(
                agg["seconds_model_call"] + entry["seconds_model_call"], 1
            )

    payload = {
        "facts_dir": str(relative(facts_dir)),
        "started": run_started.isoformat(timespec="seconds"),
        "finished": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "model": args.model,
        "base_url": args.base_url or "api.openai.com",
        "temperature": args.temperature,
        "passes_per_stage": args.passes,
        "stages": [s.key for s in STAGES],
        "documents": len(doc_timings),
        "documents_timed": len(timed),
        "seconds_total": round(run_seconds, 1),
        "seconds_model_calls": round(model_seconds, 1),
        "seconds_deterministic": round(run_seconds - model_seconds, 1),
        "model_calls": calls,
        "seconds_per_document": (round(run_seconds / len(timed), 1) if timed else None),
        "seconds_per_model_call": round(model_seconds / calls, 1) if calls else None,
        "by_stage": by_stage,
        "by_document": doc_timings,
    }

    if not args.dry_run:
        (facts_dir / "repair_timings.json").write_text(json.dumps(payload, indent=2))

    print(
        f"\n  repair total: {payload['seconds_total']}s over {len(timed)} document(s), "
        f"{calls} model call(s) ({payload['seconds_model_calls']}s in the model, "
        f"{payload['seconds_deterministic']}s deterministic)"
    )
    for stage_key, entry in by_stage.items():
        print(
            f"    {stage_key:<14} {entry['passes']:>3} call(s)  "
            f"{entry['seconds']:>7}s  ({entry['seconds_model_call']}s in the model)"
        )


if __name__ == "__main__":
    sys.exit(main())
