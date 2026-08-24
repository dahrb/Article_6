"""
validate_shapes.py
-------------------
Runs the static SHACL gatekeeper (``ontology/echr-shapes.ttl``) over extracted
facts graphs and reports functional-property clobbering, invented vocabulary
terms, and missing evidence anchors -- the defect classes named in
ontology/extraction_quality_report.md and extraction_fixes_evaluation.md.

This is the static counterpart to validate_source_quotes.py, which checks
echr:hasSupportingQuote against each document's own text and cannot be static
shapes for that reason -- the legal set of values is a property of the
document, not the schema. This validator needs no source text: everything it
checks is a property of the graph alone.

Usage:
  uv run python -m art6.ontology.validate_shapes \\
      --facts-dir results/experiment_ttl_20260818_161537/gemma4/repaired

  # every model in an experiment directory, one summary table
  uv run python -m art6.ontology.validate_shapes \\
      --experiment-dir results/experiment_ttl_20260818_161537
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import OWL, RDF, RDFS, Graph, Namespace, URIRef

from art6.paths import REPO_ROOT, relative

SH = Namespace("http://www.w3.org/ns/shacl#")
SHAPES_PATH = REPO_ROOT / "ontology" / "echr-shapes.ttl"
ONTOLOGY_TTL = Path(
    os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr.ttl")
)
ECHR_NS = "https://growgraph.dev/echr#"

# Turtle long strings are quoted with ''' throughout this template so they
# never collide with the Python triple quotes wrapping them.
_UNDEFINED_TERM_TEMPLATE = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix gen: <https://growgraph.dev/echr-shapes/generated#> .

gen:UndefinedTermShape
    a sh:NodeShape ;
    sh:target [
        a sh:SPARQLTarget ;
        sh:select '''SELECT ?this WHERE { ?this ?p ?o . FILTER(isIRI(?this)) }''' ;
    ] ;
    sh:sparql [
        sh:message "predicate {?value} is not a term the ontology defines - invented vocabulary" ;
        sh:severity sh:Violation ;
        sh:select '''
            SELECT $this ?value WHERE {
                $this ?value ?o .
                FILTER(STRSTARTS(STR(?value), "__NS__"))
                FILTER(?value NOT IN (__ALLOWED__))
            }
        ''' ;
    ] ;
    sh:sparql [
        sh:message "object {?value} is not a term the ontology defines - invented vocabulary" ;
        sh:severity sh:Violation ;
        sh:select '''
            SELECT $this ?value WHERE {
                $this ?p ?value .
                FILTER(isIRI(?value))
                FILTER(STRSTARTS(STR(?value), "__NS__"))
                FILTER(?value NOT IN (__ALLOWED__))
            }
        ''' ;
    ] .
"""


