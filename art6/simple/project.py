"""Project a stage-1 payload (the current, detailed model) onto the simplified one.

    uv run python -m art6.simple.project IN_S1_DIR OUT_DIR

This is how today's pipeline output is scored under the simplified model, at no
model cost. It applies the same rules as the simplified gold (NOTES.md §1), as
far as the payload's own fields allow:
  set       = the payload's `track` (stage 1's own "one set of proceedings")
  decision  = an event with a decision date whose outcome is one of merits
              decided / inadmissible / remitted / settled or withdrawn;
              outcome `other` or none is a procedural order, dropped
  start     = the track's earliest filing (start date, no decision date), if it
              comes before the track's first decision; other filings dropped
  pending   = a `pending_span` on any of the track's events
Investigative measures and execution steps cannot be told apart from decisions
by fields alone, so they survive the projection when they carry a merits
outcome; the extractor prompt (prompts/extract_simple.txt) handles those.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from art6.ontology.vote import event_date

LEVEL = {
    "first instance": "first_instance",
    "appeal": "appeal",
    "cassation": "cassation",
    "investigative": "investigation",
    "administrative review": "administrative",
    "case review": "other",
    "reopening": "other",
    "unknown": "other",
}
OUTCOME = {
    "merits decided": "merits",
    "inadmissible": "procedural",
    "remitted": "remitted",
    "settled or withdrawn": "settled",
}
DIRECTION = {
    "favourable to applicant": "for_applicant",
    "unfavourable to applicant": "against_applicant",
    "mixed": "mixed",
    "unknown": "unknown",
}


def _text(span):
    return span.get("text") if isinstance(span, dict) else None


def project(case: str, payload: dict) -> dict:
    from art6.simple.model import level_from_body

    events, sets = [], []
    tracks = {t["id"]: t for t in payload.get("tracks") or []}
    by_track: dict[str, list] = {}
    for e in payload["events"]:
        by_track.setdefault(e.get("track") or "T?", []).append(e)
    for tid, evs in by_track.items():
        decisions = [
            e
            for e in evs
            if event_date(e, "decision")
            and str(e.get("outcome") or "").lower() in OUTCOME
        ]
        if not decisions:
            continue
        first = min(
            (event_date(e, "decision") or "9999").ljust(10, "9") for e in decisions
        )
        filings = sorted(
            (
                e
                for e in evs
                if event_date(e, "start") and not event_date(e, "decision")
            ),
            key=lambda e: event_date(e, "start").ljust(10, "0"),
        )
        start = (
            filings[0]
            if filings and event_date(filings[0], "start").ljust(10, "0") <= first
            else None
        )
        sets.append(
            {
                "id": tid,
                "kind": str(evs[0].get("proceeding_type") or "other"),
                "label": tracks.get(tid, {}).get("label", ""),
                "pending": any(e.get("pending_span") for e in evs),
            }
        )
        if start:
            events.append(
                {
                    "id": start["id"],
                    "set": tid,
                    "role": "start",
                    "date": event_date(start, "start"),
                    "body": _text(start.get("authority_span")),
                    "quote": _text(start.get("what_happened_span")),
                }
            )
        for e in decisions:
            body = _text(e.get("authority_span"))
            events.append(
                {
                    "id": e["id"],
                    "set": tid,
                    "role": "decision",
                    "date": event_date(e, "decision"),
                    "body": body,
                    "quote": _text(e.get("what_happened_span")),
                    "level": level_from_body(
                        LEVEL.get(str(e.get("instance_level")), "other"), body
                    ),
                    "outcome": OUTCOME[str(e["outcome"]).lower()],
                    "direction": DIRECTION.get(
                        str(e.get("outcome_direction") or "unknown"), "unknown"
                    ),
                }
            )
    return {"case_id": case, "sets": sets, "events": events}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("s1_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(a.s1_dir.glob("*.compress.json")):
        case = f.name.removesuffix(".compress.json")
        doc = project(case, json.loads(f.read_text(encoding="utf-8"))["payload"])
        (a.out_dir / f"{case}.json").write_text(
            json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
