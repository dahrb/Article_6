"""Score the chain-based reasonable-time rule against the Court's own period.

    uv run python -m art6.ontology.diagnostics.score_reasonable_time \
        [--labels annotation/court_periods.yaml] LABEL=GRAPH_DIR ...

`gold` (the annotated chains in annotation/test and annotation/gold) is always
scored first: it is the rule's error with the chain right, so the gap between it
and a system is the chain's share of the error.

Per case: the inferred start / end and the signed error in months against the
Court's. Per system: MAE in months of start, end and duration over the cases it
gave a period for, and how many it gave one for (`n`). A case with no graph, or
no chain holding a start act, is counted as missing, not as an error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from art6.ontology import reasonable_time as rt

GOLD_DIRS = (Path("annotation/test"), Path("annotation/gold"))
META = Path("data/art_6_judgments_metadata_processed.json")
MONTH = 30.44


def metadata(cases: set[str]) -> dict:
    out = {}
    with META.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["itemid"] in cases:
                out[r["itemid"]] = r
    return out


def court_period(label: dict, judgment_date: str):
    end = judgment_date if label["end"] == "pending" else label["end"]
    return rt.interval(str(label["start"])), rt.interval(str(end))


def months(a, b) -> float:
    return (rt.midpoint(b) - rt.midpoint(a)).days / MONTH


def gold_acts(case: str):
    for d in GOLD_DIRS:
        p = d / f"{case}.yaml"
        if p.exists():
            return rt.acts_from_gold(p)
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--labels", type=Path, default=Path("annotation/court_periods.yaml")
    )
    ap.add_argument("systems", nargs="*", help="LABEL=DIR of <case>.facts.ttl graphs")
    args = ap.parse_args()

    labels = yaml.safe_load(args.labels.read_text(encoding="utf-8"))
    meta = metadata(set(labels))
    systems = {"gold": None} | dict(s.split("=", 1) for s in args.systems)
    errs = {name: [] for name in systems}
    for case, label in labels.items():
        m = meta[case]
        jdate = m["judgementdate"][:10]
        limb = label.get("limb") or m["article_6_limb"]
        cs, ce = court_period(label, jdate)
        print(
            f"\n{case} {limb} {m['respondent']}  Court {cs[0]} -> "
            f"{'pending' if label['end'] == 'pending' else ce[0]} "
            f"({months(cs, ce):.0f} mo)"
        )
        for name, d in systems.items():
            acts = (
                gold_acts(case)
                if d is None
                else rt.acts_from_graph(Path(d) / f"{case}.facts.ttl")
            )
            if not acts:
                print(f"  {name:20} -- no {'gold' if d is None else 'graph'}")
                continue
            p = rt.period(acts, limb, jdate, m["respondent"])
            if p is None:
                print(f"  {name:20} -- no chain with a start act")
                continue
            e = (
                months(cs, p.start),
                months(ce, p.end),
                p.days / MONTH - months(cs, ce),
            )
            errs[name].append(e)
            print(
                f"  {name:20} {rt.midpoint(p.start)} -> "
                f"{'pending' if p.pending else rt.midpoint(p.end)}"
                f"{' (clipped)' if p.clipped else ''}"
                f"  start {e[0]:+6.1f}  end {e[1]:+6.1f}  length {e[2]:+6.1f} mo"
                f"  [chain of {p.chain_size}]"
            )
    print(f"\n{'MAE (months)':20} {'n':>4} {'start':>7} {'end':>7} {'length':>7}")
    for name, es in errs.items():
        if not es:
            print(f"  {name:18} {0:>4}")
            continue
        mae = [sum(abs(e[i]) for e in es) / len(es) for i in range(3)]
        print(
            f"  {name:18} {len(es):>2}/{len(labels):<2}"
            f" {mae[0]:7.1f} {mae[1]:7.1f} {mae[2]:7.1f}"
        )


if __name__ == "__main__":
    main()
