"""The major-events view of each final graph, for scoring.

    uv run python -m art6.ontology.diagnostics.major_only GRAPH_DIR STAGE1_DIR OUT_DIR

Events stage 1 tagged significance=minor are dropped and links are contracted through
them: a -> minor -> b becomes a -> b, keeping a's own link kind. Events stage 1 does not
know (minted by stage 3) are kept. Everything else in the graph is copied unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rdflib import Graph

from art6.ontology.diagnostics.score_graph_metrics import EDGE_KINDS
from art6.ontology.reconcile import match_events

PREDS = tuple(EDGE_KINDS.values())


def minor_ids(s1: Path, case: str) -> set[str]:
    f = s1 / f"{case}.compress.json"
    if not f.exists():
        return set()
    return {
        e["id"]
        for e in json.loads(f.read_text(encoding="utf-8"))["payload"]["events"]
        if e.get("significance") == "minor"
    }


def minor_nodes(g: Graph, s1: Path, case: str) -> set:
    """Graph nodes of stage-1 minor events, matched by quote as stage 3 does.

    Matching on node ids alone missed documents where stage 2 renumbered the
    events (2026-11-09, vote9: 001-248395 and 001-5878 kept every minor event,
    hearings and enquiries included). Nodes no stage-1 event claims are kept.
    """
    f = s1 / f"{case}.compress.json"
    if not f.exists():
        return set()
    events = json.loads(f.read_text(encoding="utf-8"))["payload"]["events"]
    minor = {e["id"] for e in events if e.get("significance") == "minor"}
    m = match_events(g, events)
    return {node for node, eid in m.entry_of.items() if eid in minor}


def contract(g: Graph, minor: set[str], nodes: set = frozenset()) -> int:
    local = lambda s: str(s).rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    drop = {s for p in PREDS for s in g.subjects(p, None) if local(s) in minor}
    drop |= {o for p in PREDS for o in g.objects(None, p) if local(o) in minor}
    drop |= {s for s in set(g.subjects()) if local(s) in minor}
    drop |= set(nodes)

    def targets(node, seen):
        # the nearest kept events a dropped node links to, through any link kind
        out = set()
        for p in PREDS:
            for o in g.objects(node, p):
                if o in seen:
                    continue
                seen.add(o)
                out |= targets(o, seen) if o in drop else {o}
        return out

    for p in PREDS:
        for s, o in list(g.subject_objects(p)):
            if s in drop or o not in drop:
                continue
            g.remove((s, p, o))
            for t in targets(o, {o, s}):
                if t != s:
                    g.add((s, p, t))
    for d in drop:
        g.remove((d, None, None))
        g.remove((None, None, d))
    return len(drop)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("graph_dir", type=Path, help="<case>.facts.ttl files")
    ap.add_argument(
        "stage1_dir", type=Path, help="the stage-1 payloads the graphs came from"
    )
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(args.graph_dir.glob("*.facts.ttl")):
        case = f.name.split(".facts")[0]
        g = Graph()
        g.parse(f, format="turtle")
        n += contract(
            g, minor_ids(args.stage1_dir, case), minor_nodes(g, args.stage1_dir, case)
        )
        g.serialize(args.out_dir / f.name, format="turtle")
    print(f"{args.graph_dir} -> {args.out_dir}: dropped {n} minor events")


if __name__ == "__main__":
    main()
