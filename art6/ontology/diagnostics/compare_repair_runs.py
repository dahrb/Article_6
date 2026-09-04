"""Before/after comparison for a repair re-run.

Diffs the current repaired/ graphs against a backup of the previous ones on the
axes the 2026-08-24 applier fix was meant to move: SHACL conformance, the
structural defects the judge pass found by hand, and whether repair's own
remove operations actually landed.

  uv run python -m art6.ontology.diagnostics.compare_repair_runs \
      --experiment results/jurix_phase1 \
      --backup results/_prefix_backup_20260824_230236
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import warnings

warnings.filterwarnings("ignore")

from pyshacl import validate
from rdflib import RDF, Graph, Namespace, URIRef

ECHR = Namespace("https://growgraph.dev/echr#")
EVENT_CLASSES = (
    "DomesticProceeding",
    "AdministrativeAction",
    "EnforcementAction",
    "ProsecutorialReview",
)
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)


def _short(term) -> str:
    return str(term).split("/")[-1].split("#")[-1]


def _shapes():
    from art6.ontology.validate_shapes import load_shapes

    return load_shapes()


def graph_metrics(path: str, shapes: Graph | None, ont: Graph | None) -> dict:
    """Structural defect counts for one graph.

    SHACL is OPT-IN (`--shacl`). Running pyshacl over every graph twice costs
    minutes and duplicates work the driver already did -- run_arms.sh and
    rerun_repair.sh both run the gate and write per-arm totals to validate.log,
    which this script reads instead. Recomputing it here also competes for CPU
    with a repair sweep that may still be running.
    """
    g = Graph()
    g.parse(path, format="turtle")
    txt = ""
    if shapes is not None:
        _, _, txt = validate(
            g, shacl_graph=shapes, ont_graph=ont, inference="none", advanced=True
        )
    events = [
        s
        for s in set(g.subjects(RDF.type, None))
        if any(_short(t) in EVENT_CLASSES for t in g.objects(s, RDF.type))
    ]
    edges = list(g.subject_objects(ECHR.followsProceeding))
    subjects = set(g.subjects(RDF.type, None))
    multi = [
        e
        for e in events
        if len(list(g.objects(e, ECHR.hasCourt))) > 1
        or len(list(g.objects(e, ECHR.hasDecisionDate))) > 1
    ]
    multilabel = [
        s
        for s in subjects
        if len(
            {
                str(o)
                for o in g.objects(
                    s, URIRef("http://www.w3.org/2000/01/rdf-schema#label")
                )
            }
        )
        > 1
    ]
    inbound = collections.Counter()
    for _, target in g.subject_objects(ECHR.hasParticipation):
        inbound[target] += 1
    parts = set(g.subjects(RDF.type, ECHR.Participation))
    return {
        "triples": len(g),
        "events": len(events),
        "edges": len(edges),
        "shacl": len(re.findall(r"Message: ", txt)),
        "multi_court_date": len(multi),
        "multi_label": len(multilabel),
        "dangling": len([1 for _, o in edges if o not in subjects]),
        "shared_participation": len([k for k, v in inbound.items() if v > 1]),
        "partyless_participation": len(
            [p for p in parts if not list(g.objects(p, ECHR.participatingParty))]
        ),
    }


def gate_total(arm_dir: str) -> str:
    """The arm's current SHACL gate line, as validate_shapes wrote it."""
    log = os.path.join(arm_dir, "validate.log")
    if not os.path.exists(log):
        return ""
    with open(log) as fh:
        hits = [ln.strip() for ln in fh if ln.strip().startswith("TOTAL")]
    return hits[-1] if hits else ""


def remove_stats(repairs_json: str) -> tuple[int, int, int]:
    """(remove ops, silently skipped, applied via lexical fallback)."""
    try:
        with open(repairs_json) as fh:
            ops = json.load(fh)["operations"]
    except (OSError, ValueError, KeyError, TypeError):
        return (0, 0, 0)
    removes = [o for o in ops if o.get("action") == "remove"]
    skipped = [o for o in removes if "triple not present" in str(o.get("status"))]
    fallback = [o for o in removes if "lexical form" in str(o.get("status"))]
    return (len(removes), len(skipped), len(fallback))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="results/jurix_phase1")
    ap.add_argument("--backup", required=True)
    ap.add_argument(
        "--shacl",
        action="store_true",
        help="recompute SHACL per graph (slow); default reads validate.log totals",
    )
    args = ap.parse_args()

    shapes = ont = None
    if args.shacl:
        shapes = _shapes()
        ont = Graph()
        ont.parse(os.path.join(REPO_ROOT, "ontology", "echr.ttl"), format="turtle")

    keys = [
        "multi_court_date",
        "multi_label",
        "dangling",
        "shared_participation",
        "partyless_participation",
        "events",
        "edges",
    ]
    tot_before = collections.Counter()
    tot_after = collections.Counter()
    rows = []

    for arm_dir in sorted(glob.glob(os.path.join(args.experiment, "o2_*"))):
        arm = os.path.basename(arm_dir)
        old_dir = os.path.join(args.backup, f"{arm}__repaired")
        new_dir = os.path.join(arm_dir, "repaired")
        if not (os.path.isdir(old_dir) and os.path.isdir(new_dir)):
            continue
        b = collections.Counter()
        a = collections.Counter()
        rem = skip = fb = 0
        n = 0
        for new_path in sorted(glob.glob(os.path.join(new_dir, "*.facts.ttl"))):
            stem = os.path.basename(new_path)
            old_path = os.path.join(old_dir, stem)
            if not os.path.exists(old_path):
                continue
            n += 1
            for k, v in graph_metrics(old_path, shapes, ont).items():
                b[k] += v
            for k, v in graph_metrics(new_path, shapes, ont).items():
                a[k] += v
            r, s, f = remove_stats(
                new_path.replace(".facts.ttl", ".facts.repairs.json")
            )
            rem += r
            skip += s
            fb += f
        rows.append((arm, n, b, a, rem, skip, fb, gate_total(arm_dir)))
        tot_before.update(b)
        tot_after.update(a)

    hdr = f"{'arm':22} {'n':>2} " + " ".join(f"{k[:11]:>12}" for k in keys)
    print(hdr)
    print("-" * len(hdr))
    for arm, n, b, a, rem, skip, fb, gate in rows:
        cells = " ".join(f"{b[k]:5} ->{a[k]:5}" for k in keys)
        print(f"{arm:22} {n:2} {cells}")
        print(
            f"{'':22} {'':2}   removes={rem}  still-skipped={skip}  "
            f"applied-by-lexical-fallback={fb}"
        )
        if gate:
            print(f"{'':22} {'':2}   SHACL gate now: {gate}")
    print("-" * len(hdr))
    cells = " ".join(f"{tot_before[k]:5} ->{tot_after[k]:5}" for k in keys)
    print(f"{'TOTAL':22} {'':2} {cells}")


if __name__ == "__main__":
    main()
