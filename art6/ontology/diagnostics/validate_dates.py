"""
validate_dates.py
------------------
SHACL check that every date asserted in a facts graph is actually *stated* in
the document the facts were extracted from.

HOW A DATE IS VERIFIED
----------------------
A date does not appear in a judgment as an ISO string; it appears as "3
February 2009", "3 Feb 2009", "3.2.2009", or "3 février 2009". So the check
runs in the opposite direction from the quote checker: RENDER the asserted
date every way this corpus plausibly writes it, and look for any of those
renderings in the normalized source text.

Three outcomes, not two:

    exact       a full rendering including the year is present
                ("3 February 2009")
    loose       the day-and-month rendering is present and the year appears
                somewhere in the document, but never adjacently
    unverified  the day-and-month combination does not appear at all

The `loose` tier exists because of a real and common construction in this
corpus: a date range writes the year once at the end --

    "from 31 May to 15 June 2005"

-- so 2005-05-31 is rendered "31 May" with no year beside it. Treating that as
a fabricated date would bury the real errors under false positives, which is
exactly what makes a validator get switched off. Only `unverified` is a SHACL
violation; `loose` is counted and reported so the rate stays visible.

The residual false-positive risk is a date the model correctly *inferred*
rather than read (a judgment saying "three weeks later" and the model
computing the date). Those are rare, and a small handful of legitimate
disagreements is the accepted cost of catching the systematic ones.

Usage:
  uv run python -m art6.ontology.diagnostics.validate_dates \\
      --facts-dir results/experiment_full3arm_20260820/art6_gemma4_nochunk_mv1/repaired \\
      --input-jsonl results/experiment_full3arm_20260820/input.jsonl

  # every run in an experiment directory, one summary table
  uv run python -m art6.ontology.diagnostics.validate_dates \\
      --experiment-dir results/experiment_full3arm_20260820

  # keep the generated shapes for inspection / reuse in CI
  uv run python -m art6.ontology.diagnostics.validate_dates ... --write-shapes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pyshacl import validate as shacl_validate
from rdflib import RDF, RDFS, BNode, Graph, Literal, Namespace, URIRef

from art6.ontology.diagnostics.validate_source_quotes import LINE_NUMBER_RE, normalize
from art6.paths import REPO_ROOT, relative

ECHR = Namespace("https://growgraph.dev/echr#")
SH = Namespace("http://www.w3.org/ns/shacl#")

ONTOLOGY_TTL = Path(
    os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr.ttl")
)

ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
ISO_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
ISO_YEAR_RE = re.compile(r"^(\d{4})$")

# English and French, because the corpus is both. Index 1..12; each entry is
# every spelling of that month the corpus uses, long form first.
MONTH_NAMES: dict[int, tuple[str, ...]] = {
    1: ("January", "Jan", "janvier"),
    2: ("February", "Feb", "février", "fevrier"),
    3: ("March", "Mar", "mars"),
    4: ("April", "Apr", "avril"),
    5: ("May", "mai"),
    6: ("June", "Jun", "juin"),
    7: ("July", "Jul", "juillet"),
    8: ("August", "Aug", "août", "aout"),
    9: ("September", "Sep", "Sept", "septembre"),
    10: ("October", "Oct", "octobre"),
    11: ("November", "Nov", "novembre"),
    12: ("December", "Dec", "décembre", "decembre"),
}


def _ordinal_suffix(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")


def day_month_renderings(year: int, month: int, day: int) -> list[str]:
    """Every way this corpus writes a day-and-month, WITHOUT the year.

    Kept separate from the year-bearing forms because the `loose` tier needs
    exactly this set -- see the module docstring on "from 31 May to 15 June
    2005", where the day-and-month is present and the year is not beside it.
    """
    out: list[str] = []
    for name in MONTH_NAMES[month]:
        out.append(f"{day} {name}")  # 3 February
        out.append(f"{day:02d} {name}")  # 03 February
        out.append(f"{name} {day}")  # February 3
        out.append(f"{day}{_ordinal_suffix(day)} {name}")  # 3rd February
        if day == 1:
            out.append(f"1er {name}")  # 1er février
    out += [
        f"{day:02d}/{month:02d}",
        f"{day}/{month}",
        f"{day:02d}.{month:02d}",
        f"{day}.{month}",
    ]
    return out


def full_date_renderings(year: int, month: int, day: int) -> list[str]:
    """Every way this corpus writes a complete date, year included."""
    out: list[str] = []
    for stem in day_month_renderings(year, month, day):
        # "February 3" takes a comma before the year in US style; the others
        # simply append it.
        out.append(f"{stem} {year}")
        out.append(f"{stem}, {year}")
    out += [
        f"{year}-{month:02d}-{day:02d}",
        f"{day:02d}/{month:02d}/{year}",
        f"{day}/{month}/{year}",
        f"{day:02d}.{month:02d}.{year}",
        f"{day}.{month}.{year}",
        f"{day:02d}-{month:02d}-{year}",
    ]
    return out


def year_month_renderings(year: int, month: int) -> list[str]:
    out = [f"{name} {year}" for name in MONTH_NAMES[month]]
    out += [f"{month:02d}/{year}", f"{year}-{month:02d}"]
    return out


# A range or conjunction states the month and year ONCE, at the end, leaving
# the first date as a bare day (or a bare month). Both forms are common in this
# corpus and both are correct extractions that a naive adjacency search calls
# fabricated:
#
#     "The third period of leave was authorised from 15 to 25 September 2006"
#         -> 2006-09-15 is stated, but "15 September" never appears
#     "In October and November 1993 the applicants instituted proceedings"
#         -> 1993-10 is stated, but "October 1993" never appears
#
# Both were live false positives on the 2026-08-20 graphs before this existed.
# The connector list covers English and French, hyphen, en dash and em dash.
_RANGE_CONNECTOR = r"(?:to|and|until|through|or|[-–—]|au|et|jusqu'au)"


def bare_day_in_range_pattern(year: int, month: int, day: int) -> re.Pattern[str]:
    """Matches ``15 to 25 September 2006`` when looking for 2006-09-15.

    Deliberately one-directional: only the FIRST date of a range can lose its
    month this way. A trailing bare day ("from 1 September to 15") does not
    occur in this corpus, and matching it would make the check credulous.
    """
    months = "|".join(re.escape(name) for name in MONTH_NAMES[month])
    return re.compile(
        rf"\b{day}\b\s*{_RANGE_CONNECTOR}\s*\d{{1,2}}(?:st|nd|rd|th)?\s+"
        rf"(?:{months})\s+{year}\b",
        re.IGNORECASE,
    )


def bare_month_in_range_pattern(year: int, month: int) -> re.Pattern[str]:
    """Matches ``October and November 1993`` when looking for 1993-10."""
    months = "|".join(re.escape(name) for name in MONTH_NAMES[month])
    other = "|".join(
        re.escape(name) for names in MONTH_NAMES.values() for name in names
    )
    return re.compile(
        rf"\b(?:{months})\s*{_RANGE_CONNECTOR}\s*(?:{other})\s+{year}\b",
        re.IGNORECASE,
    )


@dataclass(frozen=True)
class DateCheck:
    """One asserted date and what the source text says about it."""

    literal: str
    predicate: str
    subject: str
    precision: str  # "date" | "gYearMonth" | "gYear" | "unparsed"
    status: str  # "exact" | "loose" | "unverified" | "unparsed"
    matched: str | None = None


def check_date_literal(
    value: str, normalized_text: str, *, predicate: str, subject: str
) -> DateCheck:
    """Classify one date literal against the document text.

    Case-insensitive throughout: the corpus writes "3 February 2009" and
    validate_source_quotes.normalize() does not fold case, so comparing
    verbatim would miss a rendering differing only in capitalisation.
    """
    haystack = normalized_text.lower()

    def _first_present(candidates: list[str]) -> str | None:
        return next((c for c in candidates if c.lower() in haystack), None)

    if match := ISO_DATE_RE.match(value):
        year, month, day = (int(g) for g in match.groups())
        if not (1 <= month <= 12):
            return DateCheck(value, predicate, subject, "date", "unparsed")
        if hit := _first_present(full_date_renderings(year, month, day)):
            return DateCheck(value, predicate, subject, "date", "exact", hit)
        # Year-detached rendering: the day-and-month is written, and the year
        # is somewhere in the document, just not adjacent. See the docstring.
        if (hit := _first_present(day_month_renderings(year, month, day))) and str(
            year
        ) in haystack:
            return DateCheck(value, predicate, subject, "date", "loose", hit)
        # Month-detached: a bare day opening a range whose month and year come
        # after the connector -- "from 15 to 25 September 2006".
        if found := bare_day_in_range_pattern(year, month, day).search(normalized_text):
            return DateCheck(value, predicate, subject, "date", "loose", found.group(0))
        return DateCheck(value, predicate, subject, "date", "unverified")

    if match := ISO_YEAR_MONTH_RE.match(value):
        year, month = (int(g) for g in match.groups())
        if not (1 <= month <= 12):
            return DateCheck(value, predicate, subject, "gYearMonth", "unparsed")
        if hit := _first_present(year_month_renderings(year, month)):
            return DateCheck(value, predicate, subject, "gYearMonth", "exact", hit)
        # "October and November 1993" states 1993-10 without ever writing
        # "October 1993".
        if found := bare_month_in_range_pattern(year, month).search(normalized_text):
            return DateCheck(
                value, predicate, subject, "gYearMonth", "loose", found.group(0)
            )
        return DateCheck(value, predicate, subject, "gYearMonth", "unverified")

    if match := ISO_YEAR_RE.match(value):
        year = match.group(1)
        if year in haystack:
            return DateCheck(value, predicate, subject, "gYear", "exact", year)
        return DateCheck(value, predicate, subject, "gYear", "unverified")

    # Not a shape this validator can judge. Reported, never counted as a
    # violation -- malformed literals are validate_shapes.py's job, and
    # double-reporting one defect in two tools makes both harder to read.
    return DateCheck(value, predicate, subject, "unparsed", "unparsed")


@lru_cache(maxsize=1)
def date_properties() -> tuple[URIRef, ...]:
    """Every property the ontology ranges over echr:PartialDate.

    Read live rather than hardcoded, for the same reason
    repair_facts.functional_properties() is: adding hasDetentionEndDate to the
    schema must not silently leave it unchecked here.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    return tuple(sorted(g.subjects(RDFS.range, ECHR.PartialDate), key=str))


