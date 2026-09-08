"""Did stage 2 keep the events stage 1 gave it?

Stage 1 emits a NUMBERED event inventory -- e1..eN, each with a verbatim
`what_happened` span. Stage 2 renders that into a graph. Nothing has ever
checked that the graph still contains N events, so an event dropped in
rendering has been indistinguishable from an event the document never had.

That is the one recall question on this pipeline with an EXACT denominator.
"Did we capture everything in the judgment" needs annotation to answer, because
the denominator is a judgement call. "Did we keep the nine events stage 1
claimed" does not: stage 1 states the denominator, so the loss rate is
measurable on every document of every run, with no gold and no model call.

    uv run python -m art6.ontology.check_stage_boundary --run-dir results/ablation_250_mv1

WHAT COUNTS AS KEPT
-------------------
An event is matched when stage 1's `what_happened` span and some graph event's
echr:hasSupportingQuote contain one another (whitespace-normalised, either
direction -- the graph's quote is routinely a truncation of the span, and
occasionally the reverse). No similarity threshold and nothing tunable: a
number that moves when someone retunes a cutoff is not a measurement.

Unmatched splits two ways, and the split is the useful part:

    dropped      nothing in the graph carries this event's quote, date or
                 authority -- stage 2 lost it outright
    paraphrased  the graph has an event with this event's decision date AND
                 authority, but its quote is not stage 1's span

`paraphrased` is not a softer form of `dropped`, it is a different defect:
prompts/facts.txt requires quotes copied verbatim from the input, so a graph
event whose quote is not a substring of the input it was rendered from is
itself a violation -- and one that evidence-integrity scoring against the
JUDGMENT will not catch, because stage 1's spans are verbatim from the
judgment and a paraphrase of one often still appears there.

ONLY THE COMPRESSED ARM HAS THIS BOUNDARY. C1/C2 extract from the judgment
directly, so there is no stage-1 inventory to check them against, and this
script says so rather than reporting them as perfect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import RDF, Graph, Namespace

ECHR = Namespace("https://growgraph.dev/echr#")

EVENT_CLASSES = (
    "DomesticProceeding",
    "AdministrativeAction",
    "EnforcementAction",
    "ProsecutorialReview",
)


def norm(text: str) -> str:
    """Whitespace-collapsed, NFC-normalised. Nothing else -- no case folding and
    no punctuation stripping, because a quote that differs in either is not the
    verbatim span the prompt asked for and should not be scored as if it were."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


@dataclass
class Stage1Event:
    id: str
    quote: str
    date: str
    authority: str


@dataclass
class GraphEvent:
    iri: str
    quotes: list[str] = field(default_factory=list)
    date: str = ""
    authority: str = ""


@dataclass
class DocResult:
    case_id: str
    facts_key: str
    stage1_events: int
    kept: list[str] = field(default_factory=list)
    paraphrased: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    graph_events: int = 0
    note: str = ""

    @property
    def loss(self) -> int:
        return len(self.paraphrased) + len(self.dropped)


def _span(block: dict | None) -> str:
    return norm(block.get("text", "")) if isinstance(block, dict) else ""


def load_stage1_events(compress_json: Path) -> list[Stage1Event]:
    payload = json.loads(compress_json.read_text(encoding="utf-8")).get("payload", {})
    out = []
    for i, ev in enumerate(payload.get("events", []), 1):
        out.append(
            Stage1Event(
                id=ev.get("id") or f"e{i}",
                quote=_span(ev.get("what_happened_span")),
                date=_span(ev.get("decision_date_span"))
                or _span(ev.get("start_date_span")),
                authority=_span(ev.get("authority_span")),
            )
        )
    return out


