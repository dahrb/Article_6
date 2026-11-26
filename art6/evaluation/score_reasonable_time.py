"""Score the chain-based reasonable-time rule against the Court's own period.

    uv run python -m art6.evaluation.score_reasonable_time \
        [--labels annotation/court_periods.yaml] LABEL=GRAPH_DIR ...

`gold` (the set-model annotations in --gold-dir) is always
scored first: it is the rule's error with the chain right, so the gap between it
and a system is the chain's share of the error.

Per case: the inferred start / end and the signed error in months against the
Court's. Per system: MAE in months of start, end and duration over the cases it
gave a period for, and how many it gave one for (`n`). A case with no graph, or
no set holding a start act, is counted as missing, not as an error.
LABEL=DIR holds <case>.facts.ttl graphs, or <case>.json / .yaml set-model
documents (an extraction, a vote, an annotator's files).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from art6.evaluation import reasonable_time as rt
from art6.evaluation.score import load_pred
from art6.model import load_doc

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


def system_acts(d: Path, case: str):
    doc = load_pred(d, case)
    return rt.acts_from_doc(doc) if doc["events"] else None


def gold_acts(gold_dir: Path, case: str):
    p = gold_dir / f"{case}.yaml"
    return rt.acts_from_doc(load_doc(p)) if p.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--labels", type=Path, default=Path("annotation/court_periods.yaml")
    )
    ap.add_argument("--gold-dir", type=Path, default=Path("annotation/test_simple"))
    ap.add_argument(
        "systems", nargs="*", help="LABEL=DIR of graphs or set-model documents"
    )
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
                gold_acts(args.gold_dir, case)
                if d is None
                else system_acts(Path(d), case)
            )
            if not acts:
                print(f"  {name:20} -- no {'gold' if d is None else 'output'}")
                continue
            p = rt.period(acts, limb, jdate, m["respondent"])
            if p is None:
                print(f"  {name:20} -- no set with a start act")
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
