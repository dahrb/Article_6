"""
check_annotations.py
--------------------
Validates a directory of annotation YAML against its source texts.

Three things go wrong in an annotation and none of them is visible by reading:
a quote that was retyped rather than copied (guide §5), a vocabulary value that
is not in the closed list (§6), and a `follows` pointing at an id that is not
there (§5.4). All three survive a careful proofread and none survives this.

    uv run python -m art6.data.check_annotations                     # gold
    uv run python -m art6.data.check_annotations --dir annotation/test \
                                                 --text-dir annotation/test_text

Exit status is 1 if anything failed, so it can gate a commit.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import yaml

# Closed vocabularies, guide §6 / ontology/echr.ttl. Kept as literals rather
# than parsed out of the .ttl so a mistake in the ontology cannot silently
# license a mistake in the annotation.
VOCAB = {
    "type": {
        "DomesticProceeding",
        "AdministrativeAction",
        "EnforcementAction",
        "ProsecutorialReview",
    },
    "level": {
        "Investigative",
        "AdministrativeReview",
        "FirstInstance",
        "Appeal",
        "Cassation",
        "CaseReview",
        "Reopening",
        "Unknown",
    },
    "proceeding_type": {
        "Criminal",
        "Civil",
        "Administrative",
        "Constitutional",
        "Disciplinary",
        "Other",
        "Unknown",
    },
    "outcome": {
        "MeritsDecided",
        "Inadmissible",
        "SettledOrWithdrawn",
        "Remitted",
        "Other",
        "Unknown",
    },
    "outcome_direction": {
        "FavourableToApplicant",
        "UnfavourableToApplicant",
        "Mixed",
        "Unknown",
    },
    "authority_kind": {
        "Judicial",
        "Prosecutorial",
        "Administrative",
        "Disciplinary",
        "Unknown",
    },
}
# `matter` (guide §5.1, §6.7) is closed but, unlike VOCAB above, NOT required
# on every event -- checked separately below.
MATTER = {
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
    "Other",
    "Unknown",
}
SIDES = {"Initiating", "Responding", "ThirdParty", "Unknown"}

DATE_FIELDS = ("start_date", "decision_date")


def normalise(text: str) -> str:
    """Collapse the differences a copy-paste legitimately loses.

    Whitespace only, plus NFC. Curly quotes, spacing inside brackets and the
    corpus's other typographic quirks are NOT normalised away: reproducing them
    is what makes a quote a copy rather than a retype, which is the whole point
    of the check.
    """
    return " ".join(unicodedata.normalize("NFC", text).split())


def check_date(value) -> str | None:
    """Dates are quoted PartialDate strings: YYYY-MM-DD | YYYY-MM | YYYY (§5.1)."""
    if not isinstance(value, str):
        return f"must be a QUOTED string, got {type(value).__name__} ({value!r})"
    parts = value.split("-")
    if not all(p.isdigit() for p in parts):
        return f"not a partial date: {value!r}"
    if [len(p) for p in parts] not in ([4], [4, 2], [4, 2, 2]):
        return f"not a partial date: {value!r}"
    return None


def check_case(path: Path, text_dir: Path) -> tuple[list[str], int, list]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems: list[str] = []

    if doc is None:
        return [f"{path.name}: empty file"], 0, []

    case_id = doc.get("case_id")
    if case_id != path.stem:
        problems.append(f"{path.name}: case_id {case_id!r} != filename {path.stem!r}")

    source = text_dir / f"{path.stem}.txt"
    haystack = (
        normalise(source.read_text(encoding="utf-8")) if source.exists() else None
    )
    if haystack is None:
        problems.append(f"{path.name}: no source text at {source}")

    events = doc.get("proceedings") or []
    ids = [e.get("id") for e in events]
    if ids != list(range(1, len(ids) + 1)):
        problems.append(f"{path.name}: ids are not 1..n in order: {ids}")
    known = set(ids)

    # Guide §3.2. An act carrying `part_of: n` is one act of the proceeding
    # headed by act n; an act without it heads its own. Heads are the only
    # legal targets of `follows` and `occurs_during`, which relate proceedings.
    by_id = {e.get("id"): e for e in events}
    heads = {e.get("id") for e in events if e.get("part_of") is None}

    for event in events:
        eid = event.get("id")
        where = f"{path.name} id {eid}"

        for field, allowed in VOCAB.items():
            value = event.get(field)
            if value is None:
                problems.append(f"{where}: {field} is missing")
            elif value not in allowed:
                problems.append(f"{where}: {field}={value!r} not in vocabulary")

        for field in DATE_FIELDS:
            if field in event and event[field] is not None:
                bad = check_date(event[field])
                if bad:
                    problems.append(f"{where}: {field} {bad}")
        if (
            event.get("start_date") is None
            and event.get("decision_date") is None
            and not event.get("non_occurring")
        ):
            problems.append(f"{where}: neither start_date nor decision_date (§3.1)")

        group = event.get("part_of")
        if group is not None:
            if group not in known:
                problems.append(f"{where}: part_of {group}, which is not an id")
            elif group == eid:
                problems.append(f"{where}: part_of points at itself")
            elif group not in heads:
                problems.append(
                    f"{where}: part_of {group} is itself an act of another "
                    f"proceeding -- groups are one level deep (§3.2)"
                )
            else:
                # The five fields describe the proceeding, so its acts must
                # agree on them. This is the check the two-level model buys.
                for field in (
                    "type",
                    "body",
                    "level",
                    "proceeding_type",
                    "authority_kind",
                ):
                    if event.get(field) != by_id[group].get(field):
                        problems.append(
                            f"{where}: {field}={event.get(field)!r} disagrees "
                            f"with id {group}, the same proceeding "
                            f"({by_id[group].get(field)!r})"
                        )
            if event.get("follows") is not None:
                problems.append(
                    f"{where}: has both `part_of` and `follows` -- acts "
                    f"within a proceeding are ordered by date (§3.2)"
                )

        follows = event.get("follows")
        for target in follows if isinstance(follows, list) else [follows]:
            if target is None:
                continue
            if target not in known:
                problems.append(f"{where}: follows {target}, which is not an id")
            elif target == eid:
                problems.append(f"{where}: follows itself")
            elif target not in heads:
                head = by_id[target].get("part_of")
                problems.append(
                    f"{where}: follows {target}, which is an act of the "
                    f"proceeding headed by id {head} -- `follows` relates "
                    f"proceedings, so point at that (§5.4)"
                )

        during = event.get("occurs_during")
        if during is not None:
            if during not in known:
                problems.append(f"{where}: occurs_during {during}, not an id")
            elif during == eid:
                problems.append(f"{where}: occurs_during itself")
            elif during not in heads:
                problems.append(
                    f"{where}: occurs_during {during}, which is an act of "
                    f"another proceeding -- point at its head (§5.5)"
                )
            elif event.get("part_of") is not None:
                problems.append(
                    f"{where}: an act cannot carry `occurs_during` -- it "
                    f"belongs to a proceeding already (§5.5)"
                )

        if "is_pending" in event:
            if not isinstance(event["is_pending"], bool):
                problems.append(f"{where}: is_pending must be true/false")
            elif event["is_pending"] and event.get("part_of") is not None:
                problems.append(
                    f"{where}: is_pending belongs on the proceeding, not one "
                    f"of its acts -- put it on id {event.get('part_of')}"
                )

        if "non_occurring" in event:
            if not isinstance(event["non_occurring"], bool):
                problems.append(f"{where}: non_occurring must be true/false")
            elif event["non_occurring"] and event.get("decision_date") is not None:
                problems.append(
                    f"{where}: non_occurring true but decision_date is set -- "
                    f"an event the text says never happened cannot have been "
                    f"decided (§5.1)"
                )

        if "matter" in event:
            matter = event["matter"]
            values = matter if isinstance(matter, list) else [matter]
            if not values:
                problems.append(
                    f"{where}: matter is an empty list -- omit the key instead"
                )
            for value in values:
                if value not in MATTER:
                    problems.append(f"{where}: matter={value!r} not in vocabulary")

        for party in event.get("parties") or []:
            if party.get("side") not in SIDES:
                problems.append(f"{where}: party side {party.get('side')!r} invalid")
            if not isinstance(party.get("natural_person"), bool):
                problems.append(f"{where}: party natural_person must be true/false")
            if not party.get("name"):
                problems.append(f"{where}: party has no name")

        if event.get("borderline") and not event.get("borderline_reason"):
            problems.append(f"{where}: borderline true without borderline_reason")

        quote = event.get("quote")
        if not quote:
            problems.append(f"{where}: no quote")
        elif haystack is not None and normalise(quote) not in haystack:
            problems.append(f"{where}: QUOTE NOT VERBATIM -- {quote[:70]}...")

    # A proceeding cannot follow itself round a loop. Nothing checked this
    # before; over acts it was a weak property, over proceedings it is the one
    # you want. Same walk covers `occurs_during`, which nests and so cannot
    # cycle either.
    for relation in ("follows", "occurs_during"):
        edges: dict[int, list[int]] = {}
        for event in events:
            if event.get("part_of") is not None:
                continue
            value = event.get(relation)
            targets = value if isinstance(value, list) else [value]
            edges[event["id"]] = [t for t in targets if t in heads]
        state: dict[int, int] = {}

        def walk(
            node: int,
            trail: list[int],
            edges: dict[int, list[int]] = edges,
            state: dict[int, int] = state,
            relation: str = relation,
        ) -> bool:
            state[node] = 1
            for nxt in edges.get(node, []):
                if state.get(nxt) == 1:
                    loop = trail[trail.index(nxt) :] + [nxt]
                    problems.append(
                        f"{path.name}: {relation} forms a cycle: "
                        + " -> ".join(str(n) for n in loop)
                    )
                    return True
                if state.get(nxt, 0) == 0 and walk(nxt, trail + [nxt]):
                    return True
            state[node] = 2
            return False

        for start in edges:
            if state.get(start, 0) == 0 and walk(start, [start]):
                break

    # `is_pending` says the text reports the proceeding as undecided. If one of
    # its acts carries a decision_date, one of the two is wrong.
    for event in events:
        if not event.get("is_pending") or event.get("part_of") is not None:
            continue
        decided = [
            e.get("id")
            for e in events
            if (e.get("id") == event["id"] or e.get("part_of") == event["id"])
            and e.get("decision_date")
        ]
        if decided:
            problems.append(
                f"{path.name} id {event['id']}: is_pending, but act(s) "
                f"{decided} carry a decision_date"
            )

    flags = [e.get("id") for e in events if e.get("final_domestic_decision")]
    return problems, len(events), flags


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", type=Path, default=Path("annotation/gold"))
    ap.add_argument("--text-dir", type=Path, default=None)
    args = ap.parse_args()

    text_dir = args.text_dir or Path(f"{args.dir}_text")

    failed = 0
    for path in sorted(args.dir.glob("*.yaml")):
        problems, n_events, flags = check_case(path, text_dir)
        status = "ok " if not problems else "FAIL"
        print(f"{status} {path.name:<18} {n_events:>3} events  fdd={flags}")
        for problem in problems:
            print(f"       {problem}")
        failed += bool(problems)

    print(f"\n{failed} file(s) with problems")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
