"""Read a final knowledge graph (stage 3, <case>.facts.ttl) into the flat set-model form.

    from art6.evaluation.kg import read_kg
    doc = read_kg(path, case_id)

Set-model graphs (ontology 4.0.0, echr:ProceedingSet present) are read as
they are: every event node with a date is an event of the set it is
echr:inProceedingSet; a start act has a start date and no decision date;
reviews, appealedBy, appeal and communication dates are read off the graph.

Graphs without sets (the v2 pipeline, the JURIX paper's configuration, the
one-shot baseline) are PROJECTED onto the set model, so every system is
scored the same way:
  set       = a connected component over followsProceeding / partOfProceeding /
              occursDuring (v2's own chains)
  decision  = an event with a decision date whose outcome is MeritsDecided,
              Inadmissible, Remitted or SettledOrWithdrawn (Other / Unknown =
              a procedural order, dropped)
  start     = the component's earliest event with a start date and no
              decision date, if it comes before its first decision
  reviews, appealed_by, appeal_date = derived from v2's chain exactly as
              for the gold (model.convert_records)
"""

from __future__ import annotations

import collections
from collections import defaultdict
from pathlib import Path

from rdflib import Graph, Namespace, URIRef

from art6.model import date_key, is_applicant, is_review

ECHR = Namespace("https://growgraph.dev/echr#")
DECIDED = {"MeritsDecided", "Inadmissible", "Remitted", "SettledOrWithdrawn"}
PREFIX = {
    "level": "Level",
    "outcome": "Outcome",
    "outcome_direction": "Direction",
    "authority_kind": "Authority",
    "side": "Side",
    "matter": "Matter",
    "appealed_by": "AppealedBy",
    "costs": "CostsBorneBy",
    "kind": "Type",
}


def level_from_body(level: str, body: str | None) -> str:
    """Gold v1 has no constitutional level: read it off the body."""
    if body and "constitutional court" in body.lower():
        return "Constitutional"
    return level


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _parties(g) -> list[dict]:
    return [
        {"name": p.get("name"), "side": p.get("side", "Unknown")}
        for p in g.get("parties") or []
    ]


def _matter(g) -> list[str]:
    m = g.get("matter")
    return [x for x in (m if isinstance(m, list) else [m]) if x]


def _appeal_side(filing: dict, applicant_names) -> str | None:
    init = [
        p.get("name")
        for p in filing.get("parties") or []
        if p.get("side") == "Initiating"
    ]
    if not init:
        return None
    app = [is_applicant(n, applicant_names) for n in init]
    if all(app):
        return "Applicant"
    return "Both" if any(app) else "OpposingParty"


def convert_records(
    case: str,
    records: list[dict],
    set_map: list[dict],
    applicant_names: set[str] = frozenset(),
) -> dict:
    """Project guide-v1-style records (a v2 graph's events) onto the set model,
    given one document's set mapping.

    `reviews`: walk the v1 chain back (part_of, else follows) from each
    decision through events the set model drops, to the first kept decision
    of the same set; reaching the start act or another set means none.
    `appealed_by` / `appeal_date`: the first dropped FILING passed on that
    walk (its Initiating parties), else the decision's own Initiating party.
    """
    by_id = {e["id"]: e for e in records}
    names = set(applicant_names) | {
        p["name"]
        for g in records
        for p in g.get("parties") or []
        if "applicant" in (p.get("name") or "").lower()
    }
    sets, events = [], []
    kept = {}  # gold id -> (set id, event id)
    n = 0
    for s in set_map:
        sets.append(
            {
                "id": s["id"],
                "kind": s["kind"],
                "label": s["label"],
                "pending": bool(s.get("pending")),
                "enforces": None,
            }
        )
        if s.get("start") is not None:
            kept[s["start"]] = (s["id"], f"{s['id']}.s")
        for gid in s["decisions"]:
            n += 1
            kept[gid] = (s["id"], f"D{n}")
    pairs: dict = {}  # v1 filing id -> kept decisions that are part_of it
    for gid in kept:
        po = _first(by_id[gid].get("part_of"))
        if po is not None and by_id[gid].get("decision_date"):
            pairs.setdefault(po, []).append(gid)
    for s in set_map:
        if s.get("start") is not None:
            g = by_id[s["start"]]
            events.append(
                {
                    "id": f"{s['id']}.s",
                    "set": s["id"],
                    "role": "start",
                    "date": str(g.get("start_date") or g.get("decision_date")),
                    "body": g.get("body"),
                    "authority_kind": g.get("authority_kind", "Unknown"),
                    "parties": _parties(g),
                    "matter": _matter(g),
                    "quote": g["quote"].strip(),
                    "gold_id": g["id"],
                }
            )
        for gid in s["decisions"]:
            g = by_id[gid]
            if not g.get("decision_date"):
                raise ValueError(f"{case}: decision {gid} has no decision_date")
            reviews = appealed = appeal_date = None
            x, seen = g, set()
            while True:
                nxt = _first(x.get("part_of")) or _first(x.get("follows"))
                if nxt is None or nxt in seen or nxt not in by_id:
                    break
                seen.add(nxt)
                # v1 links aim at a proceeding's FILING; the decision of that
                # filing/decision pair is what the set model links to
                paired = [
                    d
                    for d in pairs.get(nxt, [])
                    if d != gid
                    and kept[d][0] == s["id"]
                    and str(by_id[d]["decision_date"]) <= str(g["decision_date"])
                ]
                if paired:
                    reviews = kept[
                        max(paired, key=lambda d: str(by_id[d]["decision_date"]))
                    ][1]
                    break
                if nxt in kept:
                    sid, eid = kept[nxt]
                    if sid == s["id"] and not eid.endswith(".s"):
                        reviews = eid
                    break
                y = by_id[nxt]
                if (
                    appealed is None
                    and y.get("start_date")
                    and not y.get("decision_date")
                ):
                    appealed = _appeal_side(y, names)
                    appeal_date = str(y["start_date"])
                x = y
            level = level_from_body(g.get("level", "Unknown"), g.get("body"))
            target = (
                by_id[next(k for k, v in kept.items() if v[1] == reviews)]
                if reviews
                else None
            )
            if reviews and not is_review(level, target, appealed is not None):
                reviews = None
            if reviews and appealed is None:
                appealed = _appeal_side(g, names) or "Unknown"
            events.append(
                {
                    "id": kept[gid][1],
                    "set": s["id"],
                    "role": "decision",
                    "date": str(g["decision_date"]),
                    "body": g.get("body"),
                    "authority_kind": g.get("authority_kind", "Unknown"),
                    "level": level,
                    "outcome": g.get("outcome", "Unknown"),
                    "direction": g.get("outcome_direction", "Unknown"),
                    "parties": _parties(g),
                    "matter": _matter(g),
                    "reviews": reviews,
                    "appealed_by": appealed if reviews else None,
                    "appeal_date": appeal_date if reviews else None,
                    "quote": g["quote"].strip(),
                    "gold_id": g["id"],
                }
            )
    for st, s in zip(sets, set_map, strict=True):
        if s.get("enforces") is not None:
            st["enforces"] = kept[s["enforces"]][1]
    return {"case_id": case, "sets": sets, "events": events}


