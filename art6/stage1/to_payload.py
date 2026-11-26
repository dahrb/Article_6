"""Set-model extraction -> the stage-1 payload the rest of the pipeline reads.

    uv run python -m art6.stage1.to_payload IN_DIR SOURCE_JSONL OUT_DIR

Writes OUT_DIR/<case>.compress.json, the payload the bridge (render_bundles),
OntoCast (stage 2) and reconcile (stage 3) read. Every text field becomes a span with its character offsets,
found by string search (exact, then whitespace/quote/case-insensitive; an
event's body and dates are searched in its own quote first). The payload is
flagged `set_model: true`; each set becomes a track carrying its kind,
pending flag and `enforces`; each event carries its role, the DERIVED
`follows` (start first, then decisions by date), `reviews`, `appealed_by` and
the appeal and communication dates.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from art6.model import derived_follows, load_doc

_WS = re.compile(r"\s+")


def _norm_with_map(text: str) -> tuple[str, list[int]]:
    """Lower-cased text with quotes unified and whitespace runs collapsed,
    plus the source offset of every kept character."""
    out, idx, prev_space = [], [], False
    for i, ch in enumerate(text):
        ch = {"’": "'", "‘": "'", "“": '"', "”": '"'}.get(ch, ch)
        if ch.isspace():
            if prev_space:
                continue
            ch, prev_space = " ", True
        else:
            prev_space = False
        out.append(ch.lower())
        idx.append(i)
    return "".join(out), idx


class Locator:
    def __init__(self, text: str):
        self.text = text
        self.norm, self.idx = _norm_with_map(text)

    def find(
        self, needle: str | None, lo: int = 0, hi: int | None = None
    ) -> dict | None:
        if not needle:
            return None
        needle = str(needle).strip()
        hi = len(self.text) if hi is None else hi
        i = self.text.find(needle, lo, hi)
        if i >= 0:
            return {"text": needle, "start": i, "end": i + len(needle)}
        n, _ = _norm_with_map(needle)
        n = n.strip()
        if not n:
            return None
        # restrict the normalised search to [lo, hi)
        nlo = next((k for k, v in enumerate(self.idx) if v >= lo), len(self.idx))
        j = self.norm.find(n, nlo)
        if j < 0 or self.idx[j] >= hi:
            return None
        start, end = self.idx[j], self.idx[j + len(n) - 1] + 1
        return {"text": self.text[start:end], "start": start, "end": end}


LEVEL = {
    "Investigative": "investigative",
    "AdministrativeReview": "administrative review",
    "FirstInstance": "first instance",
    "Appeal": "appeal",
    "Cassation": "cassation",
    "Constitutional": "constitutional",
    "CaseReview": "case review",
    "Reopening": "reopening",
    "Unknown": "unknown",
}


def words(camel: str | None) -> str | None:
    """'SettledOrWithdrawn' -> 'settled or withdrawn' (the payload's vocabulary)."""
    if not camel:
        return None
    return re.sub(r"(?<!^)(?=[A-Z])", " ", camel).lower()


def to_payload(doc: dict, text: str) -> dict:
    loc = Locator(text)
    follows = derived_follows(doc)
    sets = {s["id"]: s for s in doc["sets"]}
    # payload ids are e1..eN, the form stage 2 and the graph IRIs expect
    eid = {e["id"]: f"e{i}" for i, e in enumerate(doc["events"], 1)}
    events = []
    for order, e in enumerate(doc["events"], 1):
        q = loc.find(e.get("quote"))
        lo, hi = (q["start"], q["end"]) if q else (0, None)
        body = loc.find(e.get("body"), lo, hi) or loc.find(e.get("body"))
        ev = {
            "id": eid[e["id"]],
            "set_model_id": e["id"],
            "order": order,
            "role": e["role"],
            "track": e["set"],
            "description": f"{'start of' if e['role'] == 'start' else 'decision in'} "
            f"{sets.get(e['set'], {}).get('label') or e['set']}",
            "significance": "major",
            "proceeding_type": (
                sets.get(e["set"], {}).get("kind") or "Unknown"
            ).lower(),
            "matter": [words(m) for m in e.get("matter") or []],
            "authority_kind": words(e.get("authority_kind")),
            "parties": [],
        }
        if q:
            ev["what_happened_span"] = q
        if body:
            ev["authority_span"] = body
        key = "start_date_iso" if e["role"] == "start" else "decision_date_iso"
        ev[key] = e.get("date")
        if e["role"] == "decision":
            ev["instance_level"] = LEVEL.get(e.get("level") or "Unknown", "unknown")
            ev["outcome"] = words(e.get("outcome") or "Unknown")
            ev["outcome_direction"] = words(e.get("direction") or "Unknown")
            if e.get("reviews") in eid:
                ev["reviews"] = eid[e["reviews"]]
            if e.get("appealed_by"):
                ev["appealed_by"] = words(e["appealed_by"])
            if e.get("costs"):
                ev["costs"] = words(e["costs"])
            for k in ("appeal_date", "communication_date"):
                if e.get(k):
                    ev[f"{k}_iso"] = e[k]
        else:
            ev["instance_level"] = "unknown"
        if e["id"] in follows:
            ev["follows"] = eid[follows[e["id"]]]
        for p in e.get("parties") or []:
            name = loc.find(p.get("name"), lo, hi) or loc.find(p.get("name"))
            if name:
                ev["parties"].append({"name_span": name, "side": words(p.get("side"))})
        events.append(ev)

    case = doc.get("case") or {}
    payload = {
        "set_model": True,
        "case": {
            f"{k}_span": loc.find(case.get(k))
            for k in (
                "case_name",
                "application_number",
                "respondent_state",
                "judgment_date",
            )
            if loc.find(case.get(k))
        },
        "tracks": [
            {
                "id": s["id"],
                "label": s.get("label") or "",
                "kind": (s.get("kind") or "Unknown").lower(),
                "pending": bool(s.get("pending")),
                "enforces": eid.get(s.get("enforces")),
            }
            for s in doc["sets"]
        ],
        "track_relations": [],
        "events": events,
        "applicants": [],
        "persons": [],
        "authorities": [],
    }
    for a in doc.get("applicants") or []:
        q = loc.find(a.get("quote"))
        lo, hi = (q["start"], q["end"]) if q else (0, None)
        entry = {
            "name_span": loc.find(a.get("name"), lo, hi) or loc.find(a.get("name"))
        }
        for k in ("birth_year", "nationality", "residence", "gender"):
            sp = loc.find(a.get(k), lo, hi) if q else None
            if sp:
                entry[f"{k}_span"] = sp
        if q:
            entry["description_span"] = q
        if entry["name_span"]:
            payload["applicants"].append(entry)
    for p in doc.get("persons") or []:
        name = loc.find(p.get("name"))
        if name:
            entry = {"name_span": name}
            role = loc.find(p.get("role"))
            if role:
                entry["role_span"] = role
            payload["persons"].append(entry)
    seen = set()
    for ev in events:
        sp = ev.get("authority_span")
        if sp and sp["text"].lower() not in seen:
            seen.add(sp["text"].lower())
            payload["authorities"].append({"name_span": sp})
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("in_dir", type=Path)
    ap.add_argument("source_jsonl", type=Path)
    ap.add_argument("out_dir", type=Path)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for line in a.source_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        f = a.in_dir / f"{rec['case_id']}.json"
        if not f.exists():
            continue
        payload = to_payload(load_doc(f), rec["text"])
        (a.out_dir / f"{rec['case_id']}.compress.json").write_text(
            json.dumps(
                {"doc": rec["case_id"], "stats": {}, "payload": payload},
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        n += 1
    print(f"to_payload: {n} document(s) -> {a.out_dir}")


if __name__ == "__main__":
    main()
