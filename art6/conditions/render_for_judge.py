"""
render_for_judge.py
-------------------
Render every ablation condition into ONE readable form, for scoring.

C0 emits nine flat JSON fields; C1-C4 emit an RDF graph. Scoring them needs a
common presentation, and the presentation is where a comparison is most easily
rigged. Two rules follow from that:

1. **Project upward, not downward.** An earlier renderer flattened the graph
   conditions into C0's nine fields, discarding participations-as-nodes, party
   side, authority kind and per-participation quotes -- i.e. exactly what the
   ontology conditions uniquely express -- and then scored them on the flattened
   form. That denies the graph credit for its own content while making it look
   like a worse C0. Here the graph's structure is shown, and C0 is shown as what
   it is: the same proceedings with less structure.

2. **Same skeleton for every condition.** Each proceeding prints as body, date,
   level, outcome, quote, parties and `follows`, in that order, whatever produced
   it, so a difference on the page is a difference in the output rather than in
   the rendering.

Blinding is offered (`--blind`) but is not achievable for every contrast: the
compression conditions are identifiable on sight from their quote style, and C0
from its free-text outcomes. Report scoring as unblinded where that is true.

Usage:
  uv run python -m art6.conditions.render_for_judge \\
      --run-dir results/ablation_test --out-dir results/ablation_test/judge
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from rdflib import RDFS, Graph, Namespace

ECHR = Namespace("https://growgraph.dev/echr#")


def _short(term) -> str:
    """A closed-vocabulary member as words, not as an IRI."""
    if term is None:
        return ""
    text = str(term)
    if "#" in text:
        text = text.rsplit("#", 1)[1]
    for prefix in ("Side", "Authority", "Instance", "Outcome", "Level"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
    return text


def _label(graph: Graph, node) -> str:
    for predicate in (RDFS.label, ECHR.hasAuthorityName, ECHR.hasPersonName):
        value = graph.value(node, predicate)
        if value:
            return str(value)
    return str(node).rsplit("/", 1)[-1]


def render_graph(path: Path) -> str:
    """One proceeding per block, ordered by date then IRI."""
    graph = Graph().parse(path, format="turtle")
    # A proceeding is identified by what it HAS, not by one type IRI. The
    # ontology types most of them by subclass -- ProsecutorialReview,
    # AdministrativeAction, JudicialProceeding -- so selecting on
    # echr:DomesticProceeding alone returned 3 of 13 on the first document and
    # would have scored these conditions on a quarter of their output.
    events = [
        subject
        for subject in set(graph.subjects())
        if graph.value(subject, ECHR.hasDecisionDate) is not None
        or graph.value(subject, ECHR.hasInstanceLevel) is not None
    ]
    rows = []
    for event in events:
        date = graph.value(event, ECHR.hasDecisionDate)
        rows.append((str(date or "9999"), str(event), event))
    rows.sort()

    order = {event: n for n, (_, _, event) in enumerate(rows, 1)}
    out: list[str] = []
    for n, (_, _, event) in enumerate(rows, 1):
        body = graph.value(event, ECHR.hasCourt)
        line = [f"[{n}]"]
        line.append(f"body: {_label(graph, body) if body else '-'}")
        kind = graph.value(body, ECHR.hasAuthorityKind) if body else None
        if kind:
            line.append(f"({_short(kind)})")
        date = graph.value(event, ECHR.hasDecisionDate)
        line.append(f"| date: {date or '-'}")
        level = graph.value(event, ECHR.hasInstanceLevel)
        line.append(f"| level: {_short(level) or '-'}")
        outcome = graph.value(event, ECHR.hasOutcome)
        line.append(f"| outcome: {_short(outcome) or '-'}")
        out.append(" ".join(line))

        quote = graph.value(event, ECHR.hasSupportingQuote)
        if quote:
            out.append(f'      quote: "{quote}"')
        parties = []
        for participation in graph.objects(event, ECHR.hasParticipation):
            party = graph.value(participation, ECHR.participatingParty)
            side = graph.value(participation, ECHR.hasPartySide)
            name = _label(graph, party) if party else "?"
            parties.append(f"{name} ({_short(side).lower() or 'side unstated'})")
        if parties:
            out.append(f"      parties: {'; '.join(sorted(parties))}")
        follows = graph.value(event, ECHR.followsProceeding)
        if follows is not None and follows in order:
            out.append(f"      follows: {order[follows]}")
    return "\n".join(out) if out else "(no proceedings)"


def render_o1(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload if isinstance(payload, list) else payload.get("proceedings") or []
    out: list[str] = []
    for entry in entries:
        out.append(
            f"[{entry.get('order')}] body: {entry.get('deciding_body') or '-'}"
            f" | date: {entry.get('decision_date') or '-'}"
            f" | level: {entry.get('instance_level') or '-'}"
            f" | outcome: {entry.get('outcome') or '-'}"
        )
        if entry.get("supporting_quote"):
            out.append(f'      quote: "{entry["supporting_quote"]}"')
        if entry.get("parties"):
            out.append(f"      parties: {'; '.join(entry['parties'])}")
        if entry.get("follows"):
            follows = entry["follows"]
            out.append(
                f"      follows: {', '.join(map(str, follows)) if isinstance(follows, list) else follows}"
            )
        if entry.get("custodial_measure"):
            out.append(f"      custodial: {entry['custodial_measure']}")
    return "\n".join(out) if out else "(no proceedings)"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument(
        "--blind", action="store_true", help="label conditions A-E and seal the key"
    )
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    run = args.run_dir
    args.out_dir.mkdir(parents=True, exist_ok=True)
    source = {}
    for line in (run / "input.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            source[record["case_id"]] = record["text"]
    case_ids = list(source)

    conditions = {
        "C0": ("o1", run / "C0"),
        "C1": ("ttl", run / "C1_C2" / "raw"),
        "C2": ("ttl", run / "C1_C2" / "repaired"),
        "C3": ("ttl", run / "C3_C4" / "raw"),
        "C4": ("ttl", run / "C3_C4" / "repaired"),
    }

    key: dict[str, dict[str, str]] = {}
    for index, case_id in enumerate(case_ids, 1):
        blocks: dict[str, str] = {}
        for cond, (kind, directory) in conditions.items():
            if kind == "o1":
                path = directory / f"input.L{index}.o1.json"
                blocks[cond] = render_o1(path) if path.exists() else "(missing)"
            else:
                stem = "input" if "C1_C2" in str(directory) else "bundles"
                path = directory / f"{stem}.L{index}.facts.ttl"
                blocks[cond] = render_graph(path) if path.exists() else "(missing)"

        labels = list(blocks)
        if args.blind:
            shuffled = labels[:]
            random.Random(args.seed + index).shuffle(shuffled)
            labels = shuffled
            key[case_id] = {chr(65 + n): c for n, c in enumerate(labels)}

        # Delimiters the SOURCE cannot contain. Judgments carry their own
        # markdown headings (PROCEDURE, THE FACTS), so "## X" as a separator
        # silently shreds the source and any later parse of this file.
        parts = [f"# {case_id}", "", "===== SOURCE =====", source[case_id], ""]
        for n, cond in enumerate(labels):
            heading = chr(65 + n) if args.blind else cond
            parts += [f"===== CONDITION {heading} =====", blocks[cond], ""]
        (args.out_dir / f"{case_id}.md").write_text("\n".join(parts), encoding="utf-8")
        print(
            f"  {case_id}: "
            + "  ".join(f"{c}={len(blocks[c].splitlines())}L" for c in blocks)
        )

    if args.blind:
        (args.out_dir / "KEY.json").write_text(
            json.dumps(key, indent=1), encoding="utf-8"
        )
    print(f"\nwrote {len(case_ids)} document(s) -> {args.out_dir}")


if __name__ == "__main__":
    main()
