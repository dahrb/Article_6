"""Graph patches with evidence guards, and the structural rewrites stage 3 uses.

A patch is a flat list of add/remove triple operations in groups
(RepairPatch), never prose or Turtle, so every change is auditable.
apply_patch applies one and refuses, per operation, what a graph-only pass has
no standing to do:
  - add an echr:hasSupportingQuote (a span for someone else's claim);
  - touch anything outside the document's own doc: namespace;
  - leave an event with no participation, or add an incomplete one;
  - point at a doc: node that was never minted.
Duplicate entities are merged by merge_nodes: inbound edges are re-pointed to
the survivor and the duplicate is deleted, so no orphan is left behind.

Also here: the lossless rewrites reconcile.py runs first (entailed types,
split shared and multiparty participations, mirrored party labels, pruned
unbuilt references), the SHACL check (find_shape_violations), and the finders
the SHACL loop collects (shacl_loop.collect_findings).

History: this was the old stage 3, a sequence of topic-scoped repair stages
with their own CLI. Those were removed on 2026-11-09; reconcile.py is stage 3.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, Field, model_validator
from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef
from rdflib import Literal as RDFLiteral

from art6.paths import REPO_ROOT

# Overridable so a run can be pinned to the same snapshot it extracted against:
#   ART6_ONTOLOGY_TTL=results/<run>/echr.ttl.snapshot uv run python -m art6.ontology.graph_patch ...
ONTOLOGY_TTL = Path(
    os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr.ttl")
)

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


@lru_cache(maxsize=1)
def disjoint_class_sets() -> tuple[frozenset[URIRef], ...]:
    """Every owl:AllDisjointClasses group declared in echr.ttl, as a tuple of
    frozensets of member classes.

    Nothing else in this pipeline reads these. SHACL does not evaluate
    owl:AllDisjointClasses, so a patch could satisfy every shape by typing a
    node into two classes the ontology declares mutually exclusive -- e.g.
    adding echr:DomesticProceeding to a node already typed
    echr:AdministrativeAction to satisfy CaseDocumentShape's range check on
    echr:hasDomesticProceeding, producing a node that is logically
    inconsistent even though pyshacl reports conforms. Read live so an edit to
    echr.ttl's disjointness groups is picked up without a code change.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    groups = []
    for restriction in g.subjects(RDF.type, OWL.AllDisjointClasses):
        members = set()
        for lst in g.objects(restriction, OWL.members):
            cur = lst
            while cur and cur != RDF.nil:
                for first in g.objects(cur, RDF.first):
                    if isinstance(first, URIRef):
                        members.add(first)
                cur = next(g.objects(cur, RDF.rest), None)
        if members:
            groups.append(frozenset(members))
    return tuple(groups)


def disjoint_conflict(existing_types: set[URIRef], new_type: URIRef) -> URIRef | None:
    """The member of `existing_types` that owl:AllDisjointClasses forbids
    coexisting with `new_type`, or None if adding it is safe."""
    for group in disjoint_class_sets():
        if new_type not in group:
            continue
        for other in existing_types:
            if other != new_type and other in group:
                return other
    return None


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
# This is the same pathology previously documented for facts-render.
# It is reliably reproducible on L10's proceedings prompt and is a decoding
# bug, not a prompt or schema defect. Leave --max-tokens as the backstop.


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


def _normalize_name(value) -> str:
    """Casefold and strip punctuation, for comparing authority names."""
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def _demographics_conflict(graph: Graph, nodes: list[URIRef]) -> bool:
    """Do these same-named nodes disagree on a fact one person cannot disagree with?

    Co-applicants routinely share a designation -- a judgment calls all five of
    them "the applicant" in the sentences that describe them individually -- so
    the name key alone groups people the extraction was RIGHT to keep apart.
    Sending such a group to the merge prompt asks the model to undo the person
    splitting the extraction prompt just achieved, and the group looks maximally
    mergeable when it happens: identical name, identical type, few triples each.

    A differing birth year or gender is positive evidence of distinct people.
    Both are owl:FunctionalProperty, so the disagreement is not two readings of
    one person that a merge could reconcile -- it is two people, and merging
    them would drop one of the two values outright.

    Deliberately narrower than "any property differs": co-applicants usually DO
    differ in residence and often in what quote anchors them, and treating every
    difference as disqualifying would suppress the real duplicates this check
    exists to surface. Absence never conflicts -- one node with a birth year and
    one without are still candidates, which is the ordinary shape of a genuine
    duplicate where one copy is thinner than the other.
    """
    for prop in (ECHR.hasBirthYear, ECHR.hasGender):
        seen = {value for node in nodes for value in graph.objects(node, prop)}
        if len(seen) > 1:
            return True
    return False


def find_duplicate_candidates(graph: Graph) -> list[dict]:
    """Groups of same-typed doc: nodes that look like one entity split in two.

    Deterministic surfacing only -- the model decides whether a group really is
    one entity. Two keys, both chosen because a false positive is expensive
    (merging two genuinely distinct proceedings asserts a falsehood):

      DomesticProceeding  same hasCourt AND same hasDecisionDate. This is a
                          CANDIDATE, not a verdict: a court deciding the same
                          case twice on one day is unusual but not rare, and
                          this test corpus alone has it happen for real at
                          least five times -- twin appeals by the applicant
                          and by the respondent State against different
                          judgments, two rulings on two different requests
                          disposed of in one shared sentence, a same-day
                          "further orders" ruling separate from that day's
                          main judgment. Measured 2026-09-08: this key's own
                          candidates, merged without checking who the parties
                          were or what was decided, silently undid several of
                          exactly those genuine splits. `_members` therefore
                          returns each candidate's participating parties and
                          `hasMatter` values alongside its quotes -- read them
                          before merging, not just the court and the date.
      DomesticAuthority   same normalized hasAuthorityName / rdfs:label.
      NaturalPerson       same normalized hasPersonName / rdfs:label, MINUS any
                          group whose members disagree on a birth year or a
                          gender (see `_demographics_conflict`). Weaker than the
                          other two keys -- two distinct people can share a name
                          (a father and son, two co-accused, five co-applicants
                          the judgment all calls "the applicant") -- so this one
                          leans harder on the model actually checking the quotes
                          before merging.

    Each group is returned with enough context -- label, dates, outcome, quotes,
    and how many triples the node carries -- for the model to pick which node to
    keep without needing the source text.
    """
    doc_ns = str(next((ns for prefix, ns in graph.namespaces() if prefix == "doc"), ""))
    groups: list[dict] = []

    def _party_names(n: URIRef) -> list[str]:
        names = []
        for participation in graph.objects(n, ECHR.hasParticipation):
            party = next(graph.objects(participation, ECHR.participatingParty), None)
            if party is None:
                continue
            name = (
                next(graph.objects(party, ECHR.hasPartyName), None)
                or next(graph.objects(party, ECHR.hasPersonName), None)
                or next(graph.objects(party, RDFS.label), None)
            )
            if name is not None:
                names.append(str(name))
        return names

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
                    "parties": _party_names(n),
                    "matter": sorted(
                        _curie(graph, m) for m in graph.objects(n, ECHR.hasMatter)
                    ),
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
        if len(nodes) > 1 and not _demographics_conflict(graph, nodes):
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


