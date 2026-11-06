"""Small data adapter; deliberately independent of extraction and scoring code."""

from __future__ import annotations

import calendar
import json
import re
from datetime import date
from pathlib import Path

DEMO_PATH = Path(__file__).parent / "data" / "validation_demo.json"
RELATIONS = {
    "follows": "Follows",
    "part_of": "Part of",
    "occurs_during": "Occurs during",
}


def span(value: object) -> str:
    return str(value.get("text") or "") if isinstance(value, dict) else ""


def date_bounds(value: str) -> tuple[date, date] | None:
    """Keep year/month precision as an interval; never repair invalid dates."""
    if not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value):
        return None
    try:
        parts = list(map(int, value.split("-")))
        year = parts[0]
        if len(parts) == 1:
            return date(year, 1, 1), date(year, 12, 31)
        month = parts[1]
        if len(parts) == 2:
            return date(year, month, 1), date(
                year, month, calendar.monthrange(year, month)[1]
            )
        day = date(*parts)
        return day, day
    except ValueError:
        return None


def event_date(event: dict) -> tuple[str, tuple[date, date] | None]:
    for kind in ("decision", "start"):
        value = event.get(f"{kind}_date_iso") or ""
        if bounds := date_bounds(value):
            return value, bounds
    text = span(event.get("decision_date_span")) or span(event.get("start_date_span"))
    return text or "Undated", None


def event_track(event: dict, by_id: dict[str, dict]) -> str:
    """Inherit explicit membership through containment only, with cycle protection."""
    seen = set()
    while event:
        if event.get("track"):
            return str(event["track"])
        if event["id"] in seen:
            break
        seen.add(event["id"])
        event = by_id.get(event.get("part_of"))
    return "Unassigned"


def event_rows(payload: dict) -> list[dict]:
    by_id = {e["id"]: e for e in payload["events"]}
    tracks = {t["id"]: t.get("label", t["id"]) for t in payload.get("tracks", [])}
    rows = []
    for event in payload["events"]:
        when, bounds = event_date(event)
        track = event_track(event, by_id)
        rows.append(
            {
                "id": event["id"],
                "date": when,
                "bounds": bounds,
                "track": track,
                "track_label": tracks.get(track, track),
                "court": span(event.get("authority_span")) or "Unspecified authority",
                "level": event.get("instance_level") or "unknown",
                "outcome": event.get("outcome") or "unknown",
                "direction": event.get("outcome_direction") or "unknown",
                "significance": event.get("significance") or "unspecified",
                "description": event.get("description")
                or span(event.get("what_happened_span")),
                "quote": span(event.get("what_happened_span")),
                "type": event.get("proceeding_type") or "unknown",
                "raw": event,
            }
        )
    return sorted(
        rows, key=lambda r: (r["bounds"][0] if r["bounds"] else date.max, r["id"])
    )


def filter_rows(
    rows: list[dict], filters: dict[str, list[str]], query: str = ""
) -> list[dict]:
    words = query.casefold().split()
    return [
        r
        for r in rows
        if all(r[k] in values for k, values in filters.items())
        and all(
            word
            in " ".join(
                str(r[k]) for k in ("id", "description", "court", "quote")
            ).casefold()
            for word in words
        )
    ]


def visible_links(rows: list[dict]) -> list[tuple[str, str, str]]:
    ids = {r["id"] for r in rows}
    return [
        (r["id"], r["raw"][kind], kind)
        for r in rows
        for kind in RELATIONS
        if r["raw"].get(kind) in ids
    ]


def load_demo(path: Path = DEMO_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
