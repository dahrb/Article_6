"""Score final graphs (`<case>.facts.ttl`) against gold: the paper's table.

    uv run python -m art6.ontology.diagnostics.score_graphs [--gold-dir annotation/test] LABEL=DIR ...

Columns:
  len/med/str  event F1 at the lenient / medium / strict tier (score_against_gold);
               P/R are lenient precision and recall
  follows, part_of, occ
               typed edge scores, correct/asserted and F1; both ends must be
               aligned to gold events (score_graph_metrics.score_edges)
  out/dir/lvl  outcome, direction and level accuracy over aligned events
               whose gold value is stated
  mat, mat*    matter F1 over aligned events; mat* only where gold HAS a matter
               (several documents carry no matter labels at all)
  trans        TRANSITIVE follows: A -> B -> C also counts as A -> C (directed
               reachability over follows + part_of). P over the arm's reachable
               pairs whose two ends both align to gold, R over all gold pairs
  chain P/R    pairwise "same chain" agreement between aligned events, with the
               number of chains asserted and (in gold)

For the major-events-only numbers, run diagnostics.major_only on the graphs
first.
"""

from __future__ import annotations

import argparse
import itertools
from pathlib import Path

from art6.ontology.diagnostics import field_agreement as fa
from art6.ontology.diagnostics import score_against_gold as sag
from art6.ontology.diagnostics import score_graph_metrics as sgm


def f1(correct: int, asserted: int, gold: int) -> float:
    return 200 * correct / (asserted + gold) if asserted + gold else 0.0


def reach(nodes, kinds=("follows", "part_of")) -> dict:
    nxt = {n.key: {t for k in kinds for t in n.edges.get(k, ())} for n in nodes}
    out = {}
    for s in nxt:
        seen, todo = set(), [s]
        while todo:
            for t in nxt.get(todo.pop(), ()):
                if t not in seen and t in nxt:
                    seen.add(t)
                    todo.append(t)
        out[s] = seen
    return out


def components(nodes) -> dict:
    parent = {n.key: n.key for n in nodes}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    for n in nodes:
        for k in sgm.EDGE_KINDS:
            for t in n.edges.get(k, ()):
                if t in parent:
                    parent[find(n.key)] = find(t)
    return {n.key: find(n.key) for n in nodes}


def score_dir(d: Path, gold_dir: Path) -> str:
    tiers = {k: [0, 0, 0] for k in ("lenient", "medium", "strict")}
    edges = {k: [0, 0, 0] for k in sgm.EDGE_KINDS}
    fields = {k: [0, 0] for k in ("outcome", "outcome_direction", "level")}
    mat, mat_star, trans = [0, 0, 0], [0, 0, 0], [0, 0, 0]
    tp = fp = fn = gold_chains = arm_chains = 0
    for f in sorted(d.glob("*.facts.ttl")):
        case = f.name.split(".facts")[0]
        ge, ae = sag.load_gold(case), sag.load_arm(f)
        for mode, t in tiers.items():
            r = sag.score_document_date_quote(case, ge, ae, **sag.MODE_PARAMS[mode])
            t[0] += r.matched
            t[1] += r.matched + r.gold_only
            t[2] += r.arm_matched + r.arm_only
        gn, an = sgm.load_gold_nodes(gold_dir, case), sgm.load_arm_nodes(f) or []
        m = sgm.align(gn, an)
        for k, e in edges.items():
            s = sgm.score_edges(gn, an, m, k)
            e[0] += s["correct"]
            e[1] += s["arm_edges"]
            e[2] += s["gold_edges"]
        gold_matters = {n.key: n.matters for n in gn}
        for a in an:
            if a.key in m:
                g = gold_matters[m[a.key]]
                mat[0] += len(g & a.matters)
                mat[1] += len(g)
                mat[2] += len(a.matters)
                if g:
                    mat_star[0] += len(g & a.matters)
                    mat_star[1] += len(g)
                    mat_star[2] += len(a.matters)
        g_reach, a_reach = reach(gn), reach(an)
        g_pairs = {(s, t) for s in g_reach for t in g_reach[s]}
        a_pairs = {
            (m[s], m[t]) for s in a_reach for t in a_reach[s] if s in m and t in m
        }
        trans[0] += len(g_pairs & a_pairs)
        trans[1] += len(a_pairs)
        trans[2] += len(g_pairs)
        gf = {n.key: n.fields for n in fa.load_gold(gold_dir, case)}
        af = {n.key: n.fields for n in (fa.load_arm(f) or [])}
        for ak, gk in m.items():
            for k, acc in fields.items():
                gv = gf[gk].get(k)
                if gv:
                    av = af.get(ak, {}).get(k)
                    acc[1] += 1
                    acc[0] += av == gv or (gv == "Unknown" and av is None)
        gc, ac = components(gn), components(an)
        gold_chains += len(set(gc.values()))
        arm_chains += len(set(ac.values()))
        for (a1, g1), (a2, g2) in itertools.combinations(m.items(), 2):
            same_g, same_a = gc[g1] == gc[g2], ac[a1] == ac[a2]
            tp += same_g and same_a
            fp += same_a and not same_g
            fn += same_g and not same_a

    def edge(k):
        return f"{edges[k][0]}/{edges[k][1]} {f1(*edges[k]):3.0f}"

    def accuracy(k):
        return f"{100 * fields[k][0] / max(1, fields[k][1]):3.0f}"

    lenient = tiers["lenient"]
    return (
        f"{f1(*tiers['lenient']):5.1f} {f1(*tiers['medium']):5.1f} {f1(*tiers['strict']):5.1f} "
        f"{100 * lenient[0] / max(1, lenient[2]):3.0f} {100 * lenient[0] / max(1, lenient[1]):3.0f} | "
        f"{edge('follows'):>11} {edge('part_of'):>10} {edge('occurs_during'):>7} | "
        f"{accuracy('outcome')} {accuracy('outcome_direction')} {accuracy('level')} | "
        f"{f1(*mat):3.0f} {f1(*mat_star):4.0f} | "
        f"{100 * trans[0] / max(1, trans[1]):3.0f}/{100 * trans[0] / max(1, trans[2]):3.0f}/"
        f"{f1(*trans):5.1f} | "
        f"{100 * tp / max(1, tp + fp):3.0f}/{100 * tp / max(1, tp + fn):3.0f}  "
        f"{arm_chains}({gold_chains})"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--gold-dir", type=Path, default=sag.GOLD_DIR)
    ap.add_argument("runs", nargs="+", help="LABEL=DIR, DIR holding <case>.facts.ttl")
    args = ap.parse_args()
    sag.GOLD_DIR = args.gold_dir
    print(
        f"{'variant':22} {'len':>5} {'med':>5} {'str':>5} {'P':>3} {'R':>3} | "
        f"{'follows':>11} {'part_of':>10} {'occ':>7} | {'out':>3} {'dir':>3} {'lvl':>3} | "
        "mat mat* | trans P/R/F1   | chain P/R  n(gold)"
    )
    for run in args.runs:
        label, d = run.split("=", 1)
        print(f"{label:22} {score_dir(Path(d), args.gold_dir)}")


if __name__ == "__main__":
    main()
