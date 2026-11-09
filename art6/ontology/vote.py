"""Stage 1b: vote across several stage-1 runs of the same documents.

Single stage-1 runs disagree with each other: on the 10-document validation
set two identical Luna runs scored 45 and 56 `follows` F1. Voting across runs
of DIFFERENT models removes most of that variance and most of the spurious
events (2026-11-06: 2 Luna + 2 DeepSeek runs, events kept by >= 2 of 4, link
vote: event F1 87 -> 89, follows 58-60 -> 64-66; a fifth, same-model run made
it worse, and weighting the stronger model's votes did not help).

The vote works on MAJOR events only (`significance` != minor). Each other run
is aligned to the BASE run with the scorer's own aligner (the base plays gold),
then:

  event vote   a base event is kept if at least `min_runs` runs have it
               (the base counts as one);
  link vote    each kept event's predecessor (its follows/part_of target,
               walking through events the vote dropped) is the one most runs
               give; a tie keeps the base's. The link kind comes with it.

The base run's MINOR events are then put back as side branches
(with_minor): each keeps its own link when the target survived, otherwise it
moves up to the nearest surviving ancestor. Nothing major links to a minor
event, so major-only scores are the same with or without them.

    uv run python -m art6.ontology.vote --out DIR BASE_DIR OTHER_DIR [OTHER_DIR ...]
"""

from __future__ import annotations

import argparse
import collections
import copy
import json
import re
from pathlib import Path

from art6.ontology.agentic_stage1 import parse_date
from art6.ontology.diagnostics import score_graph_metrics as sgm

LINKS = ("follows", "part_of")
ALL_LINKS = ("follows", "part_of", "occurs_during")


# ---------------------------------------------------------------------------
# Payload views
# ---------------------------------------------------------------------------


def _text(span) -> str | None:
    return span.get("text") if isinstance(span, dict) else None


def _iso(span) -> str | None:
    d = parse_date(span["text"]) if isinstance(span, dict) else None
    if not d:
        return None
    return (
        f"{d[0]:04d}"
        + (f"-{d[1]:02d}" if d[1] else "")
        + (f"-{d[2]:02d}" if d[2] else "")
    )


_ISO = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def event_date(event: dict, kind: str) -> str | None:
    """`kind` is "decision" or "start": the normalised date, else the span parsed."""
    value = event.get(f"{kind}_date_iso")
    if isinstance(value, str) and _ISO.match(value.strip()):
        return value.strip()
    return _iso(event.get(f"{kind}_date_span"))


def major_only(payload: dict) -> dict:
    """The payload without minor events; links are contracted through them
    (a -> minor -> b becomes a -> b, keeping a's own link kind)."""
    by = {e["id"]: e for e in payload["events"]}
    minor = {i for i, e in by.items() if e.get("significance") == "minor"}

    def up(target, seen):
        while target in minor and target not in seen:
            seen.add(target)
            e = by[target]
            target = next((e[k] for k in sgm.EDGE_KINDS if e.get(k)), None)
        return None if target in minor else target

    events = []
    for e in payload["events"]:
        if e["id"] in minor:
            continue
        e = dict(e)
        for k in sgm.EDGE_KINDS:
            if e.get(k) in minor:
                t = up(e[k], set())
                if t:
                    e[k] = t
                else:
                    e.pop(k)
        events.append(e)
    return {**payload, "events": events}


def payload_nodes(payload: dict) -> list:
    """The payload's events as scorer nodes (score_graph_metrics.Node)."""
    out = []
    for e in payload["events"]:
        n = sgm.Node(
            key=e["id"],
            body=_text(e.get("authority_span")),
            decision_date=event_date(e, "decision"),
            start_date=event_date(e, "start"),
            quote=_text(e.get("what_happened_span")),
        )
        n.matters = {str(m).capitalize() for m in e.get("matter") or []}
        for k in sgm.EDGE_KINDS:
            if e.get(k):
                n.edges[k].add(e[k])
        out.append(n)
    return out


# ---------------------------------------------------------------------------
# The vote
# ---------------------------------------------------------------------------


def predecessor(payload: dict, eid: str, keep: set) -> tuple[str, str] | None:
    """(kind, target) of eid's first predecessor inside `keep`, walking
    through events outside it; None if the chain leaves `keep` empty-handed."""
    by = {e["id"]: e for e in payload["events"]}
    seen, cur, kind = set(), eid, None
    while True:
        e = by.get(cur)
        nxt = next(((k, e[k]) for k in LINKS if e and e.get(k)), None)
        if not nxt or nxt[1] in seen:
            return None
        kind = kind or nxt[0]
        cur = nxt[1]
        seen.add(cur)
        if cur in keep:
            return kind, cur


