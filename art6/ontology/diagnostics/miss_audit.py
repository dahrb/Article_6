"""Why each gold event is missed, and what the extra events are (strict alignment, procedural events).

    uv run python -m art6.ontology.diagnostics.miss_audit --source SOURCE_JSONL [--extras] [--verbose] STAGE1_DIR ...

For every gold event the strict aligner leaves unmatched, find the arm event(s) whose own text
spans cover the gold quote in the judgment, then say why they did not align:
  kind            covering arm event has the gold date but as the other date kind (filing vs decision)
  date            covering arm event carries a different date (or none)
  merged          covering arm event is already aligned to another gold event (two acts in one entry)
  lost-tie        covering arm event free, same kind and date, but lost the 1:1 match
  circumstance    the quote went into `circumstances`, not events
  same-sentence   no span covers the quote but an arm event quotes the same sentence
  not-extracted   nothing in stage 1 touches the sentence
--verbose prints each case; --extras also classifies the events gold does not have.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

import yaml

from art6.ontology import agentic_stage1 as a
from art6.ontology.diagnostics import score_graph_metrics as sgm
from art6.ontology.vote import event_date, payload_nodes

G = Path("annotation/test")


def _load(d, case):
    return json.loads((Path(d) / f"{case}.compress.json").read_text(encoding="utf-8"))[
        "payload"
    ]


def _gold(case):
    return {
        e["id"]: e
        for e in yaml.safe_load((G / f"{case}.yaml").read_text(encoding="utf-8"))[
            "proceedings"
        ]
    }


def _cases(d):
    return sorted(
        f.name.removesuffix(".compress.json") for f in Path(d).glob("*.compress.json")
    )


ws = lambda s: re.sub(r"\s+", " ", s or "").strip()


def locate(text, quote):
    """char range of the gold quote in the judgment (whitespace-insensitive, prefix fallback)"""
    flat, idx = [], []
    for i, ch in enumerate(text):
        if ch.isspace():
            if flat and flat[-1] == " ":
                continue
            flat.append(" ")
            idx.append(i)
        else:
            flat.append(ch)
            idx.append(i)
    flat = "".join(flat)
    q = ws(quote)
    for n in (len(q), 120, 60, 35):
        k = flat.find(q[:n])
        if k >= 0:
            return idx[k], idx[min(k + len(q), len(idx)) - 1] + 1
    return None


def spans(e):
    for k, v in e.items():
        if (
            k.endswith("_span")
            and isinstance(v, dict)
            and isinstance(v.get("start"), int)
        ):
            yield v["start"], v["end"]


def dates(e):
    return {x for x in (event_date(e, "decision"), event_date(e, "start")) if x}


def audit(d, src):
    text = {
        json.loads(l)["case_id"]: json.loads(l)["text"]
        for l in Path(src).read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    C, rows = collections.Counter(), []
    for case in _cases(d):
        full = _load(d, case)
        gy = _gold(case)
        gn = sgm.load_gold_nodes(G, case)
        m = sgm.align(gn, payload_nodes(full))
        got = set(m.values())
        src_t = text[case]
        for g in gn:
            if g.key in got:
                continue
            q = gy[g.key].get("quote") or ""
            loc = locate(src_t, q)
            gdate = {x for x in (g.decision_date, g.start_date) if x}
            gkind = "filing" if g.is_filing else "decision"
            cov = []
            if loc:
                lo, hi = loc
                for e in full["events"]:
                    ov = max(
                        (min(hi, b) - max(lo, s0) for s0, b in spans(e)), default=0
                    )
                    if ov > 0.3 * (hi - lo) or ov > 60:
                        cov.append(e)
            if cov:
                e = cov[0]
                ek = (
                    "filing"
                    if (event_date(e, "start") and not event_date(e, "decision"))
                    else "decision"
                )
                if any(x["id"] in m for x in cov) and all(x["id"] in m for x in cov):
                    c = "merged"
                elif gdate & dates(e) and ek != gkind:
                    c = "kind"
                elif not (gdate & dates(e)) and not any(
                    sgm.date_sim(str(x), str(y)) >= sgm.DATE_SIM_THRESHOLD
                    for x in gdate
                    for y in dates(e)
                ):
                    c = "date"
                else:
                    c = "lost-tie"
            else:
                circ = []
                if loc:
                    circ = [
                        x
                        for x in full.get("circumstances") or []
                        if max(
                            (min(loc[1], b) - max(loc[0], s0) for s0, b in spans(x)),
                            default=0,
                        )
                        > 0
                    ]
                if circ:
                    c = "circumstance"
                elif loc and any(
                    a.sentence_at(src_t, s0, b)
                    == a.sentence_at(src_t, loc[0], loc[0] + 1)
                    for e in full["events"]
                    for s0, b in spans(e)
                ):
                    c = "same-sentence"
                elif not loc:
                    c = "quote not found in text"
                else:
                    c = "not-extracted"
            C[c] += 1
            gg = gy[g.key]
            rows.append(
                (
                    c,
                    case,
                    g.key,
                    gkind,
                    sorted(gdate),
                    gg.get("type"),
                    gg.get("level"),
                    gg.get("body"),
                    ws(q)[:110],
                    [
                        (
                            x["id"],
                            sorted(dates(x)),
                            ws(x.get("description"))[:70],
                        )
                        for x in cov[:2]
                    ],
                )
            )
    return C, rows


def extras(d, src):
    """arm events with no gold partner: what are they?"""
    text = {
        json.loads(l)["case_id"]: json.loads(l)["text"]
        for l in Path(src).read_text(encoding="utf-8").splitlines()
        if l.strip()
    }
    C, rows = collections.Counter(), []
    for case in _cases(d):
        core = _load(d, case)
        gy = _gold(case)
        gn = sgm.load_gold_nodes(G, case)
        an = payload_nodes(core)
        m = sgm.align(gn, an)
        t = text[case]
        gloc = {k: locate(t, v.get("quote") or "") for k, v in gy.items()}
        gsent = {k: a.sentence_at(t, l[0], l[0] + 1) for k, l in gloc.items() if l}
        for e in core["events"]:
            if e["id"] in m:
                continue
            sp = list(spans(e))
            main = e.get("what_happened_span") or (
                sp and {"start": sp[0][0], "end": sp[0][1]}
            )
            hit = [
                k
                for k, l in gloc.items()
                if l and any(min(l[1], b) - max(l[0], s0) > 0 for s0, b in sp)
            ]
            sent = (
                a.sentence_at(t, main["start"], main["start"] + 1)
                if isinstance(main, dict)
                else None
            )
            if hit:
                gk = hit[0]
                c = (
                    "gold act already matched to another arm event (duplicate/split)"
                    if gk in m.values()
                    else "gold act, field mismatch (kind/date)"
                )
            elif sent and sent in gsent.values():
                c = "same sentence as a gold act (split-off part)"
            else:
                mt = set(e.get("matter") or [])
                c = "not in gold: " + (
                    "health"
                    if "health" in mt
                    else "immigration/enforcement"
                    if mt & {"immigration", "enforcement"}
                    else (e.get("instance_level") or "?")
                )
            C[c] += 1
            rows.append(
                (c, case, e["id"], sorted(dates(e)), ws(e.get("description"))[:110])
            )
    return C, rows


def main() -> None:
    global G
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--source", required=True, type=Path, help="the judgments JSONL (case_id, text)"
    )
    ap.add_argument("--gold-dir", type=Path, default=G)
    ap.add_argument("--extras", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("dirs", nargs="+", type=Path)
    args = ap.parse_args()
    G = args.gold_dir
    allrows = []
    for d in args.dirs:
        C, rows = audit(d, args.source)
        allrows += [(str(d),) + r for r in rows]
        print(f"== {d}: {sum(C.values())} gold events missed: {dict(C.most_common())}")
    if args.verbose:
        for r in sorted(allrows, key=lambda r: (r[1], r[2], r[3])):
            print(
                f"  {Path(r[0]).parent.name[:10]:10} {r[1]:14} {r[2]} g{r[3]} {r[4]:8} {r[5]} {r[6]}/{str(r[7])[:25]} | {r[9]}\n        -> {r[10]}"
            )
    if args.extras:
        for d in args.dirs:
            C, rows = extras(d, args.source)
            print(f"== EXTRAS {d}: {sum(C.values())}: {dict(C.most_common())}")
            if args.verbose:
                for r in rows:
                    print("   ", r)


if __name__ == "__main__":
    main()