def load_graph_events(ttl: Path) -> list[GraphEvent]:
    g = Graph()
    g.parse(ttl, format="turtle")
    out = []
    for cls in EVENT_CLASSES:
        for node in g.subjects(RDF.type, ECHR[cls]):
            court = g.value(node, ECHR.hasCourt)
            authority = ""
            if court is not None:
                name = g.value(court, ECHR.hasAuthorityName)
                authority = norm(str(name)) if name is not None else ""
            date = g.value(node, ECHR.hasDecisionDate) or g.value(
                node, ECHR.hasProceedingStartDate
            )
            out.append(
                GraphEvent(
                    iri=str(node),
                    quotes=[
                        norm(str(q)) for q in g.objects(node, ECHR.hasSupportingQuote)
                    ],
                    date=str(date) if date is not None else "",
                    authority=authority,
                )
            )
    return out


def _date_matches(stage1_date: str, graph_date: str) -> bool:
    """Stage 1 dates are surface text ("3 March 1999"); the graph's are typed
    literals ("1999-03-03"). Compare on the year alone plus day-of-month, which
    is enough to tell two events of one document apart without reimplementing
    date parsing that parse_date already does elsewhere."""
    if not stage1_date or not graph_date:
        return False
    year = re.search(r"\b(\d{4})\b", stage1_date)
    day = re.search(r"\b(\d{1,2})\b", stage1_date)
    if not year or year.group(1) not in graph_date:
        return False
    return bool(day and re.search(rf"-0?{day.group(1)}\b", graph_date))


def classify(stage1: list[Stage1Event], graph: list[GraphEvent]) -> DocResult:
    result = DocResult(case_id="", facts_key="", stage1_events=len(stage1))
    result.graph_events = len(graph)
    for ev in stage1:
        if ev.quote and any(
            ev.quote in q or q in ev.quote for ge in graph for q in ge.quotes if q
        ):
            result.kept.append(ev.id)
            continue
        near = any(
            _date_matches(ev.date, ge.date)
            and ev.authority
            and (ev.authority in ge.authority or ge.authority in ev.authority)
            for ge in graph
        )
        (result.paraphrased if near else result.dropped).append(ev.id)
    return result


