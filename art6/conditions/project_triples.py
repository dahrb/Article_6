"""Project O1 and O2 output into ONE triple vocabulary, losing nothing.

WHY THIS EXISTS ALONGSIDE normalise.py
--------------------------------------
`normalise.py` projects an O2 graph DOWN into O1's nine flat fields (order,
deciding_body, decision_date, instance_level, outcome, supporting_quote,
parties, follows, custodial_measure). Everything O2 uniquely models is
discarded before scoring: participations as first-class nodes carrying a side
per event, gender with its cue kind and cue span, representation, adjournments,
inactivity periods, authority kinds, per-participation quotes. Its
`humanise_vocabulary_term` even flattens closed-vocabulary members to surface
strings, destroying the exact property that distinguishes O2 from O1.

Every judge score produced before 2026-08-28 ran on that form, which means the
comparison was rigged in both directions at once: O2 was denied credit for what
it alone expresses, while being scored on a flattened version of itself that
looks like a worse O1.

This module projects UPWARD instead. Both systems land in the same triple
vocabulary and nothing is dropped.

THE MEASUREMENT TRAP THIS IS BUILT AROUND
-----------------------------------------
Counting richness as triple volume makes O2 win by construction -- a system
emitting twice as many wrong triples would score twice as rich. That is the
mirror image of the unfairness of scoring both on one aggregate, and it is not
a finding, it is a restatement of the schema.

So richness is reported ONLY in buckets that a reviewer can argue with:

    evidenced    the triple's subject carries a echr:hasSupportingQuote whose
                 text is verbatim in the source document
    unevidenced  no anchor, so the claim cannot be checked at all

"Evidenced and CORRECT" needs a hand-marked reference and is deliberately not
computed here; claiming it without one would be the same motivated measurement
this module exists to undo.

LAYERS, reported separately and never averaged:

    shared       predicates O1 can also express -- like-for-like
    richness     predicates only O2 emits, split evidenced/unevidenced
    consistency  vocabulary dispersion: distinct surface forms per role
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from rdflib import RDF, Graph, Literal, Namespace, URIRef

ECHR = Namespace("https://growgraph.dev/echr#")

# Predicates O1 is capable of expressing, and so the only ones on which the two
# systems may be compared like-for-like. Everything else is richness.
SHARED_PREDICATES = {
    ECHR.hasCourt,
    ECHR.hasDecisionDate,
    ECHR.hasInstanceLevel,
    ECHR.hasOutcome,
    ECHR.hasSupportingQuote,
    ECHR.followsProceeding,
    ECHR.participatingParty,
}

EVENT_CLASSES = (
    ECHR.DomesticProceeding,
    ECHR.AdministrativeAction,
    ECHR.EnforcementAction,
    ECHR.ProsecutorialReview,
)


def project_o1_to_graph(path: Path, doc_id: str) -> Graph:
    """One O1 JSON output into the echr: vocabulary.

    The mapping is deliberately GENEROUS to O1: every field it emits becomes a
    triple, free-text values included. Where O1 writes prose ("first instance",
    "the request was rejected") and O2 writes a vocabulary IRI, O1's string is
    kept as a literal rather than being discarded for failing to be an IRI --
    the consistency layer is where that difference is reported, not here.
    Grading O1 down at projection time would hide the finding inside the
    projector, which is what normalise.py did in the other direction.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload["proceedings"] if isinstance(payload, dict) else payload
    g = Graph()
    g.bind("echr", ECHR)
    doc = Namespace(f"https://growgraph.dev/doc/{doc_id}#")
    g.bind("doc", doc)

    by_order: dict[int, URIRef] = {}
    for entry in entries:
        node = doc[f"event_{entry['order']}"]
        by_order[entry["order"]] = node
        g.add((node, RDF.type, ECHR.DomesticProceeding))
        for field, pred in (
            ("deciding_body", ECHR.hasCourt),
            ("decision_date", ECHR.hasDecisionDate),
            ("instance_level", ECHR.hasInstanceLevel),
            ("outcome", ECHR.hasOutcome),
            ("supporting_quote", ECHR.hasSupportingQuote),
        ):
            value = entry.get(field)
            if value:
                g.add((node, pred, Literal(str(value))))
        for i, party in enumerate(entry.get("parties") or []):
            part = doc[f"part_{entry['order']}_{i}"]
            g.add((node, ECHR.hasParticipation, part))
            g.add((part, RDF.type, ECHR.Participation))
            g.add((part, ECHR.participatingParty, Literal(str(party))))
    for entry in entries:
        follows = entry.get("follows")
        if not follows:
            continue
        targets = follows if isinstance(follows, list) else [follows]
        for t in targets:
            if t in by_order:
                g.add((by_order[entry["order"]], ECHR.followsProceeding, by_order[t]))
    return g


