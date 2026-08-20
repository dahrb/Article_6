"""
validate_source_quotes.py
--------------------------
SHACL check that every ``echr:hasSupportingQuote`` is actually present in the
document the facts were extracted from.

Motivation: extraction_fixes_evaluation.md cites "quote fidelity ... checked
by substring match" as a benchmark, but no such check exists in this repo --
it referred to a one-off measurement, not a reusable tool. This is the
reusable tool. The legal set of valid values is a property of the *document*,
not the schema, so the SHACL shapes graph is GENERATED per document rather
than written once and shared.

HOW IT WORKS
------------
    1. normalize the document text and every asserted quote the same way
       (curly quotes -> straight, all whitespace/newlines -> single spaces),
    2. a quote elided with an ellipsis ("...", "…", "[...]") is split into
       segments at the ellipsis and each segment checked independently --
       this is the "regex matching style" bit: elision is common in this
       corpus (`"By a judgment ... again ordered a stay"`) and a literal
       whole-string search would reject every one of them as fabricated,
    3. a quote VERIFIES when every one of its segments is a substring of the
       normalized document text,
    4. emit a NodeShape whose sh:targetSubjectsOf is echr:hasSupportingQuote
       and whose sh:in lists exactly the quote literals (unmodified,
       original casing/quote-style) that verified for this document,
    5. run pyshacl over the facts graph with that shape -- any quote not in
       the allowed set is a SHACL violation.

The shapes graph is built with rdflib's graph API rather than string
interpolation, because quote text can itself contain quote characters,
backslashes and newlines that would be unsafe to splice into a Turtle
literal by hand.

Usage:
  uv run python -m art6.ontology.validate_source_quotes \\
      --facts-dir results/experiment_ttl_20260818_161537/gemma4/repaired \\
      --input-json results/experiment_ttl_20260818_161537/gemma4/input.json

  # every model in an experiment directory, one summary table
  uv run python -m art6.ontology.validate_source_quotes \\
      --experiment-dir results/experiment_ttl_20260818_161537

  # keep the generated shapes for inspection / reuse in CI
  uv run python -m art6.ontology.validate_source_quotes ... --write-shapes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef

from art6.paths import relative

ECHR = Namespace("https://growgraph.dev/echr#")
SH = Namespace("http://www.w3.org/ns/shacl#")

ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.+|…|\[\s*\.\.\.\s*\]|\[\s*…\s*\])\s*")
WHITESPACE_RE = re.compile(r"\s+")
CURLY_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})

LINE_NUMBER_RE = re.compile(r"\.L(\d+)\.facts\.ttl$")


def normalize(text: str) -> str:
    """Curly quotes -> straight, all whitespace runs -> single space."""
    return WHITESPACE_RE.sub(" ", text.translate(CURLY_QUOTES)).strip()


def quote_verifies(quote: str, normalized_text: str) -> bool:
    """True when every ellipsis-delimited segment of ``quote`` is a substring
    of ``normalized_text``, after the same normalization."""
    segments = [
        normalize(segment)
        for segment in ELLIPSIS_RE.split(normalize(quote))
        if segment.strip()
    ]
    if not segments:
        return False
    return all(segment in normalized_text for segment in segments)


@dataclass
class FileReport:
    """Outcome for one facts file."""

    path: Path
    line_number: int | None
    case_id: str | None
    quotes: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    shacl_conforms: bool = True
    shacl_text: str = ""

    @property
    def bad(self) -> int:
        return len(self.unverified)

    def as_dict(self) -> dict:
        return {
            "file": str(relative(self.path)),
            "line_number": self.line_number,
            "case_id": self.case_id,
            "quotes_asserted": len(self.quotes),
            "verified": len(self.verified),
            "unverified": sorted(set(self.unverified)),
            "shacl_conforms": self.shacl_conforms,
        }


def build_shapes_graph(verified_quotes: list[Literal], *, doc_label: str) -> Graph:
    """A SHACL shapes graph pinning hasSupportingQuote to ``verified_quotes``.

    Built with the graph API, not Turtle string interpolation: quote text can
    contain characters (quote marks, backslashes, newlines) that are unsafe
    to splice into a literal by hand. ``verified_quotes`` must be the exact
    rdflib Literal terms taken from the data graph -- sh:in membership is RDF
    term equality, and this corpus asserts hasSupportingQuote with a mix of
    "..."@en and "..."^^xsd:string, so echoing back a plain str and
    re-wrapping it with an assumed language tag would silently mismatch half
    the corpus's own valid quotes.
    """
    graph = Graph()
    graph.bind("sh", SH)
    graph.bind("echr", ECHR)

    shape = URIRef("urn:art6:shape:sourceQuoteShape")
    prop = BNode()
    in_list_head = _rdf_list(graph, verified_quotes)

    graph.add((shape, RDF.type, SH.NodeShape))
    graph.add((shape, SH.targetSubjectsOf, ECHR.hasSupportingQuote))
    graph.add((shape, SH.property, prop))
    graph.add((prop, SH.path, ECHR.hasSupportingQuote))
    graph.add((prop, SH["in"], in_list_head))
    graph.add((prop, SH.severity, SH.Violation))
    graph.add(
        (
            prop,
            SH.message,
            Literal(
                f"hasSupportingQuote is not found (even after ellipsis-segment and "
                f"quote-style normalization) in the source text ({doc_label})"
            ),
        )
    )
    return graph


def _rdf_list(graph: Graph, items: list[Literal]) -> URIRef | BNode:
    """Build an RDF collection out of ``items``, preserving each term as-is."""
    if not items:
        return RDF.nil
    head = BNode()
    node = head
    for i, item in enumerate(items):
        graph.add((node, RDF.first, item))
        if i == len(items) - 1:
            graph.add((node, RDF.rest, RDF.nil))
        else:
            nxt = BNode()
            graph.add((node, RDF.rest, nxt))
            node = nxt
    return head


def check_file(
    facts_path: Path,
    text: str,
    *,
    case_id: str | None,
    line_number: int | None,
    write_shapes: bool,
) -> FileReport:
    """Validate one facts graph's quotes against the document it came from."""
    normalized_text = normalize(text)
    report = FileReport(path=facts_path, line_number=line_number, case_id=case_id)

    data_graph = Graph()
    data_graph.parse(facts_path, format="turtle")

    literals = [
        value
        for _, _, value in data_graph.triples((None, ECHR.hasSupportingQuote, None))
    ]
    report.quotes = [str(value) for value in literals]
    verified_literals: list[Literal] = []
    for literal in literals:
        if quote_verifies(str(literal), normalized_text):
            verified_literals.append(literal)
            report.verified.append(str(literal))
        else:
            report.unverified.append(str(literal))

    shapes_graph = build_shapes_graph(
        verified_literals, doc_label=case_id or facts_path.name
    )
    if write_shapes:
        shapes_path = facts_path.with_suffix(".quotes.shapes.ttl")
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
            out = facts_path.with_suffix(".quotes.json")
            out.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return reports


