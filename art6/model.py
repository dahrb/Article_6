"""The set-based event model (annotation_guide.txt v3.1, ontology 4.1.0).

A document is a list of SETS of proceedings and a flat list of EVENTS. An
event is the act that opened its set (`role: start`) or a decision at one
level (`role: decision`). Set membership and `reviews` are the links; the
plain order inside a set (`derived_follows`) comes from dates.

Flat form, used by every tool here (vocabulary values are the guide's short
forms, i.e. the ontology's local names minus their prefix):

    {"case_id": ..., "applicants": [...], "persons": [...], "case": {...},
     "sets":   [{"id", "kind", "label", "pending", "enforces"}],
     "events": [{"id", "set", "role", "date", "body", "authority_kind",
                 "parties": [{"name", "side"}], "matter": [...], "quote",
                 # decisions only:
                 "level", "outcome", "direction", "reviews", "appealed_by",
                 "appeal_date", "communication_date", "costs"}]}

The annotation file (annotation/test_simple/<case>.yaml, guide section 8) nests the
events under their sets; `load_doc` and `dump_yaml` convert, `validate`
checks one against the guide's schema.
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from pathlib import Path

import yaml

from art6.evaluation.align import Node

KINDS = (
    "Criminal",
    "Civil",
    "Administrative",
    "Constitutional",
    "Disciplinary",
    "Other",
    "Unknown",
)
LEVELS = (
    "Investigative",
    "AdministrativeReview",
    "FirstInstance",
    "Appeal",
    "Cassation",
    "Constitutional",
    "CaseReview",
    "Reopening",
    "Unknown",
)
OUTCOMES = (
    "MeritsDecided",
    "Inadmissible",
    "Remitted",
    "SettledOrWithdrawn",
    "Other",
    "Unknown",
)
DIRECTIONS = ("FavourableToApplicant", "UnfavourableToApplicant", "Mixed", "Unknown")
AUTHORITY_KINDS = (
    "Judicial",
    "Prosecutorial",
    "Administrative",
    "Disciplinary",
    "Unknown",
)
SIDES = ("Initiating", "Responding", "ThirdParty", "Unknown")
APPEALED_BY = ("Applicant", "OpposingParty", "Both", "Unknown")
COSTS = APPEALED_BY  # who bears the costs: same four values
MATTERS = (
    "Arrest",
    "Detention",
    "Bail",
    "Charge",
    "Conviction",
    "Investigation",
    "Damages",
    "Custody",
    "Employment",
    "Pension",
    "Property",
    "Extradition",
    "LegalAid",
    "Vetting",
    "Disciplinary",
    "Requests",
    "Health",
    "Immigration",
    "Enforcement",
    "Other",
    "Unknown",
)

DECISION_FIELDS = (
    "level",
    "outcome",
    "direction",
    "reviews",
    "appealed_by",
    "appeal_date",
    "communication_date",
    "costs",
)


def is_applicant(name: str | None, applicant_names: set[str] = frozenset()) -> bool:
    n = (name or "").lower()
    return "applicant" in n or any(a and a.lower() in n for a in applicant_names)


# ---------------------------------------------------------------------------
# File formats
# ---------------------------------------------------------------------------


def from_nested(data: dict) -> dict:
    """Annotation YAML (sets with nested start/decisions) -> flat form."""
    sets, events = [], []
    for s in data.get("sets") or []:
        sets.append(
            {
                "id": s["id"],
                "kind": s.get("kind", "Unknown"),
                "label": s.get("label", ""),
                "pending": bool(s.get("pending")),
                "enforces": s.get("enforces"),
                **({"note": s["note"]} if s.get("note") else {}),
            }
        )
        if s.get("start"):
            events.append(
                {"id": f"{s['id']}.s", "set": s["id"], "role": "start", **s["start"]}
            )
        for d in s.get("decisions") or []:
            events.append({"set": s["id"], "role": "decision", **d})
    return {
        "case_id": str(data["case_id"]),
        "sets": sets,
        "events": events,
        **{k: data[k] for k in ("case", "applicants", "persons") if k in data},
    }


def to_nested(doc: dict, guide_version: str = "3.1") -> dict:
    out = {"case_id": doc["case_id"], "guide_version": guide_version}
    for k in ("case", "applicants", "persons"):
        if doc.get(k):
            out[k] = doc[k]
    out["sets"] = []
    for s in doc["sets"]:
        ns = {k: s[k] for k in ("id", "kind", "label") if s.get(k) is not None}
        if s.get("pending"):
            ns["pending"] = True
        if s.get("enforces"):
            ns["enforces"] = s["enforces"]
        if s.get("note"):
            ns["note"] = s["note"]
        for e in doc["events"]:
            if e["set"] == s["id"] and e["role"] == "start":
                ns["start"] = {
                    k: v
                    for k, v in e.items()
                    if k not in ("id", "set", "role") and v not in (None, [], "")
                }
        ns["decisions"] = [
            {
                k: v
                for k, v in e.items()
                if k not in ("set", "role") and v not in (None, [], "")
            }
            for e in doc["events"]
            if e["set"] == s["id"] and e["role"] == "decision"
        ]
        out["sets"].append(ns)
    return out


def load_doc(path: Path) -> dict:
    if path.suffix in (".yaml", ".yml"):
        return from_nested(yaml.safe_load(path.read_text(encoding="utf-8")))
    return json.loads(path.read_text(encoding="utf-8"))


_ISO = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_SET_KEYS = {"id", "kind", "label", "pending", "enforces", "start", "decisions", "note"}
_EVENT_KEYS = {
    "date",
    "body",
    "authority_kind",
    "parties",
    "matter",
    "quote",
    "note",
    "gold_id",
}
_DECISION_KEYS = _EVENT_KEYS | {"id", *DECISION_FIELDS}
_CLOSED = {
    "kind": KINDS,
    "authority_kind": AUTHORITY_KINDS,
    "level": LEVELS,
    "outcome": OUTCOMES,
    "direction": DIRECTIONS,
    "appealed_by": APPEALED_BY,
    "costs": COSTS,
}


def validate(data: dict) -> list[str]:
    """Check one annotation file (nested YAML form) against the guide's
    schema: known keys only, closed-list values, ISO dates, a quote on every
    event, `reviews` within the set, `enforces` to an existing decision.
    Returns the problems found; an empty list means the file is valid."""
    errs: list[str] = []
    if not data.get("case_id"):
        errs.append("missing case_id")
    dec_set: dict[str, str] = {}
    for s in data.get("sets") or []:
        for d in s.get("decisions") or []:
            dec_set[str(d.get("id"))] = s.get("id")

    def check_event(where: str, e: dict, keys: set[str]) -> None:
        for k in set(e) - keys:
            errs.append(f"{where}: unknown field {k!r}")
        for k in ("date", "appeal_date", "communication_date"):
            if e.get(k) is not None and not _ISO.match(str(e[k])):
                errs.append(f"{where}: {k} {e[k]!r} is not YYYY, YYYY-MM or YYYY-MM-DD")
        if not e.get("date"):
            errs.append(f"{where}: missing date")
        if not e.get("quote"):
            errs.append(f"{where}: missing quote")
        for k, allowed in _CLOSED.items():
            if k in e and e[k] is not None and e[k] not in allowed:
                errs.append(f"{where}: {k} {e[k]!r} not in {'|'.join(allowed)}")
        for p in e.get("parties") or []:
            if not isinstance(p, dict) or not p.get("name"):
                errs.append(f"{where}: party without a name")
            elif p.get("side", "Unknown") not in SIDES:
                errs.append(f"{where}: side {p['side']!r} not in {'|'.join(SIDES)}")
        matter = e.get("matter") or []
        for m in matter if isinstance(matter, list) else [matter]:
            if m not in MATTERS:
                errs.append(f"{where}: matter {m!r} not in the matter list")

    seen_sets: set = set()
    for s in data.get("sets") or []:
        sid = s.get("id")
        if not sid or sid in seen_sets:
            errs.append(f"set {sid!r}: missing or repeated id")
        seen_sets.add(sid)
        for k in set(s) - _SET_KEYS:
            errs.append(f"set {sid}: unknown field {k!r}")
        if s.get("kind") not in KINDS:
            errs.append(f"set {sid}: kind {s.get('kind')!r} not in {'|'.join(KINDS)}")
        if s.get("enforces") is not None and str(s["enforces"]) not in dec_set:
            errs.append(f"set {sid}: enforces {s['enforces']!r} is not a decision id")
        if s.get("start"):
            check_event(f"set {sid} start", s["start"], _EVENT_KEYS)
        if not s.get("start") and not s.get("decisions"):
            errs.append(f"set {sid}: no start and no decisions")
        for d in s.get("decisions") or []:
            where = f"set {sid} decision {d.get('id')}"
            check_event(where, d, _DECISION_KEYS)
            for k in ("level", "outcome", "direction"):
                if k not in d:
                    errs.append(f"{where}: missing {k}")
            r = d.get("reviews")
            if r is not None and (
                dec_set.get(str(r)) != sid or str(r) == str(d.get("id"))
            ):
                errs.append(
                    f"{where}: reviews {r!r} is not an earlier decision of this set"
                )
            if r is None and (d.get("appealed_by") or d.get("appeal_date")):
                errs.append(f"{where}: appealed_by / appeal_date without reviews")
    if len(dec_set) != sum(
        len(s.get("decisions") or []) for s in data.get("sets") or []
    ):
        errs.append("repeated decision id")
    return errs


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, value):
    style = '"' if value[:1].isdigit() else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str)


def dump_yaml(doc: dict, header: str = "") -> str:
    body = yaml.dump(
        to_nested(doc), Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100
    )
    return header + body


# ---------------------------------------------------------------------------
# reviews
# ---------------------------------------------------------------------------


REVIEW_LEVELS = {"Appeal", "Cassation", "Constitutional", "CaseReview", "Reopening"}


def is_review(level: str, target: dict, appealed: bool = False) -> bool:
    """Guide v3 `reviews`: an appeal, cassation, constitutional, supervisory or
    reopening decision rules on the decision before it; any other decision
    reviews only an administrative decision (a court reviewing it) or the
    decision that remitted the case to it. A trial after an investigation,
    or two steps of one administrative procedure, are just in sequence --
    unless the later one decides an appeal against the earlier (`appealed`:
    an inspector or tribunal on appeal from an administrative decision)."""
    if level in REVIEW_LEVELS:
        return True
    return target.get("outcome") == "Remitted" or (
        target.get("level") == "AdministrativeReview"
        and (level != "AdministrativeReview" or appealed)
    )


# ---------------------------------------------------------------------------
# Order and scorer nodes
# ---------------------------------------------------------------------------


def date_key(date: str | None) -> str:
    return (date or "9999").ljust(10, "9")  # a year-only date sorts to its end


def ordered(doc: dict, set_id: str) -> list[dict]:
    """The set's events in derived order: the start first, then decisions by
    date, a decision after the one it reviews on the same day, text order
    breaking remaining ties."""
    evs = [(i, e) for i, e in enumerate(doc["events"]) if e["set"] == set_id]
    starts = [e for _, e in evs if e["role"] == "start"]
    decs = [(i, e) for i, e in evs if e["role"] == "decision"]
    ids = {e["id"] for _, e in decs}

    def depth(e, seen=()):
        r = e.get("reviews")
        if r in ids and r not in seen:
            return 1 + depth(next(x for _, x in decs if x["id"] == r), (*seen, e["id"]))
        return 0

    decisions = [
        e
        for _, e in sorted(
            decs, key=lambda ie: (date_key(ie[1].get("date")), depth(ie[1]), ie[0])
        )
    ]
    return starts[:1] + decisions


def derived_follows(doc: dict) -> dict[str, str]:
    out = {}
    for s in doc["sets"]:
        for prev, cur in pairwise(ordered(doc, s["id"])):
            out[cur["id"]] = prev["id"]
    return out


def nodes(doc: dict) -> list[Node]:
    """Scorer nodes: a start is a filing (start date only), a decision a
    decision. Edge kinds: the derived `follows` and the extracted `reviews`."""
    follows = derived_follows(doc)
    out = []
    for e in doc["events"]:
        start = e["role"] == "start"
        n = Node(
            key=e["id"],
            body=e.get("body"),
            decision_date=None if start else e.get("date"),
            start_date=e.get("date") if start else None,
            quote=e.get("quote"),
            level=e.get("level"),
        )
        if e["id"] in follows:
            n.edges["follows"].add(follows[e["id"]])
        if e.get("reviews"):
            n.edges["reviews"].add(e["reviews"])
        out.append(n)
    return out


# ---------------------------------------------------------------------------
# Command line: check annotation files
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def check(data: dict, text: str | None = None) -> list[str]:
    """validate() plus, given the source text, every quote found verbatim
    (up to whitespace, curly quotes and case)."""
    errs = validate(data)
    if text is not None:
        tn = _norm(text)
        try:
            events = from_nested(data)["events"]
        except (KeyError, TypeError):
            events = []
        for e in events:
            if e.get("quote") and _norm(str(e["quote"])) not in tn:
                errs.append(
                    f"{e['id']}: quote not found in the text: {str(e['quote'])[:60]!r}"
                )
    return errs


def main() -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Check set-model annotation files (guide section 8)."
    )
    ap.add_argument(
        "paths", nargs="+", type=Path, help="<case>.yaml files or directories of them"
    )
    ap.add_argument(
        "--text-dir",
        type=Path,
        help="directory of <case>.txt sources: also check quotes",
    )
    a = ap.parse_args()
    files = [
        f for p in a.paths for f in (sorted(p.glob("0*.yaml")) if p.is_dir() else [p])
    ]
    bad = 0
    for f in files:
        src = a.text_dir / f"{f.stem}.txt" if a.text_dir else None
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        errs = check(
            data, src.read_text(encoding="utf-8") if src and src.exists() else None
        )
        bad += bool(errs)
        print(f"{'ok  ' if not errs else 'FAIL'} {f}")
        for e in errs:
            print(f"     {e}")
    print(f"{len(files) - bad}/{len(files)} file(s) valid")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
