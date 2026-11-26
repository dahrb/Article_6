"""Score set-model output against the set-model gold (annotation/test_simple by default).

    uv run python -m art6.evaluation.score [--gold-dir annotation/test_simple] LABEL=PATH ...

PATH is a directory of flat set-model documents (<case>.json: an extraction,
a vote, an annotator's YAML) or of final knowledge graphs (<case>.facts.ttl:
stage 3 output, read by art6/evaluation/kg.py). Events are aligned by
art6/evaluation/align.py (date gate + quote similarity; a start act only
pairs with a start act).

Columns
  ev P R F1        all events; dec / st = decisions / start acts only (F1)
  lvl out dir      accuracy over aligned decisions: level, outcome, direction
  auth             authority kind, over aligned events
  mat              matter, exact set match over aligned events where gold has one
  side             the applicant's side, over aligned events where gold
                   names the applicant as a party
  appby            appealed_by, over aligned decisions where gold has one
  costs            costs, over aligned decisions where gold has one
  reviews          `reviews` links: correct / asserted, F1
  follows          DERIVED order (start, then decisions by date, per set): F1
  set F1           pairwise "same set" over aligned events
  strt end lvls    the Court's appendix columns per gold set, read off the
                   predicted set holding most of its aligned events: start
                   date, end date (or pending) at gold's precision, number of
                   court levels -- % of gold sets
"""

from __future__ import annotations

import argparse
import collections
import itertools
from pathlib import Path

from art6.evaluation import align as al
from art6.model import is_applicant, load_doc, nodes, ordered

COURT_LEVELS = {"FirstInstance", "Appeal", "Cassation", "Constitutional"}


def f1(c, a, g):
    return 200 * c / (a + g) if a + g else 0.0


def load_pred(path: Path, case: str) -> dict:
    ttl = path / f"{case}.facts.ttl"
    if ttl.exists():
        from art6.evaluation.kg import read_kg

        return read_kg(ttl, case)
    for ext in (".json", ".yaml"):
        f = path / f"{case}{ext}"
        if f.exists():
            return load_doc(f)
    return {"case_id": case, "sets": [], "events": []}


def set_summary(doc: dict, set_id: str) -> tuple:
    seq = ordered(doc, set_id)
    pending = next((s.get("pending") for s in doc["sets"] if s["id"] == set_id), False)
    decisions = [e for e in seq if e["role"] == "decision"]
    start = seq[0]["date"] if seq else None
    end = (
        "pending"
        if pending
        else max(
            (e["date"] for e in decisions if e.get("date")),
            default=None,
            key=lambda d: d.ljust(10, "9"),
        )
    )
    return start, end, len({e.get("level") for e in decisions} & COURT_LEVELS)


def same_at(gold, pred) -> bool:
    if gold is None or pred is None:
        return False
    if "pending" in (gold, pred):
        return gold == pred
    return str(pred)[: len(gold)] == gold


def _applicant_side(e: dict, names) -> str | None:
    sides = [
        p.get("side")
        for p in e.get("parties") or []
        if is_applicant(p.get("name"), names)
    ]
    return sides[0] if sides else None


