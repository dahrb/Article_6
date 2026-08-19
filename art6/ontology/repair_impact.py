"""
repair_impact.py
----------------
Measures what `repair_facts.py` actually changed, by diffing each model's
`raw/` graph against its `repaired/` graph on the metrics the extraction
quality report scores, and cross-referencing the per-document
`<stem>.facts.repairs.json` audit logs.

Answers three questions the audit logs alone cannot:
  1. how much of the graph the repair pass touches at all (files, triples),
  2. whether it moves the metrics that matter (grievance layer, court
     coverage, functional-property collisions, connectivity),
  3. what it costs -- deleting a contradictory edge also fragments the graph,
     and the components / singletons rows make that trade visible.

Edit RUNS and MODELS below to point at other experiment directories.

Usage:
  uv run python -m art6.ontology.repair_impact
"""

from __future__ import annotations

import collections
import glob
import json
import os

from rdflib import RDF, Graph, Namespace, URIRef

ECHR = Namespace("https://growgraph.dev/echr#")
RUNS = {
    "jsonld": "results/experiment_20260818_143651",
    "turtle": "results/experiment_ttl_20260818_161537",
}
MODELS = ["gpt5mini", "gemma4"]


def load(p):
    g = Graph()
    try:
        g.parse(p, format="turtle")
    except Exception as exc:  # noqa: BLE001 - one unparseable file must not stop the sweep
        print("  PARSE FAIL", p, exc)
    return g


FUNCTIONAL = [
    "hasCourt",
    "hasDecisionDate",
    "hasOutcome",
    "hasInstanceLevel",
    "hasProceedingType",
    "hasStartDate",
    "hasOutcomeDirection",
    "hasAuthorityName",
    "hasGender",
    "hasBirthYear",
]


def stats(g):
    s = {}
    s["triples"] = len(g)
    s["typed"] = len(set(g.subjects(RDF.type, None)))
    s["a6issue"] = len(set(g.subjects(RDF.type, ECHR.Article6Issue)))
    s["proceedings"] = set(g.subjects(RDF.type, ECHR.DomesticProceeding))
    s["n_proc"] = len(s["proceedings"])
    s["follows"] = len(list(g.triples((None, ECHR.followsProceeding, None))))
    # functional violations
    viol = 0
    for p in FUNCTIONAL:
        pred = ECHR[p]
        c = collections.Counter(sub for sub, _, _ in g.triples((None, pred, None)))
        viol += sum(v - 1 for v in c.values() if v > 1)
    s["func_viol"] = viol
    # orphans: typed nodes with no in- or out- echr link to another typed node
    nodes = set(g.subjects(RDF.type, None))
    deg = collections.Counter()
    for sub, pred, obj in g:
        if pred == RDF.type:
            continue
        if isinstance(obj, URIRef) and sub in nodes and obj in nodes:
            deg[sub] += 1
            deg[obj] += 1
    s["singletons"] = sum(1 for n in nodes if deg[n] == 0)
    # connected components over typed nodes
    adj = collections.defaultdict(set)
    for sub, pred, obj in g:
        if pred == RDF.type:
            continue
        if isinstance(obj, URIRef) and sub in nodes and obj in nodes:
            adj[sub].add(obj)
            adj[obj].add(sub)
    seen, comps = set(), 0
    for n in nodes:
        if n in seen:
            continue
        comps += 1
        stack = [n]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(adj[x] - seen)
    s["components"] = comps
    s["proc_no_court"] = sum(
        1 for p in s["proceedings"] if (p, ECHR.hasCourt, None) not in g
    )
    s["proc_no_outcome"] = sum(
        1 for p in s["proceedings"] if (p, ECHR.hasOutcome, None) not in g
    )
    s["srcpara"] = len(list(g.triples((None, ECHR.hasSourceParagraph, None))))
    s["quotes"] = len(list(g.triples((None, ECHR.hasSupportingQuote, None))))
    return s


KEYS = [
    "triples",
    "typed",
    "a6issue",
    "n_proc",
    "follows",
    "func_viol",
    "singletons",
    "components",
    "proc_no_court",
    "proc_no_outcome",
    "srcpara",
    "quotes",
]

print("# repair_facts.py impact — gemma4 & gpt5mini\n")
for fmt, run in RUNS.items():
    for m in MODELS:
        raws = sorted(glob.glob(f"{run}/{m}/raw/*.facts.ttl"))
        if not raws:
            continue
        tot_raw = collections.Counter()
        tot_rep = collections.Counter()
        ops_applied = collections.Counter()
        ops_skipped = collections.Counter()
        files_touched = 0
        per_file = []
        for r in raws:
            base = os.path.basename(r)
            rep = f"{run}/{m}/repaired/{base}"
            if not os.path.exists(rep):
                continue
            gr, gp = load(r), load(rep)
            sr, sp = stats(gr), stats(gp)
            for k in KEYS:
                tot_raw[k] += sr[k]
                tot_rep[k] += sp[k]
            # triple-level delta
            added = len(set(gp) - set(gr))
            removed = len(set(gr) - set(gp))
            rj = rep.replace(".ttl", ".repairs.json")
            nops = 0
            if os.path.exists(rj):
                with open(rj) as fh:
                    d = json.load(fh)
                files_touched += 1
                for op in d.get("operations", []):
                    st = op.get("status", "?")
                    key = (op["action"], op["predicate"])
                    if st == "applied":
                        ops_applied[key] += 1
                    else:
                        ops_skipped[(st, op["action"], op["predicate"])] += 1
                    nops += 1
            per_file.append((base, nops, added, removed, sr["triples"], sp["triples"]))
        label = f"{fmt}/{m}"
        print(
            f"## {label}   ({len(per_file)} files, {files_touched} with a repairs.json)"
        )
        print("| file | ops | triples+ | triples- | raw | repaired |")
        print("|---|--:|--:|--:|--:|--:|")
        for row in per_file:
            print("| {} | {} | {} | {} | {} | {} |".format(*row))
        print()
        print("| metric | raw | repaired | delta |")
        print("|---|--:|--:|--:|")
        for k in KEYS:
            d = tot_rep[k] - tot_raw[k]
            print(f"| {k} | {tot_raw[k]} | {tot_rep[k]} | {d:+d} |")
        print()
        if ops_applied:
            print("applied ops by (action, predicate):")
            for (a, p), c in ops_applied.most_common():
                print(f"  - {a:<6} {p:<40} {c}")
        if ops_skipped:
            print("skipped ops:")
            for (st, a, p), c in ops_skipped.most_common():
                print(f"  - [{st}] {a:<6} {p:<40} {c}")
        print("\n---\n")
