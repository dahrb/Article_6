"""Score simplified documents against the simplified gold.

    uv run python -m art6.simple.score [--gold-dir annotation/test_simple] LABEL=DIR ...

DIR holds <case>.json in the simplified format (art6/simple/model.py). Events
are aligned with the strict scorer's own alignment (score_graph_metrics.align:
date gate + quote similarity, a start only ever pairs with a start).

Columns
  ev P/R/F1      all events (starts + decisions)
  dec F1, st F1  decisions only, starts only
  lvl out dir    accuracy over aligned decisions
  follows        DERIVED order (date order inside each set), correct/asserted F1
  set P/R/F1     pairwise "same set" over aligned events (the chain measure)
  n(gold)        number of sets
  start end lvls the Court's three appendix columns, per gold set, read off the
                 predicted set that holds most of its aligned events: start date,
                 end date (or pending) at gold's precision, and the number of
                 court levels (first instance / appeal / cassation /
                 constitutional), as % of gold sets
"""

from __future__ import annotations

import argparse
import collections
import itertools
from pathlib import Path

from art6.ontology.diagnostics import score_graph_metrics as sgm
from art6.simple.model import load_doc, nodes, ordered

COURT_LEVELS = {"first_instance", "appeal", "cassation", "constitutional"}


def f1(c, a, g):
    return 200 * c / (a + g) if a + g else 0.0


def set_summary(doc: dict, set_id: str) -> tuple:
    seq = ordered(doc, set_id)
    pending = next(s.get("pending") for s in doc["sets"] if s["id"] == set_id)
    decisions = [e for e in seq if e["role"] == "decision"]
    start = seq[0]["date"] if seq else None
    end = (
        "pending"
        if pending
        else (
            max(
                (e["date"] for e in decisions if e.get("date")),
                default=None,
                key=lambda d: d.ljust(10, "9"),
            )
        )
    )
    levels = len({e.get("level") for e in decisions} & COURT_LEVELS)
    return start, end, levels


def same_at(gold: str | None, pred: str | None) -> bool:
    if gold is None or pred is None:
        return False
    if gold == "pending" or pred == "pending":
        return gold == pred
    return pred[: len(gold)] == gold


def score(run_dir: Path, gold_dir: Path) -> dict:
    t = collections.Counter()
    for gf in sorted(gold_dir.glob("*.json")):
        gold = load_doc(gf)
        pf = run_dir / gf.name
        pred = load_doc(pf) if pf.exists() else {"sets": [], "events": []}
        gn, pn = nodes(gold), nodes(pred)
        m = sgm.align(gn, pn)  # pred key -> gold key
        gev = {e["id"]: e for e in gold["events"]}
        pev = {e["id"]: e for e in pred["events"]}
        t["c"] += len(m)
        t["a"] += len(pn)
        t["g"] += len(gn)
        for role in ("decision", "start"):
            t[f"{role}_c"] += sum(1 for p in m if pev[p]["role"] == role)
            t[f"{role}_a"] += sum(1 for e in pred["events"] if e["role"] == role)
            t[f"{role}_g"] += sum(1 for e in gold["events"] if e["role"] == role)
        for p, g in m.items():
            if gev[g]["role"] != "decision":
                continue
            for k in ("level", "outcome", "direction"):
                if gev[g].get(k) is not None:
                    t[f"{k}_n"] += 1
                    t[f"{k}_ok"] += pev[p].get(k) == gev[g][k]
        # derived follows
        s = sgm.score_edges(gn, pn, m, "follows")
        t["fo_c"] += s["correct"]
        t["fo_a"] += s["arm_edges"]
        t["fo_g"] += s["gold_edges"]
        # same-set pairs over aligned events
        inv = {g: p for p, g in m.items()}
        aligned = sorted(inv)
        for x, y in itertools.combinations(aligned, 2):
            gs = gev[x]["set"] == gev[y]["set"]
            ps = pev[inv[x]]["set"] == pev[inv[y]]["set"]
            t["ss_c"] += gs and ps
            t["ss_a"] += ps
            t["ss_g"] += gs
        t["sets_g"] += len(gold["sets"])
        t["sets_p"] += len(pred["sets"])
        # appendix columns per gold set
        for gs in gold["sets"]:
            members = [
                inv[e["id"]]
                for e in gold["events"]
                if e["set"] == gs["id"] and e["id"] in inv
            ]
            t["col_n"] += 1
            if not members:
                continue
            ps = collections.Counter(pev[p]["set"] for p in members).most_common(1)[0][
                0
            ]
            g_start, g_end, g_lv = set_summary(gold, gs["id"])
            p_start, p_end, p_lv = set_summary(pred, ps)
            t["col_start"] += same_at(g_start, p_start)
            t["col_end"] += same_at(g_end, p_end)
            t["col_lvls"] += g_lv == p_lv
    return t


def row(label: str, t: dict) -> str:
    acc = lambda k: 100 * t[f"{k}_ok"] / t[f"{k}_n"] if t[f"{k}_n"] else 0.0
    pct = lambda k: 100 * t[k] / t["col_n"] if t["col_n"] else 0.0
    p = 100 * t["c"] / t["a"] if t["a"] else 0.0
    r = 100 * t["c"] / t["g"] if t["g"] else 0.0
    ssp = 100 * t["ss_c"] / t["ss_a"] if t["ss_a"] else 0.0
    ssr = 100 * t["ss_c"] / t["ss_g"] if t["ss_g"] else 0.0
    return (
        f"{label:22} {t['a']:4d} {p:4.0f} {r:4.0f} {f1(t['c'], t['a'], t['g']):5.1f} | "
        f"{f1(t['decision_c'], t['decision_a'], t['decision_g']):5.1f} {f1(t['start_c'], t['start_a'], t['start_g']):5.1f} | "
        f"{acc('level'):4.0f} {acc('outcome'):4.0f} {acc('direction'):4.0f} | "
        f"{t['fo_c']:3d}/{t['fo_a']:<3d} {f1(t['fo_c'], t['fo_a'], t['fo_g']):5.1f} | "
        f"{ssp:4.0f} {ssr:4.0f} {f1(t['ss_c'], t['ss_a'], t['ss_g']):5.1f} {t['sets_p']:3d}({t['sets_g']}) | "
        f"{pct('col_start'):4.0f} {pct('col_end'):4.0f} {pct('col_lvls'):4.0f}"
    )


HEADER = (
    f"{'run':22} {'ev':>4} {'P':>4} {'R':>4} {'F1':>5} | {'dec':>5} {'st':>5} | "
    f"{'lvl':>4} {'out':>4} {'dir':>4} | {'follows':>13} | {'setP':>4} {'setR':>4} {'setF':>5} {'n(g)':>7} | "
    f"{'strt':>4} {'end':>4} {'lvls':>4}"
)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--gold-dir", type=Path, default=Path("annotation/test_simple"))
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args()
    print(HEADER)
    for spec in a.runs:
        label, d = spec.split("=", 1)
        print(row(label, score(Path(d), a.gold_dir)))


if __name__ == "__main__":
    main()