@dataclass
class FileReport:
    """Outcome for one facts file."""

    path: Path
    line_number: int | None
    case_id: str | None
    checks: list[DateCheck] = field(default_factory=list)
    shacl_conforms: bool = True

    def _of(self, status: str) -> list[DateCheck]:
        return [c for c in self.checks if c.status == status]

    @property
    def exact(self) -> list[DateCheck]:
        return self._of("exact")

    @property
    def loose(self) -> list[DateCheck]:
        return self._of("loose")

    @property
    def unverified(self) -> list[DateCheck]:
        return self._of("unverified")

    @property
    def unparsed(self) -> list[DateCheck]:
        return self._of("unparsed")

    @property
    def bad(self) -> int:
        return len(self.unverified)

    def as_dict(self) -> dict:
        return {
            "file": str(relative(self.path)),
            "line_number": self.line_number,
            "case_id": self.case_id,
            "dates_asserted": len(self.checks),
            "exact": len(self.exact),
            "loose": len(self.loose),
            "unparsed": len(self.unparsed),
            "unverified": [
                {
                    "value": c.literal,
                    "predicate": c.predicate,
                    "subject": c.subject,
                    "precision": c.precision,
                }
                for c in sorted(self.unverified, key=lambda c: (c.subject, c.literal))
            ],
            # Audit trail for the year-detached tier (a date range writing its
            # year once, at the end -- see the module docstring). `matched`
            # names the exact substring the range/conjunction heuristic hit,
            # so a reviewer can tell a genuine year-detached date from a
            # heuristic false positive without re-deriving it from the source.
            "loose_detail": [
                {
                    "value": c.literal,
                    "predicate": c.predicate,
                    "subject": c.subject,
                    "precision": c.precision,
                    "matched": c.matched,
                }
                for c in sorted(self.loose, key=lambda c: (c.subject, c.literal))
            ],
            "shacl_conforms": self.shacl_conforms,
        }


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


