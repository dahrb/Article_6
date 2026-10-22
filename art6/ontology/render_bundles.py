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

import argparse
import bisect
import json
import re
from pathlib import Path

from art6.ontology.agentic_stage1 import _ANY_DATE, facts_region, sentence_at


def _span(value) -> str | None:
    if isinstance(value, dict) and "text" in value:
        return f'"{value["text"]}" [{value["start"]}:{value["end"]}]'
    return None


def _line(label: str, value, indent: str = "  ") -> list[str]:
    rendered = _span(value)
    return [f"{indent}{label}: {rendered}"] if rendered else []


# Spans that place an event in the text. Parties and persons are left out:
# their spans are often taken from wherever the document first names someone.
CORE_SPANS = (
    "what_happened_span",
    "decision_date_span",
    "start_date_span",
    "institution_narrated_span",
    "outcome_span",
    "authority_span",
)


def evidence(event: dict, source: str) -> list[tuple[int, int]]:
    """The sentences holding the event's own words, merged where they touch."""
    found = []
    for key in CORE_SPANS:
        value = event.get(key)
        if isinstance(value, dict) and "start" in value:
            found.append(sentence_at(source, value["start"], value["end"]))
    merged: list[list[int]] = []
    for lo, hi in sorted(set(found)):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def render(payload: dict, source: str | None = None, mode: str = "bundle") -> str:
    """mode: see MODES. `source` is the judgment; needed by every mode but
    "bundle"."""
    if mode.startswith(("paragraphs", "dated")):
        return render_paragraphs(payload, source or "", mode)
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
    # unless it is occurs_during. Absent on payloads from compress.py.
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
        if mode == "evidence" and source:
            for lo, hi in evidence(event, source):
                out.append(f'  evidence: "{source[lo:hi]}" [{lo}:{hi}]')
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
        # at the target proceeding's earliest act; compress.py payloads carry
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


MODES = (
    "bundle",  # the verified fields alone
    "evidence",  # ... plus each event's verbatim sentences
    "paragraphs",  # the judgment's event paragraphs, verbatim, and nothing else
    "paragraphs+index",  # ... followed by a compact index of stage 1's events
    "paragraphs+index-nolinks",  # ... the index without stage 1's links
    "dated+index",  # paragraphs+index, plus every dated paragraph of the Facts
)


def _index_line(event: dict, para_of) -> str:
    def t(key):
        v = event.get(key)
        return v["text"] if isinstance(v, dict) else None

    bits = [f"{event.get('id')}"]
    if event.get("description"):
        bits.append(event["description"])
    for label, key in (
        ("filed", "start_date_span"),
        ("decided", "decision_date_span"),
        ("by", "authority_span"),
    ):
        if t(key):
            bits.append(f"{label}: {t(key)}")
    for key in ("instance_level", "outcome", "outcome_direction"):
        if event.get(key):
            bits.append(f"{key}: {event[key]}")
    if event.get("matter"):
        bits.append("matter: " + ", ".join(event["matter"]))
    anchor = event.get("what_happened_span")
    if isinstance(anchor, dict):
        bits.append(f"para {para_of(anchor['start'])}")
    return " | ".join(bits)