def vote(base: dict, others: list[dict], min_runs: int = 2) -> dict:
    """Vote over MAJOR-ONLY payloads (major_only). Returns the voted base."""
    base = copy.deepcopy(base)
    ids = {e["id"] for e in base["events"]}
    maps = [(o, sgm.align(payload_nodes(base), payload_nodes(o))) for o in others]
    support = collections.Counter({i: 1 for i in ids})
    for _, m in maps:
        for b in m.values():
            support[b] += 1
    keep = {i for i in ids if support[i] >= min_runs}
    for e in base["events"]:
        if e["id"] not in keep:
            continue
        mine = predecessor(base, e["id"], keep)
        votes = collections.Counter({mine: 1})
        for o, m in maps:
            inv = {v: k for k, v in m.items()}
            if e["id"] not in inv:
                continue
            theirs_keep = {k for k, v in m.items() if v in keep}
            r = predecessor(o, inv[e["id"]], theirs_keep)
            votes[(r[0], m[r[1]]) if r else None] += 1
        best = max(votes.items(), key=lambda kv: (kv[1], kv[0] == mine))[0]
        for k in LINKS:
            e.pop(k, None)
        if best:
            e[best[0]] = best[1]
    base["events"] = [e for e in base["events"] if e["id"] in keep]
    return base


def with_minor(voted: dict, full: dict) -> tuple[dict, int]:
    """Put the base run's minor events back onto the voted payload as side
    branches. A minor event keeps its link when the target survived, else
    moves up to the nearest surviving ancestor in the base run (same kind),
    else loses it. Circumstances and links pointing at a removed event lose
    that pointer. Returns (payload, how many minor events were added)."""
    by = {e["id"]: e for e in full["events"]}
    minor = [
        copy.deepcopy(e) for e in full["events"] if e.get("significance") == "minor"
    ]
    keep = {e["id"] for e in voted["events"]} | {e["id"] for e in minor}
    for e in minor:
        for k in ALL_LINKS:
            target, seen = e.get(k), set()
            while target and target not in keep and target not in seen:
                seen.add(target)
                up = by.get(target, {})
                target = next((up[x] for x in LINKS if up.get(x)), None)
            if e.get(k):
                if target and target in keep and target != e["id"]:
                    e[k] = target
                else:
                    e.pop(k)
    order = {e["id"]: i for i, e in enumerate(full["events"])}
    events = sorted(voted["events"] + minor, key=lambda e: order.get(e["id"], 1e9))
    ids = {e["id"] for e in events}
    payload = {**full, "events": events}
    for e in events:
        for k in ALL_LINKS:
            if e.get(k) and e[k] not in ids:
                e.pop(k)
    for c in payload.get("circumstances") or []:
        if c.get("gives_rise_to") and c["gives_rise_to"] not in ids:
            c.pop("gives_rise_to")
    return payload, len(minor)


def vote_document(
    base_doc: dict, other_payloads: list[dict], min_runs: int
) -> tuple[dict, int]:
    """One document: base `.compress.json` record + the other runs' payloads ->
    the voted record (base stats kept) and how many minor events were re-added."""
    full = base_doc["payload"]
    voted = vote(major_only(full), [major_only(p) for p in other_payloads], min_runs)
    payload, n_minor = with_minor(voted, full)
    return {**base_doc, "payload": payload}, n_minor


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--out", required=True, type=Path, help="voted stage-1 directory")
    ap.add_argument(
        "--min-runs",
        type=int,
        default=2,
        help="runs (base included) that must have an event to keep it",
    )
    ap.add_argument("base", type=Path, help="stage-1 directory the vote builds on")
    ap.add_argument(
        "others", type=Path, nargs="+", help="the other stage-1 directories"
    )
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    cases = sorted(
        f.name.removesuffix(".compress.json") for f in args.base.glob("*.compress.json")
    )
    added = missing = 0
    for case in cases:
        others = []
        for d in args.others:
            f = d / f"{case}.compress.json"
            if f.exists():
                others.append(json.loads(f.read_text(encoding="utf-8"))["payload"])
            else:
                missing += 1
        base_doc = json.loads(
            (args.base / f"{case}.compress.json").read_text(encoding="utf-8")
        )
        doc, n = vote_document(base_doc, others, args.min_runs)
        added += n
        (args.out / f"{case}.compress.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
    print(
        f"voted {len(cases)} document(s) into {args.out} "
        f"({len(args.others) + 1} runs, events kept by >= {args.min_runs}; "
        f"{added} minor events re-attached"
        + (f"; {missing} document(s) missing from other runs" if missing else "")
        + ")"
    )
    if missing:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
