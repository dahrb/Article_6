"""Field-level agreement (Table 3a/3b in docs/paper_draft.md): Cohen's kappa
for the five closed-vocabulary fields, and mean Jaccard similarity for the
multi-valued `parties` field, over gold<->arm event pairs.

Reuses score_graph_metrics.align()'s lenient date_quote 1:1 bipartite match
(same as event alignment elsewhere), so "aligned event" here means the same
thing it means in the rest of the report. Kappa is computed only over pairs
where BOTH gold and the arm assert a value for that field -- the arm's own
explicit "Unknown" enum member counts as asserted (it is one of the label
categories, not a missing value); only a field with no predicate emitted at
all counts as unasserted and is dropped from that pair's kappa/Jaccard input.

Usage:
  uv run python -m art6.ontology.diagnostics.field_agreement \\
      --run-dir results/final_gemma_gold30 --arm C4
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from rdflib import Graph, Namespace
from sklearn.metrics import cohen_kappa_score

from art6.ontology.diagnostics.score_graph_metrics import (
    ARM_PATHS,
    Node,
    align,
)

ECHR = Namespace("https://growgraph.dev/echr#")
REPO_ROOT = Path(__file__).resolve().parents[3]

# arm local name -> gold string, by stripping the class-name prefix ontology
# uses (echr:LevelFirstInstance -> "FirstInstance", echr:DirectionUnknown ->
# "Unknown", etc). Checked against the full vocab in ontology/echr.ttl and
# against every value actually occurring in annotation/gold/*.yaml.
_PREFIXES = {
    "authority_kind": "Authority",
    "level": "Level",
    "proceeding_type": "Type",
    "outcome": "Outcome",
    "outcome_direction": "Direction",
}


def _local(term) -> str | None:
    if term is None:
        return None
    text = str(term)
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _strip_prefix(name: str | None, prefix: str) -> str | None:
    if name is None:
        return None
    return name.removeprefix(prefix)


@dataclass
class FieldNode(Node):
    fields: dict[str, str | None] = field(default_factory=dict)
    parties: set[str] = field(default_factory=set)


def load_gold(gold_dir: Path, case_id: str) -> list[FieldNode]:
    path = gold_dir / f"{case_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    nodes = []
    for p in data.get("proceedings") or []:
        n = FieldNode(
            key=p.get("id"),
            body=p.get("body"),
            decision_date=str(p["decision_date"]) if p.get("decision_date") else None,
            start_date=str(p["start_date"]) if p.get("start_date") else None,
            quote=p.get("quote"),
            level=p.get("level"),
        )
        n.fields = {
            "authority_kind": p.get("authority_kind"),
            "level": p.get("level"),
            "proceeding_type": p.get("proceeding_type"),
            "outcome": p.get("outcome"),
            "outcome_direction": p.get("outcome_direction"),
        }
        n.parties = {
            str(party.get("side"))
            for party in (p.get("parties") or [])
            if party.get("side") is not None
        }
        nodes.append(n)
    return nodes


def load_arm(ttl_path: Path) -> list[FieldNode] | None:
    if not ttl_path.exists():
        return None
    graph = Graph()
    try:
        graph.parse(ttl_path, format="turtle")
    except Exception:  # noqa: BLE001 -- a malformed graph is a data point
        return None
    subjects = {
        s
        for s in set(graph.subjects())
        if graph.value(s, ECHR.hasDecisionDate) is not None
        or graph.value(s, ECHR.hasProceedingStartDate) is not None
        or graph.value(s, ECHR.hasInstanceLevel) is not None
    }
    nodes = []
    for s in subjects:
        court = graph.value(s, ECHR.hasCourt)
        decision_date = graph.value(s, ECHR.hasDecisionDate)
        start_date = graph.value(s, ECHR.hasProceedingStartDate)
        quote = graph.value(s, ECHR.hasSupportingQuote)
        n = FieldNode(
            key=s,
            body=str(graph.value(court, ECHR.hasAuthorityName)) if court else None,
            decision_date=str(decision_date) if decision_date else None,
            start_date=str(start_date) if start_date else None,
            quote=str(quote) if quote else None,
        )
        authority_kind = (
            _local(graph.value(court, ECHR.hasAuthorityKind)) if court else None
        )
        n.fields = {
            "authority_kind": _strip_prefix(
                authority_kind, _PREFIXES["authority_kind"]
            ),
            "level": _strip_prefix(
                _local(graph.value(s, ECHR.hasInstanceLevel)), _PREFIXES["level"]
            ),
            "proceeding_type": _strip_prefix(
                _local(graph.value(s, ECHR.hasProceedingType)),
                _PREFIXES["proceeding_type"],
            ),
            "outcome": _strip_prefix(
                _local(graph.value(s, ECHR.hasOutcome)), _PREFIXES["outcome"]
            ),
            "outcome_direction": _strip_prefix(
                _local(graph.value(s, ECHR.hasOutcomeDirection)),
                _PREFIXES["outcome_direction"],
            ),
        }
        sides = set()
        for participation in graph.objects(s, ECHR.hasParticipation):
            side = _local(graph.value(participation, ECHR.hasPartySide))
            if side:
                sides.add(_strip_prefix(side, "Side") or side)
        n.parties = sides
        nodes.append(n)
    return nodes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--arm", default="C4")
    ap.add_argument("--gold-dir", type=Path, default=REPO_ROOT / "annotation/gold")
    args = ap.parse_args()

    manifest = json.loads((args.gold_dir / "manifest.json").read_text())
    docs = manifest["documents"]
    if docs and "line_id" not in docs[0]:
        docs = [{**d, "line_id": f"L{i}"} for i, d in enumerate(docs, 1)]

    subdir, stage, stem = ARM_PATHS[args.arm]
    field_pairs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    party_jaccards = []
    gold_has_party = arm_has_party = aligned_total = 0

    for doc in docs:
        gold = load_gold(args.gold_dir, doc["case_id"])
        arm = load_arm(
            args.run_dir / subdir / stage / f"{stem}.{doc['line_id']}.facts.ttl"
        )
        if arm is None:
            continue
        mapping = align(gold, arm)  # arm.key -> gold.key
        gold_by_key = {n.key: n for n in gold}
        arm_by_key = {n.key: n for n in arm}
        for akey, gkey in mapping.items():
            g, a = gold_by_key[gkey], arm_by_key[akey]
            aligned_total += 1
            for fname in _PREFIXES:
                gv, av = g.fields.get(fname), a.fields.get(fname)
                if gv is not None and av is not None:
                    field_pairs[fname].append((gv, av))
            if g.parties:
                gold_has_party += 1
            if a.parties:
                arm_has_party += 1
            if g.parties or a.parties:
                union = g.parties | a.parties
                inter = g.parties & a.parties
                party_jaccards.append(len(inter) / len(union) if union else 1.0)

    print(f"aligned events: {aligned_total}\n")
    for fname in _PREFIXES:
        pairs = field_pairs[fname]
        if not pairs:
            print(f"{fname}: no aligned pairs with a value on both sides")
            continue
        gold_labels, arm_labels = zip(*pairs)
        kappa = cohen_kappa_score(gold_labels, arm_labels)
        print(f"{fname}: kappa={kappa:.3f}  n={len(pairs)}")

    if party_jaccards:
        mean_jaccard = sum(party_jaccards) / len(party_jaccards)
        print(
            f"\nparties: mean_jaccard={mean_jaccard:.3f}  "
            f"n={len(party_jaccards)}  "
            f"arm_has_party={arm_has_party}/{aligned_total}  "
            f"gold_has_party={gold_has_party}/{aligned_total}"
        )


if __name__ == "__main__":
    main()