def complete_entailed_types(graph: Graph, doc_ns: str) -> int:
    """Assert the rdf:type that a property's rdfs:range already entails.

    A node reached by echr:hasParticipation IS a echr:Participation -- the
    ontology says so as the property's range -- but SHACL's sh:class checks the
    asserted type, not the entailed one, so a participation added without an
    explicit rdf:type is reported as "hasParticipation must point at a node
    typed echr:Participation". Measured 2026-08-28 on the compressed L3: the
    review stage added ten participations this way and the document went from
    6 violations to 10. The post-review round then tried to fix them, created
    six party-less participations in the attempt, and was reverted by the
    improvement gate -- so all ten survived.

    Asking a model to add a type the ontology already entails is asking it to
    do arithmetic it will sometimes get wrong, in exchange for nothing. This
    adds it directly. It invents no facts: the triple asserting the link is
    the model's, and the type is what that link already means.

    Deliberately narrow. Only ranges whose entailment is unambiguous and
    carries no further claim are completed; echr:Party is NOT among them,
    because typing a node echr:Party where it is also a echr:DomesticAuthority
    is a real assertion about its role, not bookkeeping.

    Returns the number of types added.
    """
    entailed = (
        (ECHR.hasParticipation, ECHR.Participation),
        (ECHR.hasPreTrialDetention, ECHR.PreTrialDetention),
    )
    added = 0
    for prop, cls in entailed:
        for obj in set(graph.objects(None, prop)):
            if not isinstance(obj, URIRef) or not str(obj).startswith(doc_ns):
                continue
            if (obj, RDF.type, cls) in graph:
                continue
            graph.add((obj, RDF.type, cls))
            added += 1
    return added


def split_shared_participations(graph: Graph, doc_ns: str) -> int:
    """Give every event its own copy of a participation several events share.

    echr:ParticipationSingleEventShape fires when one echr:Participation is the
    target of echr:hasParticipation from more than one event, because
    echr:hasPartySide is functional and a shared node can hold only one side
    for all of them. The shape is right. What repair did with it was not: the
    cheapest way to satisfy sh:maxCount 1 is to drop the surplus
    echr:hasParticipation links, and that is what the model proposed and the
    applier accepted -- so every event but one lost that party entirely.

    Measured 2026-08-28 across the ten v12 documents: extraction produced 36
    events carrying two party sides, repair returned 20. Sixteen adversarial
    proceedings were left recording only one of their two sides, on L1, L3,
    L4, L6, L8 and L10. The graph got more conformant and less true.

    The defect is a serialisation shortcut, not a missing fact -- the party,
    the side and the quote are all present, they are simply written once and
    pointed at N times. Copying the node N times is therefore lossless and
    needs no model: it asserts nothing the graph did not already assert, it
    only stops one assertion standing in for several.

    Runs before the loop sees the graph, so the violation never reaches a
    prompt and never gets "fixed" by deletion.

    Returns the number of participation copies minted.
    """
    minted = 0
    for part in set(graph.objects(None, ECHR.hasParticipation)):
        if not isinstance(part, URIRef) or not str(part).startswith(doc_ns):
            continue
        events = sorted(
            {s for s in graph.subjects(ECHR.hasParticipation, part)},
            key=str,
        )
        if len(events) < 2:
            continue
        payload = [(pred, obj) for pred, obj in graph.predicate_objects(part)]
        # The first event keeps the original node; the rest get copies, so any
        # inbound reference from elsewhere in the graph stays valid.
        for index, event in enumerate(events[1:], start=1):
            local = str(part)[len(doc_ns) :]
            copy = URIRef(f"{doc_ns}{local}_e{index}")
            suffix = 1
            while (copy, None, None) in graph:
                suffix += 1
                copy = URIRef(f"{doc_ns}{local}_e{index}x{suffix}")
            for pred, obj in payload:
                graph.add((copy, pred, obj))
            graph.remove((event, ECHR.hasParticipation, part))
            graph.add((event, ECHR.hasParticipation, copy))
            minted += 1
    return minted


