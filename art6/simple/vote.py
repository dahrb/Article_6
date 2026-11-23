"""Vote across simplified-extractor runs.

    uv run python -m art6.simple.vote --out OUT [--min-runs 2] RUN_DIR ...

The first run is the base. Every other run is aligned to it with the strict
scorer's alignment (date gate + quote, start with start). An event is KEPT if
at least --min-runs runs (the base included) have it. An event the base lacks
is ADDED when at least --min-runs of the other runs agree on it (aligned to
each other); it joins the base set that most of its own set-mates map to, or a
new set. Field values (level, outcome, direction) are majority-voted over the
runs that have the event, the base winning ties. Sets keep the base's ids; a
set is pending if most runs holding it say so.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from art6.ontology.diagnostics import score_graph_metrics as sgm
from art6.simple.model import load_doc, nodes


def align(base: dict, other: dict) -> dict:
    """other event id -> base event id."""
    return sgm.align(nodes(base), nodes(other))


def majority(values, base_value):
    c = collections.Counter(v for v in values if v is not None)
    if not c:
        return base_value
    top = max(c.values())
    return base_value if c.get(base_value) == top else c.most_common(1)[0][0]


def vote(docs: list[dict], min_runs: int) -> dict:
    base, others = docs[0], docs[1:]
    maps = [align(base, o) for o in others]  # o id -> base id
    inv = [{b: o for o, b in m.items()} for m in maps]
    bev = {e["id"]: e for e in base["events"]}
    kept = []
    for e in base["events"]:
        support = [o for o, iv in zip(others, inv, strict=True) if e["id"] in iv]
        if 1 + len(support) < min_runs:
            continue
        e = dict(e)
        if e["role"] == "decision":
            same = [
                next(x for x in o["events"] if x["id"] == iv[e["id"]])
                for o, iv in zip(others, inv, strict=True)
                if e["id"] in iv
            ]
            for k in ("level", "outcome", "direction"):
                e[k] = majority([e.get(k)] + [x.get(k) for x in same], e.get(k))
        kept.append(e)

    # events the base lacks, agreed by >= min_runs other runs
    added, new_sets = [], {}
    pool = [
        (i, x)
        for i, (o, m) in enumerate(zip(others, maps, strict=True))
        for x in o["events"]
        if x["id"] not in m
    ]
    used = set()
    for a_i, (i, x) in enumerate(pool):
        if (i, x["id"]) in used:
            continue
        cluster = [(i, x)]
        for j in range(i + 1, len(others)):
            cand = {
                "sets": others[j]["sets"],
                "events": [
                    y for (jj, y) in pool if jj == j and (jj, y["id"]) not in used
                ],
            }
            single = {"sets": others[i]["sets"], "events": [x]}
            m = sgm.align(nodes(single), nodes(cand))
            hit = next((k for k, v in m.items() if v == x["id"]), None)
            if hit:
                cluster.append((j, next(y for y in cand["events"] if y["id"] == hit)))
        if len(cluster) < min_runs:
            continue
        used.update((j, y["id"]) for j, y in cluster)
        # its set: where its set-mates in run i map to in the base
        mates = [
            maps[i].get(y["id"])
            for y in others[i]["events"]
            if y["set"] == x["set"] and y["id"] != x["id"]
        ]
        targets = collections.Counter(bev[b]["set"] for b in mates if b and b in bev)
        if targets:
            set_id = targets.most_common(1)[0][0]
        else:
            set_id = new_sets.setdefault((i, x["set"]), f"V{len(new_sets) + 1}")
        e = dict(x, id=f"v{a_i}_{x['id']}", set=set_id)
        if e["role"] == "decision":
            for k in ("level", "outcome", "direction"):
                e[k] = majority([y.get(k) for _, y in cluster], x.get(k))
        added.append(e)

    events = kept + added
    used_sets = {e["set"] for e in events}
    sets = [dict(s) for s in base["sets"] if s["id"] in used_sets]
    for (i, sid), vid in new_sets.items():
        src = next(
            (s for s in others[i]["sets"] if s["id"] == sid),
            {"kind": "other", "label": ""},
        )
        sets.append(
            {
                "id": vid,
                "kind": src.get("kind", "other"),
                "label": src.get("label", ""),
                "pending": bool(src.get("pending")),
            }
        )
    # pending: set ids differ between runs, so go through aligned events -- each
    # other run votes with the set holding most of its events aligned to this one
    base_ids = {x["id"] for x in base["sets"]}
    for s in sets:
        if s["id"] not in base_ids:
            continue
        votes = [bool(s.get("pending"))]
        members = {e["id"] for e in base["events"] if e["set"] == s["id"]}
        for o, iv in zip(others, inv, strict=True):
            oev = {x["id"]: x for x in o["events"]}
            theirs = collections.Counter(oev[iv[b]]["set"] for b in members if b in iv)
            if theirs:
                sid = theirs.most_common(1)[0][0]
                votes.append(
                    bool(
                        next(
                            (x.get("pending") for x in o["sets"] if x["id"] == sid),
                            False,
                        )
                    )
                )
        s["pending"] = sum(votes) * 2 > len(votes)
    return {"case_id": base["case_id"], "sets": sets, "events": events}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--min-runs", type=int, default=2)
    ap.add_argument("runs", nargs="+", type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for f in sorted(a.runs[0].glob("*.json")):
        docs = [load_doc(f)] + [
            load_doc(r / f.name) for r in a.runs[1:] if (r / f.name).exists()
        ]
        (a.out / f.name).write_text(
            json.dumps(vote(docs, a.min_runs), indent=1, ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