def _local(term, field: str) -> str | None:
    if term is None:
        return None
    name = str(term).rsplit("#", 1)[-1].rsplit("/", 1)[-1]
    p = PREFIX[field]
    return name.removeprefix(p)


def _str(v) -> str | None:
    return None if v is None else str(v)


def _name(g: Graph, node) -> str | None:
    for p in (ECHR.hasPartyName, ECHR.hasPersonName, ECHR.hasAuthorityName):
        v = g.value(node, p)
        if v is not None:
            return str(v)
    from rdflib import RDFS

    v = g.value(node, RDFS.label)
    return None if v is None else str(v)


def read_records(path: Path) -> tuple[list[dict], dict, bool]:
    g = Graph()
    g.parse(path, format="turtle")
    set_nodes = set(g.subjects(predicate=None, object=ECHR.ProceedingSet))
    subjects = {
        s
        for s in set(g.subjects())
        if isinstance(s, URIRef)
        and s not in set_nodes
        and (
            g.value(s, ECHR.hasDecisionDate) is not None
            or g.value(s, ECHR.hasProceedingStartDate) is not None
        )
    }
    records = []
    for s in sorted(subjects, key=str):
        court = g.value(s, ECHR.hasCourt)
        parties = []
        for part in g.objects(s, ECHR.hasParticipation):
            who = g.value(part, ECHR.participatingParty)
            parties.append(
                {
                    "name": _name(g, who) if who is not None else None,
                    "side": _local(g.value(part, ECHR.hasPartySide), "side")
                    or "Unknown",
                }
            )
        records.append(
            {
                "id": str(s),
                "body": _name(g, court) if court is not None else None,
                "authority_kind": _local(
                    g.value(court, ECHR.hasAuthorityKind), "authority_kind"
                )
                if court
                else None,
                "start_date": _str(g.value(s, ECHR.hasProceedingStartDate)),
                "decision_date": _str(g.value(s, ECHR.hasDecisionDate)),
                "level": _local(g.value(s, ECHR.hasInstanceLevel), "level")
                or "Unknown",
                "outcome": _local(g.value(s, ECHR.hasOutcome), "outcome") or "Unknown",
                "outcome_direction": _local(
                    g.value(s, ECHR.hasOutcomeDirection), "outcome_direction"
                )
                or "Unknown",
                "parties": parties,
                "matter": sorted(
                    _local(m, "matter") for m in g.objects(s, ECHR.hasMatter)
                ),
                "quote": _str(g.value(s, ECHR.hasSupportingQuote)) or "",
                "follows": _str(g.value(s, ECHR.followsProceeding)),
                "part_of": _str(g.value(s, ECHR.partOfProceeding)),
                "occurs_during": _str(g.value(s, ECHR.occursDuring)),
                "reviews": _str(g.value(s, ECHR.reviewsDecision)),
                "appealed_by": _local(g.value(s, ECHR.appealedBy), "appealed_by"),
                "appeal_date": _str(g.value(s, ECHR.hasAppealDate)),
                "communication_date": _str(g.value(s, ECHR.hasCommunicationDate)),
                "costs": _local(g.value(s, ECHR.costsBorneBy), "costs"),
                "set": _str(g.value(s, ECHR.inProceedingSet)),
                "kind": _local(g.value(s, ECHR.hasProceedingType), "kind"),
            }
        )
    sets = {}
    for n in sorted(set_nodes, key=str):
        sets[str(n)] = {
            "label": _name(g, n) or "",
            "kind": _local(g.value(n, ECHR.hasProceedingType), "kind") or "Unknown",
            "pending": str(g.value(n, ECHR.isPending)).lower() == "true",
            "enforces": _str(g.value(n, ECHR.enforcesDecision)),
        }
    applicants = {
        n
        for case in g.subjects(None, ECHR.CaseDocument)
        for a in g.objects(case, ECHR.hasApplicant)
        if (n := _name(g, a))
    }
    return records, sets, bool(set_nodes), applicants


