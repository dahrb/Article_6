"""Stage 1 -> stage 2 handoff: the digest OntoCast reads instead of the judgment.

`render(payload)` turns one stage-1 payload (art6/stage1/to_payload.py) into a
compact text document. The digest REPLACES the judgment: stage 2 cannot
introduce content that did not pass stage 1's verbatim check, so every span it
can quote is one already proven to exist in the source, and the ontology
prompt does not compete with 24,000-38,000 characters of narrative.

Offsets travel in the text as [start:end] markers, so a triple stays traceable
to a character range of the judgment without another string search.
"""

from __future__ import annotations


def _span(value) -> str | None:
    if isinstance(value, dict) and "text" in value:
        return f'"{value["text"]}" [{value["start"]}:{value["end"]}]'
    return None


def _line(label: str, value, indent: str = "  ") -> list[str]:
    rendered = _span(value)
    return [f"{indent}{label}: {rendered}"] if rendered else []


DATES = (
    ("decision_date_iso", "decision date (ISO)"),
    ("start_date_iso", "start date (ISO)"),
    ("appeal_date_iso", "appeal lodged (ISO)"),
    ("communication_date_iso", "communicated to the applicant (ISO)"),
)
# classifications: no offsets, evidenced by the event's own quote
CLASSES = (
    "instance_level",
    "outcome",
    "proceeding_type",
    "outcome_direction",
    "authority_kind",
    "appealed_by",
    "costs",
)


def render(payload: dict) -> str:
    out: list[str] = []
    case = payload.get("case") or {}
    if case:
        out.append("CASE")
        for key in (
            "case_name",
            "application_number",
            "respondent_state",
            "judgment_date",
        ):
            out += _line(key, case.get(f"{key}_span"))
        out.append("")

    tracks = payload.get("tracks") or []
    labels = {t.get("id"): t.get("label") or "" for t in tracks}
    if tracks:
        out.append(
            f"SETS OF PROCEEDINGS ({len(tracks)}) -- each TRACK below is one echr:ProceedingSet"
        )
        for track in tracks:
            extra = ", ".join(
                f"{k}: {track[k]}"
                for k in ("kind", "pending", "enforces")
                if track.get(k)
            )
            out.append(
                f"  {track.get('id')}: {track.get('label') or '(unlabelled)'}"
                + (f" [{extra}]" if extra else "")
            )
        out.append("")

    events = payload.get("events") or []
    out.append(
        f"DOMESTIC EVENTS ({len(events)}), in the order the judgment gives them."
    )
    out.append(
        "Each block is ONE jurisdictional instance. Quoted text is verbatim from"
    )
    out.append(
        "the judgment; the [start:end] pair is its character range in the source."
    )
    out.append("")
    for event in events:
        out.append(f"EVENT {event.get('id', '?')}")
        if event.get("track"):
            label = labels.get(event["track"])
            out.append(f"  track: {event['track']}" + (f" ({label})" if label else ""))
        if event.get("role"):
            out.append(f"  role: {event['role']} act of its set")
        if event.get("description"):
            out.append(
                f"  description (a summary, not a quote): {event['description']}"
            )
        for key, label in DATES:
            if event.get(key):
                out.append(f"  {label}: {event[key]}")
        for key in ("what_happened", "authority"):
            out += _line(key, event.get(f"{key}_span"))
        for key in CLASSES:
            if event.get(key):
                out.append(f"  {key}: {event[key]}")
        if event.get("matter"):
            out.append(f"  matter: {', '.join(event['matter'])}")
        for key in ("follows", "reviews"):
            if event.get(key):
                out.append(f"  {key}: {event[key]}")
        for party in event.get("parties") or []:
            name = _span(party.get("name_span"))
            if name:
                out.append(
                    f"  party: {name}"
                    + (f" — side: {party['side']}" if party.get("side") else "")
                )
        out.append("")

    persons = payload.get("persons") or []
    if persons:
        out.append(f"PERSONS ({len(persons)})")
        for person in persons:
            out.append("  person:")
            for key in ("name", "role"):
                out += _line(key, person.get(f"{key}_span"), "    ")
        out.append("")

    # A separate section from PERSONS: one list asked to be both an exhaustive
    # cast and a described applicant got neither (measured 2026-08-28).
    applicants = payload.get("applicants") or []
    if applicants:
        out.append(f"APPLICANTS ({len(applicants)}), described by the document itself.")
        out.append("One block per applicant, in the order the description gives them.")
        out.append("")
        for applicant in applicants:
            out.append("  applicant:")
            for key in (
                "name",
                "birth_year",
                "nationality",
                "residence",
                "gender",
                "description",
            ):
                out += _line(key, applicant.get(f"{key}_span"), "    ")
        out.append("")

    authorities = payload.get("authorities") or []
    if authorities:
        out.append(f"AUTHORITIES ({len(authorities)})")
        for authority in authorities:
            name = _span(authority.get("name_span"))
            if name:
                out.append(f"  {name}")
    return "\n".join(out).strip() + "\n"