def print_table(title: str, reports: list[FileReport]) -> None:
    print(f"\n{title}")
    print(f"  {'file':<28} {'quotes':>7} {'verified':>9} {'unverified':>11}")
    totals = {"quotes": 0, "verified": 0, "unverified": 0}
    for report in reports:
        print(
            f"  {report.path.name:<28} {len(report.quotes):>7} "
            f"{len(report.verified):>9} {len(report.unverified):>11}"
        )
        totals["quotes"] += len(report.quotes)
        totals["verified"] += len(report.verified)
        totals["unverified"] += len(report.unverified)
    print(
        f"  {'TOTAL':<28} {totals['quotes']:>7} "
        f"{totals['verified']:>9} {totals['unverified']:>11}"
    )
    rate = totals["unverified"] / totals["quotes"] if totals["quotes"] else 0.0
    print(
        f"  unverified: {totals['unverified']}/{totals['quotes']} ({rate:.1%} of quotes)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SHACL-check echr:hasSupportingQuote against the source text."
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
        help="Also write the generated <stem>.quotes.shapes.ttl per document.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write the per-file <stem>.quotes.json report.",
    )
    parser.add_argument(
        "--fail-on",
        type=int,
        default=None,
        help="Exit non-zero when total unverified quotes exceed this count.",
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

    unverified_total = 0
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
        unverified_total += sum(report.bad for report in reports)

    if args.fail_on is not None and unverified_total > args.fail_on:
        raise SystemExit(
            f"\n{unverified_total} unverified hasSupportingQuote values exceeds "
            f"--fail-on {args.fail_on}"
        )


if __name__ == "__main__":
    main()
