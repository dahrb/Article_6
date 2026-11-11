"""Score stage-1 payloads (`<case>.compress.json`) against gold, no graph involved.

    uv run python -m art6.ontology.diagnostics.score_payloads [--gold-dir annotation/test] LABEL=DIR ...

Strict scorer (score_graph_metrics.align and score_edges) over the procedural
events (`events`; factual `circumstances` are not scored). Prints asserted
events, precision, recall and F1, then `follows` and `part_of` as
correct/asserted F1, and transitive `follows` (reachability over follows +
part_of, as score_graphs computes it) as correct/asserted F1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from art6.ontology.diagnostics import score_graph_metrics as sgm
from art6.ontology.diagnostics.score_graphs import reach
from art6.ontology.vote import payload_nodes


def f1(correct: int, asserted: int, gold: int) -> float:
    return 200 * correct / (asserted + gold) if asserted + gold else 0.0


def score_dir(d: Path, gold_dir: Path) -> tuple[list, dict]:
    events = [0, 0, 0]
    edges = {k: [0, 0, 0] for k in ("follows", "part_of", "transitive")}
    for f in sorted(d.glob("*.compress.json")):
        case = f.name.removesuffix(".compress.json")
        payload = json.loads(f.read_text(encoding="utf-8"))["payload"]
        an = payload_nodes(payload)
        gn = sgm.load_gold_nodes(gold_dir, case)
        m = sgm.align(gn, an)
        events[0] += len(m)
        events[1] += len(an)
        events[2] += len(gn)
        for k in ("follows", "part_of"):
            s = sgm.score_edges(gn, an, m, k)
            edges[k][0] += s["correct"]
            edges[k][1] += s["arm_edges"]
            edges[k][2] += s["gold_edges"]
        g_reach, a_reach = reach(gn), reach(an)
        g_pairs = {(s, t) for s in g_reach for t in g_reach[s]}
        a_pairs = {
            (m[s], m[t]) for s in a_reach for t in a_reach[s] if s in m and t in m
        }
        t = edges["transitive"]
        t[0] += len(g_pairs & a_pairs)
        t[1] += len(a_pairs)
        t[2] += len(g_pairs)
    return events, edges


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--gold-dir", type=Path, default=Path("annotation/test"))
    ap.add_argument(
        "runs", nargs="+", help="LABEL=DIR, DIR holding <case>.compress.json"
    )
    args = ap.parse_args()
    print(
        f"{'run':30} {'ev':>4} {'P':>3} {'R':>3} {'evF1':>5} | {'follows':>12} {'part_of':>11} {'transitive':>13}"
    )
    for run in args.runs:
        label, d = run.split("=", 1)
        ev, e = score_dir(Path(d), args.gold_dir)

        def edge(k, e=e):
            return f"{e[k][0]}/{e[k][1]} {f1(*e[k]):4.1f}"

        print(
            f"{label:30} {ev[1]:4} {100 * ev[0] / max(1, ev[1]):3.0f} "
            f"{100 * ev[0] / max(1, ev[2]):3.0f} {f1(*ev):5.1f} | "
            f"{edge('follows'):>12} {edge('part_of'):>11} {edge('transitive'):>13}"
        )


if __name__ == "__main__":
    main()
