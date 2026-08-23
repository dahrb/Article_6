"""
quality_metrics.py
------------------
Mechanical metrics behind the extraction quality reports, keyed to the CURRENT
ontology rather than a hardcoded list.

Everything here is computed from the graphs alone (plus the source text for
evidence anchoring), with no LLM in the loop, so a report's numbers can always
be regenerated and diffed. The class and property inventories, the closed
vocabularies and the functional-property set are all read live from the
ontology file, so pointing this at a different snapshot measures that
snapshot's surface -- not the previous schema's.

Usage:
  uv run python -m art6.ontology.diagnostics.quality_metrics \\
      --experiment-dir results/experiment_echr2_20260819_122533

  # compare against an older run
  uv run python -m art6.ontology.diagnostics.quality_metrics \\
      --experiment-dir results/experiment_echr2_20260819_122533 \\
      --compare-dir results/experiment_ttl_20260818_161537
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef
from rdflib import Literal as RDFLiteral

from art6.paths import REPO_ROOT, relative

ECHR = Namespace("https://growgraph.dev/echr#")
ONTOLOGY_TTL = Path(
    os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr.ttl")
)
LINE_RE = re.compile(r"\.L(\d+)\.facts\.ttl$")

# Valid XSD types that rdflib nonetheless cannot cast to a Python value; a
# None from .value on these says nothing about the literal's wellformedness.
XSD_NS = "http://www.w3.org/2001/XMLSchema#"
UNPARSED_XSD = frozenset(
    URIRef(XSD_NS + t) for t in ("gYearMonth", "gYear", "gMonth", "gDay", "gMonthDay")
)


def load_schema() -> dict:
    """Class list, property list, closed vocabularies and functional set."""
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    classes = {s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
    props = {
        s
        for kind in (OWL.ObjectProperty, OWL.DatatypeProperty)
        for s in g.subjects(RDF.type, kind)
    }
    functional = set(g.subjects(RDF.type, OWL.FunctionalProperty))
    # Custom datatypes (echr:PartialDate) are declared vocabulary too; without
    # this they read as invented terms and their literals as malformed.
    datatypes = {
        s_ for s_ in g.subjects(RDF.type, RDFS.Datatype) if isinstance(s_, URIRef)
    }

    # Every named individual an owl:oneOf enumeration admits. A term in the
    # echr: namespace that is neither a class, a property, nor one of these is
    # invented vocabulary.
    enumerated: set[URIRef] = set()
    for lst in g.objects(None, OWL.oneOf):
        cur = lst
        while cur and cur != RDF.nil:
            for f in g.objects(cur, RDF.first):
                if isinstance(f, URIRef):
                    enumerated.add(f)
            cur = next(g.objects(cur, RDF.rest), None)

    return {
        "classes": classes,
        "properties": props,
        "functional": functional,
        "enumerated": enumerated,
        "datatypes": datatypes,
        "known": classes | props | enumerated | datatypes,
    }


def components(g: Graph) -> tuple[int, int, float]:
    """(component count, singleton count, largest-component share) over typed nodes."""
    typed = set(g.subjects(RDF.type, None))
    adj: dict = collections.defaultdict(set)
    for s, p, o in g:
        if p == RDF.type:
            continue
        if isinstance(o, URIRef) and s in typed and o in typed:
            adj[s].add(o)
            adj[o].add(s)
    seen: set = set()
    sizes = []
    for n in typed:
        if n in seen:
            continue
        size = 0
        stack = [n]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            size += 1
            stack.extend(adj[x] - seen)
        sizes.append(size)
    singles = sum(1 for n in typed if not adj[n])
    share = max(sizes) / len(typed) if typed and sizes else 0.0
    return len(sizes), singles, share


def measure(paths: list[Path], schema: dict) -> dict:
    """Aggregate every metric over one model's set of facts files."""
    m: collections.Counter = collections.Counter()
    per_case_components = []
    shares = []
    invented_terms: set[str] = set()
    malformed_durations: set[str] = set()

    for path in paths:
        g = Graph()
        g.parse(path)
        m["files"] += 1
        m["triples"] += len(g)
        typed = set(g.subjects(RDF.type, None))
        m["typed_nodes"] += len(typed)
        m["blank_nodes"] += sum(1 for s in g.subjects() if not isinstance(s, URIRef))

        for cls in schema["classes"]:
            local = str(cls).split("#")[-1]
            if local.startswith("n"):  # anonymous restriction nodes
                continue
            m[f"class:{local}"] += len(set(g.subjects(RDF.type, cls)))

        for prop in schema["properties"]:
            local = str(prop).split("#")[-1]
            m[f"prop:{local}"] += len(list(g.triples((None, prop, None))))

        # invented vocabulary: any echr: IRI used that the ontology never defines
        for s, p, o in g:
            for term in (s, p, o):
                if (
                    isinstance(term, URIRef)
                    and str(term).startswith(str(ECHR))
                    and term not in schema["known"]
                ):
                    invented_terms.add(str(term).split("#")[-1])
        m["invented_terms"] = len(invented_terms)

        # functional-property violations
        for prop in schema["functional"]:
            counts = collections.Counter(s for s, _, _ in g.triples((None, prop, None)))
            m["functional_violations"] += sum(v - 1 for v in counts.values() if v > 1)

        # Malformed typed literals -- e.g. "7 years and 23 days"^^xsd:duration.
        # Scoped to datatypes rdflib can actually adjudicate: a custom datatype
        # the ontology declares (echr:PartialDate) has no parser, and rdflib
        # also returns None for perfectly valid xsd:gYearMonth. Counting either
        # as malformed manufactures defects that are not there.
        for _, _, o in g:
            dt = o.datatype if isinstance(o, RDFLiteral) else None
            if dt is None or dt in schema["datatypes"] or dt in UNPARSED_XSD:
                continue
            try:
                bad = o.value is None
            except Exception:  # noqa: BLE001
                bad = True
            if bad:
                malformed_durations.add(f"{o} ({str(dt).split('#')[-1]})")
        m["malformed_literals"] = len(malformed_durations)

        procs = set(g.subjects(RDF.type, ECHR.DomesticProceeding))
        m["proc_no_court"] += sum(1 for p in procs if (p, ECHR.hasCourt, None) not in g)
        m["proc_no_outcome"] += sum(
            1 for p in procs if (p, ECHR.hasOutcome, None) not in g
        )
        m["proc_no_date"] += sum(
            1 for p in procs if (p, ECHR.hasDecisionDate, None) not in g
        )
        m["proc_no_quote"] += sum(
            1 for p in procs if (p, ECHR.hasSupportingQuote, None) not in g
        )

        # False merges that no functional-property check can see: rdfs:label is
        # not owl:FunctionalProperty, so one node carrying five court names is
        # schema-legal and still asserts that five distinct courts are one.
        for node in set(g.subjects(RDF.type, None)):
            if len(set(g.objects(node, RDFS.label))) > 1:
                m["multi_label_nodes"] += 1
        m["authorities_without_name"] += sum(
            1
            for a in g.subjects(RDF.type, ECHR.DomesticAuthority)
            if (a, ECHR.hasAuthorityName, None) not in g
        )

        # duplicate authority names (the old report's identity metric)
        names = collections.Counter(
            re.sub(r"[^a-z0-9]+", " ", str(o).lower()).strip()
            for o in g.objects(None, ECHR.hasAuthorityName)
        )
        m["duplicate_authority_names"] += sum(v - 1 for v in names.values() if v > 1)

        # asymmetry / self-loop violations on followsProceeding
        for s, _, o in g.triples((None, ECHR.followsProceeding, None)):
            if s == o:
                m["follows_self_loops"] += 1
            if (o, ECHR.followsProceeding, s) in g:
                m["follows_2cycles"] += 1

        comps, singles, share = components(g)
        per_case_components.append(comps)
        shares.append(share)
        m["singletons"] += singles

    m["components_per_case"] = (
        round(sum(per_case_components) / len(per_case_components), 1)
        if per_case_components
        else 0
    )
    m["largest_component_share"] = (
        round(sum(shares) / len(shares), 2) if shares else 0.0
    )
    m["_invented"] = sorted(invented_terms)
    m["_malformed"] = sorted(malformed_durations)
    return dict(m)


