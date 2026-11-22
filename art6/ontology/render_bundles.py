"""Stage 1 -> stage 2 handoff.

Renders verified evidence bundles as a compact text document and writes a JSONL
in the shape OntoCast already consumes, so stage 2 needs no pipeline changes.

The rendered document REPLACES the judgment. That is the point of the design:
stage 2 cannot introduce content that did not pass stage 1's verbatim check, so
every span it can quote is one already proven to exist in the source. It also
means the ontology prompt no longer competes with 24,000-38,000 characters of
narrative for the model's attention.

Offsets travel in the rendered text as [start:end] markers rather than being
dropped. They are what makes a downstream triple traceable to a character range
without re-running a string search, and they survive into the facts graph as the
supporting quote's provenance.
"""

from __future__ import annotations


def _span(value) -> str | None:
    if isinstance(value, dict) and "text" in value:
        return f'"{value["text"]}" [{value["start"]}:{value["end"]}]'
    return None


def _line(label: str, value, indent: str = "  ") -> list[str]:
    rendered = _span(value)
    return [f"{indent}{label}: {rendered}"] if rendered else []


def render(payload: dict, source: str | None = None, mode: str = "bundle") -> str:
    """The stage-1 digest stage 2 reads: the verified fields alone. `source`
    and `mode` are kept for callers; only the "bundle" format remains (the
    evidence and paragraph variants were bridge experiments, removed
    2026-11-09)."""
    out: list[str] = []
    case = payload.get("case") or {}
    if case:
        out.append("CASE")
        for key in (
            "case_name_span",
            "application_number_span",
            "respondent_state_span",
            "judgment_date_span",
        ):
            out += _line(key.removesuffix("_span"), case.get(key))
        out.append("")

    # Tracks are a planning device, not an ontology class: they tell stage 2
    # which events belong to one matter, and every link below stays inside one
    # unless it is occurs_during. Absent on payloads from the old single-call stage 1.
    tracks = payload.get("tracks") or []
    labels = {t.get("id"): t.get("label") or "" for t in tracks}
    if tracks:
        out.append(
            f"TRACKS ({len(tracks)}) -- the separate matters these events belong to"
        )
        for track in tracks:
            extra = ", ".join(
                f"{k}: {track[k]}" for k in ("kind", "subject") if track.get(k)
            )
            out.append(
                f"  {track.get('id')}: {track.get('label') or '(unlabelled)'}"
                + (f" [{extra}]" if extra else "")
            )
        for rel in payload.get("track_relations") or []:
            words = (
                rel.get("span", {}).get("text")
                if isinstance(rel.get("span"), dict)
                else ""
            )
            out.append(
                f"  {rel['track']} {rel['relation'].replace('_', ' ')} {rel['other']}"
                + (f' -- "{words}"' if words else "")
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
        if event.get("description"):
            out.append(
                f"  description (a summary, not a quote): {event['description']}"
            )
        for key, label in (
            ("decision_date_iso", "decision date (ISO)"),
            ("start_date_iso", "start date (ISO)"),
        ):
            if event.get(key):
                out.append(f"  {label}: {event[key]}")
        for key in (
            "what_happened_span",
            "authority_span",
            "authority_kind_span",
            "start_date_span",
            "decision_date_span",
            "proceeding_type_span",
            "finality_span",
            "pending_span",
            "follows_span",
        ):
            out += _line(key.removesuffix("_span"), event.get(key))
        # instance_span / outcome_span render like any other span.
        # instance_level / outcome / proceeding_type / outcome_direction are
        # CLASSIFICATIONS: no offsets, so _line would drop them. They are
        # evidenced by the event's own what_happened_span above, and
        # corroborated by their _span partner wherever the document stated
        # the rung or result outright.
        out += _line("outcome_span", event.get("outcome_span"))
        out += _line("instance_span", event.get("instance_span"))
        for key in (
            "instance_level",
            "outcome",
            "proceeding_type",
            "outcome_direction",
        ):
            if event.get(key):
                out.append(f"  {key}: {event[key]}")
        # Matter is a list, and was never rendered before 2026-10-22: stage 2
        # had to re-derive it from the quotes, and matter agreement on the
        # bundle arm trailed the raw judgment's by 20-40 points.
        if event.get("matter"):
            out.append(f"  matter: {', '.join(event['matter'])}")
        # Chain links. Payloads from agentic_stage1 carry them TYPED and aimed
        # at the target proceeding's earliest act; old single-call payloads carry
        # an untyped `follows`, which facts.txt tells stage 2 to type itself.
        for key in ("follows", "part_of", "occurs_during"):
            if event.get(key):
                out.append(f"  {key}: {event[key]}")
        out += _line("link_words", event.get("link_span"))
        for party in event.get("parties") or []:
            name, role = _span(party.get("name_span")), _span(party.get("role_span"))
            if name:
                out.append(f"  party: {name}" + (f" — did: {role}" if role else ""))
        for item in event.get("interim_spans") or []:
            rendered = _span(item)
            if rendered:
                out.append(f"  interim step (same body): {rendered}")
        for detention in event.get("detention") or []:
            out.append("  detention:")
            for key in (
                "detainee_span",
                "start_span",
                "end_span",
                "measure_span",
                "duration_span",
                "still_detained_span",
            ):
                out += _line(key.removesuffix("_span"), detention.get(key), "    ")
        for inactivity in event.get("inactivity") or []:
            out.append("  inactivity:")
            for key in ("start_span", "end_span", "cause_span"):
                out += _line(key.removesuffix("_span"), inactivity.get(key), "    ")
        for adjournment in event.get("adjournments") or []:
            out.append("  adjournment:")
            for key in ("date_span", "resumption_span", "cause_span"):
                out += _line(key.removesuffix("_span"), adjournment.get(key), "    ")
        out.append("")

    # Timeline extension (extract_permissive.txt + ontology/echr_timeline.ttl):
    # dated occurrences no authority took, and the lodging in Strasbourg.
    circumstances = payload.get("circumstances") or []
    if circumstances:
        out.append(
            f"CIRCUMSTANCES ({len(circumstances)}) -- dated occurrences in the case's "
            "history that are NOT steps by an authority."
        )
        out.append("")
        for item in circumstances:
            out.append(f"CIRCUMSTANCE {item.get('id', '?')}")
            if item.get("description"):
                out.append(
                    f"  description (a summary, not a quote): {item['description']}"
                )
            out += _line("what_happened", item.get("what_happened_span"))
            out += _line("date", item.get("date_span"))
            out.append(f"  kind: {item.get('kind') or 'other'}")
            for person in item.get("persons_spans") or []:
                out += _line("concerns", person)
            if item.get("gives_rise_to"):
                out.append(f"  gives_rise_to: {item['gives_rise_to']}")
            out.append("")
    app = payload.get("strasbourg_application")
    if app:
        out.append("STRASBOURG APPLICATION")
        out += _line("lodged", app.get("date_span"))
        out += _line("what_happened", app.get("what_happened_span"))
        out.append("")

    persons = payload.get("persons") or []
    if persons:
        out.append(f"PERSONS ({len(persons)})")
        for person in persons:
            out.append("  person:")
            for key in ("name_span", "role_span", "gender_cue_span", "represents_span"):
                out += _line(key.removesuffix("_span"), person.get(key), "    ")
        out.append("")

    # Rendered as its own section rather than folded into PERSONS above, for the
    # same reason it is a separate list in the stage-1 schema: measured
    # 2026-08-28 on gemma-4-31b, putting the demographic fields on every person
    # entry made the model treat `persons` as a choice between an exhaustive
    # cast list and a described applicant, and it could not do both. Asking for
    # the two separately gets both -- birth-year recall went from 5/9 to 7/7
    # while the person list returned to its baseline size. The applicant appears
    # in both lists; stage 2 joins them on the name.
    applicants = payload.get("applicants") or []
    if applicants:
        out.append(f"APPLICANTS ({len(applicants)}), described by the document itself.")
        out.append("One block per applicant, in the order the description gives them.")
        out.append("")
        for applicant in applicants:
            out.append("  applicant:")
            for key in (
                "name_span",
                "birth_year_span",
                "nationality_span",
                "residence_span",
                "gender_span",
                "description_span",
            ):
                out += _line(key.removesuffix("_span"), applicant.get(key), "    ")
        out.append("")

    authorities = payload.get("authorities") or []
    if authorities:
        out.append(f"AUTHORITIES ({len(authorities)})")
        for authority in authorities:
            name, kind = (
                _span(authority.get("name_span")),
                _span(authority.get("kind_span")),
            )
            if name:
                out.append(f"  {name}" + (f" — kind: {kind}" if kind else ""))
    return "\n".join(out).strip() + "\n"
