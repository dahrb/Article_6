"""
validate_source_paragraphs.py
-----------------------------
SHACL check that every ``echr:hasSourceParagraph`` anchor points at a paragraph
that actually exists in the document the facts were extracted from.

Motivation (see ontology/extraction_quality_report.md, fix #4): gemma4 asserted
27 paragraph anchors on L7 (Sawoniuk), a decision whose text carries no numbered
paragraphs at all. Nothing in the pipeline noticed. Quote fidelity is checked by
substring match; paragraph anchors were checked by nothing.

HOW IT WORKS
------------
The ontology cannot state the constraint, because the legal set of paragraph
numbers is a property of the *document*, not of the schema. So the shapes graph
is GENERATED per document:

    1. read the document text and collect every paragraph number it prints,
    2. emit a NodeShape whose sh:targetSubjectsOf is echr:hasSourceParagraph
       and whose sh:in lists exactly those numbers (as strings),
    3. run pyshacl over the facts graph with that shape.

Paragraph numbering in the HUDOC markdown appears in two forms, both anchored to
the start of a line:

    **12.** On 3 May 2001 the District Court ...        (bold, most documents)
    12. On 3 May 2001 the District Court ...            (plain, some documents)

Both are collected. Sub-headings reuse the same bold form ("**1.** Provisions of
the placement agreement"), which makes the allowed set slightly PERMISSIVE --
deliberately so. This check exists to catch fabrication, and a validator that
invents violations is worse than none.

THREE KINDS OF FAILURE, REPORTED SEPARATELY
-------------------------------------------
``unnumbered``  The document prints no paragraph numbers at all, so every anchor
                on it is fabricated. This is the L7/L8 case and the strongest
                signal the check produces.
``out_of_range`` The anchor is outside [min, max] of what the document prints --
                a number the model made up.
``gap``         The anchor is inside the range but that paragraph is not in this
                text. Usually the paragraph was real in the full judgment and
                landed in the ``law``/``legal_framework`` HUDOC field, which the
                test-set build drops. Still unanchorable in what the model was
                shown, but a weaker signal than the other two.
``malformed``   Not a bare integer at all ("paragraph 12", "12-14", "§ 12").

Usage:
  uv run python -m art6.ontology.validate_source_paragraphs \\
      --facts-dir results/experiment_ttl_20260818_161537/gemma4/repaired \\
      --input-json results/experiment_ttl_20260818_161537/gemma4/input.json

  # every model in an experiment directory, one summary table
  uv run python -m art6.ontology.validate_source_paragraphs \\
      --experiment-dir results/experiment_ttl_20260818_161537

  # keep the generated shapes for inspection / reuse in CI
  uv run python -m art6.ontology.validate_source_paragraphs ... --write-shapes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import Graph, Namespace

from art6.paths import relative

ECHR = Namespace("https://growgraph.dev/echr#")
SH = Namespace("http://www.w3.org/ns/shacl#")

# Paragraph markers, both anchored at line start. The plain form requires a
# following capital (or an opening quote) so that dates, sums of money and
# list items inside a paragraph are not mistaken for paragraph numbers.
PARAGRAPH_PATTERNS = (
    re.compile(r"(?m)^\*\*(\d+)\.\*\*"),
    re.compile(r"(?m)^(\d+)\.\s+(?=[A-Z“А-Я])"),
)

# `input.L7.facts.ttl` -> 7. The line number is OntoCast's own naming: it turns
# each line of the input .jsonl into one document and names outputs after it.
LINE_NUMBER_RE = re.compile(r"\.L(\d+)\.facts\.ttl$")


def document_paragraphs(text: str) -> set[int]:
    """Every paragraph number the document actually prints."""
    numbers: set[int] = set()
    for pattern in PARAGRAPH_PATTERNS:
        numbers |= {int(match.group(1)) for match in pattern.finditer(text)}
    return numbers


def build_shapes_graph(paragraphs: set[int], *, doc_label: str) -> Graph:
    """A SHACL shapes graph pinning hasSourceParagraph to ``paragraphs``.

    Two shapes rather than one, so the report distinguishes "not a paragraph
    number at all" from "a paragraph number this document does not have".

    Args:
        paragraphs: Paragraph numbers the document prints. May be empty, in
            which case ``sh:in ()`` rejects every anchor -- exactly right for a
            document with no numbering.
        doc_label: Used in the shape's message so a merged report stays readable.
    """
    graph = Graph()
    graph.bind("sh", SH)
    graph.bind("echr", ECHR)
    graph.bind("xsd", "http://www.w3.org/2001/XMLSchema#")

    allowed = " ".join(f'"{number}"' for number in sorted(paragraphs))
    known = (
        f"{min(paragraphs)}-{max(paragraphs)} ({len(paragraphs)} numbered paragraphs)"
        if paragraphs
        else "NONE -- this document prints no numbered paragraphs"
    )
    graph.parse(
        data=f"""
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix echr: <https://growgraph.dev/echr#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

