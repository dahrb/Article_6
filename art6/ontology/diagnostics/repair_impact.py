"""
repair_impact.py
----------------
Measures what `repair_facts.py` actually changed, by diffing each model's
`raw/` graph against its `repaired/` graph on the metrics the extraction
quality report scores, and cross-referencing the per-document
`<stem>.facts.repairs.json` audit logs.

Answers three questions the audit logs alone cannot:
  1. how much of the graph the repair pass touches at all (files, triples),
  2. whether it moves the metrics that matter (grievance layer, court
     coverage, functional-property collisions, connectivity),
  3. what it costs -- deleting a contradictory edge also fragments the graph,
     and the components / singletons rows make that trade visible.

Usage:
  # every arm under an experiment directory
  uv run python -m art6.ontology.diagnostics.repair_impact \\
      --experiment-dir results/experiment_arms_20260823_120000

  # one arm, comparing the staged repair against the legacy one on the same raw/
  uv run python -m art6.ontology.diagnostics.repair_impact \\
      --experiment-dir results/experiment_arms_20260823_120000 \\
      --arms nochunk_ttl_mv1 --repaired-subdir repaired_legacy
"""

from __future__ import annotations

import argparse
import collections
import json
import os
from functools import lru_cache
from pathlib import Path

from rdflib import OWL, RDF, Graph, Namespace, URIRef

from art6.paths import REPO_ROOT

ECHR = Namespace("https://growgraph.dev/echr#")
ONTOLOGY_TTL = Path(
    os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr.ttl")
)


def load(p):
    g = Graph()
    try:
        g.parse(p, format="turtle")
    except Exception as exc:  # noqa: BLE001 - one unparseable file must not stop the sweep
        print("  PARSE FAIL", p, exc)
    return g


@lru_cache(maxsize=1)
def functional_properties() -> tuple[URIRef, ...]:
    """Every owl:FunctionalProperty the CURRENT ontology declares.

    Read live rather than hardcoded, for the same reason
    repair_facts.functional_properties() is: the previous hardcoded list had
    drifted out of the schema entirely -- it named `hasStartDate` (the real
    term is `hasProceedingStartDate`) and `hasBirthYear` (deleted), so two of
    its ten entries could never match a triple and the violation count they
    contributed was silently always zero.
    """
    g = Graph()
    g.parse(ONTOLOGY_TTL)
    return tuple(sorted(g.subjects(RDF.type, OWL.FunctionalProperty), key=str))


def stats(g):
    s = {}
    s["triples"] = len(g)
    s["typed"] = len(set(g.subjects(RDF.type, None)))
    s["a6issue"] = len(set(g.subjects(RDF.type, ECHR.Article6Issue)))
    s["proceedings"] = set(g.subjects(RDF.type, ECHR.DomesticProceeding))
    s["n_proc"] = len(s["proceedings"])
    s["follows"] = len(list(g.triples((None, ECHR.followsProceeding, None))))
    # functional violations
    viol = 0
    for pred in functional_properties():
        c = collections.Counter(sub for sub, _, _ in g.triples((None, pred, None)))
        viol += sum(v - 1 for v in c.values() if v > 1)
    s["func_viol"] = viol
    # orphans: typed nodes with no in- or out- echr link to another typed node
    nodes = set(g.subjects(RDF.type, None))
    deg = collections.Counter()
    for sub, pred, obj in g:
        if pred == RDF.type:
            continue
        if isinstance(obj, URIRef) and sub in nodes and obj in nodes:
            deg[sub] += 1
            deg[obj] += 1
    s["singletons"] = sum(1 for n in nodes if deg[n] == 0)
    # connected components over typed nodes
    adj = collections.defaultdict(set)
    for sub, pred, obj in g:
        if pred == RDF.type:
            continue
        if isinstance(obj, URIRef) and sub in nodes and obj in nodes:
            adj[sub].add(obj)
            adj[obj].add(sub)
    seen, comps = set(), 0
    for n in nodes:
        if n in seen:
            continue
        comps += 1
        stack = [n]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(adj[x] - seen)
    s["components"] = comps
    s["proc_no_court"] = sum(
        1 for p in s["proceedings"] if (p, ECHR.hasCourt, None) not in g
    )
    s["proc_no_outcome"] = sum(
        1 for p in s["proceedings"] if (p, ECHR.hasOutcome, None) not in g
    )
    s["quotes"] = len(list(g.triples((None, ECHR.hasSupportingQuote, None))))
    return s


