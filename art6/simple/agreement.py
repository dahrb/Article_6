"""Run-to-run agreement: how stable is a definition, without any gold?

    uv run python -m art6.simple.agreement simple RUN_DIR RUN_DIR ...
    uv run python -m art6.simple.agreement payload S1_DIR S1_DIR ...

For every pair of runs, the strict alignment of one against the other gives an
agreement F1 on events (2 x aligned / (n_a + n_b)), and on derived or asserted
`follows` edges between aligned events. Reported as the mean over pairs. A
clearer task should give higher agreement between independent runs and models.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from statistics import mean

from art6.ontology.diagnostics import score_graph_metrics as sgm
from art6.ontology.vote import payload_nodes
from art6.simple.model import load_doc, nodes


def load(kind: str, d: Path) -> dict:
    out = {}
    if kind == "simple":
        for f in d.glob("*.json"):
            out[f.stem] = nodes(load_doc(f))
    else:
        for f in d.glob("*.compress.json"):
            out[f.name.removesuffix(".compress.json")] = payload_nodes(
                json.loads(f.read_text())["payload"]
            )
    return out


def pair(a: dict, b: dict) -> tuple[float, float]:
    ev_c = ev_n = fo_c = fo_n = 0
    for case in sorted(set(a) & set(b)):
        m = sgm.align(a[case], b[case])  # b key -> a key
        ev_c += 2 * len(m)
        ev_n += len(a[case]) + len(b[case])
        s = sgm.score_edges(a[case], b[case], m, "follows")
        fo_c += 2 * s["correct"]
        fo_n += s["arm_edges"] + s["gold_edges"]
    return 100 * ev_c / ev_n if ev_n else 0.0, 100 * fo_c / fo_n if fo_n else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("kind", choices=("simple", "payload"))
    ap.add_argument("runs", nargs="+", type=Path)
    a = ap.parse_args()
    runs = {r.name: load(a.kind, r) for r in a.runs}
    scores = []
    for x, y in itertools.combinations(runs, 2):
        ev, fo = pair(runs[x], runs[y])
        scores.append((ev, fo))
        print(f"  {x:>16} vs {y:<16} events {ev:5.1f}  follows {fo:5.1f}")
    print(
        f"mean over {len(scores)} pairs: events {mean(s[0] for s in scores):5.1f}  "
        f"follows {mean(s[1] for s in scores):5.1f}"
    )


if __name__ == "__main__":
    main()