def _flat_set_model(case: str, records: list[dict], sets: dict) -> dict:
    ids = {r["id"]: f"N{i}" for i, r in enumerate(records, 1)}
    flat_sets = [
        {
            "id": sid,
            "kind": s["kind"],
            "label": s["label"],
            "pending": s["pending"],
            "enforces": ids.get(s["enforces"]),
        }
        for sid, s in sets.items()
    ]
    events = []
    for r in records:
        sid = r["set"] or "unassigned"
        role = "decision" if r["decision_date"] else "start"
        e = {
            "id": ids[r["id"]],
            "set": sid,
            "role": role,
            "date": r["decision_date"] or r["start_date"],
            "body": r["body"],
            "authority_kind": r["authority_kind"] or "Unknown",
            "parties": r["parties"],
            "matter": r["matter"],
            "quote": r["quote"],
        }
        if role == "decision":
            e.update(
                {
                    "level": r["level"],
                    "outcome": r["outcome"],
                    "direction": r["outcome_direction"],
                    "reviews": ids.get(r["reviews"]),
                    "appealed_by": r["appealed_by"],
                    "appeal_date": r["appeal_date"],
                    "communication_date": r["communication_date"],
                    "costs": r["costs"],
                }
            )
        events.append(e)
    if any(e["set"] == "unassigned" for e in events):
        flat_sets.append(
            {
                "id": "unassigned",
                "kind": "Unknown",
                "label": "",
                "pending": False,
                "enforces": None,
            }
        )
    return {"case_id": case, "sets": flat_sets, "events": events}


def _components(records: list[dict]) -> list[list[dict]]:
    by_id = {r["id"]: r for r in records}
    adj = defaultdict(set)
    for r in records:
        for k in ("follows", "part_of", "occurs_during"):
            t = r.get(k)
            if t in by_id:
                adj[r["id"]].add(t)
                adj[t].add(r["id"])
    seen, comps = set(), []
    for r in records:
        if r["id"] in seen:
            continue
        stack, comp = [r["id"]], []
        seen.add(r["id"])
        while stack:
            x = stack.pop()
            comp.append(by_id[x])
            for y in sorted(adj[x]):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        comps.append(comp)
    return comps


def _flat_projected(case: str, records: list[dict], applicants: set[str]) -> dict:
    set_map = []
    rank = {r["id"]: i for i, r in enumerate(records)}  # graph order breaks date ties
    for i, comp in enumerate(_components(records), 1):
        decisions = sorted(
            (r for r in comp if r["decision_date"] and r["outcome"] in DECIDED),
            key=lambda r: (date_key(r["decision_date"]), rank[r["id"]]),
        )
        if not decisions:
            continue
        first = date_key(decisions[0]["decision_date"])
        filings = sorted(
            (r for r in comp if r["start_date"] and not r["decision_date"]),
            key=lambda r: (r["start_date"].ljust(10, "0"), rank[r["id"]]),
        )
        start = (
            filings[0]["id"]
            if filings and filings[0]["start_date"].ljust(10, "0") <= first
            else None
        )
        kinds = [r["kind"] for r in decisions if r.get("kind")]
        kind = collections.Counter(kinds).most_common(1)[0][0] if kinds else "Unknown"
        set_map.append(
            {
                "id": f"C{i}",
                "kind": kind,
                "label": "",
                "start": start,
                "decisions": [r["id"] for r in decisions],
                "pending": False,
            }
        )
    return convert_records(case, records, set_map, applicants)


def read_kg(path: Path, case: str) -> dict:
    records, sets, set_model, applicants = read_records(path)
    doc = (
        _flat_set_model(case, records, sets)
        if set_model
        else _flat_projected(case, records, applicants)
    )
    doc["applicant_names"] = sorted(applicants)
    return doc