def case_id_to_key(jsonl: Path) -> dict[str, str]:
    """`bundles.jsonl` line i produced `bundles.L{i}.facts.ttl`. Join on case_id,
    never on position: the files are parallel today and nothing enforces it."""
    stem = jsonl.stem
    out = {}
    for i, line in enumerate(jsonl.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() and (case_id := json.loads(line).get("case_id")):
            out[case_id] = f"{stem}.L{i}"
    return out


def check_run(
    stage1_dir: Path, facts_dir: Path, jsonl: Path, limit: int | None = None
) -> list[DocResult]:
    keys = case_id_to_key(jsonl)
    results = []
    for compress_json in sorted(stage1_dir.glob("*.compress.json"))[:limit]:
        case_id = compress_json.name.removesuffix(".compress.json")
        key = keys.get(case_id)
        if key is None:
            results.append(
                DocResult(case_id, "", 0, note="no case_id match in the arm's jsonl")
            )
            continue
        ttl = facts_dir / f"{key}.facts.ttl"
        if not ttl.exists():
            results.append(
                DocResult(case_id, key, 0, note="no graph -- stage 2 produced none")
            )
            continue
        stage1 = load_stage1_events(compress_json)
        try:
            graph = load_graph_events(ttl)
        except Exception as exc:  # noqa: BLE001 -- one bad graph must not end the run
            results.append(
                DocResult(case_id, key, len(stage1), note=f"unparseable: {exc}")
            )
            continue
        r = classify(stage1, graph)
        r.case_id, r.facts_key = case_id, key
        results.append(r)
    return results


def report(results: list[DocResult], label: str) -> dict:
    scored = [r for r in results if not r.note]
    total_s1 = sum(r.stage1_events for r in scored)
    kept = sum(len(r.kept) for r in scored)
    para = sum(len(r.paraphrased) for r in scored)
    drop = sum(len(r.dropped) for r in scored)
    lossy = [r for r in scored if r.loss]

    print(f"\n=== {label} ===")
    if skipped := [r for r in results if r.note]:
        print(f"  {len(skipped)} document(s) not scored: {skipped[0].note}")
    if not total_s1:
        print("  nothing to score")
        return {}
    print(f"  documents scored     {len(scored)}")
    print(f"  stage-1 events       {total_s1}")
    print(f"  kept in graph        {kept}  ({kept / total_s1:.1%})")
    print(f"  paraphrased quote    {para}  ({para / total_s1:.1%})")
    print(f"  dropped outright     {drop}  ({drop / total_s1:.1%})")
    print(f"  documents with loss  {len(lossy)} / {len(scored)}")
    for r in sorted(lossy, key=lambda r: -r.loss)[:10]:
        bits = []
        if r.dropped:
            bits.append(f"dropped {','.join(r.dropped)}")
        if r.paraphrased:
            bits.append(f"paraphrased {','.join(r.paraphrased)}")
        print(
            f"    {r.case_id}  {r.stage1_events} -> {len(r.kept)}   {'; '.join(bits)}"
        )
    return {
        "label": label,
        "documents": len(scored),
        "stage1_events": total_s1,
        "kept": kept,
        "paraphrased": para,
        "dropped": drop,
        "documents_with_loss": len(lossy),
        "per_document": [
            {
                "case_id": r.case_id,
                "facts_key": r.facts_key,
                "stage1_events": r.stage1_events,
                "graph_events": r.graph_events,
                "kept": r.kept,
                "paraphrased": r.paraphrased,
                "dropped": r.dropped,
                "note": r.note,
            }
            for r in results
        ],
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--run-dir",
        type=Path,
        help="an ablation run directory; checks C3 (raw) and C4 (repaired)",
    )
    ap.add_argument("--stage1-dir", type=Path, help="directory of *.compress.json")
    ap.add_argument("--facts-dir", type=Path, help="directory of *.facts.ttl")
    ap.add_argument(
        "--jsonl", type=Path, help="the arm's input jsonl (for case_id -> key)"
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    ap.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit non-zero if the kept share falls below this (e.g. 0.95). Off by "
        "default: this is an instrument first, and a gate only where a run has a "
        "baseline to hold itself to.",
    )
    args = ap.parse_args(argv)

    reports = []
    if args.run_dir:
        stage1 = args.run_dir / "stage1"
        jsonl = args.run_dir / "bundles.jsonl"
        if not stage1.is_dir() or not jsonl.is_file():
            raise SystemExit(
                f"{args.run_dir} has no stage1/ and bundles.jsonl -- this check "
                "only applies to the compressed arm, which is the only one with a "
                "stage-1 inventory to check against."
            )
        for label, sub in (
            ("C3 (stage 2 output)", "raw"),
            ("C4 (after repair)", "repaired"),
        ):
            facts = args.run_dir / "C3_C4" / sub
            if facts.is_dir():
                reports.append(
                    report(check_run(stage1, facts, jsonl, args.limit), label)
                )
    elif args.stage1_dir and args.facts_dir and args.jsonl:
        reports.append(
            report(
                check_run(args.stage1_dir, args.facts_dir, args.jsonl, args.limit),
                str(args.facts_dir),
            )
        )
    else:
        raise SystemExit("need --run-dir, or all of --stage1-dir --facts-dir --jsonl")

    if args.out:
        args.out.write_text(json.dumps(reports, indent=1), encoding="utf-8")
        print(f"\nreport -> {args.out}")

    if args.fail_under is not None:
        for rep in reports:
            if rep and rep["kept"] / rep["stage1_events"] < args.fail_under:
                print(
                    f"\nFAIL: {rep['label']} kept "
                    f"{rep['kept'] / rep['stage1_events']:.1%} < {args.fail_under:.1%}",
                    file=sys.stderr,
                )
                raise SystemExit(1)


if __name__ == "__main__":
    main()
