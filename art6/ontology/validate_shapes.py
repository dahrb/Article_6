"""
validate_shapes.py
-------------------
Runs the static SHACL gatekeeper (``ontology/echr-shapes.ttl``) over extracted
facts graphs and reports functional-property clobbering, invented vocabulary
terms, and missing evidence anchors -- the defect classes named in
ontology/extraction_quality_report.md and extraction_fixes_evaluation.md.

This is the static counterpart to validate_source_paragraphs.py, which checks
echr:hasSourceParagraph against each document's own text and cannot be static
shapes for that reason. This validator needs no source text: everything it
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
from dataclasses import dataclass
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import Graph, Namespace

from art6.paths import REPO_ROOT, relative

SH = Namespace("http://www.w3.org/ns/shacl#")
SHAPES_PATH = REPO_ROOT / "ontology" / "echr-shapes.ttl"


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

    shapes_graph = Graph()
    shapes_graph.parse(args.shapes, format="turtle")

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