KEYS = [
    "triples",
    "typed",
    "a6issue",
    "n_proc",
    "follows",
    "func_viol",
    "singletons",
    "components",
    "proc_no_court",
    "proc_no_outcome",
    "quotes",
]


def report_arm(arm_dir: Path, raw_subdir: str, repaired_subdir: str) -> bool:
    """One arm's raw-vs-repaired table. False if there was nothing to compare."""
    raw_dir = arm_dir / raw_subdir
    repaired_dir = arm_dir / repaired_subdir
    raws = sorted(raw_dir.glob("*.facts.ttl"))
    if not raws or not repaired_dir.is_dir():
        return False

    tot_raw = collections.Counter()
    tot_rep = collections.Counter()
    ops_applied = collections.Counter()
    ops_skipped = collections.Counter()
    files_touched = 0
    per_file = []
    repair_seconds = 0.0
    model_calls = 0

    for r in raws:
        rep = repaired_dir / r.name
        if not rep.exists():
            continue
        gr, gp = load(r), load(rep)
        sr, sp = stats(gr), stats(gp)
        for k in KEYS:
            tot_raw[k] += sr[k]
            tot_rep[k] += sp[k]
        added = len(set(gp) - set(gr))
        removed = len(set(gr) - set(gp))
        rj = rep.with_suffix("").with_suffix(".facts.repairs.json")
        nops = 0
        if rj.exists():
            d = json.loads(rj.read_text())
            files_touched += 1
            for op in d.get("operations", []):
                st = op.get("status", "?")
                if st == "applied":
                    ops_applied[(op["action"], op.get("predicate", ""))] += 1
                else:
                    ops_skipped[(st, op["action"], op.get("predicate", ""))] += 1
                nops += 1
            # Present only for runs made after timing landed; older audit logs
            # simply contribute nothing rather than breaking the sweep.
            timing = d.get("timings") or {}
            repair_seconds += timing.get("seconds_total", 0.0)
            model_calls += timing.get("model_calls", 0)
        per_file.append((r.name, nops, added, removed, sr["triples"], sp["triples"]))

    print(
        f"## {arm_dir.name} / {repaired_subdir}   "
        f"({len(per_file)} files, {files_touched} with a repairs.json)"
    )
    if model_calls:
        print(
            f"\nrepair cost: {repair_seconds:.1f}s over {model_calls} model call(s)\n"
        )
    print("| file | ops | triples+ | triples- | raw | repaired |")
    print("|---|--:|--:|--:|--:|--:|")
    for row in per_file:
        print("| {} | {} | {} | {} | {} | {} |".format(*row))
    print()
    print("| metric | raw | repaired | delta |")
    print("|---|--:|--:|--:|")
    for k in KEYS:
        d = tot_rep[k] - tot_raw[k]
        print(f"| {k} | {tot_raw[k]} | {tot_rep[k]} | {d:+d} |")
    print()
    if ops_applied:
        print("applied ops by (action, predicate):")
        for (a, p), c in ops_applied.most_common():
            print(f"  - {a:<6} {p:<40} {c}")
    if ops_skipped:
        print("skipped ops:")
        for (st, a, p), c in ops_skipped.most_common():
            print(f"  - [{st}] {a:<6} {p:<40} {c}")
    print("\n---\n")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--experiment-dir",
        type=Path,
        required=True,
        help="Experiment root holding one subdirectory per arm (or per model).",
    )
    parser.add_argument(
        "--arms",
        nargs="*",
        default=None,
        help="Arm subdirectory names. Default: every subdirectory that has a raw/.",
    )
    parser.add_argument("--raw-subdir", default="raw")
    parser.add_argument(
        "--repaired-subdir",
        default="repaired",
        help=(
            "Which repaired tree to diff against raw/. Use repaired_legacy to "
            "score the pre-staging repair implementation on the same input."
        ),
    )
    args = parser.parse_args(argv)

    root = args.experiment_dir
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    arms = args.arms or sorted(
        p.name for p in root.iterdir() if (p / args.raw_subdir).is_dir()
    )
    if not arms:
        raise SystemExit(f"no arms with a {args.raw_subdir}/ under {root}")

    print(f"# repair_facts.py impact — {root.name}\n")
    reported = 0
    for arm in arms:
        if report_arm(root / arm, args.raw_subdir, args.repaired_subdir):
            reported += 1
    if not reported:
        print(f"nothing to compare (no {args.repaired_subdir}/ trees found)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