def split_multiparty_participations(graph: Graph, doc_ns: str) -> int:
    """Give every party its own participation when several share one node.

    The mirror of split_shared_participations, for the other way the same
    mistake is made. That one fixes ONE participation pointed at by SEVERAL
    events; this fixes ONE participation pointing at SEVERAL parties.
    echr.ttl asks for exactly one instance per party per echr:DomesticEvent,
    and echr:ParticipationAtomicShape caps echr:participatingParty at one.

    Measured 2026-08-30 on holdout L10 (Ilascu and Others v. Moldova and
    Russia, four co-applicants). Extraction produced four participations with
    one party each and the graph reported zero violations. The review stage,
    which reads the document, then correctly noticed that all four applicants
    were arrested together -- and recorded that by hanging all four off the
    single existing participation, taking the document from 0 to 4 violations.
    It was the first time repair made a graph worse all day.

    The added parties are RIGHT and must not be discarded: the other three
    co-applicants really were parties to those events, and refusing the ops
    would lose three real litigants to protect a cardinality. What is wrong is
    only the packing. So this splits rather than blocks, which keeps the
    review stage's recall and costs nothing -- the party, the side and the
    quote are all present, written once and pointed at N times.

    Returns the number of participation copies minted.
    """
    minted = 0
    for part in set(graph.subjects(ECHR.participatingParty, None)):
        if not isinstance(part, URIRef) or not str(part).startswith(doc_ns):
            continue
        parties = sorted(set(graph.objects(part, ECHR.participatingParty)), key=str)
        if len(parties) < 2:
            continue
        events = sorted(set(graph.subjects(ECHR.hasParticipation, part)), key=str)
        shared = [
            (pred, obj)
            for pred, obj in graph.predicate_objects(part)
            if pred != ECHR.participatingParty
        ]
        # The first party keeps the original node, so inbound references stay
        # valid; the rest get a node each.
        for party in parties[1:]:
            graph.remove((part, ECHR.participatingParty, party))
            local = str(party)[len(doc_ns) :] if str(party).startswith(doc_ns) else "p"
            copy = URIRef(f"{part!s}_{local}")
            suffix = 1
            while (copy, None, None) in graph:
                suffix += 1
                copy = URIRef(f"{part!s}_{local}{suffix}")
            for pred, obj in shared:
                graph.add((copy, pred, obj))
            graph.add((copy, ECHR.participatingParty, party))
            for event in events:
                graph.add((event, ECHR.hasParticipation, copy))
            minted += 1
    return minted


def prune_unbuilt_proceedings(graph: Graph, doc_ns: str) -> int:
    """Remove a case's list-membership triple for a proceeding that was never built.

    OntoCast's facts renderer sometimes emits the case-level scaffold --
    `echr:hasDomesticProceeding` naming every event the document identified --
    without materialising a node for all of them. Measured 2026-08-31 on
    holdout L1 (Enea v. Italy, 29 domestic events, the largest event count in
    either tuning set): the scaffold names all 29, but only 7 carry any content
    at all -- no rdf:type, no date, no court, nothing. The root cause is
    upstream of this repository (OntoCast's one-shot render hit a generation
    limit on this document, and its finding-driven repair pass targets
    quarantined literals and unknown terms, not missing content, so it never
    revisits the gap) -- see docs/ablation_c0_c4_20case.md SS9-10.

    This is the cheap mitigation available from our side: a reference this
    unfalsifiable is worse than no reference, so it is dropped rather than
    left for repair to try to complete with an invented quote. A node with ANY
    property at all is left alone, even an incomplete one -- completing a
    partial node is repair's job; this only removes what is provably nothing.
    """
    removed = 0
    for case in list(graph.subjects(RDF.type, ECHR.CaseDocument)):
        for node in list(graph.objects(case, ECHR.hasDomesticProceeding)):
            if not str(node).startswith(doc_ns):
                continue
            if next(graph.predicate_objects(node), None) is not None:
                continue
            graph.remove((case, ECHR.hasDomesticProceeding, node))
            removed += 1
    return removed


def mirror_party_labels(graph: Graph, doc_ns: str) -> int:
    """Copy echr:hasPartyName / echr:hasPersonName into a missing rdfs:label.

    Nothing requires a echr:Party to carry rdfs:label, so extraction names
    parties through whichever predicate the ontology offered for their kind:
    measured 2026-08-28 on the ten v12 documents, 5 of 37 party nodes had no
    rdfs:label at all -- doc:partyPastraDirector, doc:party_iktisat_bank and
    three others -- while every one of them carried a perfectly good
    echr:hasPartyName ("Director of the Pastra social care home").

    So this is not missing data, it is an inconsistent naming predicate, and
    it matters for exactly one thing: cross-document entity linking reads
    rdfs:label, and would silently skip an eighth of the parties in the
    corpus. Mirroring costs nothing and invents nothing -- the name is the
    model's, only the predicate is added.

    echr:hasPartyName wins over echr:hasPersonName when both are present: it
    is the party's name IN THE PROCEEDING, which is what a label on a party
    node should say.

    Returns the number of labels added.
    """
    added = 0
    for party in set(graph.objects(None, ECHR.participatingParty)):
        if not isinstance(party, URIRef) or not str(party).startswith(doc_ns):
            continue
        if list(graph.objects(party, RDFS.label)):
            continue
        names = list(graph.objects(party, ECHR.hasPartyName)) or list(
            graph.objects(party, ECHR.hasPersonName)
        )
        if len(names) != 1:
            # Zero means there is nothing to mirror; more than one means the
            # node is conflating two entities, which is a real defect for the
            # duplicate finder rather than something to paper over with a
            # label picked arbitrarily.
            continue
        graph.add((party, RDFS.label, names[0]))
        added += 1
    return added


def find_unchained_events(graph: Graph, doc_ns: str) -> list[dict]:
    """Domestic events that follow nothing and are followed by nothing.

    THE CHAIN IS THE POINT OF THIS DATASET and until 2026-08-28 no stage of
    repair could see it. No finder reported it; the loop prompt named
    echr:followsProceeding only when splitting a conflated event, and the
    review prompt only for events it newly added. An event extracted with no
    chain link at all was invisible to every stage.

    Reported with the event's date and the dates either side of it in document
    order, because that is what a reader needs to judge the gap and it costs
    nothing to supply. An ISOLATED event -- no inbound and no outbound link --
    is the signal worth acting on: an event that is merely the head of a track
    legitimately follows nothing, but one that nothing follows EITHER is
    either the whole of a one-step track or a link that was missed.

    Advisory, not a violation: parallel tracks are normal in these judgments,
    and a document can genuinely contain an isolated administrative act. This
    names candidates for a stage that can read the document, and says so.
    """
    events: list[tuple[str, str]] = []
    for cls in domestic_event_classes():
        for s in graph.subjects(RDF.type, cls):
            if not str(s).startswith(doc_ns):
                continue
            date = ""
            for pred in (ECHR.hasDecisionDate, ECHR.hasProceedingStartDate):
                for o in graph.objects(s, pred):
                    date = str(o)
                    break
                if date:
                    break
            events.append((str(s), date))
    out: list[dict] = []
    for iri, date in sorted(set(events), key=lambda e: (e[1] or "9999", e[0])):
        node = URIRef(iri)
        outbound = (node, ECHR.followsProceeding, None) in graph
        inbound = (None, ECHR.followsProceeding, node) in graph
        if outbound or inbound:
            continue
        out.append(
            {
                "event": _curie(graph, node),
                "date": date,
                "note": (
                    "no echr:followsProceeding in either direction: this event "
                    "continues nothing and nothing continues it"
                ),
            }
        )
    return out


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