def build_shapes_graph(
    verified_by_predicate: dict[URIRef, list[Literal]], *, doc_label: str
) -> Graph:
    """A shapes graph pinning each date property to the values the text states.

    One property shape per date predicate, each sh:in the literals that
    verified for THIS document -- the legal set of values is a property of the
    document, not the schema, exactly as in validate_source_quotes.

    The literals must be the exact rdflib terms taken from the data graph:
    sh:in membership is RDF term equality, and this corpus asserts dates at
    three different datatypes (xsd:date, xsd:gYearMonth, xsd:gYear), so
    echoing back a plain string and re-wrapping it with an assumed datatype
    would silently mismatch every date that is not a full one.
    """
    graph = Graph()
    graph.bind("sh", SH)
    graph.bind("echr", ECHR)

    shape = URIRef("urn:art6:shape:sourceDateShape")
    graph.add((shape, RDF.type, SH.NodeShape))

    for predicate, literals in sorted(
        verified_by_predicate.items(), key=lambda kv: str(kv[0])
    ):
        graph.add((shape, SH.targetSubjectsOf, predicate))
        prop = BNode()
        graph.add((shape, SH.property, prop))
        graph.add((prop, SH.path, predicate))
        graph.add((prop, SH["in"], _rdf_list(graph, literals)))
        graph.add((prop, SH.severity, SH.Violation))
        graph.add(
            (
                prop,
                SH.message,
                Literal(
                    f"the date asserted under {predicate.split('#')[-1]} is not "
                    f"stated anywhere in the source text ({doc_label}) -- neither "
                    f"as a full date nor as a day-and-month within a range"
                ),
            )
        )
    return graph


