"""The simplified event model (docs/event_simplification/NOTES.md).

A document is a list of **sets** of proceedings and a list of **events**. An
event is either the act that opened its set (`role: start`) or a decision at
one level (`role: decision`). The only link is set membership; order inside a
set is derived from dates (`derived_follows`), never extracted.

    {"case_id": ..., "sets": [{"id", "kind", "label", "pending"}],
     "events": [{"id", "set", "role", "date", "body", "quote",
                 "level", "outcome", "direction"}]}
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import yaml

from art6.ontology.diagnostics import score_graph_metrics as sgm

KINDS = (
    "civil",
    "criminal",
    "administrative",
    "disciplinary",
    "enforcement",
    "length_remedy",
    "other",
)
LEVELS = (
    "investigation",
    "administrative",
    "first_instance",
    "appeal",
    "cassation",
    "constitutional",
    "other",
)
OUTCOMES = ("merits", "remitted", "procedural", "settled")
DIRECTIONS = ("for_applicant", "against_applicant", "mixed", "unknown")

GOLD_LEVEL = {
    "FirstInstance": "first_instance",
    "Appeal": "appeal",
    "Cassation": "cassation",
    "Investigative": "investigation",
    "AdministrativeReview": "administrative",
    "Reopening": "other",
    "Unknown": "other",
}
GOLD_OUTCOME = {
    "MeritsDecided": "merits",
    "Remitted": "remitted",
    "Inadmissible": "procedural",
    "SettledOrWithdrawn": "settled",
    "Other": "procedural",
    "Unknown": "merits",
}
GOLD_DIRECTION = {
    "FavourableToApplicant": "for_applicant",
    "UnfavourableToApplicant": "against_applicant",
    "Mixed": "mixed",
    "Unknown": "unknown",
}


def level_from_body(level: str, body: str | None) -> str:
    """The original model has no constitutional level; read it off the body."""
    if body and "constitutional court" in body.lower():
        return "constitutional"
    return level


# ---------------------------------------------------------------------------
# Gold
# ---------------------------------------------------------------------------


def convert_gold(case: str, gold_dir: Path, mapping: dict) -> dict:
    """Simplified gold for one document from the original gold + the mapping."""
    raw = yaml.safe_load((gold_dir / f"{case}.yaml").read_text(encoding="utf-8"))
    by_id = {e["id"]: e for e in raw["proceedings"]}
    sets, events = [], []
    for s in mapping[case]:
        sets.append(
            {
                "id": s["id"],
                "kind": s["kind"],
                "label": s["label"],
                "pending": bool(s.get("pending")),
            }
        )
        if s.get("start") is not None:
            g = by_id[s["start"]]
            events.append(
                {
                    "id": f"g{g['id']}",
                    "set": s["id"],
                    "role": "start",
                    "date": g.get("start_date") or g.get("decision_date"),
                    "body": g.get("body"),
                    "quote": g["quote"],
                    "src": [g["id"]],
                }
            )
        for gid in s["decisions"]:
            g = by_id[gid]
            if not g.get("decision_date"):
                raise ValueError(f"{case}: decision {gid} has no decision_date")
            events.append(
                {
                    "id": f"g{g['id']}",
                    "set": s["id"],
                    "role": "decision",
                    "date": g["decision_date"],
                    "body": g.get("body"),
                    "quote": g["quote"],
                    "level": level_from_body(GOLD_LEVEL[g["level"]], g.get("body")),
                    "outcome": GOLD_OUTCOME[g["outcome"]],
                    "direction": GOLD_DIRECTION[g["outcome_direction"]],
                    "src": [g["id"]],
                }
            )
    return {"case_id": case, "sets": sets, "events": events}


# ---------------------------------------------------------------------------
# Order and scorer nodes
# ---------------------------------------------------------------------------


def _date_key(date: str | None) -> str:
    return (date or "9999").ljust(10, "9")  # a year-only date sorts to its end


def ordered(doc: dict, set_id: str) -> list[dict]:
    """The set's events in derived order: the start first, then decisions by
    date, text order breaking ties."""
    evs = [(i, e) for i, e in enumerate(doc["events"]) if e["set"] == set_id]
    starts = [e for _, e in evs if e["role"] == "start"]
    decisions = [
        e
        for _, e in sorted(
            ((i, e) for i, e in evs if e["role"] == "decision"),
            key=lambda ie: (_date_key(ie[1].get("date")), ie[0]),
        )
    ]
    return starts[:1] + decisions


def derived_follows(doc: dict) -> dict[str, str]:
    out = {}
    for s in doc["sets"]:
        seq = ordered(doc, s["id"])
        for prev, cur in pairwise(seq):
            out[cur["id"]] = prev["id"]
    return out


def nodes(doc: dict) -> list[sgm.Node]:
    """Scorer nodes: a start is a filing (start date only), a decision a decision."""
    follows = derived_follows(doc)
    out = []
    for e in doc["events"]:
        start = e["role"] == "start"
        n = sgm.Node(
            key=e["id"],
            body=e.get("body"),
            decision_date=None if start else e.get("date"),
            start_date=e.get("date") if start else None,
            quote=e.get("quote"),
            level=e.get("level"),
        )
        if e["id"] in follows:
            n.edges["follows"].add(follows[e["id"]])
        out.append(n)
    return out


def load_doc(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))