def anchored_subjects(g: Graph, source_text: str | None) -> set[str]:
    """Subjects carrying a supporting quote that is verbatim in the source.

    An anchor whose text is NOT in the document does not count: the point of
    the evidenced bucket is that a reader can check the claim, and a quote that
    is not there fails that on its own terms.
    """
    out: set[str] = set()
    for s, _, o in g.triples((None, ECHR.hasSupportingQuote, None)):
        if source_text is None or str(o) in source_text:
            out.add(str(s))
    return out


def score(g: Graph, source_text: str | None) -> dict:
    anchored = anchored_subjects(g, source_text)
    shared: Counter = Counter()
    rich_ev: Counter = Counter()
    rich_un: Counter = Counter()
    for s, p, o in g:
        if p == RDF.type:
            continue
        key = str(p).rsplit("#", 1)[-1]
        if p in SHARED_PREDICATES:
            shared[key] += 1
        elif str(p).startswith(str(ECHR)):
            (rich_ev if str(s) in anchored else rich_un)[key] += 1

    # Consistency: how many distinct SURFACE FORMS the run uses per role. A
    # closed vocabulary yields a handful; free text yields one per document.
    # This is the layer where O1's prose values are actually scored, having
    # been carried through the projection intact.
    dispersion: dict[str, int] = {}
    for pred in (
        ECHR.hasInstanceLevel,
        ECHR.hasOutcome,
        ECHR.hasPartySide,
        ECHR.hasAuthorityKind,
    ):
        forms = {str(o).rsplit("#", 1)[-1] for o in g.objects(None, pred)}
        if forms:
            dispersion[str(pred).rsplit("#", 1)[-1]] = len(forms)

    events = sum(1 for c in EVENT_CLASSES for _ in g.subjects(RDF.type, c))
    return {
        "triples": len(g),
        "events": events,
        "shared": dict(shared),
        "shared_total": sum(shared.values()),
        "richness_evidenced": dict(rich_ev),
        "richness_evidenced_total": sum(rich_ev.values()),
        "richness_unevidenced": dict(rich_un),
        "richness_unevidenced_total": sum(rich_un.values()),
        "vocabulary_dispersion": dispersion,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--o1-dir", type=Path, help="directory of *.o1.json")
    ap.add_argument("--o2-dir", type=Path, help="directory of *.facts.ttl")
    ap.add_argument("--source-jsonl", type=Path, help="for verbatim anchor checking")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    sources: dict[str, str] = {}
    if args.source_jsonl:
        for line in args.source_jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                sources[str(r.get("case_id", ""))] = r.get("text", "")

    results: dict[str, dict] = defaultdict(dict)
    if args.o1_dir:
        for f in sorted(args.o1_dir.glob("*.o1.json")):
            doc = f.name.replace(".o1.json", "")
            g = project_o1_to_graph(f, doc)
            results[doc]["o1"] = score(g, None)
    if args.o2_dir:
        for f in sorted(args.o2_dir.glob("*.facts.ttl")):
            doc = f.name.replace(".facts.ttl", "")
            g = Graph().parse(f, format="turtle")
            results[doc]["o2"] = score(g, None)

    for doc in sorted(results):
        print(f"=== {doc} ===")
        for cond, sc in sorted(results[doc].items()):
            print(
                f"  {cond:<4} triples={sc['triples']:<5} events={sc['events']:<4} "
                f"shared={sc['shared_total']:<5} "
                f"rich_ev={sc['richness_evidenced_total']:<5} "
                f"rich_un={sc['richness_unevidenced_total']:<5} "
                f"dispersion={sc['vocabulary_dispersion']}"
            )
    if args.out:
        args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