def find_disjoint_type_conflicts(graph: Graph) -> list[dict]:
    """Nodes carrying two rdf:type values the ontology declares mutually
    exclusive via owl:AllDisjointClasses.

    SHACL does not evaluate owl:AllDisjointClasses, so this is invisible to
    find_shape_violations and to pyshacl's own conforms/violates report.
    Without it, a patch can silently satisfy a range constraint (e.g.
    CaseDocumentShape requiring echr:hasDomesticProceeding to point at an
    echr:DomesticProceeding) by adding that type to a node already typed
    echr:AdministrativeAction -- the SHACL violation disappears and the graph
    becomes logically inconsistent, and nothing scores the difference. This
    finder exists so the repair loop is told about that class of defect at
    all, and the corresponding guard in apply_patch stops a patch from
    introducing one in the first place.
    """
    findings: list[dict] = []
    for subject in {s for s in graph.subjects(RDF.type, None) if isinstance(s, URIRef)}:
        types = {t for t in graph.objects(subject, RDF.type) if isinstance(t, URIRef)}
        seen: set[frozenset[URIRef]] = set()
        for group in disjoint_class_sets():
            hit = types & group
            if len(hit) > 1 and frozenset(hit) not in seen:
                seen.add(frozenset(hit))
                findings.append(
                    {
                        "kind": "disjoint_type_conflict",
                        "severity": "error",
                        "message": (
                            "this node is typed as "
                            + " and ".join(sorted(_curie(graph, t) for t in hit))
                            + ", but the ontology declares these classes mutually "
                            "exclusive (owl:AllDisjointClasses) -- remove the "
                            "wrong type, or better, remove or retarget the "
                            "predicate that required it, rather than keeping "
                            "both types"
                        ),
                        "subject": _curie(graph, subject),
                        "predicate": "rdf:type",
                        "values": sorted(_curie(graph, t) for t in hit),
                    }
                )
    return findings


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------


_REASONING_TOKEN_PARAM_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _token_limit_kwargs(model: str, max_tokens: int) -> dict[str, int]:
    """The output-cap kwarg this model actually accepts."""
    name = model.lower().lstrip()
    if name.startswith(_REASONING_TOKEN_PARAM_PREFIXES):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens}


def accepts_temperature(model: str) -> bool:
    """False for the families that reject any temperature but the default.

    The pipeline asks for temperature 0 nearly everywhere, which a gpt-5/o-series
    endpoint answers with a 400 rather than by clamping. Omitting the parameter
    gets that model's own default, which is what those families support.
    """
    return not model.lower().lstrip().startswith(_REASONING_TOKEN_PARAM_PREFIXES)


def model_call_kwargs(
    model: str, *, temperature: float, max_tokens: int, seed: int | None = None
) -> dict:
    """Sampling kwargs a given model will accept, in one place.

    Every completion call in this repository goes through here so that swapping
    a local vLLM model for a hosted one is a --model change and nothing else.
    `seed` rides with temperature: it is a sampling control, and the families
    that refuse a temperature have no sampling loop for it to control.
    """
    kwargs: dict = dict(_token_limit_kwargs(model, max_tokens))
    if accepts_temperature(model):
        kwargs["temperature"] = temperature
        if seed is not None:
            kwargs["seed"] = seed
    return kwargs


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
    guided: bool = True,
) -> tuple[str, str | None, bool]:
    """Stream one patch, aborting the moment the generation stalls.

    Cached on the request when LLM_CACHE_ENABLED is on (art6.ontology.llm_cache).
    Stage 3 makes tens of calls per document and had no cache at all, so C2/C4
    could not be reproduced even with OntoCast's own cache warm. The cached
    value is the returned tuple, not the stream: a replayed call does not stall,
    so `stalled` is whatever the original generation reported.

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
    from art6.ontology import llm_cache
    from art6.ontology.provider import extra_body

    routing = extra_body()
    cache_key = llm_cache.make_key(
        "repair_patch",
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "token_kwargs": token_kwargs,
            "guided": guided,
            **({"extra_body": routing} if routing else {}),
        },
    )
    cached = llm_cache.load("repair_patch", cache_key)
    if cached is not llm_cache.MISS:
        return cached[0], cached[1], cached[2]

    parts: list[str] = []
    finish_reason: str | None = None
    stalled = False
    trailing_ws = 0

    # Unconstrained mode drops response_format entirely: the schema goes in
    # the prompt instead and the JSON is recovered afterwards. The stall
    # detection below stays either way -- it is not grammar-specific, and an
    # unconstrained model can still run away, just far less reliably so.
    fmt = {"response_format": RepairPatch} if guided else {}
    # Reasoning models reject an explicit temperature; see accepts_temperature.
    temp = {"temperature": temperature} if accepts_temperature(model) else {}
    # Wait out a throttled or restarting endpoint the way stage 1 does
    # (compress.call_with_recovery): a 429 from a hosted provider is "slow
    # down", not "this patch failed", and the two quick retries in call_model
    # used to burn through it in seconds and drop the call.
    from art6.ontology.extraction import (
        TRANSIENT_BACKOFF,
        TRANSIENT_MAX_WAIT_SECONDS,
        _is_transient,
    )

    waited, attempt = 0.0, 0
    while True:
        try:
            stream_cm = client.chat.completions.stream(
                model=model,
                messages=messages,
                **temp,
                **fmt,
                **token_kwargs,
                **({"extra_body": routing} if routing else {}),
            )
            stream = stream_cm.__enter__()
            break
        except Exception as exc:
            if not _is_transient(exc) or waited >= TRANSIENT_MAX_WAIT_SECONDS:
                raise
            delay = TRANSIENT_BACKOFF[min(attempt, len(TRANSIENT_BACKOFF) - 1)]
            attempt += 1
            waited += delay
            print(
                f"      {type(exc).__name__}: endpoint throttled or unreachable -- "
                f"retrying in {delay}s (waited {waited:.0f}s)",
                flush=True,
            )
            time.sleep(delay)
    with stream_cm:
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

    result = ("".join(parts), finish_reason, stalled)
    llm_cache.store("repair_patch", cache_key, list(result))
    return result


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


_SCHEMA_INSTRUCTION = """
Return ONE JSON object and nothing else -- no prose before or after it, no
markdown fence. It must match this schema exactly:

{schema}

Every operation goes in `groups[].ops[]`. `action` is exactly "add" or
"remove". `subject` and `predicate` are always CURIEs. `object` is a CURIE when
`object_is_literal` is false and the plain text value when it is true. Set
`datatype` OR `lang`, never both, and omit both for a CURIE object. Keep every
`rationale` to one short sentence.
"""


def parse_unconstrained_patch(raw: str) -> RepairPatch | None:
    """A RepairPatch out of free-form model output, or None.

    Four escalating attempts, cheapest first: parse it as-is; unwrap a
    markdown fence; rebuild the container closers from an explicit stack
    (the model closes the WRONG bracket rather than truncating, so the bad
    bytes are present and must be replaced, not appended to); and finally fall
    back to the truncation salvage, which returns the largest well-formed
    prefix.
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    candidates = [raw]
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        candidates.append(fence.group(1).strip())
    brace = raw.find("{")
    if brace > 0:
        candidates.append(raw[brace:])

    for cand in list(candidates):
        try:
            from art6.ontology.json_closers import rebuild_closers

            candidates.append(rebuild_closers(cand))
        except Exception:  # noqa: BLE001, S110
            pass

    for cand in candidates:
        try:
            return RepairPatch.model_validate_json(cand)
        except Exception:  # noqa: BLE001,S112 - most candidates fail by design
            continue
    return salvage_truncated_patch(raw)


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


def _quote_in_pool(graph: Graph, text: str) -> bool:
    """True if `text` is already carried by some echr:hasSupportingQuote in the
    graph -- exactly, or as a contiguous span of one.

    The pool is what makes a quote ADD safe for a stage that cannot see the
    document: every member of it was chosen by extraction, which could. See the
    QUOTE REUSE note in apply_patch.
    """
    from art6.ontology.diagnostics.validate_source_quotes import normalize

    wanted = normalize(unwrap_literal(text))
    if not wanted:
        return False
    quote_predicate = ECHR.hasSupportingQuote
    for existing in graph.objects(None, quote_predicate):
        if wanted in normalize(str(existing)):
            return True
    return False