def render_paragraphs(payload: dict, source: str, mode: str) -> str:
    """The judgment's own paragraphs that narrate an event, verbatim and in
    order, so stage 2 reads text rather than fields. Stage 1 decides only WHICH
    paragraphs: every one holding an event's own words, the applicants'
    description and the case header. With an index, stage 1's events follow as
    a checklist of what the paragraphs contain."""
    lines = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", source)]
    starts = [lo for lo, _ in lines]

    def line_of(pos: int) -> int:
        return max(0, bisect.bisect_right(starts, pos) - 1)

    def para_of(pos: int) -> str:
        text = source[slice(*lines[line_of(pos)])]
        m = re.match(r"\s*\*\*(\d+)\.\*\*", text)
        return m.group(1) if m else f"line {line_of(pos) + 1}"

    keep: set[int] = set()
    for event in payload.get("events") or []:
        for key in CORE_SPANS:
            v = event.get(key)
            if isinstance(v, dict) and "start" in v:
                keep.update(
                    range(
                        line_of(v["start"]), line_of(max(v["start"], v["end"] - 1)) + 1
                    )
                )
    for applicant in payload.get("applicants") or []:
        for v in applicant.values():
            if isinstance(v, dict) and "start" in v:
                keep.add(line_of(v["start"]))
    for v in (payload.get("case") or {}).values():
        if isinstance(v, dict) and "start" in v:
            keep.add(line_of(v["start"]))
    if mode.startswith("dated"):
        # Stage 1's events are a floor, not a ceiling: a dated paragraph in the
        # Facts it found nothing in is where its missed steps are.
        lo, hi = facts_region(source)
        for n, (a, b) in enumerate(lines):
            if lo <= a < hi and _ANY_DATE.search(source, a, b):
                keep.add(n)
    out = [
        "EXCERPT -- the paragraphs of the judgment that narrate the domestic events,",
        "copied VERBATIM and in order; paragraphs with nothing to model are left out",
        "and marked [...]. Character offsets refer to the full judgment.",
        "",
    ]
    previous = None
    for n in sorted(keep):
        if previous is not None and n != previous + 1:
            out.append("[...]")
        lo, hi = lines[n]
        out.append(source[lo:hi])
        previous = n
    if mode != "paragraphs":
        out += [
            "",
            (
                f"EVENT INDEX ({len(payload.get('events') or [])}) -- the steps a first reading "
                "found in these paragraphs, one per line. A checklist, not a source: every "
                "fact you model must come from the paragraphs above. Model every step listed "
                "here, and any step the paragraphs narrate that the list missed."
            ),
        ]
        for event in payload.get("events") or []:
            line = _index_line(event, para_of)
            if mode == "paragraphs+index":
                for key in ("follows", "part_of", "occurs_during"):
                    if event.get(key):
                        line += f" | {key}: {event[key]}"
            out.append("  " + line)
    return "\n".join(out).strip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--compress-dir", required=True, type=Path)
    ap.add_argument(
        "--source-jsonl",
        required=True,
        type=Path,
        help="original input.jsonl, for the facts_user_instruction and ids",
    )
    ap.add_argument("--out-jsonl", required=True, type=Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--mode", default="bundle", choices=MODES)
    args = ap.parse_args()

    wanted = {w.strip() for w in args.only.split(",") if w.strip()}
    source_lines = [
        l
        for l in args.source_jsonl.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    written = 0
    with args.out_jsonl.open("w", encoding="utf-8") as handle:
        for index, line in enumerate(source_lines, 1):
            record = json.loads(line)
            # Stage 1 names its output after the DOCUMENT when the input carries
            # a case_id, and falls back to the positional name otherwise. Both
            # spellings are accepted here: assuming the positional one silently
            # skipped every document and produced an empty bundles file, which
            # reads exactly like a compression stage that found nothing.
            candidates = [
                f"{record[key]}"
                for key in ("case_id", "itemid")
                if str(record.get(key) or "").strip()
            ] + [f"input.L{index}"]
            doc_id = next(
                (
                    c
                    for c in candidates
                    if (args.compress_dir / f"{c}.compress.json").exists()
                ),
                candidates[0],
            )
            if wanted and doc_id not in wanted:
                continue
            path = args.compress_dir / f"{doc_id}.compress.json"
            if not path.exists():
                print(f"  {doc_id}: no compression output, skipped")
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))["payload"]
            rendered = render(payload, record.get("text", ""), args.mode)
            record["text"] = rendered
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            print(
                f"  {doc_id}: {len(rendered):6,} chars "
                f"({len(payload.get('events', [])):3} events) "
                f"<- was {len(json.loads(line).get('text', '')):6,}"
            )
    print(f"\nwrote {written} record(s) to {args.out_jsonl}")
    # A SHORT BUNDLE FILE SILENTLY MISALIGNS THE COMPARISON. Stage 2 names its
    # outputs from the input file stem plus LINE POSITION, so if compression is
    # missing for document 5 of ten, every later document shifts up a line and
    # bundles.L5 is no longer the same case as input.L5. Nothing downstream
    # would notice; the ablation would just quietly compare different judgments
    # against each other. Fail here instead.
    if written != len(source_lines):
        raise SystemExit(
            f"{len(source_lines) - written} of {len(source_lines)} document(s) have no "
            "compression output -- refusing to write a bundle file whose line "
            "numbering no longer matches the source"
        )


if __name__ == "__main__":
    main()