def check_file(
    facts_path: Path,
    text: str,
    *,
    case_id: str | None,
    line_number: int | None,
    write_shapes: bool,
) -> FileReport:
    """Validate one facts graph's dates against the document it came from."""
    normalized_text = normalize(text)
    report = FileReport(path=facts_path, line_number=line_number, case_id=case_id)

    data_graph = Graph()
    data_graph.parse(facts_path, format="turtle")

    def _curie(term) -> str:
        try:
            return data_graph.namespace_manager.qname(term)
        except Exception:  # noqa: BLE001
            return str(term)

    verified_by_predicate: dict[URIRef, list[Literal]] = {}
    for predicate in date_properties():
        literals = list(data_graph.objects(None, predicate))
        if not literals:
            continue
        verified_by_predicate.setdefault(predicate, [])
        for subject, _, literal in data_graph.triples((None, predicate, None)):
            check = check_date_literal(
                str(literal),
                normalized_text,
                predicate=_curie(predicate),
                subject=_curie(subject),
            )
            report.checks.append(check)
            # `loose` and `unparsed` are allowed through the shape: the first
            # is a real date written year-detached, the second is not this
            # validator's defect to report. Only `unverified` fails.
            if check.status != "unverified":
                verified_by_predicate[predicate].append(literal)

    shapes_graph = build_shapes_graph(
        verified_by_predicate, doc_label=case_id or facts_path.name
    )
    if write_shapes:
        shapes_path = facts_path.with_suffix(".dates.shapes.ttl")
        shapes_path.write_text(
            shapes_graph.serialize(format="turtle"), encoding="utf-8"
        )

    if verified_by_predicate:
        conforms, _, _ = shacl_validate(
            data_graph,
            shacl_graph=shapes_graph,
            advanced=False,
            inference="none",
            abort_on_first=False,
            meta_shacl=False,
        )
        report.shacl_conforms = conforms
    return report


