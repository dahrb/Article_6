"""Vote across stage-1 runs.

    uv run python -m art6.stage1.vote --out OUT [--min-runs 2] RUN_DIR ...

The first run is the base. Every other run is aligned to it with the strict
scorer's alignment (art6/evaluation/align.py). An event is KEPT if
at least --min-runs runs (the base included) have it. An event the base lacks
is ADDED when at least --min-runs of the other runs agree on it (aligned to
each other); it joins the base set that most of its own set-mates map to, or a
new set. Field values (level, outcome, direction, authority kind, matter,
appealed_by, costs, appeal and communication dates, and `reviews`, compared through the
alignment) are majority-voted over the runs that have the event, the base
winning ties; parties and quotes are the base's. Sets keep the base's ids; a
set is pending if most runs holding it say so.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from art6.evaluation import align as al
from art6.model import load_doc, nodes


def align(base: dict, other: dict) -> dict:
    """other event id -> base event id."""
    return al.align(nodes(base), nodes(other))


def majority(values, base_value):
    c = collections.Counter(v for v in values if v is not None)
    if not c:
        return base_value
    top = max(c.values())
    return base_value if c.get(base_value) == top else c.most_common(1)[0][0]


FIELDS = (
    "level",
    "outcome",
    "direction",
    "authority_kind",
    "appealed_by",
    "appeal_date",
    "communication_date",
    "costs",
)


def vote_fields(e: dict, same: list[tuple[dict, dict]]) -> None:
    """Majority-vote e's fields over the aligned events of the other runs
    (each with its run's other-id -> base-id map), e's own value winning ties.
    `reviews` is compared in base ids; matter as a set; parties stay e's."""
    for k in FIELDS:
        if e["role"] == "decision" or k == "authority_kind":
            e[k] = majority([e.get(k)] + [x.get(k) for x, _ in same], e.get(k))
    e["matter"] = list(
        majority(
            [tuple(sorted(e.get("matter") or []))]
            + [tuple(sorted(x.get("matter") or [])) for x, _ in same],
            tuple(sorted(e.get("matter") or [])),
        )
    )
    if e["role"] == "decision":
        theirs = [
            m.get(x.get("reviews")) if x.get("reviews") else "none" for x, m in same
        ]
        mine = e.get("reviews") or "none"
        r = majority([mine] + theirs, mine)
        e["reviews"] = None if r == "none" else r


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
        same = [
            (next(x for x in o["events"] if x["id"] == iv[e["id"]]), m)
            for o, iv, m in zip(others, inv, maps, strict=True)
            if e["id"] in iv
        ]
        vote_fields(e, same)
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
            m = al.align(nodes(single), nodes(cand))
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
        e["reviews"] = maps[i].get(x.get("reviews")) if x.get("reviews") else None
        vote_fields(e, [(y, maps[j]) for j, y in cluster[1:]])
        added.append(e)

    events = kept + added
    used_sets = {e["set"] for e in events}
    sets = [dict(s) for s in base["sets"] if s["id"] in used_sets]
    for (i, sid), vid in new_sets.items():
        src = next(
            (s for s in others[i]["sets"] if s["id"] == sid),
            {"kind": "Unknown", "label": ""},
        )
        sets.append(
            {
                "id": vid,
                "kind": src.get("kind", "Unknown"),
                "label": src.get("label", ""),
                "pending": bool(src.get("pending")),
                "enforces": None,
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
    # reviews/enforces must point at a kept event of the same set
    ids = {e["id"]: e["set"] for e in events if e["role"] == "decision"}
    for e in events:
        if e["role"] == "decision" and ids.get(e.get("reviews")) != e["set"]:
            e["reviews"] = None
        if e["role"] == "decision" and not e.get("reviews"):
            e["appealed_by"] = e["appeal_date"] = None
    for s in sets:
        if s.get("enforces") not in ids:
            s["enforces"] = None
    return {
        "case_id": base["case_id"],
        "sets": sets,
        "events": events,
        **{k: base[k] for k in ("case", "applicants", "persons") if k in base},
    }


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