def score(run: Path, gold_dir: Path) -> collections.Counter:
    t = collections.Counter()
    for gf in sorted(gold_dir.glob("0*.yaml")):
        gold = load_doc(gf)
        pred = load_pred(run, gold["case_id"])
        names = set(pred.get("applicant_names") or []) | {
            a.get("name") for a in pred.get("applicants") or [] if a.get("name")
        }
        gn, pn = nodes(gold), nodes(pred)
        m = al.align(gn, pn)  # pred id -> gold id
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
            ge, pe = gev[g], pev[p]
            if ge["role"] == "decision":
                for k in ("level", "outcome", "direction"):
                    t[f"{k}_n"] += 1
                    t[f"{k}_ok"] += pe.get(k) == ge.get(k)
                if ge.get("appealed_by"):
                    t["appby_n"] += 1
                    t["appby_ok"] += pe.get("appealed_by") == ge["appealed_by"]
                if ge.get("costs"):
                    t["costs_n"] += 1
                    t["costs_ok"] += pe.get("costs") == ge["costs"]
            t["auth_n"] += 1
            t["auth_ok"] += (pe.get("authority_kind") or "Unknown") == (
                ge.get("authority_kind") or "Unknown"
            )
            if ge.get("matter"):
                t["mat_n"] += 1
                t["mat_ok"] += set(pe.get("matter") or []) == set(ge["matter"])
            gs = _applicant_side(ge, set())
            if gs:
                t["side_n"] += 1
                t["side_ok"] += _applicant_side(pe, names) == gs
        for kind in ("follows", "reviews"):
            s = al.score_edges(gn, pn, m, kind)
            t[f"{kind}_c"] += s["correct"]
            t[f"{kind}_a"] += s["arm_edges"]
            t[f"{kind}_g"] += s["gold_edges"]
        inv = {g: p for p, g in m.items()}
        for x, y in itertools.combinations(sorted(inv), 2):
            gs_ = gev[x]["set"] == gev[y]["set"]
            ps_ = pev[inv[x]]["set"] == pev[inv[y]]["set"]
            t["ss_c"] += gs_ and ps_
            t["ss_a"] += ps_
            t["ss_g"] += gs_
        t["sets_g"] += len(gold["sets"])
        t["sets_p"] += len({e["set"] for e in pred["events"]})
        for gset in gold["sets"]:
            members = [
                inv[e["id"]]
                for e in gold["events"]
                if e["set"] == gset["id"] and e["id"] in inv
            ]
            t["col_n"] += 1
            if not members:
                continue
            ps = collections.Counter(pev[p]["set"] for p in members).most_common(1)[0][
                0
            ]
            g_start, g_end, g_lv = set_summary(gold, gset["id"])
            p_start, p_end, p_lv = set_summary(pred, ps)
            t["col_start"] += same_at(g_start, p_start)
            t["col_end"] += same_at(g_end, p_end)
            t["col_lvls"] += g_lv == p_lv
    return t


def row(label: str, t) -> str:
    def acc(k):
        return 100 * t[f"{k}_ok"] / t[f"{k}_n"] if t[f"{k}_n"] else 0.0

    def pct(k):
        return 100 * t[k] / t["col_n"] if t["col_n"] else 0.0

    p = 100 * t["c"] / t["a"] if t["a"] else 0.0
    r = 100 * t["c"] / t["g"] if t["g"] else 0.0
    return (
        f"{label:24} {t['a']:4d} {p:4.0f} {r:4.0f} {f1(t['c'], t['a'], t['g']):5.1f} | "
        f"{f1(t['decision_c'], t['decision_a'], t['decision_g']):5.1f} {f1(t['start_c'], t['start_a'], t['start_g']):5.1f} | "
        f"{acc('level'):4.0f} {acc('outcome'):4.0f} {acc('direction'):4.0f} {acc('auth'):4.0f} "
        f"{acc('mat'):4.0f} {acc('side'):4.0f} {acc('appby'):5.0f} {acc('costs'):5.0f} | "
        f"{t['reviews_c']:3d}/{t['reviews_a']:<3d} {f1(t['reviews_c'], t['reviews_a'], t['reviews_g']):5.1f} | "
        f"{t['follows_c']:3d}/{t['follows_a']:<3d} {f1(t['follows_c'], t['follows_a'], t['follows_g']):5.1f} | "
        f"{f1(t['ss_c'], t['ss_a'], t['ss_g']):5.1f} {t['sets_p']:3d}({t['sets_g']}) | "
        f"{pct('col_start'):4.0f} {pct('col_end'):4.0f} {pct('col_lvls'):4.0f}"
    )


HEADER = (
    f"{'run':24} {'ev':>4} {'P':>4} {'R':>4} {'F1':>5} | {'dec':>5} {'st':>5} | "
    f"{'lvl':>4} {'out':>4} {'dir':>4} {'auth':>4} {'mat':>4} {'side':>4} {'appby':>5} {'costs':>5} | "
    f"{'reviews':>13} | {'follows':>13} | {'setF':>5} {'n(g)':>7} | {'strt':>4} {'end':>4} {'lvls':>4}"
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