def load_texts(input_path: Path) -> dict[int, dict]:
    """Records keyed by 1-based line number, from .jsonl or .json."""
    if input_path.suffix == ".jsonl":
        records = [
            json.loads(line)
            for line in input_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        records = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise SystemExit(f"{relative(input_path)} is not a JSON array of records")
    return {i: record for i, record in enumerate(records, start=1)}


def run_directory(
    facts_dir: Path,
    input_path: Path,
    *,
    write_shapes: bool,
    write_json: bool,
) -> list[FileReport]:
    """Validate every ``*.facts.ttl`` directly inside ``facts_dir``."""
    records = load_texts(input_path)
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
        record = records.get(line_number)
        if record is None:
            print(
                f"  skip (L{line_number} beyond {len(records)} records): "
                f"{relative(facts_path)}",
                file=sys.stderr,
            )
            continue
        report = check_file(
            facts_path,
            record.get("text", ""),
            case_id=record.get("case_id"),
            line_number=line_number,
            write_shapes=write_shapes,
        )
        reports.append(report)
        if write_json:
            out = facts_path.with_suffix(".dates.json")
            out.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return reports


def print_table(title: str, reports: list[FileReport], *, show_detail: bool) -> None:
    print(f"\n{title}")
    print(
        f"  {'file':<28} {'dates':>6} {'exact':>6} {'loose':>6} "
        f"{'unparsed':>9} {'unverified':>11}"
    )
    totals = dict.fromkeys(("dates", "exact", "loose", "unparsed", "unverified"), 0)
    for report in reports:
        print(
            f"  {report.path.name:<28} {len(report.checks):>6} {len(report.exact):>6} "
            f"{len(report.loose):>6} {len(report.unparsed):>9} "
            f"{len(report.unverified):>11}"
        )
        totals["dates"] += len(report.checks)
        totals["exact"] += len(report.exact)
        totals["loose"] += len(report.loose)
        totals["unparsed"] += len(report.unparsed)
        totals["unverified"] += len(report.unverified)
    print(
        f"  {'TOTAL':<28} {totals['dates']:>6} {totals['exact']:>6} "
        f"{totals['loose']:>6} {totals['unparsed']:>9} {totals['unverified']:>11}"
    )
    rate = totals["unverified"] / totals["dates"] if totals["dates"] else 0.0
    print(
        f"  unverified: {totals['unverified']}/{totals['dates']} ({rate:.1%} of dates)"
    )
    if show_detail:
        for report in reports:
            for check in sorted(report.unverified, key=lambda c: c.subject):
                print(
                    f"    UNVERIFIED {report.path.name} {check.subject} "
                    f"{check.predicate} {check.literal}"
                )
            for check in sorted(report.loose, key=lambda c: c.subject):
                print(
                    f"    loose      {report.path.name} {check.subject} "
                    f"{check.predicate} {check.literal}  (matched {check.matched!r})"
                )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SHACL-check asserted dates against the source text."
    )
    parser.add_argument(
        "--facts-dir", type=Path, help="Directory of *.facts.ttl files to validate."
    )
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        help=(
            "The .jsonl (or .json array) of {case_id, text} records the facts "
            "were extracted from. Defaults to input.jsonl beside --facts-dir's "
            "parent, then input.json."
        ),
    )
    parser.add_argument(
        "--experiment-dir",
        type=Path,
        help=(
            "An experiment directory. Validates <run>/repaired (falling back to "
            "<run>/raw) for every run in it, against the experiment's own "
            "input.jsonl."
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
        help="Also write the generated <stem>.dates.shapes.ttl per document.",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Do not write the per-file <stem>.dates.json report.",
    )
    parser.add_argument(
        "--detail",
        action="store_true",
        help="List every unverified date, not just the counts.",
    )
    parser.add_argument(
        "--fail-on",
        type=int,
        default=None,
        help="Exit non-zero when total unverified dates exceed this count.",
    )
    args = parser.parse_args()

    if not args.facts_dir and not args.experiment_dir:
        parser.error("one of --facts-dir or --experiment-dir is required")

    def _resolve_input(base: Path) -> Path | None:
        for name in ("input.jsonl", "input.json"):
            if (candidate := base / name).exists():
                return candidate
        return None

    jobs: list[tuple[str, Path, Path]] = []
    if args.experiment_dir:
        shared_input = args.input_jsonl or _resolve_input(args.experiment_dir)
        for run_dir in sorted(p for p in args.experiment_dir.iterdir() if p.is_dir()):
            facts_dir = run_dir / args.stage
            if not facts_dir.is_dir():
                facts_dir = run_dir / "raw"
            input_path = _resolve_input(run_dir) or shared_input
            if not facts_dir.is_dir() or input_path is None:
                continue
            jobs.append((run_dir.name, facts_dir, input_path))
        if not jobs:
            raise SystemExit(
                f"no run directories with inputs found under "
                f"{relative(args.experiment_dir)}"
            )
    else:
        input_path = args.input_jsonl or _resolve_input(args.facts_dir.parent)
        if input_path is None or not input_path.exists():
            raise SystemExit(
                f"input records not found beside {relative(args.facts_dir.parent)} "
                "(pass --input-jsonl explicitly)"
            )
        jobs.append((args.facts_dir.name, args.facts_dir, input_path))

    unverified_total = 0
    for label, facts_dir, input_path in jobs:
        reports = run_directory(
            facts_dir,
            input_path,
            write_shapes=args.write_shapes,
            write_json=not args.no_json,
        )
        if not reports:
            print(f"\n{label}: no *.facts.ttl files in {relative(facts_dir)}")
            continue
        print_table(
            f"{label}  ({relative(facts_dir)})", reports, show_detail=args.detail
        )
        unverified_total += sum(report.bad for report in reports)

    if args.fail_on is not None and unverified_total > args.fail_on:
        raise SystemExit(
            f"\n{unverified_total} unverified date(s) exceeds --fail-on {args.fail_on}"
        )


if __name__ == "__main__":
    main()