def collect(experiment_dir: Path, stage: str) -> dict[str, dict]:
    schema = load_schema()
    out = {}
    for model_dir in sorted(p for p in experiment_dir.iterdir() if p.is_dir()):
        facts_dir = model_dir / stage
        if not facts_dir.is_dir():
            facts_dir = model_dir / "raw"
        if not facts_dir.is_dir():
            continue
        paths = sorted(
            facts_dir.glob("*.facts.ttl"),
            key=lambda p: int(mm.group(1)) if (mm := LINE_RE.search(p.name)) else 0,
        )
        if paths:
            out[model_dir.name] = measure(paths, schema)
    return out


ROWS = [
    ("Volume and structure", None),
    ("triples", "triples"),
    ("typed nodes", "typed_nodes"),
    ("DomesticProceeding", "class:DomesticProceeding"),
    ("DomesticAuthority", "class:DomesticAuthority"),
    ("CaseDocument", "class:CaseDocument"),
    ("Delay module", None),
    ("Adjournment", "class:Adjournment"),
    ("InactivityPeriod", "class:InactivityPeriod"),
    ("DelayAttribution", "class:DelayAttribution"),
    ("Link density and evidence", None),
    ("hasCourt", "prop:hasCourt"),
    ("followsProceeding", "prop:followsProceeding"),
    ("hasOutcome", "prop:hasOutcome"),
    ("hasInstanceLevel", "prop:hasInstanceLevel"),
    ("hasOutcomeDirection", "prop:hasOutcomeDirection"),
    ("supporting quotes", "prop:hasSupportingQuote"),
    ("proceedings w/o court", "proc_no_court"),
    ("proceedings w/o outcome", "proc_no_outcome"),
    ("proceedings w/o date", "proc_no_date"),
    ("proceedings w/o quote", "proc_no_quote"),
    ("Conformance and hygiene", None),
    ("invented echr: terms", "invented_terms"),
    ("functional violations", "functional_violations"),
    ("malformed literals", "malformed_literals"),
    ("duplicate authority names", "duplicate_authority_names"),
    ("multi-label nodes (false merge)", "multi_label_nodes"),
    ("authorities w/o hasAuthorityName", "authorities_without_name"),
    ("followsProceeding self-loops", "follows_self_loops"),
    ("followsProceeding 2-cycles", "follows_2cycles"),
    ("blank nodes", "blank_nodes"),
    ("Network topology", None),
    ("components per case", "components_per_case"),
    ("singleton nodes", "singletons"),
    ("largest component share", "largest_component_share"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--experiment-dir", type=Path, required=True)
    ap.add_argument("--compare-dir", type=Path, default=None)
    ap.add_argument("--stage", default="repaired", choices=("repaired", "raw"))
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    main_set = collect(args.experiment_dir, args.stage)
    cmp_set = collect(args.compare_dir, args.stage) if args.compare_dir else {}

    cols = [(f"{k}", v) for k, v in main_set.items()]
    cols += [(f"OLD/{k}", v) for k, v in cmp_set.items()]

    header = "| metric | " + " | ".join(c[0] for c in cols) + " |"
    print(f"\n{relative(args.experiment_dir)}  (stage: {args.stage})\n")
    print(header)
    print("|---|" + "---:|" * len(cols))
    for label, key in ROWS:
        if key is None:
            print(f"| **{label}** |" + " |" * len(cols))
            continue
        cells = " | ".join(str(c[1].get(key, 0)) for c in cols)
        print(f"| {label} | {cells} |")

    for name, data in main_set.items():
        if data["_invented"]:
            print(f"\n{name} invented terms: {', '.join(data['_invented'])}")
        if data["_malformed"]:
            print(f"{name} malformed literals: {data['_malformed']}")

    if args.json:
        args.json.write_text(json.dumps({**main_set}, indent=2, default=str))
        print(f"\nwrote {relative(args.json)}")


if __name__ == "__main__":
    main()
