"""Agreement between any two sources of set-model annotations, with no gold.

    uv run python -m art6.evaluation.agreement A=DIR B=DIR [C=DIR ...] [--cases FILE] [--boot 2000]

A source is a directory of <case>.yaml (an annotator: annotation_tool/ writes
these), <case>.json (an extraction, a vote) or <case>.facts.ttl (a final
graph). Every pair of sources is compared on the cases both have (or on
--cases, a file of case ids, one per line; a case one source lacks then counts
as an empty annotation).

Agreement is symmetric: events are aligned 1:1 by art6/evaluation/align.py
(the scorer's rule), so

  events / decisions / starts   F1 = 2 x aligned / (n_A + n_B)
  follows / reviews             F1 over edges between aligned events
  same set                      pairwise F1 over aligned events: do A and B
                                put the same two events in one set?
  level ... costs               over aligned decisions (events, for
                                authority_kind and matter): raw agreement and
                                Cohen's kappa
  start / end                   per set of A matched to B's set holding most
                                of its aligned events: same date at A's
                                precision (the Court's appendix columns)

95% intervals come from a bootstrap over documents (--boot resamples), so a
document with many events weighs what it weighs in the point estimate.
"""

from __future__ import annotations

import argparse
import collections
import itertools
import random
from pathlib import Path

from art6.evaluation import align as al
from art6.evaluation.score import load_pred, same_at, set_summary
from art6.model import nodes

FIELDS = (
    "level",
    "outcome",
    "direction",
    "authority_kind",
    "matter",
    "appealed_by",
    "costs",
)
STRUCT = (
    "events",
    "decisions",
    "starts",
    "follows",
    "reviews",
    "same_set",
    "start",
    "end",
)


def cases_in(d: Path) -> set[str]:
    out = set()
    for f in d.iterdir():
        for suf in (".facts.ttl", ".yaml", ".json"):
            if f.name.endswith(suf) and f.name.startswith("0"):
                out.add(f.name.removesuffix(suf))
    return out


def compare(a: dict, b: dict) -> dict:
    """Counts for one document: c (agreed), n (total both sides) per measure,
    and the (a, b) value pairs per field."""
    an, bn = nodes(a), nodes(b)
    m = al.align(an, bn)  # b id -> a id
    aev = {e["id"]: e for e in a["events"]}
    bev = {e["id"]: e for e in b["events"]}
    out = collections.defaultdict(lambda: [0, 0])
    pairs = collections.defaultdict(list)

    def add(k, c, n):
        out[k][0] += c
        out[k][1] += n

    add("events", 2 * len(m), len(an) + len(bn))
    for role, key in (("decision", "decisions"), ("start", "starts")):
        c = sum(1 for x in m if bev[x]["role"] == role)
        add(
            key,
            2 * c,
            sum(e["role"] == role for e in a["events"])
            + sum(e["role"] == role for e in b["events"]),
        )
    for kind in ("follows", "reviews"):
        s = al.score_edges(an, bn, m, kind)
        add(kind, 2 * s["correct"], s["arm_edges"] + s["gold_edges"])
    inv = {y: x for x, y in m.items()}  # a id -> b id
    for x, y in itertools.combinations(sorted(inv), 2):
        sa = aev[x]["set"] == aev[y]["set"]
        sb = bev[inv[x]]["set"] == bev[inv[y]]["set"]
        add("same_set", 2 * (sa and sb), sa + sb)
    for bid, aid in m.items():
        ea, eb = aev[aid], bev[bid]
        for f in FIELDS:
            if f in ("authority_kind", "matter") or ea["role"] == "decision":
                va, vb = ea.get(f), eb.get(f)
                if f == "matter":
                    va, vb = "+".join(sorted(va or [])), "+".join(sorted(vb or []))
                pairs[f].append((va or "-", vb or "-"))
    for s in a["sets"]:
        members = [
            inv[e["id"]] for e in a["events"] if e["set"] == s["id"] and e["id"] in inv
        ]
        a_start, a_end, _ = set_summary(a, s["id"])
        if not members:
            add("start", 0, 1)
            add("end", 0, 1)
            continue
        bs = collections.Counter(bev[x]["set"] for x in members).most_common(1)[0][0]
        b_start, b_end, _ = set_summary(b, bs)
        add("start", same_at(a_start, b_start), 1)
        add("end", same_at(a_end, b_end), 1)
    return {"counts": dict(out), "pairs": dict(pairs)}


def kappa(pairs: list[tuple]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(x == y for x, y in pairs) / n
    ca = collections.Counter(x for x, _ in pairs)
    cb = collections.Counter(y for _, y in pairs)
    pe = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def summarise(docs: list[dict]) -> dict:
    out = {}
    for k in STRUCT:
        c = sum(d["counts"].get(k, [0, 0])[0] for d in docs)
        n = sum(d["counts"].get(k, [0, 0])[1] for d in docs)
        out[k] = 100 * c / n if n else None
    for f in FIELDS:
        p = [x for d in docs for x in d["pairs"].get(f, [])]
        out[f] = 100 * sum(x == y for x, y in p) / len(p) if p else None
        out[f"{f}_k"] = kappa(p)
        out[f"{f}_n"] = len(p)
    return out


def bootstrap(docs: list[dict], boot: int, seed: int = 0) -> dict:
    rng = random.Random(seed)
    draws = collections.defaultdict(list)
    for _ in range(boot):
        s = summarise([rng.choice(docs) for _ in docs])
        for k, v in s.items():
            if v is not None:
                draws[k].append(v)
    ci = {}
    for k, v in draws.items():
        v.sort()
        ci[k] = (v[int(0.025 * len(v))], v[min(len(v) - 1, int(0.975 * len(v)))])
    return ci


def fmt(v, ci=None, kappa_=False) -> str:
    if v is None:
        return "     -"
    s = f"{v:.2f}" if kappa_ else f"{v:5.1f}"
    if ci:
        lo, hi = ci
        s += f" [{lo:.2f}-{hi:.2f}]" if kappa_ else f" [{lo:4.1f}-{hi:4.1f}]"
    return s


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("sources", nargs="+", help="LABEL=DIR")
    ap.add_argument(
        "--cases",
        type=Path,
        help="file of case ids to compare on (default: cases both have)",
    )
    ap.add_argument(
        "--boot", type=int, default=2000, help="bootstrap resamples (0: none)"
    )
    a = ap.parse_args()
    src = dict(s.split("=", 1) for s in a.sources)
    wanted = a.cases.read_text().split() if a.cases else None
    for (la, da), (lb, db) in itertools.combinations(src.items(), 2):
        da, db = Path(da), Path(db)
        cases = sorted(wanted or (cases_in(da) & cases_in(db)))
        docs = [compare(load_pred(da, c), load_pred(db, c)) for c in cases]
        s = summarise(docs)
        ci = bootstrap(docs, a.boot) if a.boot and len(docs) > 1 else {}
        print(f"\n{la} vs {lb}: {len(cases)} document(s)")
        print("  structure (F1 / % agreement)")
        for k in STRUCT:
            print(f"    {k:15} {fmt(s[k], ci.get(k))}")
        print("  fields on aligned events (raw %, Cohen's kappa, n)")
        for f in FIELDS:
            print(
                f"    {f:15} {fmt(s[f], ci.get(f))}   k {fmt(s[f + '_k'], ci.get(f + '_k'), True)}"
                f"   n {s[f + '_n']}"
            )


if __name__ == "__main__":
    main()