<urn:art6:shape:sourceParagraphShape> a sh:NodeShape ;
    sh:targetSubjectsOf echr:hasSourceParagraph ;
    sh:property [
        sh:path echr:hasSourceParagraph ;
        sh:pattern "^[0-9]+$" ;
        sh:severity sh:Violation ;
        sh:message "hasSourceParagraph must be a bare paragraph number, not a \
range, a section sign or prose ({doc_label})" ;
    ] ;
    sh:property [
        sh:path echr:hasSourceParagraph ;
        sh:in ( {allowed} ) ;
        sh:severity sh:Violation ;
        sh:message "hasSourceParagraph anchors a paragraph absent from the \
source text; this document prints {known} ({doc_label})" ;
    ] .
""",
        format="turtle",
    )
    return graph


@dataclass
class FileReport:
    """Outcome for one facts file."""

    path: Path
    line_number: int | None
    case_id: str | None
    document_paragraphs: set[int]
    anchors: list[str] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)
    unnumbered: list[str] = field(default_factory=list)
    out_of_range: list[str] = field(default_factory=list)
    gap: list[str] = field(default_factory=list)
    shacl_conforms: bool = True
    shacl_text: str = ""

    @property
    def bad(self) -> int:
        return len(self.malformed) + len(self.unnumbered) + len(self.out_of_range)

    @property
    def flagged(self) -> int:
        return self.bad + len(self.gap)

    def as_dict(self) -> dict:
        return {
            "file": str(relative(self.path)),
            "line_number": self.line_number,
            "case_id": self.case_id,
            "document_paragraph_count": len(self.document_paragraphs),
            "document_paragraph_range": (
                [min(self.document_paragraphs), max(self.document_paragraphs)]
                if self.document_paragraphs
                else None
            ),
            "anchors_asserted": len(self.anchors),
            "violations": {
                "malformed": sorted(set(self.malformed)),
                "unnumbered": sorted(set(self.unnumbered)),
                "out_of_range": sorted(set(self.out_of_range)),
                "gap": sorted(set(self.gap)),
            },
            "counts": {
                "malformed": len(self.malformed),
                "unnumbered": len(self.unnumbered),
                "out_of_range": len(self.out_of_range),
                "gap": len(self.gap),
                "hard_violations": self.bad,
                "flagged_total": self.flagged,
            },
            "shacl_conforms": self.shacl_conforms,
        }


def classify(anchor: str, paragraphs: set[int]) -> str | None:
    """Which failure bucket ``anchor`` falls into, or None when it is valid."""
    stripped = anchor.strip()
    if not re.fullmatch(r"\d+", stripped):
        return "malformed"
    if not paragraphs:
        return "unnumbered"
    number = int(stripped)
    if number in paragraphs:
        return None
    if number < min(paragraphs) or number > max(paragraphs):
        return "out_of_range"
    return "gap"


def check_file(
    facts_path: Path,
    text: str,
    *,
    case_id: str | None,
    line_number: int | None,
    write_shapes: bool,
) -> FileReport:
    """Validate one facts graph against the paragraphs its document prints."""
    paragraphs = document_paragraphs(text)
    report = FileReport(
        path=facts_path,
        line_number=line_number,
        case_id=case_id,
        document_paragraphs=paragraphs,
    )

    data_graph = Graph()
    data_graph.parse(facts_path, format="turtle")

    report.anchors = [
        str(value)
        for _, _, value in data_graph.triples((None, ECHR.hasSourceParagraph, None))
    ]
    for anchor in report.anchors:
        bucket = classify(anchor, paragraphs)
        if bucket is not None:
            getattr(report, bucket).append(anchor)

    shapes_graph = build_shapes_graph(paragraphs, doc_label=case_id or facts_path.name)
    if write_shapes:
        shapes_path = facts_path.with_suffix(".paragraphs.shapes.ttl")
        shapes_path.write_text(
            shapes_graph.serialize(format="turtle"), encoding="utf-8"
        )

    conforms, _, text_report = shacl_validate(
        data_graph,
        shacl_graph=shapes_graph,
        advanced=False,
        inference="none",
        abort_on_first=False,
        meta_shacl=False,
    )
    report.shacl_conforms = conforms
    report.shacl_text = text_report
    return report


def load_records(input_json: Path) -> list[dict]:
    records = json.loads(input_json.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise SystemExit(f"{relative(input_json)} is not a JSON array of records")
    return records


def run_directory(
    facts_dir: Path,
    input_json: Path,
    *,
    write_shapes: bool,
    write_json: bool,
) -> list[FileReport]:
    """Validate every ``*.facts.ttl`` directly inside ``facts_dir``."""
    records = load_records(input_json)
    reports: list[FileReport] = []

    for facts_path in sorted(
        facts_dir.glob("*.facts.ttl"),
        key=lambda p: int(m.group(1)) if (m := LINE_NUMBER_RE.search(p.name)) else 0,
    ):
        match = LINE_NUMBER_RE.search(facts_path.name)
        if match is None:
            print(
                f"  skip (no .L<n>. in name): {relative(facts_path)}", file=sys.stderr
            )
            continue
        line_number = int(match.group(1))
        if not 1 <= line_number <= len(records):
            print(
                f"  skip (L{line_number} beyond {len(records)} records): "
                f"{relative(facts_path)}",
                file=sys.stderr,
            )
            continue
        record = records[line_number - 1]
        report = check_file(
            facts_path,
            record.get("text", ""),
            case_id=record.get("case_id"),
            line_number=line_number,
            write_shapes=write_shapes,
        )
        reports.append(report)
        if write_json:
            out = facts_path.with_suffix(".paragraphs.json")
            out.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return reports


def print_table(title: str, reports: list[FileReport]) -> None:
    print(f"\n{title}")
    print(
        f"  {'file':<28} {'§ in doc':>9} {'anchors':>8} {'malf':>5} "
        f"{'unnum':>6} {'range':>6} {'gap':>5}"
    )
    totals = {
        "anchors": 0,
        "malformed": 0,
        "unnumbered": 0,
        "out_of_range": 0,
        "gap": 0,
    }
    for report in reports:
        span = (
            f"{min(report.document_paragraphs)}-{max(report.document_paragraphs)}"
            if report.document_paragraphs
            else "NONE"
        )
        print(
            f"  {report.path.name:<28} {span:>9} {len(report.anchors):>8} "
            f"{len(report.malformed):>5} {len(report.unnumbered):>6} "
            f"{len(report.out_of_range):>6} {len(report.gap):>5}"
        )
        totals["anchors"] += len(report.anchors)
        for key in ("malformed", "unnumbered", "out_of_range", "gap"):
            totals[key] += len(getattr(report, key))
    print(
        f"  {'TOTAL':<28} {'':>9} {totals['anchors']:>8} "
        f"{totals['malformed']:>5} {totals['unnumbered']:>6} "
        f"{totals['out_of_range']:>6} {totals['gap']:>5}"
    )
    hard = totals["malformed"] + totals["unnumbered"] + totals["out_of_range"]
    rate = hard / totals["anchors"] if totals["anchors"] else 0.0
    print(f"  hard violations: {hard}/{totals['anchors']} ({rate:.1%} of anchors)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SHACL-check echr:hasSourceParagraph against the source text."
    )
    parser.add_argument(
        "--facts-dir", type=Path, help="Directory of *.facts.ttl files to validate."
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        help=(
            "The JSON array of {case_id, text} records the facts were extracted "
            "from. Defaults to input.json beside --facts-dir's parent."
        ),
    )
    parser.add_argument(
        "--experiment-dir",
        type=Path,
        help=(
            "An experiment directory. Validates <model>/repaired (falling back "
            "to <model>/raw) for every model in it, against that model's "
            "input.json."
        ),
    )
    parser.add_argument(
        "--stage",
        choices=("repaired", "raw"),
        default="repaired",
        help="With --experiment-dir, which stage to validate (default: repaired).",
    )
    parser.add_argument(
        "--write-shapes",
        action="store_true",
        help="Also write the generated <stem>.paragraphs.shapes.ttl per document.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write the per-file <stem>.paragraphs.json report.",
    )
    parser.add_argument(
        "--fail-on",
        type=int,
        default=None,
        help=(
            "Exit non-zero when hard violations (malformed + unnumbered + "
            "out_of_range) exceed this count. Gaps never count."
        ),
    )
    args = parser.parse_args()

    if not args.facts_dir and not args.experiment_dir:
        parser.error("one of --facts-dir or --experiment-dir is required")

    jobs: list[tuple[str, Path, Path]] = []
    if args.experiment_dir:
        for model_dir in sorted(p for p in args.experiment_dir.iterdir() if p.is_dir()):
            facts_dir = model_dir / args.stage
            if not facts_dir.is_dir():
                facts_dir = model_dir / "raw"
            input_json = model_dir / "input.json"
            if not facts_dir.is_dir() or not input_json.exists():
                continue
            jobs.append((model_dir.name, facts_dir, input_json))
        if not jobs:
            raise SystemExit(
                f"no model directories found under {relative(args.experiment_dir)}"
            )
    else:
        input_json = args.input_json or args.facts_dir.parent / "input.json"
        if not input_json.exists():
            raise SystemExit(
                f"input records not found: {relative(input_json)} "
                "(pass --input-json explicitly)"
            )
        jobs.append((args.facts_dir.name, args.facts_dir, input_json))

    hard_total = 0
    for label, facts_dir, input_json in jobs:
        reports = run_directory(
            facts_dir,
            input_json,
            write_shapes=args.write_shapes,
            write_json=not args.no_json,
        )
        if not reports:
            print(f"\n{label}: no *.facts.ttl files in {relative(facts_dir)}")
            continue
        print_table(f"{label}  ({relative(facts_dir)})", reports)
        hard_total += sum(report.bad for report in reports)

    if args.fail_on is not None and hard_total > args.fail_on:
        raise SystemExit(
            f"\n{hard_total} hard hasSourceParagraph violations exceeds "
            f"--fail-on {args.fail_on}"
        )


if __name__ == "__main__":
    main()