def apply_patch(
    graph: Graph,
    patch: RepairPatch,
    doc_ns: str,
    source_text: str | None = None,
    unverified_quotes: list[dict] | None = None,
    allow_quote_adds: bool = False,
    allow_quote_reuse: bool = False,
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
    # ANY add op makes its subject a node this patch is building, not just an
    # `add rdf:type`. Requiring the type op was too strict and was silently
    # destroying the patch's most important work: measured 2026-08-25 on
    # o2_low_jsonld L1, eight `add ... echr:hasParticipation` ops were refused
    # as dangling in a single round because the model created each
    # participation node by giving it echr:participatingParty and
    # echr:hasPartySide -- fully specifying it -- without a separate
    # rdf:type echr:Participation op, which the range of hasParticipation
    # already entails. The guard exists to catch references to nodes NOBODY
    # creates; a node the patch gives properties to is created.
    for group in patch.groups:
        for op in group.ops:
            if op.action != "add":
                continue
            subj_prefix, _, subj_local = op.subject.strip().partition(":")
            if subj_prefix == "doc":
                known_doc_nodes.add(doc_ns + subj_local)

    # Subjects this patch gives a (replacement) participation to. Collected in
    # the same pre-pass because the guard below has to know about an add that
    # has not been reached yet: the model routinely emits every remove first
    # and then the adds that compensate for them.
    subjects_gaining_participation = {
        op.subject.strip()
        for group in patch.groups
        for op in group.ops
        if op.action == "add" and op.predicate == "echr:hasParticipation"
    }

    quotes_by_index = {q["index"]: q for q in (unverified_quotes or []) if "index" in q}

    # THE DECIDING BODY IS NOT A PARTY TO THE EVENT IT DECIDED, enforced here
    # rather than left to the SHACL message, for the reason every other
    # structural ban in this function exists: the model read the finding and
    # made it worse. Measured 2026-08-27 on the compressed L3, condition D:
    # the graph arrived with ONE court-as-party instance and left with EIGHT,
    # all of them doc:sdif, all minted in a single loop round as
    # doc:part_e4_sdif, doc:part_e5_sdif, ... -- one template applied
    # mechanically to every event that body decided.
    #
    # The mechanism is not hallucination. The loop is shown the graph and not
    # the document, so when a finding says "this event has no participation"
    # it has no evidence about who the parties were, and the cheapest node
    # satisfying the shape is one already attached to the event: the court.
    # A prompt rule cannot fix that, because the model is not being careless
    # -- it is being asked to supply data it was given no basis for. Until
    # absence-findings stop reaching this stage at all, the least it can do is
    # refuse to name the adjudicator.
    #
    # The event behind a participation may be established by the input graph
    # or by an `add ... echr:hasParticipation ...` elsewhere in THIS patch,
    # and likewise its court, so both maps are built across both sources.
    participation_event: dict[str, str] = {}
    for s_, o_ in working.subject_objects(ECHR.hasParticipation):
        participation_event[str(o_)] = str(s_)
    event_court: dict[str, str] = {}
    for s_, o_ in working.subject_objects(ECHR.hasCourt):
        event_court[str(s_)] = str(o_)

    def _expand(term: str) -> str:
        prefix, _, local = term.strip().partition(":")
        return doc_ns + local if prefix == "doc" else term.strip()

    for group in patch.groups:
        for op in group.ops:
            if op.action != "add" or op.object_is_literal:
                continue
            if op.predicate == "echr:hasParticipation":
                participation_event[_expand(op.object)] = _expand(op.subject)
            elif op.predicate == "echr:hasCourt":
                event_court[_expand(op.subject)] = _expand(op.object)

    # The ban is GROUP-LEVEL, not per-operation. Refusing only the
    # echr:participatingParty op lets its siblings land -- the
    # echr:hasParticipation link, the echr:hasPartySide, the rdf:type -- and
    # leaves a participation node with a side and no party. That is the
    # party-less-participation defect under another name, and it is itself a
    # shape violation. Measured 2026-08-27 on the compressed L3: the
    # per-operation ban took court-as-party 8 -> 0 and left exactly 8 "a
    # participation must record exactly one party" violations in their place,
    # trading one defect for another one-for-one.
    #
    # So the whole participation is refused: every op whose subject is the
    # banned node, and the link pointing at it from the event. The event then
    # simply has no participation, which is an HONEST ABSENCE -- it surfaces
    # as a missing_participation finding, visible and true, rather than as
    # fabricated data or a stub.
    banned_participations: set[str] = set()
    for group in patch.groups:
        for op in group.ops:
            if (
                op.action == "add"
                and op.predicate == "echr:participatingParty"
                and not op.object_is_literal
            ):
                subject = _expand(op.subject)
                event = participation_event.get(subject)
                if event is not None and event_court.get(event) == _expand(op.object):
                    banned_participations.add(subject)

    # A PARTICIPATION MAY ONLY BE ADDED WHERE IT IS EVIDENCED.
    #
    # A participation asserts that a named party stood on a named side of a
    # named event. That is a factual claim about the case, and repair has no
    # business making one it cannot point at words for. Until 2026-08-27
    # nothing checked this, and nothing COULD: no party node in any condition
    # carried a supporting quote, so a participation invented by repair looked
    # exactly like one read off the document by extraction.
    #
    # So a new participation must arrive with an anchor -- either one the same
    # patch attaches to it, or one it already carries in the graph. The blind
    # loop cannot mint quotes at all, which is the point: it can no longer
    # create participations, only repair the structure of existing ones. The
    # review stage can, because it reads the document, and its quote is
    # verified verbatim against the source before it lands.
    #
    # Note this is a floor, not a guarantee of correctness: an anchored
    # participation can still name the wrong party. It guarantees only that
    # SOMEONE can check, which is the property the whole pipeline is for.
    anchored_participations = {
        str(s) for s in working.subjects(ECHR.hasSupportingQuote, None)
    }
    for group in patch.groups:
        for op in group.ops:
            if op.action == "add" and op.predicate == "echr:hasSupportingQuote":
                anchored_participations.add(_expand(op.subject))
    unevidenced_participations = {
        _expand(op.subject)
        for group in patch.groups
        for op in group.ops
        if op.action == "add"
        and op.predicate == "echr:participatingParty"
        and _expand(op.subject) not in anchored_participations
    }

    # A PROCEEDING WITH NO ANCHOR is the same hole one level up, and it is the
    # larger one. Measured 2026-08-31 on holdout L1 (Enea v. Italy): extraction
    # produced 7 domestic proceedings, all substantive and all quoted; repair
    # returned 28, of which 22 carried four triples or fewer -- a type, a court,
    # a followsProceeding and a participation, with no date, no instance level,
    # no outcome and NO SUPPORTING QUOTE. Enea is a prison-regime case with many
    # near-identical applications to one court, and the review stage extrapolated
    # the pattern into 21 proceedings the document does not describe.
    #
    # The verbatim guarantee does not catch this, and cannot: it constrains what
    # a quote must be, not whether a node must have one. A proceeding with no
    # quote is not a weak claim, it is an unfalsifiable one -- so a proceeding
    # this patch creates must arrive anchored, exactly as a participation must.
    proceeding_predicates = {
        "echr:hasDomesticProceeding",
        "echr:followsProceeding",
    }
    created_proceedings = {
        _expand(op.object)
        for group in patch.groups
        for op in group.ops
        if op.action == "add"
        and op.predicate in proceeding_predicates
        and not op.object_is_literal
    }
    created_proceedings |= {
        _expand(op.subject)
        for group in patch.groups
        for op in group.ops
        if op.action == "add"
        and op.predicate == "rdf:type"
        and not op.object_is_literal
        and "Proceeding" in op.object
    }
    unevidenced_proceedings = {
        node
        for node in created_proceedings
        if node not in anchored_participations
        and (URIRef(node), ECHR.hasSupportingQuote, None) not in working
    }

    # A PARTICIPATION WITH NO PARTY AT ALL is the other half of the same hole.
    # The evidence check above keys on `add echr:participatingParty`, so it
    # only sees participations that name SOMEBODY. A group that adds
    # echr:hasParticipation and echr:hasPartySide and no party at all names
    # nobody, trips nothing, and lands a stub: a participation asserting that
    # some unspecified entity stood on a side. Measured 2026-08-28 on the
    # compressed L3, once the entailed types were completed and the stubs
    # became visible: six of them, all from the review stage.
    #
    # Such a group is refused whole, for the same reason the deciding-body ban
    # is refused whole -- a half-built participation is a defect in its own
    # right, and the event is better off with the honest absence.
    partied = {
        _expand(op.subject)
        for group in patch.groups
        for op in group.ops
        if op.action == "add" and op.predicate == "echr:participatingParty"
    }
    partyless_participations: set[str] = set()
    for group in patch.groups:
        for op in group.ops:
            if op.action != "add":
                continue
            node = None
            if op.predicate == "echr:hasPartySide":
                node = _expand(op.subject)
            elif op.predicate == "echr:hasParticipation" and not op.object_is_literal:
                node = _expand(op.object)
            if node is None or node in partied:
                continue
            if (URIRef(node), ECHR.participatingParty, None) in working:
                continue
            partyless_participations.add(node)
    unevidenced_participations |= partyless_participations

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
                if (
                    op.action == "remove"
                    and op.predicate == "echr:hasParticipation"
                    and op.subject.strip() not in subjects_gaining_participation
                ):
                    # PARTICIPATION BALANCE GUARD. Repair may restructure an
                    # event's participations; it may not strip the event bare.
                    #
                    # The one-participation-per-event rule makes shared nodes a
                    # real defect, and the model diagnoses them correctly -- but
                    # its remedy is to unlink the shared node from every event
                    # and mint replacements, and it routinely emits far more
                    # removes than adds. Measured 2026-08-24 on o2_cf_low_jsonld
                    # L6, the first arm run after the literal-remove fix: 20
                    # hasParticipation links removed, 5 added back, and 10 of 15
                    # events left with no party information at all. Before that
                    # fix these removes silently no-opped, so the defect was
                    # invisible and the graph merely kept its shared nodes.
                    #
                    # Trading a duplicate for an absence is the wrong direction:
                    # a shared participation is a defect downstream can see and
                    # merge, an event with no parties is unrecoverable. Same
                    # reasoning as the evidence-anchor guard above -- a stage
                    # must not be able to make the graph worse than it found it.
                    s_ = resolve_term(
                        working, op.subject, is_literal=False, datatype=None, lang=None
                    )
                    remaining = len(list(working.objects(s_, ECHR.hasParticipation)))
                    targeted = 1 if op.object.strip() else remaining
                    if remaining - targeted <= 0:
                        record["status"] = (
                            "skipped: would leave the event with no participation "
                            "and the patch adds no replacement"
                        )
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
                if (
                    op.action == "add"
                    and unevidenced_proceedings
                    and (
                        _expand(op.subject) in unevidenced_proceedings
                        or (
                            not op.object_is_literal
                            and _expand(op.object) in unevidenced_proceedings
                        )
                    )
                ):
                    record["status"] = (
                        "skipped: repair may not add a proceeding with no "
                        "echr:hasSupportingQuote - an unanchored proceeding is "
                        "unfalsifiable, and the chain is better off without it"
                    )
                    audit.append(record)
                    continue
                if (
                    op.action == "add"
                    and unevidenced_participations
                    and (
                        _expand(op.subject) in unevidenced_participations
                        or (
                            op.predicate == "echr:hasParticipation"
                            and not op.object_is_literal
                            and _expand(op.object) in unevidenced_participations
                        )
                    )
                ):
                    record["status"] = (
                        "skipped: repair may not add an incomplete participation "
                        "- it must name exactly one echr:participatingParty and "
                        "carry a echr:hasSupportingQuote anchoring it, or the "
                        "event is left with an honest absence instead"
                    )
                    audit.append(record)
                    continue
                if (
                    op.action == "add"
                    and banned_participations
                    and (
                        _expand(op.subject) in banned_participations
                        or (
                            op.predicate == "echr:hasParticipation"
                            and not op.object_is_literal
                            and _expand(op.object) in banned_participations
                        )
                    )
                ):
                    record["status"] = (
                        "skipped: repair may not name the deciding body as a "
                        "participating party - the whole participation is refused "
                        "so the event is left with an honest absence rather than a "
                        "party-less stub"
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
                    # REPAIR MAY NOT MINT EVIDENCE. An earlier version allowed
                    # an add of echr:hasSupportingQuote provided the text
                    # verified against the source. That guard worked -- it
                    # rejected 26 fabricated quotes across the 2026-08-24 sweep
                    # -- but the permission itself is wrong.
                    #
                    # A supporting quote is the anchor that makes a fact
                    # checkable, and repair sees the graph, not the case. Even a
                    # verbatim span it chooses is a span IT selected, for a fact
                    # some earlier stage asserted, with no basis for believing
                    # that passage evidences that fact. A quote that verifies is
                    # not thereby the right quote: it only proves those words
                    # occur somewhere in the document.
                    #
                    # The asymmetry from the quotes-stage prompt settles it --
                    # an unanchored fact is recoverable, a falsely anchored one
                    # is not, because it defeats the very check it appears to
                    # satisfy. So repair may DELETE a bad anchor and may never
                    # supply one. Extraction is the only stage that gets to say
                    # what evidences what, because it is the only stage reading
                    # the document to find the fact in the first place.
                    if op.predicate == "echr:hasSupportingQuote":
                        if not allow_quote_adds:
                            # QUOTE REUSE: an anchor may be MOVED, never minted.
                            #
                            # The blanket ban was too blunt for one case that
                            # matters. When the loop correctly splits a
                            # conflated event in two -- the single commonest
                            # repair it performs -- the new node needs an
                            # anchor, and the right anchor is the passage
                            # already sitting on the node being split. Measured
                            # 2026-08-25 on o2_low_jsonld L1: the loop minted
                            # doc:admin_action_appointment_2002 with a court, a
                            # date and a participation, offered the parent's
                            # own quote for it, was refused, and the node
                            # landed permanently unanchored -- a fresh SHACL
                            # violation created by the pass meant to remove
                            # them.
                            #
                            # So the text must already be in the graph's own
                            # quote pool: equal to, or a substring of, some
                            # existing echr:hasSupportingQuote object. That
                            # keeps the ban's actual point intact. The
                            # objection was never to the characters, it was
                            # that a stage which cannot read the document has
                            # no basis for choosing a passage. Reused text was
                            # chosen by extraction, which did read it, and a
                            # substring of a verifying quote verifies too --
                            # so this can neither fabricate a span nor promote
                            # one that never appeared in the source.
                            reused = allow_quote_reuse and _quote_in_pool(
                                working, op.object
                            )
                            if not reused:
                                record["status"] = (
                                    "skipped: repair may not add evidence "
                                    "anchors, only remove or reuse them"
                                )
                                audit.append(record)
                                continue
                            record["status_note"] = "reused an existing quote"
                        # Reuse has already proved itself against the graph's
                        # own pool and must not fall through to the checks
                        # below: those verify against the source document,
                        # which the reusing caller does not hold. Letting it
                        # fall through refused every reuse with "quote adds
                        # need the source text to verify against" -- measured
                        # 2026-08-25, where the loop's one correct reuse was
                        # rejected by the very branch that had just approved it.
                        elif not source_text:
                            # The exemption exists for ONE caller: a stage
                            # holding the source document, which is the
                            # condition the ban is really about. It still has
                            # to be a real span -- permission to anchor is not
                            # permission to invent, and 26 fabricated quotes
                            # across the 2026-08-24 sweep are what this check
                            # caught when it applied to everyone.
                            record["status"] = (
                                "skipped: quote adds need the source text to "
                                "verify against"
                            )
                            audit.append(record)
                            continue
                        from art6.ontology.diagnostics.validate_source_quotes import (
                            normalize,
                            quote_verifies,
                        )

                        if source_text and not quote_verifies(
                            op.object, normalize(source_text)
                        ):
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

            if op.action == "add" and p == RDF.type and isinstance(o, URIRef):
                # A retype that satisfies one shape by making the node
                # LOGICALLY INCONSISTENT is worse than the violation it
                # removes. SHACL does not evaluate owl:AllDisjointClasses, so
                # nothing else in this pipeline would ever catch e.g. adding
                # echr:DomesticProceeding to a node already typed
                # echr:AdministrativeAction to satisfy CaseDocumentShape's
                # range check on echr:hasDomesticProceeding -- the violation
                # disappears from the report and the graph gets worse. Checked
                # against `working` as mutated so far, which already reflects
                # every earlier op in this same patch.
                existing_types = {
                    t for t in working.objects(s, RDF.type) if isinstance(t, URIRef)
                }
                conflict = disjoint_conflict(existing_types, o)
                if conflict is not None:
                    record["status"] = (
                        f"skipped: adding {_curie(working, o)} would make "
                        f"{_curie(working, s)} both {_curie(working, o)} and "
                        f"{_curie(working, conflict)}, which the ontology "
                        "declares mutually exclusive (owl:AllDisjointClasses) "
                        "-- remove or retarget the predicate that required "
                        "this type instead of adding a second, incompatible one"
                    )
                    audit.append(record)
                    continue

            if op.action == "add":
                working.add((s, p, o))
                record["status"] = "applied"
            elif (s, p, o) in working:
                working.remove((s, p, o))
                record["status"] = "applied"
            elif op.object_is_literal:
                # LEXICAL FALLBACK. An exact remove has to match the whole
                # literal TERM -- lexical form, datatype AND language tag --
                # and the model reliably gets the last two wrong, because it
                # is naming a VALUE while rdflib is comparing a term.
                # Extraction writes labels as "..."@en and dates as
                # "..."^^xsd:date; the model sends no lang at all, or guesses
                # xsd:string. rdflib treats all three as different terms, so
                # the remove misses a triple that is plainly there.
                #
                # Measured on the 2026-08-24 gemma sweep: 38 of 505 repair
                # operations skipped as "triple not present", every one a
                # remove, and 39 of the 72 SHACL violations surviving repair
                # were multi-label (30) and multi-decision-date (9) -- exactly
                # the conflations these removes were sent to undo. The stage
                # diagnosed them correctly every time and then failed to apply
                # its own fix. Adds landed, removes did not, so graphs could
                # only ever accumulate.
                #
                # The same defect was fixed once before, scoped to
                # echr:hasSupportingQuote (see the quote_index branch above).
                # It is not specific to quotes and the narrow fix left every
                # other predicate exposed.
                #
                # Matching on lexical form is the right comparison here: the op
                # names a value to delete, and two literals with the same
                # lexical form under one (subject, predicate) are the same
                # value however they are typed. Every match goes in the audit
                # trail so a fallback is never mistaken for an exact hit.
                wanted = unwrap_literal(op.object)
                matched = [
                    obj
                    for obj in working.objects(s, p)
                    if isinstance(obj, RDFLiteral) and str(obj) == wanted
                ]
                for obj in matched:
                    working.remove((s, p, obj))
                record["status"] = (
                    f"applied (matched {len(matched)} literal(s) by lexical form)"
                    if matched
                    else "skipped: triple not present"
                )
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

    # WITHDRAW HALF-BUILT PARTICIPATIONS.
    #
    # Ops are applied one triple at a time and a skipped op only `continue`s,
    # so a rejected member of a group leaves the rest of the group standing.
    # Measured 2026-08-30 on the compressed arm: review proposed a participation
    # as four triples -- type, side, quote, participatingParty -- and the fourth
    # was skipped ("object node does not exist in the graph or this patch")
    # because the Party it named was never minted. The first three landed, and
    # doc:part_e2_shareholders became a Participation with a side and a quote
    # and no party: 6 such nodes, each one a SHACL violation this pass created.
    #
    # Scoped deliberately. Rolling back whole groups would discard legitimate
    # removes that happen to share a group with a failed add, and DELETING the
    # node outright orphans every participation pointing at the party it
    # references -- tried, and it took violations from 35 to 133. So this
    # withdraws only the triples THIS patch added for a participation THIS
    # patch created that has no party, plus the links to it.
    created_here = {
        str(rec.get("subject", "")).strip()
        for rec in audit
        if rec.get("action") == "add"
        and str(rec.get("status", "")).startswith("applied")
    }
    withdrawn = 0
    for subject in list(created_here):
        prefix, _, local = subject.partition(":")
        if prefix != "doc":
            continue
        node = URIRef(doc_ns + local)
        # Participation-ness is established by BEHAVIOUR, not by rdf:type. The
        # type op is frequently skipped by the incomplete-participation guard
        # above while the side and quote ops land, and the type only appears
        # later when complete_entailed_types derives it from the range of
        # echr:hasParticipation. Requiring an explicit type here matched none of
        # the six real cases.
        looks_like_participation = (
            (node, RDF.type, ECHR.Participation) in working
            or working.value(node, ECHR.hasPartySide) is not None
            or (None, ECHR.hasParticipation, node) in working
        )
        if not looks_like_participation:
            continue
        if working.value(node, ECHR.participatingParty) is not None:
            continue
        for triple in list(working.triples((node, None, None))):
            working.remove(triple)
        for triple in list(working.triples((None, None, node))):
            working.remove(triple)
        withdrawn += 1
        audit.append(
            {
                "finding": "incomplete_participation",
                "action": "withdraw",
                "subject": subject,
                "status": "applied (participation had no participatingParty)",
                "rationale": (
                    "a member of this patch was skipped, leaving a participation "
                    "with no party; the patch's own triples for it are withdrawn"
                ),
            }
        )
    if withdrawn:
        print(f"    withdrew {withdrawn} half-built participation(s)", flush=True)

    return working, audit


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