@lru_cache(maxsize=1)
def defined_terms() -> frozenset[str]:
    """Every echr: term the ontology actually defines.

    Classes, properties, declared datatypes, and the named individuals inside
    every owl:oneOf enumeration. Anything else in the echr: namespace is
    invented vocabulary. Mirrors repair_facts.ontology_terms(); both read the
    live ontology, so a schema edit cannot leave a frozen allow-list behind.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    terms: set[str] = set()
    for kind in (OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, RDFS.Datatype):
        terms |= {str(s) for s in g.subjects(RDF.type, kind) if isinstance(s, URIRef)}
    for lst in g.objects(None, OWL.oneOf):
        cur = lst
        while cur and cur != RDF.nil:
            for first in g.objects(cur, RDF.first):
                if isinstance(first, URIRef):
                    terms.add(str(first))
            cur = next(g.objects(cur, RDF.rest), None)
    return frozenset(t for t in terms if t.startswith(ECHR_NS))


def find_undefined_terms(graph: Graph) -> list[tuple[str, str, str]]:
    """Invented echr: vocabulary in `graph`, as (focus, path, message) rows.

    A pure-Python twin of the two SPARQL constraints in
    `undefined_term_shape()`, and the reason it exists is cost, not taste.
    Every shape in echr-shapes.ttl is Core SHACL; those two constraints are the
    only SHACL-AF in the whole shapes graph, and running pyshacl with
    `advanced=True` to reach them is what made SHACL the repair pass's dominant
    expense. Measured 2026-08-24 over 20 documents: 115.5s with advanced=True
    against 0.2s with it off -- a 360x multiplier to evaluate two filters that
    are a set membership test.

    Both this and the SPARQL template read `defined_terms()`, so they cannot
    disagree about what the ontology defines. Verified equivalent across every
    document of the 2026-08-23 sweep.
    """
    allowed = defined_terms()
    rows: list[tuple[str, str, str]] = []
    for subj, pred, obj in graph:
        if str(pred).startswith(ECHR_NS) and str(pred) not in allowed:
            rows.append(
                (
                    str(subj),
                    str(pred),
                    (
                        f"predicate {pred} is not a term the ontology "
                        "defines - invented vocabulary"
                    ),
                )
            )
        if (
            isinstance(obj, URIRef)
            and str(obj).startswith(ECHR_NS)
            and str(obj) not in allowed
        ):
            rows.append(
                (
                    str(subj),
                    "",
                    (
                        f"object {obj} is not a term the ontology defines "
                        "- invented vocabulary"
                    ),
                )
            )
    return sorted(set(rows))


def undefined_term_shape() -> Graph:
    """A SHACL shape flagging any echr: term the ontology does not define.

    Generated from the ontology rather than written into echr-shapes.ttl,
    because a hand-maintained allow-list of legal terms is a copy: it goes
    stale the moment the schema moves, and a stale allow-list either waves
    through invented vocabulary or rejects legitimate new terms. The ontology
    is the only source of truth, so the list is derived from it on every run.

    Two SPARQL constraints, because invented vocabulary appears in both
    positions and the static shapes can only see the second:

      - PREDICATE: an invented property (echr:hasGuardianshipStatus). No sh:in
        or sh:class shape can catch this -- shapes constrain the values of
        properties they already name, so a property nobody declared is never
        validated at all.
      - OBJECT: an invented individual (echr:TypeGuardianship, seen 10x on L1
        in the 2026-08-19 verification run) or an invented class in rdf:type
        position. Per-property sh:in lists catch this only for the handful of
        properties that have one; this catches it everywhere.
    """
    # SPARQL's IN takes a comma-separated ExpressionList; space-separating the
    # IRIs parses as far as the first one and then fails on the rest.
    allowed = ", ".join(f"<{term}>" for term in sorted(defined_terms()))
    ttl = _UNDEFINED_TERM_TEMPLATE.replace("__NS__", ECHR_NS).replace(
        "__ALLOWED__", allowed
    )
    g = Graph()
    g.parse(data=ttl, format="turtle")
    return g


def load_shapes(
    shapes_path: Path | None = None, *, include_undefined_term_shape: bool = True
) -> Graph:
    """The static shapes file plus the generated undefined-term shape.

    Every consumer goes through here rather than parsing SHAPES_PATH directly,
    so the vocabulary check is never silently absent from a validation run.

    `include_undefined_term_shape=False` returns Core-SHACL-only shapes, for a
    caller that runs `find_undefined_terms()` itself and can therefore validate
    with `advanced=False`. That is not a way to skip the vocabulary check -- it
    is a way to run it 360x faster; see `find_undefined_terms`.
    """
    g = Graph()
    g.parse(shapes_path or SHAPES_PATH, format="turtle")
    if include_undefined_term_shape:
        g += undefined_term_shape()
    return g


@dataclass
class FileReport:
    path: Path
    conforms: bool
    violations: int
    warnings: int
    text: str


def check_file(facts_path: Path, shapes_graph: Graph) -> FileReport:
    data_graph = Graph()
    data_graph.parse(facts_path, format="turtle")

    conforms, results_graph, text_report = shacl_validate(
        data_graph,
        shacl_graph=shapes_graph,
        advanced=True,
        inference="none",
        abort_on_first=False,
        meta_shacl=False,
    )
    violations = sum(1 for _ in results_graph.subjects(SH.resultSeverity, SH.Violation))
    warnings = sum(1 for _ in results_graph.subjects(SH.resultSeverity, SH.Warning))
    return FileReport(
        path=facts_path,
        conforms=conforms,
        violations=violations,
        warnings=warnings,
        text=text_report,
    )


def run_directory(facts_dir: Path, shapes_graph: Graph) -> list[FileReport]:
    return [
        check_file(facts_path, shapes_graph)
        for facts_path in sorted(facts_dir.glob("*.facts.ttl"))
    ]


def print_table(title: str, reports: list[FileReport]) -> None:
    print(f"\n{title}")
    print(f"  {'file':<28} {'conforms':>9} {'violations':>11} {'warnings':>9}")
    total_v = total_w = 0
    for report in reports:
        print(
            f"  {report.path.name:<28} {report.conforms!s:>9} "
            f"{report.violations:>11} {report.warnings:>9}"
        )
        total_v += report.violations
        total_w += report.warnings
    n_bad = sum(1 for r in reports if not r.conforms)
    print(
        f"  {'TOTAL':<28} {f'{len(reports) - n_bad}/{len(reports)}':>9} "
        f"{total_v:>11} {total_w:>9}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SHACL-validate extracted facts graphs against ontology/echr-shapes.ttl."
    )
    parser.add_argument(
        "--facts-dir", type=Path, help="Directory of *.facts.ttl files."
    )
    parser.add_argument(
        "--experiment-dir",
        type=Path,
        help="An experiment directory. Validates <model>/<stage> for every model in it.",
    )
    parser.add_argument(
        "--stage",
        choices=("repaired", "raw"),
        default="repaired",
        help="With --experiment-dir, which stage to validate (default: repaired).",
    )
    parser.add_argument(
        "--shapes",
        type=Path,
        default=SHAPES_PATH,
        help=f"Shapes graph to validate against (default: {relative(SHAPES_PATH)}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full pyshacl text report per file.",
    )
    parser.add_argument(
        "--fail-on",
        type=int,
        default=None,
        help="Exit non-zero when total violations (not warnings) exceed this count.",
    )
    args = parser.parse_args()

    if not args.facts_dir and not args.experiment_dir:
        parser.error("one of --facts-dir or --experiment-dir is required")

    shapes_graph = load_shapes(args.shapes)

    jobs: list[tuple[str, Path]] = []
    if args.experiment_dir:
        for model_dir in sorted(p for p in args.experiment_dir.iterdir() if p.is_dir()):
            facts_dir = model_dir / args.stage
            if not facts_dir.is_dir():
                facts_dir = model_dir / "raw"
            if facts_dir.is_dir():
                jobs.append((model_dir.name, facts_dir))
        if not jobs:
            raise SystemExit(
                f"no model directories found under {relative(args.experiment_dir)}"
            )
    else:
        jobs.append((args.facts_dir.name, args.facts_dir))

    total_violations = 0
    for label, facts_dir in jobs:
        reports = run_directory(facts_dir, shapes_graph)
        if not reports:
            print(f"\n{label}: no *.facts.ttl files in {relative(facts_dir)}")
            continue
        print_table(f"{label}  ({relative(facts_dir)})", reports)
        total_violations += sum(r.violations for r in reports)
        if args.verbose:
            for report in reports:
                if not report.conforms:
                    print(f"\n--- {relative(report.path)} ---\n{report.text}")

    if args.fail_on is not None and total_violations > args.fail_on:
        raise SystemExit(
            f"\n{total_violations} SHACL violations exceeds --fail-on {args.fail_on}"
        )


if __name__ == "__main__":
    main()
