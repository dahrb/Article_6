"""Graph-level scoring against gold: matter capture, chain edges, shape metrics.

score_against_gold.py answers "did the arm find the right EVENTS". This answers
the questions that only make sense once events are aligned:

  * does the graph carry echr:hasMatter at all, and does it agree with gold?
  * are the chain edges (followsProceeding / partOfProceeding / occursDuring)
    the same edges gold draws, between the same events?
  * is the SHAPE right -- how many distinct proceeding chains, how deep, how
    many maximal paths, against gold's own counts?

Edge scoring is conjunctive by construction: an arm edge can only be correct if
BOTH its endpoints aligned to gold events, so chain-edge F1 is bounded above by
roughly event-recall squared. That is a property of the measure, not a defect --
a chain is only meaningful if its links are real events -- but it means chain-edge
numbers must never be read on the same scale as event numbers.

Alignment reuses score_against_gold's date_quote 1:1 bipartite match, so an event
counted as matched here is matched there too.

Usage:
  uv run python -m art6.ontology.diagnostics.score_graph_metrics \\
      --run-dir results/final_gemma_gold30 --arms C3,C4 \\
      --gold-dir annotation/gold
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rdflib import Graph, Namespace, URIRef

from art6.ontology.diagnostics.score_against_gold import (
    DATE_SIM_THRESHOLD,
    Event,
    date_sim,
    prf1,
    quote_sim,
)

ECHR = Namespace("https://growgraph.dev/echr#")
REPO_ROOT = Path(__file__).resolve().parents[3]

ARM_PATHS = {
    "C1": ("C1_C2", "raw", "input"),
    "C2": ("C1_C2", "repaired", "input"),
    "C3": ("C3_C4", "raw", "bundles"),
    "C4": ("C3_C4", "repaired", "bundles"),
}

# Edge kinds, gold field name -> arm predicate. `follows` is the sequential
# chain (this act continues/appeals/reviews that one); the other two are
# grouping relations and are counted separately rather than folded in, since a
# part_of edge and a follows edge say different things about the shape.
EDGE_KINDS = {
    "follows": ECHR.followsProceeding,
    "part_of": ECHR.partOfProceeding,
    "occurs_during": ECHR.occursDuring,
}


@dataclass
class Node:
    """An event plus the identity and links the flat Event dataclass drops."""

    key: object  # gold id (int) or arm URIRef
    body: str | None
    decision_date: str | None
    start_date: str | None
    quote: str | None = None
    level: str | None = None
    matters: set[str] = field(default_factory=set)
    edges: dict[str, set] = field(default_factory=lambda: defaultdict(set))

    def as_event(self) -> Event:
        return Event(
            body=self.body,
            decision_date=self.decision_date,
            start_date=self.start_date,
            quote=self.quote,
        )

    @property
    def is_filing(self) -> bool:
        return self.decision_date is None and self.start_date is not None


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def load_gold_nodes(gold_dir: Path, case_id: str) -> list[Node]:
    path = gold_dir / f"{case_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    nodes = []
    for p in data.get("proceedings") or []:
        node = Node(
            key=p.get("id"),
            body=p.get("body"),
            decision_date=str(p["decision_date"]) if p.get("decision_date") else None,
            start_date=str(p["start_date"]) if p.get("start_date") else None,
            quote=p.get("quote"),
            level=p.get("level"),
            matters={str(m) for m in _as_list(p.get("matter"))},
        )
        for field_name in EDGE_KINDS:
            for target in _as_list(p.get(field_name)):
                if target is not None:
                    node.edges[field_name].add(target)
        nodes.append(node)
    return nodes


def _local(term) -> str | None:
    if term is None:
        return None
    text = str(term)
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def load_arm_nodes(ttl_path: Path) -> list[Node] | None:
    if not ttl_path.exists():
        return None
    graph = Graph()
    try:
        graph.parse(ttl_path, format="turtle")
    except Exception:  # noqa: BLE001 -- a malformed graph is a data point
        return None
    subjects = {
        s
        for s in set(graph.subjects())
        if graph.value(s, ECHR.hasDecisionDate) is not None
        or graph.value(s, ECHR.hasProceedingStartDate) is not None
        or graph.value(s, ECHR.hasInstanceLevel) is not None
    }
    nodes = []
    for s in subjects:
        court = graph.value(s, ECHR.hasCourt)
        decision_date = graph.value(s, ECHR.hasDecisionDate)
        start_date = graph.value(s, ECHR.hasProceedingStartDate)
        quote = graph.value(s, ECHR.hasSupportingQuote)
        level = _local(graph.value(s, ECHR.hasInstanceLevel))
        if level and level.startswith("Level"):
            level = level[len("Level") :]
        matters = set()
        for m in graph.objects(s, ECHR.hasMatter):
            name = _local(m)
            if name and name.startswith("Matter"):
                name = name[len("Matter") :]
            if name:
                matters.add(name)
        node = Node(
            key=s,
            body=str(graph.value(court, ECHR.hasAuthorityName)) if court else None,
            decision_date=str(decision_date) if decision_date else None,
            start_date=str(start_date) if start_date else None,
            quote=str(quote) if quote else None,
            level=level,
            matters=matters,
        )
        for field_name, predicate in EDGE_KINDS.items():
            for target in graph.objects(s, predicate):
                if isinstance(target, URIRef):
                    node.edges[field_name].add(target)
        nodes.append(node)
    return nodes


def align(gold: list[Node], arm: list[Node]) -> dict:
    """1:1 bipartite alignment, same gate as score_document_date_quote."""
    pairs = []
    for gi, g in enumerate(gold):
        for ai, a in enumerate(arm):
            if g.is_filing != a.is_filing:
                continue
            key = "start_date" if g.is_filing else "decision_date"
            gv, av = getattr(g, key), getattr(a, key)
            if gv is None or av is None:
                continue
            if date_sim(str(gv), str(av)) < DATE_SIM_THRESHOLD:
                continue
            pairs.append((quote_sim(g.quote, a.quote), gi, ai))
    pairs.sort(key=lambda t: t[0], reverse=True)
    gold_used, arm_used, mapping = set(), set(), {}
    for _, gi, ai in pairs:
        if gi in gold_used or ai in arm_used:
            continue
        gold_used.add(gi)
        arm_used.add(ai)
        mapping[arm[ai].key] = gold[gi].key
    return mapping


# ---------------------------------------------------------------------------
# Shape metrics
# ---------------------------------------------------------------------------


def shape_metrics(nodes: list[Node], kind: str = "follows") -> dict:
    """Chain count, depth and maximal-path count over one edge kind.

    Chains are weakly connected components: an event linked to nothing is a
    chain of one, which is what gold means by a standalone proceeding. Depth is
    the longest directed path in the component (edge count, so a lone event is
    depth 0). Paths counts maximal source->sink routes, which is where a graph
    that fans out differs from one that runs straight even at equal depth.
    """
    keys = {n.key for n in nodes}
    out = {n.key: {t for t in n.edges.get(kind, set()) if t in keys} for n in nodes}
    incoming = defaultdict(set)
    for src, targets in out.items():
        for t in targets:
            incoming[t].add(src)

    # weakly connected components
    adjacency = defaultdict(set)
    for src, targets in out.items():
        for t in targets:
            adjacency[src].add(t)
            adjacency[t].add(src)
    seen, components = set(), 0
    for key in keys:
        if key in seen:
            continue
        components += 1
        stack = [key]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adjacency[cur] - seen)

    # longest path + maximal path count, memoised over the DAG (cycles are
    # possible in a model-built graph, so guard with a visiting set)
    depth_memo, path_memo, visiting = {}, {}, set()

    def walk(key):
        if key in depth_memo:
            return depth_memo[key], path_memo[key]
        if key in visiting:  # cycle -- stop rather than recurse forever
            return 0, 1
        visiting.add(key)
        best, paths = 0, 0
        for t in out[key]:
            d, p = walk(t)
            best = max(best, d + 1)
            paths += p
        visiting.discard(key)
        depth_memo[key], path_memo[key] = best, (paths or 1)
        return depth_memo[key], path_memo[key]

    depths, total_paths = [], 0
    for key in keys:
        d, p = walk(key)
        depths.append(d)
        if not incoming[key]:  # a source: its paths are maximal ones
            total_paths += p
    edge_count = sum(len(v) for v in out.values())
    return {
        "events": len(keys),
        "edges": edge_count,
        "chains": components,
        "max_depth": max(depths) if depths else 0,
        "mean_depth": (sum(depths) / len(depths)) if depths else 0.0,
        "paths": total_paths,
    }


def score_edges(gold: list[Node], arm: list[Node], mapping: dict, kind: str) -> dict:
    """Chain-edge agreement. An arm edge counts only if both endpoints aligned."""
    gold_keys = {n.key for n in gold}
    gold_edges = {
        (n.key, t) for n in gold for t in n.edges.get(kind, set()) if t in gold_keys
    }
    arm_edges_raw = {
        (n.key, t)
        for n in arm
        for t in n.edges.get(kind, set())
        if t in {x.key for x in arm}
    }
    projected = {
        (mapping[s], mapping[t])
        for s, t in arm_edges_raw
        if s in mapping and t in mapping
    }
    correct = projected & gold_edges
    return {
        "gold_edges": len(gold_edges),
        "arm_edges": len(arm_edges_raw),
        "arm_edges_alignable": len(projected),
        "correct": len(correct),
    }


def score_matter(gold: list[Node], arm: list[Node], mapping: dict) -> dict:
    """Matter capture and agreement over aligned pairs only.

    Matter is deliberately NOT universal in the ontology (echr:Matter's own
    scopeNote: omit rather than force a value onto a purely procedural act), so
    "gold has none and the arm has none" is agreement, not a shared miss.
    """
    gold_by_key = {n.key: n for n in gold}
    stats = {
        "gold_events_with_matter": sum(1 for n in gold if n.matters),
        "arm_events_with_matter": sum(1 for n in arm if n.matters),
        "aligned": 0,
        "both_empty": 0,
        "exact_set_match": 0,
        "partial_overlap": 0,
        "disagree": 0,
        "gold_only_labels": 0,
        "arm_only_labels": 0,
        "intersection_labels": 0,
    }
    for a in arm:
        if a.key not in mapping:
            continue
        g = gold_by_key.get(mapping[a.key])
        if g is None:
            continue
        stats["aligned"] += 1
        if not g.matters and not a.matters:
            stats["both_empty"] += 1
            continue
        if g.matters == a.matters:
            stats["exact_set_match"] += 1
        elif g.matters & a.matters:
            stats["partial_overlap"] += 1
        else:
            stats["disagree"] += 1
        stats["intersection_labels"] += len(g.matters & a.matters)
        stats["gold_only_labels"] += len(g.matters - a.matters)
        stats["arm_only_labels"] += len(a.matters - g.matters)
    return stats


def gold_filter(nodes: list[Node], exclude: str | None) -> list[Node]:
    """Drop gold events by label, keeping edges consistent.

    An edge into a dropped event is dropped with it -- otherwise the gold edge
    set would reference events the scorer can no longer match, and chain recall
    would be penalised for links to events the variant deliberately removed.
    """
    if not exclude:
        return nodes
    if exclude == "level_investigative":
        kept = [n for n in nodes if n.level != "Investigative"]
    elif exclude == "matter_investigation":
        kept = [n for n in nodes if "Investigation" not in n.matters]
    else:
        raise SystemExit(f"unknown exclusion {exclude!r}")
    keys = {n.key for n in kept}
    for n in kept:
        for field_name in list(n.edges):
            n.edges[field_name] = {t for t in n.edges[field_name] if t in keys}
    return kept


def run(
    run_dir: Path, arm: str, docs: list[dict], gold_dir: Path, exclude=None
) -> dict:
    subdir, stage, stem = ARM_PATHS[arm]
    totals = {
        "matched": 0,
        "gold_only": 0,
        "arm_matched": 0,
        "arm_only": 0,
        "gold_shape": defaultdict(float),
        "arm_shape": defaultdict(float),
        "edges": {k: defaultdict(int) for k in EDGE_KINDS},
        "matter": defaultdict(int),
        "docs": 0,
        "docs_no_output": 0,
    }
    for doc in docs:
        gold = gold_filter(load_gold_nodes(gold_dir, doc["case_id"]), exclude)
        arm_nodes = load_arm_nodes(
            run_dir / subdir / stage / f"{stem}.{doc['line_id']}.facts.ttl"
        )
        if arm_nodes is None:
            totals["docs_no_output"] += 1
            totals["gold_only"] += len(gold)
            continue
        totals["docs"] += 1
        mapping = align(gold, arm_nodes)
        totals["matched"] += len(mapping)
        totals["arm_matched"] += len(mapping)
        totals["gold_only"] += len(gold) - len(mapping)
        totals["arm_only"] += len(arm_nodes) - len(mapping)
        for label, nodes in (("gold_shape", gold), ("arm_shape", arm_nodes)):
            for key, value in shape_metrics(nodes).items():
                totals[label][key] += value
        for kind in EDGE_KINDS:
            for key, value in score_edges(gold, arm_nodes, mapping, kind).items():
                totals["edges"][kind][key] += value
        for key, value in score_matter(gold, arm_nodes, mapping).items():
            totals["matter"][key] += value
    totals.update(
        prf1(
            totals["matched"],
            totals["gold_only"],
            totals["arm_matched"],
            totals["arm_only"],
        )
    )
    return totals


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--arms", default="C3,C4")
    ap.add_argument("--gold-dir", type=Path, default=REPO_ROOT / "annotation/gold")
    ap.add_argument(
        "--exclude",
        default=None,
        choices=["level_investigative", "matter_investigation"],
    )
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    manifest = json.loads((args.gold_dir / "manifest.json").read_text())
    docs = manifest["documents"]
    if docs and "line_id" not in docs[0]:
        docs = [{**d, "line_id": f"L{i}"} for i, d in enumerate(docs, 1)]

    results = {}
    for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
        r = run(args.run_dir, arm, docs, args.gold_dir, args.exclude)
        results[arm] = r
        f1 = r["f1"] if r["f1"] is not None else float("nan")
        e = r["edges"]["follows"]
        print(
            f"{arm}: events F1={f1:.1%}  "
            f"follows-edges correct={e['correct']}/{e['arm_edges']} "
            f"(gold {e['gold_edges']})  "
            f"chains arm={r['arm_shape']['chains']:.0f} gold={r['gold_shape']['chains']:.0f}"
        )
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, default=str))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
