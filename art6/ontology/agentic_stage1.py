"""Stage 1, agentic: a fixed order of agents, routed by their own answers.

The single-call evidence stage (compress.py) asked one model call to find
every event, classify it on five axes AND decide which earlier event it
continues. The chain is where it failed: on gold30 the graphs carried half as
many chains as gold (69 vs 129) at near-correct event counts, so parallel
tracks were being merged. Here each agent has one job, everything a model
returns is checked in code before it is kept, and the CONDITIONAL agents run
only when an earlier agent's mandatory yes/no flags call for them.

    1 event detection   extract.txt: events, their parties, persons, verbatim
                        spans + classifications; no links. Flag
                        `domestic_events_found` -- with no events, nothing
                        after this runs.
    2 linking           plan.txt: tracks + each event's predecessor, chosen
                        from candidates computed here.
    3 person identifier applicants (compress.attach_applicants), then a
                        CHAIN-AWARE parties pass: each event is shown with the
                        event it continues and that event's parties. Additive.
      filters           judges, deciding body as party, actless outcomes.
    4 stage-1 critic    critic.txt, with deterministic hints. Mandatory flags:
                        needs_fix, parallel_tracks (+ verbatim evidence),
                        parties_incomplete (+ event ids). Each routes:
        4a parallel_tracks=yes    -> parallel track agent (tracks.txt):
                                     re-partition + track relations, then the
                                     linker again (link.txt) inside the new
                                     tracks
        4b parties_incomplete=yes -> parties pass on the named events only
        4c needs_fix=yes          -> fixer (fix.txt): validated operations on
                                     the payload
      filters again; another critic round only if --critic-rounds allows AND
      the round changed something.
    5 typed links       code, not a model: same body, rung, procedure and
                        track -> part_of; a during link (or a track `during`
                        relation) -> occurs_during; otherwise follows. Every
                        link targets the proceeding's earliest act.

A flag that is absent or not exactly yes/no gets ONE retry, then is derived
from the agent's findings; a yes that needs evidence and has none locatable is
read as no; a flag its own findings contradict follows the findings. Every
decision is logged with how it was reached (see resolve_critic_flags).

The output is a `.compress.json` in the shape compress.py writes, plus
`track`, `follows`, `part_of`, `occurs_during` and `link_span` on events and
`tracks` / `track_relations` lists, so render_bundles, check_stage_boundary
and every diagnostic read it unchanged. Each document also gets a
`.agentic.json` holding every agent's raw response, the flags and how each
was resolved, what was accepted, and what was refused and why.

Linking, the person identifier, the critic and everything it routes to are
improvements on an extraction that stands on its own, so an agent that
returns nothing usable is recorded and skipped rather than failing the
document. Extraction failing still fails it.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import threading
import time
from pathlib import Path

from openai import OpenAI

from art6.ontology import compress as c

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
EXTRACT_PROMPT = PROMPT_DIR / "extract.txt"

# Sections of extract.txt the critic and fixer are held to. Read live from the
# extraction prompt so the three passes can never drift onto different rules.
RULE_SECTIONS = (
    "CLASSIFY THE RUNG AND THE RESULT",
    "CLASSIFY THE TRACK AND THE DIRECTION",
    "CLASSIFY THE MATTER",
    "COVERAGE AND GRANULARITY",
)
FIX_EXTRA_SECTIONS = ("THE ONE RULE", "OUTPUT")

MAX_CANDIDATES = 15
MAX_FINDINGS = 40
MAX_HINTS = 30
PLAN_MAX_TOKENS = 8000
CRITIC_MAX_TOKENS = 8000
FIX_MAX_TOKENS = 16000

VOCABULARY = {
    "instance_level": {
        "first instance",
        "appeal",
        "cassation",
        "investigative",
        "administrative review",
        "case review",
        "reopening",
    },
    "outcome": {
        "merits decided",
        "inadmissible",
        "remitted",
        "settled or withdrawn",
        "other",
    },
    "proceeding_type": {
        "criminal",
        "civil",
        "administrative",
        "constitutional",
        "disciplinary",
        "other",
        "unknown",
    },
    "outcome_direction": {
        "favourable to applicant",
        "unfavourable to applicant",
        "mixed",
        "unknown",
    },
    "matter": {
        "arrest",
        "detention",
        "bail",
        "charge",
        "conviction",
        "investigation",
        "damages",
        "custody",
        "employment",
        "pension",
        "property",
        "extradition",
        "legal aid",
        "vetting",
        "disciplinary",
        "requests",
        "other",
        "unknown",
    },
}
LINK_KINDS = {"continues", "during"}
FINDING_KINDS = {
    "missing_event",
    "duplicate_event",
    "not_an_event",
    "wrong_field",
    "missing_link",
    "wrong_link",
    "wrong_track",
    "missing_party",
    "wrong_party",
}
# Findings about an event or party the draft already quotes may cite the
# draft's own words; every other kind must point at the judgment.
SELF_EVIDENCED_KINDS = {"duplicate_event", "not_an_event", "wrong_party"}
TRACK_KINDS = {
    "prosecution",
    "civil claim",
    "administrative",
    "detention",
    "enforcement",
    "constitutional",
    "compensation",
    "disciplinary",
    "other",
}
TRACK_RELATIONS = {"during", "arises_from", "independent"}
TRACKS_MAX_TOKENS = 8000
PARTIES_PROMPT = PROMPT_DIR / "parties.txt"
PLAN_PROMPT = PROMPT_DIR / "plan.txt"

# Keys the extraction schema allows on an event. Anything else an add_event
# carries is dropped rather than passed to stage 2 as an unknown field.
EVENT_KEYS = {
    "what_happened_span",
    "decision_date_span",
    "start_date_span",
    "institution_narrated_span",
    "authority_span",
    "authority_kind_span",
    "outcome",
    "outcome_span",
    "instance_level",
    "instance_span",
    "proceeding_type",
    "proceeding_type_span",
    "outcome_direction",
    "matter",
    "finality_span",
    "pending_span",
    "non_occurring_span",
    "interim_spans",
    "parties",
    "inactivity",
    "adjournments",
    "detention",
}
# Fields the extraction prompt used to carry and this stage now owns.
LEGACY_LINK_KEYS = ("follows", "follows_span")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def _norm_name(value) -> str:
    return c._normalise_name(_text(value))


def _events(payload: dict) -> list[dict]:
    return [e for e in payload.get("events") or [] if isinstance(e, dict)]


def _by_id(payload: dict) -> dict[str, dict]:
    return {str(e["id"]): e for e in _events(payload) if e.get("id")}


def _span_offsets(node) -> list[int]:
    """Start offsets of every verified span anywhere under `node`."""
    found: list[int] = []
    if isinstance(node, dict):
        if isinstance(node.get("start"), int) and "text" in node:
            found.append(node["start"])
        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(_span_offsets(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_span_offsets(value))
    return found


def _anchor(event: dict) -> int | None:
    """Where an event's own act sits in the document, or None.

    Only the act's own spans count. A generic span such as an authority_kind
    of "Court" is located at its FIRST occurrence in the judgment, which says
    nothing about where this event is narrated."""
    for key in ("what_happened_span", "institution_narrated_span"):
        value = event.get(key)
        if isinstance(value, dict) and isinstance(value.get("start"), int):
            return value["start"]
    return None


def load_sections(names: tuple[str, ...], path: Path = EXTRACT_PROMPT) -> str:
    """The named `## ` sections of a prompt file, in file order."""
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(?=## )", text)
    wanted = []
    for part in parts:
        heading = part.splitlines()[0].removeprefix("## ").strip() if part else ""
        if heading in names:
            wanted.append(part.rstrip())
    return "\n\n".join(wanted)


# ---------------------------------------------------------------------------
# Dates, as the judgment writes them
# ---------------------------------------------------------------------------

MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}
_MONTH_RE = "|".join(MONTHS)
_DAY_MONTH_YEAR = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_RE})\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_RE})\s+(\d{{4}})\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def parse_date(text: str) -> tuple[int, int, int] | None:
    """(year, month, day) with 0 for an unstated part, or None.

    Only the forms English ECHR judgments use. Anything else is left
    unparsed, which every caller treats as "order unknown", never as a guess.
    """
    if not text:
        return None
    if m := _DAY_MONTH_YEAR.search(text):
        return int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))
    if m := _MONTH_YEAR.search(text):
        return int(m.group(2)), MONTHS[m.group(1).lower()], 0
    if m := _YEAR.search(text):
        return int(m.group(1)), 0, 0
    return None


def event_date(event: dict) -> tuple[int, int, int] | None:
    return parse_date(_text(event.get("decision_date_span"))) or parse_date(
        _text(event.get("start_date_span"))
    )


def date_cmp(a, b) -> int | None:
    """-1 / 0 / 1 at the precision both dates state, or None if unknown."""
    if a is None or b is None:
        return None
    for x, y in zip(a, b):
        if x == 0 or y == 0:
            return 0
        if x != y:
            return -1 if x < y else 1
    return 0


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------

_JUDGE_TITLE = re.compile(r"(?<!of )\b(?:Judge|Justice|Magistrate)\s+[A-Z]")
_JUDGE_ROLE = re.compile(
    r"\b(judges?|magistrates?|registrars?|rapporteurs?|presiding|"
    r"president of the (?:chamber|section|court|bench|panel|tribunal))\b",
    re.IGNORECASE,
)
_NOT_JUDGE_ROLE = re.compile(
    r"\b(applicants?|lawyers?|counsel|representatives?|advocates?|witness(?:es)?|"
    r"co-accused|guardians?|relatives?|wife|husband|son|daughter|mother|father)\b",
    re.IGNORECASE,
)


def is_judge(name: str, role: str = "") -> bool:
    if _JUDGE_TITLE.search(name or ""):
        return True
    if _NOT_JUDGE_ROLE.search(name or "") or _NOT_JUDGE_ROLE.search(role or ""):
        return False
    return bool(role and _JUDGE_ROLE.search(role))


def drop_judges(payload: dict) -> list[str]:
    """Remove judges from persons and from every event's parties."""
    dropped: list[str] = []
    kept_persons = []
    for person in payload.get("persons") or []:
        name, role = _text(person.get("name_span")), _text(person.get("role_span"))
        if is_judge(name, role):
            dropped.append(name)
            continue
        kept_persons.append(person)
    if "persons" in payload:
        payload["persons"] = kept_persons
    for event in _events(payload):
        parties = []
        for party in event.get("parties") or []:
            name = _text(party.get("name_span"))
            # A party's role_span describes what they did ("complained about
            # the judge's bias"), not who they are, so only the name decides.
            if is_judge(name):
                dropped.append(name)
                continue
            parties.append(party)
        if "parties" in event:
            event["parties"] = parties
    return dropped


# ---------------------------------------------------------------------------
# Candidates, tracks and links
# ---------------------------------------------------------------------------


def candidate_predecessors(events: list[dict], k: int = MAX_CANDIDATES) -> dict:
    """For each event, the events that could have come before it.

    Eligible: dated no later than it (at the precision both state) and either
    earlier in the document or strictly earlier in time -- a judgment that
    narrates one track to its end and then goes back for the next still puts
    each event after its predecessor in time. With no usable date, document
    order alone decides. Ranked same-procedure first, then by nearness, and
    capped so a seventy-event judgment does not hand the planner seventy
    choices per event.
    """
    dates = [event_date(e) for e in events]
    out: dict[str, list[str]] = {}
    for i, event in enumerate(events):
        pool = []
        for j, other in enumerate(events):
            if j == i:
                continue
            cmp = date_cmp(dates[j], dates[i])
            if cmp is None:
                eligible = j < i
            else:
                eligible = cmp < 0 or (cmp == 0 and j < i)
            if not eligible:
                continue
            same_type = (other.get("proceeding_type") or "") == (
                event.get("proceeding_type") or ""
            )
            pool.append(((0 if same_type else 1, abs(i - j)), str(other["id"])))
        pool.sort()
        out[str(event["id"])] = [eid for _, eid in pool[:k]]
    return out


def _would_cycle(links: dict[str, str], event_id: str, predecessor: str) -> bool:
    seen = {event_id}
    node = predecessor
    while node is not None:
        if node in seen:
            return True
        seen.add(node)
        node = links.get(node)
    return False


def apply_plan(
    payload: dict,
    plan: dict | None,
    candidates: dict,
    source: str,
    *,
    keep_tracks: bool = False,
) -> dict:
    """Write the planner's tracks and links onto the events, refusing what the
    rules forbid. Returns an audit of accepted and refused items.

    keep_tracks: the tracks are already settled (by the parallel track agent);
    only the links are taken from `plan`, and every existing link is replaced.
    """
    audit = {"links_accepted": 0, "links_refused": [], "tracks": 0, "unassigned": []}
    events = _by_id(payload)
    index = c.SpanIndex(source)
    plan = plan if isinstance(plan, dict) else {}
    if keep_tracks:
        track_of = {eid: e.get("track", "") for eid, e in events.items()}
        audit["tracks"] = len(payload.get("tracks") or [])
        for event in events.values():
            event.pop("link", None)
        return _apply_links(plan, events, candidates, track_of, index, audit)

    tracks: list[dict] = []
    track_of: dict[str, str] = {}
    for n, track in enumerate(plan.get("tracks") or [], 1):
        if not isinstance(track, dict):
            continue
        tid = f"T{n}"
        members = [str(e) for e in track.get("events") or [] if str(e) in events]
        members = [e for e in members if e not in track_of]
        if not members:
            continue
        tracks.append({"id": tid, "label": str(track.get("label") or "").strip()})
        for e in members:
            track_of[e] = tid
    for eid in events:
        if eid not in track_of:
            tid = f"T{len(tracks) + 1}"
            tracks.append({"id": tid, "label": ""})
            track_of[eid] = tid
            audit["unassigned"].append(eid)
    payload["tracks"] = tracks
    audit["tracks"] = len(tracks)
    for eid, event in events.items():
        event["track"] = track_of[eid]
        event.pop("link", None)
    return _apply_links(plan, events, candidates, track_of, index, audit)


def _apply_links(plan, events, candidates, track_of, index, audit) -> dict:
    chosen: dict[str, str] = {}
    for link in plan.get("links") or []:
        if not isinstance(link, dict):
            continue
        eid, pred = str(link.get("event") or ""), str(link.get("predecessor") or "")
        kind = str(link.get("kind") or "continues").strip().lower()
        reason = None
        if eid not in events or pred not in events:
            reason = "unknown event id"
        elif eid in chosen:
            reason = "second link for one event"
        elif pred not in candidates.get(eid, []):
            reason = "predecessor is not one of the event's candidates"
        elif kind not in LINK_KINDS:
            reason = f"unknown kind {kind!r}"
        elif kind == "continues" and track_of[eid] != track_of[pred]:
            reason = "continues across tracks"
        elif _would_cycle(chosen, eid, pred):
            reason = "would create a cycle"
        if reason:
            audit["links_refused"].append({**link, "reason": reason})
            continue
        chosen[eid] = pred
        record = {"to": pred, "kind": kind}
        hit = index.locate(str(link.get("evidence_span") or ""))
        if hit:
            record["span"] = c._located(index, hit)
        events[eid]["link"] = record
        audit["links_accepted"] += 1
    return audit


def same_proceeding(a: dict, b: dict) -> bool:
    """Two acts of ONE proceeding: same track, rung and procedure, and the
    same deciding body. A filing often names no body, so a body missing on one
    side does not separate them; two different bodies always do."""
    if a.get("track") != b.get("track"):
        return False
    for key in ("instance_level", "proceeding_type"):
        if (a.get(key) or "") != (b.get(key) or ""):
            return False
    na, nb = _norm_name(a.get("authority_span")), _norm_name(b.get("authority_span"))
    return not na or not nb or na == nb or na in nb or nb in na


def break_cycles(payload: dict) -> int:
    """Drop the link that closes each cycle. Planning and fixing both refuse
    cycles, but a merge relinks an event's successors to the survivor, which
    can close one. Returns how many links were dropped."""
    by_id = _by_id(payload)
    broken = 0
    for event in _events(payload):
        seen = []
        node = event
        while node is not None and node.get("link"):
            if node["id"] in seen:
                node.pop("link")
                broken += 1
                break
            seen.append(node["id"])
            node = by_id.get(node["link"]["to"])
    return broken


def finalize(payload: dict) -> dict:
    """Renumber events e1..eN and turn each planned link into the ontology
    relation it is.

    echr.ttl: partOfProceeding targets the earliest act of the proceeding and
    is one level deep; followsProceeding and occursDuring relate proceedings,
    so both ends are proceedings' earliest acts. Hence every target is the
    predecessor's ROOT, computed in document order so a root is always
    settled before anything points at it.
    """
    # The list is already in document order: extraction writes it that way
    # and apply_fixes inserts added events at their own position.
    events = _events(payload)
    remap = {str(e["id"]): f"e{n}" for n, e in enumerate(events, 1)}
    for n, event in enumerate(events, 1):
        event["id"] = f"e{n}"
        event["order"] = n
        link = event.get("link")
        if link and link.get("to") in remap:
            link["to"] = remap[link["to"]]
        elif link:
            event.pop("link")
    payload["events"] = events

    broken = break_cycles(payload)
    by_id = {e["id"]: e for e in events}
    links = {e["id"]: e.pop("link") for e in events if e.get("link")}
    for event in events:
        for key in ("follows", "part_of", "occurs_during", "link_span"):
            event.pop(key, None)

    # A predecessor may sit LATER in the document than its successor (a
    # judgment that narrates one track and then goes back for another), so
    # roots are resolved recursively rather than in one forward sweep.
    root: dict[str, str] = {}

    def resolve(eid: str) -> str:
        if eid in root:
            return root[eid]
        root[eid] = eid
        link = links.get(eid)
        if link:
            pred = by_id[link["to"]]
            target = resolve(pred["id"])
            if link["kind"] == "during":
                relation = "occurs_during"
            elif same_proceeding(by_id[eid], pred):
                relation = "part_of"
                root[eid] = target
            else:
                relation = "follows"
            by_id[eid][relation] = target
            if link.get("span"):
                by_id[eid]["link_span"] = link["span"]
        return root[eid]

    for event in events:
        resolve(event["id"])
    counts = {
        rel: sum(1 for e in events if e.get(rel))
        for rel in ("follows", "part_of", "occurs_during")
    }
    counts["cycles_broken"] = broken

    used = {e.get("track") for e in events}
    tracks = [t for t in payload.get("tracks") or [] if t["id"] in used]
    track_remap = {t["id"]: f"T{n}" for n, t in enumerate(tracks, 1)}
    for t in tracks:
        t["id"] = track_remap[t["id"]]
    for event in events:
        if event.get("track") in track_remap:
            event["track"] = track_remap[event["track"]]
    payload["tracks"] = tracks
    if "track_relations" in payload:
        payload["track_relations"] = [
            {**r, "track": track_remap[r["track"]], "other": track_remap[r["other"]]}
            for r in payload["track_relations"]
            if r["track"] in track_remap and r["other"] in track_remap
        ]
    return {"id_map": remap, "relations": counts}


# ---------------------------------------------------------------------------
# Deterministic hints for the critic
# ---------------------------------------------------------------------------

_FACTS_START = re.compile(
    r"(?m)^[#\s]*(?:THE FACTS|AS TO THE FACTS|THE CIRCUMSTANCES OF THE CASE)\b"
)
_FACTS_END = re.compile(
    r"(?m)^[#\s]*(?:[IVX]+\.\s*)?(?:RELEVANT (?:DOMESTIC|LEGAL|INTERNATIONAL)|"
    r"THE LAW\b|COMPLAINTS?\b|PROCEEDINGS BEFORE THE COMMISSION)"
)
_ANY_DATE = re.compile(
    rf"\b(?:\d{{1,2}}\s+)?(?:{_MONTH_RE})\s+\d{{4}}\b", re.IGNORECASE
)


def paragraphs(text: str) -> list[tuple[int, int, str]]:
    """(start, end, text) for every non-blank line, which is how HUDOC text
    separates paragraphs."""
    out = []
    for m in re.finditer(r"[^\n]+", text):
        if m.group().strip():
            out.append((m.start(), m.end(), m.group()))
    return out


def facts_region(text: str) -> tuple[int, int]:
    start = _FACTS_START.search(text)
    lo = start.start() if start else 0
    end = _FACTS_END.search(text, lo + 1)
    return lo, end.start() if end else len(text)


def find_hints(payload: dict, source: str) -> list[dict]:
    hints: list[dict] = []
    events = _events(payload)

    lo, hi = facts_region(source)
    covered = sorted(o for e in events for o in _span_offsets(e))
    for n, (start, end, para) in enumerate(paragraphs(source), 1):
        if end <= lo or start >= hi or len(para.strip()) < 40:
            continue
        if not _ANY_DATE.search(para):
            continue
        if any(start <= o < end for o in covered):
            continue
        hints.append(
            {
                "kind": "uncovered_paragraph",
                "paragraph": n,
                "text": para.strip()[:240],
                "note": "a dated paragraph in the facts that no event quotes",
            }
        )

    groups: dict[tuple, list[str]] = {}
    for e in events:
        d = event_date(e)
        body = _norm_name(e.get("authority_span"))
        if d and d[2] and body:
            groups.setdefault((d, body), []).append(e["id"])
    for (d, body), ids in groups.items():
        if len(ids) > 1:
            hints.append(
                {
                    "kind": "same_day_same_body",
                    "events": ids,
                    "note": f"{len(ids)} events by '{body}' on one day -- distinct "
                    "acts, or one act recorded twice?",
                }
            )

    by_id = _by_id(payload)
    for e in events:
        link = e.get("link")
        if not link or link["to"] not in by_id:
            continue
        if date_cmp(event_date(by_id[link["to"]]), event_date(e)) == 1:
            hints.append(
                {
                    "kind": "link_runs_backwards",
                    "events": [e["id"], link["to"]],
                    "note": "the predecessor is dated after the event it precedes",
                }
            )

    # A merged parallel track's commonest signature: one track holding events
    # of different procedures. A hint, never a trigger -- the critic decides.
    by_track: dict[str, set[str]] = {}
    for e in events:
        if e.get("track") and e.get("proceeding_type") not in (None, "", "unknown"):
            by_track.setdefault(e["track"], set()).add(e["proceeding_type"])
    for track, types in by_track.items():
        if len(types) > 1:
            hints.append(
                {
                    "kind": "mixed_procedure_in_track",
                    "track": track,
                    "note": f"track {track} mixes {', '.join(sorted(types))} events -- "
                    "one matter changing procedure, or parallel tracks merged?",
                }
            )

    firsts: dict[str, str] = {}
    for e in events:
        firsts.setdefault(e.get("track", ""), e["id"])
    for e in events:
        if e.get("link") or firsts.get(e.get("track", "")) == e["id"]:
            continue
        hints.append(
            {
                "kind": "unlinked_in_track",
                "events": [e["id"]],
                "note": "not the first event of its track, yet continues nothing",
            }
        )
    return hints[:MAX_HINTS]


# ---------------------------------------------------------------------------
# Rendering the draft for the model
# ---------------------------------------------------------------------------

_DRAFT_SPANS = (
    "what_happened_span",
    "decision_date_span",
    "start_date_span",
    "institution_narrated_span",
    "authority_span",
    "authority_kind_span",
    "outcome_span",
    "instance_span",
    "proceeding_type_span",
    "finality_span",
    "pending_span",
    "non_occurring_span",
)
_DRAFT_CLASSES = (
    "instance_level",
    "outcome",
    "proceeding_type",
    "outcome_direction",
)


def render_event(event: dict, candidates: list[str] | None = None) -> str:
    lines = [
        f"EVENT {event['id']}"
        + (f"  [track {event['track']}]" if event.get("track") else "")
    ]
    for key in _DRAFT_SPANS:
        if value := _text(event.get(key)):
            lines.append(f'  {key.removesuffix("_span")}: "{value}"')
    for key in _DRAFT_CLASSES:
        if event.get(key):
            lines.append(f"  {key}: {event[key]}")
    if event.get("matter"):
        lines.append(f"  matter: {', '.join(map(str, event['matter']))}")
    for party in event.get("parties") or []:
        name, role = _text(party.get("name_span")), _text(party.get("role_span"))
        if name:
            lines.append(f'  party: "{name}"' + (f' -- "{role}"' if role else ""))
    link = event.get("link")
    if link:
        words = f' -- "{_text(link.get("span"))}"' if link.get("span") else ""
        lines.append(f"  link: {link['kind']} {link['to']}{words}")
    if candidates is not None:
        lines.append(
            f"  candidates: {', '.join(candidates) if candidates else '(none)'}"
        )
    return "\n".join(lines)


def render_draft(payload: dict, candidates: dict | None = None) -> str:
    out = []
    tracks = payload.get("tracks") or []
    if tracks:
        out.append("TRACKS")
        for t in tracks:
            extra = ", ".join(f"{k}: {t[k]}" for k in ("kind", "subject") if t.get(k))
            out.append(
                f"  {t['id']}: {t.get('label') or '(no label)'}"
                + (f" [{extra}]" if extra else "")
            )
        for r in payload.get("track_relations") or []:
            out.append(f"  {r['track']} {r['relation']} {r['other']}")
        out.append("")
    out.append(f"EVENTS ({len(_events(payload))}), in document order")
    for event in _events(payload):
        cands = None if candidates is None else candidates.get(event["id"], [])
        out.append(render_event(event, cands))
    return "\n".join(out)


def document_view(
    source: str,
    payload: dict,
    hints: list[dict],
    *,
    budget_tokens: int,
    count_tokens,
) -> tuple[str, bool]:
    """The judgment if it fits the budget, otherwise only the paragraphs an
    event quotes or a hint names, each numbered. Spans copied from an excerpt
    are still verbatim from the source, so verification is unaffected."""
    if count_tokens(source) <= budget_tokens:
        return source, False
    offsets = {o for e in _events(payload) for o in _span_offsets(e)}
    hinted = {h.get("paragraph") for h in hints if h.get("paragraph")}
    keep = []
    for n, (start, end, para) in enumerate(paragraphs(source), 1):
        if n in hinted or any(start <= o < end for o in offsets):
            keep.append(f"[paragraph {n}] {para}")
    return "\n".join(keep), True


# ---------------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------------


def ask_json(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    label: str,
    temperature: float,
    max_tokens: int,
    retries: int = 1,
) -> tuple[dict | None, str]:
    from art6.ontology.repair_facts import model_call_kwargs

    raw = ""
    for _ in range(retries + 1):
        kwargs = model_call_kwargs(
            model, temperature=temperature, max_tokens=max_tokens, seed=c.MODEL_SEED
        )
        with c._reserve(
            prompt, max_tokens, label, str(client.base_url), model, client.api_key or ""
        ):
            response = c.call_with_recovery(
                client,
                label=label,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        raw = response.choices[0].message.content or ""
        payload = c.parse_json(raw)
        if isinstance(payload, dict):
            return payload, raw
    return None, raw


def _document_block(view: str, excerpt: bool) -> str:
    note = (
        "\n(EXCERPT: the judgment is too long to show whole. Only the paragraphs an "
        "event quotes or a hint names are shown.)"
        if excerpt
        else ""
    )
    return f"\n\nJUDGMENT{note}\n<<<DOC\n{view}\nDOC\n"


def plan_prompt(payload: dict, candidates: dict, view: str, excerpt: bool) -> str:
    return (
        (PROMPT_DIR / "plan.txt").read_text(encoding="utf-8")
        + _document_block(view, excerpt)
        + "\n\n"
        + render_draft(payload, candidates)
        + "\n"
    )


def critic_prompt(payload: dict, hints: list[dict], view: str, excerpt: bool) -> str:
    return (
        (PROMPT_DIR / "critic.txt").read_text(encoding="utf-8")
        + "\n\nEXTRACTION RULES\n\n"
        + load_sections(RULE_SECTIONS)
        + _document_block(view, excerpt)
        + "\n\nDRAFT\n"
        + render_draft(payload)
        + "\n\nCHECK THESE\nComputed from the draft, not judged. Each is a place a defect "
        "is likely, not a defect: confirm it against the judgment or ignore it.\n"
        + (json.dumps(hints, indent=1, ensure_ascii=False) if hints else "(none)")
        + "\n"
    )


def fix_prompt(payload: dict, findings: list[dict], view: str, excerpt: bool) -> str:
    return (
        (PROMPT_DIR / "fix.txt").read_text(encoding="utf-8")
        + "\n\nEXTRACTION RULES\n\n"
        + load_sections(FIX_EXTRA_SECTIONS + RULE_SECTIONS)
        + _document_block(view, excerpt)
        + "\n\nDRAFT\n"
        + render_draft(payload)
        + "\n\nFINDINGS\n"
        + json.dumps(findings, indent=1, ensure_ascii=False)
        + "\n"
    )


# ---------------------------------------------------------------------------
# Critic findings and fix operations
# ---------------------------------------------------------------------------


def validate_findings(
    raw: dict | None, payload: dict, source: str
) -> tuple[list, list]:
    """Keep findings that name real events and carry locatable evidence."""
    index = c.SpanIndex(source)
    events = _by_id(payload)
    kept, refused = [], []
    for n, item in enumerate((raw or {}).get("findings") or [], 1):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        ids = [str(e) for e in item.get("events") or []]
        evidence = str(item.get("evidence_span") or "")
        reason = None
        if kind not in FINDING_KINDS:
            reason = f"unknown kind {kind!r}"
        elif kind != "missing_event" and not ids:
            reason = "names no event"
        elif any(e not in events for e in ids):
            reason = "names an event the draft does not have"
        elif not index.locate(evidence):
            own = " ".join(
                " ".join(
                    [_text(events[e].get("what_happened_span"))]
                    + [
                        _text(p.get(k))
                        for p in events[e].get("parties") or []
                        for k in ("name_span", "role_span")
                    ]
                )
                for e in ids
            )
            if not (kind in SELF_EVIDENCED_KINDS and evidence and evidence in own):
                reason = "evidence_span not found in the judgment"
        if reason:
            refused.append({**item, "reason": reason})
            continue
        kept.append(
            {
                "id": f"f{len(kept) + 1}",
                "kind": kind,
                "events": ids,
                **({"field": item["field"]} if item.get("field") else {}),
                "evidence_span": evidence,
                "issue": str(item.get("issue") or "")[:400],
            }
        )
        if len(kept) >= MAX_FINDINGS:
            break
    return kept, refused


def _clean_classification(field: str, value):
    """A vocabulary value, a matter list, or None if invalid."""
    if field == "matter":
        values = value if isinstance(value, list) else [value]
        values = [str(v).strip().lower() for v in values if v]
        good = [v for v in values if v in VOCABULARY["matter"]]
        return good or None
    value = str(value or "").strip().lower()
    return value if value in VOCABULARY[field] else None


def _clean_event(event: dict, source: str) -> tuple[dict | None, dict]:
    """Restrict to schema keys, verify spans, check vocabularies."""
    event = {k: v for k, v in event.items() if k in EVENT_KEYS}
    verified, stats = c.verify(event, source)
    for field in VOCABULARY:
        if field in verified:
            cleaned = _clean_classification(field, verified[field])
            if cleaned is None:
                verified.pop(field)
            else:
                verified[field] = cleaned
    if not verified.get("what_happened_span"):
        return None, stats
    return verified, stats


def apply_fixes(
    payload: dict, ops_raw: dict | None, findings: list[dict], source: str
) -> list[dict]:
    """Apply the fixer's operations, each checked here. Returns the audit."""
    audit: list[dict] = []
    finding_ids = {f["id"] for f in findings}
    index = c.SpanIndex(source)
    tracks = payload.setdefault("tracks", [])
    new_n = 0

    def log(op, status):
        audit.append(
            {
                "op": op.get("op"),
                "finding": op.get("finding"),
                "status": status,
                **{
                    k: v
                    for k, v in op.items()
                    if k in ("id", "keep", "drop", "field", "predecessor", "track")
                },
            }
        )

    def new_track(label: str) -> str:
        tid = f"T{len(tracks) + 1}"
        while any(t["id"] == tid for t in tracks):
            tid = f"T{int(tid[1:]) + 1}"
        tracks.append({"id": tid, "label": str(label or "").strip()})
        return tid

    def resolve_track(value, label) -> str | None:
        value = str(value or "").strip()
        if value.lower() == "new":
            return new_track(label)
        return value if any(t["id"] == value for t in tracks) else None

    def set_link(eid: str, pred, kind, evidence) -> str:
        events = _by_id(payload)
        event = events[eid]
        if pred in (None, "", "null"):
            event.pop("link", None)
            return "applied: link removed"
        pred = str(pred)
        kind = str(kind or "continues").strip().lower()
        if pred not in events or pred == eid:
            return "refused: unknown predecessor"
        if kind not in LINK_KINDS:
            return f"refused: unknown kind {kind!r}"
        if date_cmp(event_date(events[pred]), event_date(event)) == 1:
            return "refused: predecessor dated after the event"
        links = {
            e["id"]: e["link"]["to"]
            for e in _events(payload)
            if e.get("link") and e["id"] != eid
        }
        if _would_cycle(links, eid, pred):
            return "refused: would create a cycle"
        record = {"to": pred, "kind": kind}
        if hit := index.locate(str(evidence or "")):
            record["span"] = c._located(index, hit)
        event["link"] = record
        # A continuation belongs to its predecessor's track by definition;
        # follow the fixer's link rather than keep a contradiction.
        if kind == "continues" and event.get("track") != events[pred].get("track"):
            event["track"] = events[pred].get("track")
            return "applied: link set, event moved to its predecessor's track"
        return "applied"

    def drop_event(eid: str, relink_to: str | None = None) -> None:
        payload["events"] = [e for e in _events(payload) if e["id"] != eid]
        for e in _events(payload):
            link = e.get("link")
            if link and link["to"] == eid:
                if relink_to and relink_to != e["id"]:
                    link["to"] = relink_to
                else:
                    e.pop("link")

    for op in (ops_raw or {}).get("ops") or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op") or "")
        if str(op.get("finding") or "") not in finding_ids:
            log(op, "refused: answers no finding")
            continue
        events = _by_id(payload)
        try:
            if kind == "add_event":
                event, _ = _clean_event(op.get("event") or {}, source)
                if event is None:
                    log(op, "refused: what_happened_span not verbatim")
                    continue
                anchor = event["what_happened_span"]["start"]
                if any(_anchor(e) == anchor for e in _events(payload)):
                    log(op, "refused: an event already quotes these words")
                    continue
                new_n += 1
                event["id"] = f"n{new_n}"
                event["added_by"] = op.get("finding")
                track = resolve_track(op.get("track"), op.get("track_label"))
                event["track"] = track or new_track(op.get("track_label"))
                # Insert where the judgment narrates it, before the first
                # event whose own act is narrated later.
                position = next(
                    (
                        n
                        for n, e in enumerate(payload["events"])
                        if (_anchor(e) is not None and _anchor(e) > anchor)
                    ),
                    len(payload["events"]),
                )
                payload["events"].insert(position, event)
                status = "applied"
                if op.get("predecessor"):
                    status = set_link(
                        event["id"],
                        op.get("predecessor"),
                        op.get("kind"),
                        op.get("evidence_span"),
                    )
                    status = "applied: event added" + (
                        "" if status.startswith("applied") else f", link {status}"
                    )
                log({**op, "id": event["id"]}, status)
            elif kind == "remove_event":
                eid = str(op.get("id") or "")
                if eid not in events:
                    log(op, "refused: unknown event")
                    continue
                drop_event(eid)
                log(op, "applied")
            elif kind == "merge_events":
                keep, drop = str(op.get("keep") or ""), str(op.get("drop") or "")
                if keep not in events or drop not in events or keep == drop:
                    log(op, "refused: unknown or identical events")
                    continue
                survivor, dup = events[keep], events[drop]
                for key, value in dup.items():
                    if key in EVENT_KEYS and key != "parties" and key not in survivor:
                        survivor[key] = value
                names = {
                    _norm_name(p.get("name_span"))
                    for p in survivor.get("parties") or []
                }
                for party in dup.get("parties") or []:
                    if _norm_name(party.get("name_span")) not in names:
                        survivor.setdefault("parties", []).append(party)
                if (
                    not survivor.get("link")
                    and dup.get("link")
                    and dup["link"]["to"] != keep
                ):
                    survivor["link"] = dup["link"]
                drop_event(drop, relink_to=keep)
                log(op, "applied")
            elif kind == "set_field":
                eid, field = str(op.get("id") or ""), str(op.get("field") or "")
                if eid not in events:
                    log(op, "refused: unknown event")
                    continue
                value = op.get("value")
                if field in VOCABULARY:
                    if value in (None, "", []):
                        events[eid].pop(field, None)
                        log(op, "applied: removed")
                        continue
                    cleaned = _clean_classification(field, value)
                    if cleaned is None:
                        log(op, f"refused: {value!r} is not in the {field} vocabulary")
                        continue
                    events[eid][field] = cleaned
                    log(op, "applied")
                elif field.endswith("_span") and field in EVENT_KEYS:
                    if value in (None, ""):
                        if field == "what_happened_span":
                            log(op, "refused: an event keeps its what_happened_span")
                            continue
                        events[eid].pop(field, None)
                        log(op, "applied: removed")
                        continue
                    hit = index.locate(str(value))
                    if not hit:
                        log(op, "refused: span not verbatim")
                        continue
                    events[eid][field] = c._located(index, hit)
                    log(op, "applied")
                else:
                    log(op, f"refused: {field!r} is not an editable field")
            elif kind == "set_link":
                eid = str(op.get("id") or "")
                if eid not in events:
                    log(op, "refused: unknown event")
                    continue
                log(
                    op,
                    set_link(
                        eid,
                        op.get("predecessor"),
                        op.get("kind"),
                        op.get("evidence_span"),
                    ),
                )
            elif kind == "set_track":
                eid = str(op.get("id") or "")
                track = resolve_track(op.get("track"), op.get("track_label"))
                if eid not in events or track is None:
                    log(op, "refused: unknown event or track")
                    continue
                events[eid]["track"] = track
                # A link that now crosses tracks no longer holds.
                link = events[eid].get("link")
                if (
                    link
                    and link["kind"] == "continues"
                    and events[link["to"]].get("track") != track
                ):
                    events[eid].pop("link")
                    log(op, "applied: track set, cross-track link removed")
                    continue
                log(op, "applied")
            elif kind == "add_party":
                eid = str(op.get("id") or "")
                if eid not in events:
                    log(op, "refused: unknown event")
                    continue
                hit = index.locate(str(op.get("name_span") or ""))
                if not hit:
                    log(op, "refused: name_span not verbatim")
                    continue
                party = {"name_span": c._located(index, hit)}
                if role := index.locate(str(op.get("role_span") or "")):
                    party["role_span"] = c._located(index, role)
                name = _norm_name(party["name_span"])
                existing = events[eid].setdefault("parties", [])
                if any(_norm_name(p.get("name_span")) == name for p in existing):
                    log(op, "refused: already a party")
                    continue
                # The filters run after the fix and remove a deciding body or a
                # judge anyway; refusing here keeps the audit honest about why.
                body = _norm_name(events[eid].get("authority_span"))
                if body and (name == body or name in body or body in name):
                    log(op, "refused: the deciding body is not a party")
                    continue
                if is_judge(_text(party["name_span"])):
                    log(op, "refused: a judge is not a party")
                    continue
                existing.append(party)
                log(op, "applied")
            elif kind == "remove_party":
                eid = str(op.get("id") or "")
                if eid not in events:
                    log(op, "refused: unknown event")
                    continue
                name = c._normalise_name(str(op.get("name") or ""))
                before = events[eid].get("parties") or []
                kept = [p for p in before if _norm_name(p.get("name_span")) != name]
                if len(kept) == len(before):
                    log(op, "refused: no such party on that event")
                    continue
                events[eid]["parties"] = kept
                log(op, "applied")
            else:
                log(op, f"refused: unknown op {kind!r}")
        except Exception as exc:  # noqa: BLE001 -- one bad op must not cost the rest
            log(op, f"refused: {type(exc).__name__}: {exc}")
    return audit


# ---------------------------------------------------------------------------
# One document
# ---------------------------------------------------------------------------


def strip_legacy_links(payload: dict) -> int:
    n = 0
    for event in _events(payload):
        for key in LEGACY_LINK_KEYS:
            if key in event:
                event.pop(key)
                n += 1
    return n


# ---------------------------------------------------------------------------
# Mandatory flags: how one agent's answer decides which agent runs next
# ---------------------------------------------------------------------------


def read_flag(raw: dict | None, name: str) -> str | None:
    """'yes', 'no', or None when absent or anything else."""
    value = str((raw or {}).get(name) or "").strip().lower()
    return value if value in {"yes", "no"} else None


def ask_with_flags(
    client: OpenAI,
    model: str,
    prompt: str,
    *,
    flags: tuple[str, ...],
    label: str,
    temperature: float,
    max_tokens: int,
) -> tuple[dict | None, str, list[str]]:
    """ask_json, then ONE retry if any mandatory flag is missing or not exactly
    yes/no. Returns (answer, raw, flags still missing after the retry)."""
    answer, raw = ask_json(
        client,
        model,
        prompt,
        label=label,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    missing = [f for f in flags if read_flag(answer, f) is None]
    if not missing:
        return answer, raw, []
    reminder = (
        '\n\nYOUR PREVIOUS ANSWER omitted, or did not set to exactly "yes" or "no", '
        f"these MANDATORY fields: {', '.join(missing)}. Answer again: the complete JSON "
        "object, with every mandatory field set.\n"
    )
    retry, retry_raw = ask_json(
        client,
        model,
        prompt + reminder,
        label=f"{label} (flag retry)",
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if isinstance(retry, dict):
        answer, raw = retry, retry_raw
    return answer, raw, [f for f in flags if read_flag(answer, f) is None]


def resolve_critic_flags(
    raw: dict | None,
    findings: list[dict],
    payload: dict,
    source: str,
    missing: list[str],
) -> tuple[dict, list[dict]]:
    """Turn the critic's flags into routing decisions, and say how each was
    reached: `trusted`, `defaulted` (absent after the retry, so derived from
    the findings), `inconsistent` (contradicted by the findings, which win),
    or `unevidenced` (a yes whose evidence is not in the judgment)."""
    notes: list[dict] = []
    kinds = {f["kind"] for f in findings}
    events = _by_id(payload)

    def note(flag, how, value):
        notes.append({"flag": flag, "how": how, "value": value})

    # needs_fix
    flag = read_flag(raw, "needs_fix")
    derived = "yes" if findings else "no"
    if "needs_fix" in missing:
        note("needs_fix", "defaulted", derived)
    elif flag != derived:
        note("needs_fix", "inconsistent", derived)
    else:
        note("needs_fix", "trusted", derived)
    needs_fix = derived == "yes"

    # parallel_tracks
    flag = read_flag(raw, "parallel_tracks")
    if "parallel_tracks" in missing:
        flag = "yes" if "wrong_track" in kinds else "no"
        note("parallel_tracks", "defaulted", flag)
        evidence = next(
            (f["evidence_span"] for f in findings if f["kind"] == "wrong_track"), ""
        )
    else:
        evidence = str((raw or {}).get("parallel_tracks_evidence") or "")
        if flag == "yes" and not c.SpanIndex(source).locate(evidence):
            flag = "no"
            note("parallel_tracks", "unevidenced", flag)
        else:
            note("parallel_tracks", "trusted", flag)
    parallel = flag == "yes"

    # parties_incomplete
    flag = read_flag(raw, "parties_incomplete")
    from_findings = [
        e for f in findings if f["kind"] == "missing_party" for e in f["events"]
    ]
    if "parties_incomplete" in missing:
        flag = "yes" if from_findings else "no"
        note("parties_incomplete", "defaulted", flag)
        named = from_findings
    else:
        named = [
            str(e)
            for e in (raw or {}).get("parties_incomplete_events") or []
            if str(e) in events
        ] or from_findings
        if flag == "yes" and not named:
            flag = "no"
            note("parties_incomplete", "unevidenced", flag)
        else:
            note("parties_incomplete", "trusted", flag)
    party_events = list(dict.fromkeys(named)) if flag == "yes" else []

    return (
        {
            "needs_fix": needs_fix,
            "parallel_tracks": parallel,
            "parallel_tracks_evidence": evidence if parallel else "",
            "parties_events": party_events,
        },
        notes,
    )


# ---------------------------------------------------------------------------
# Parallel track agent
# ---------------------------------------------------------------------------


def tracks_prompt(
    payload: dict, evidence: str, findings: list[dict], view: str, excerpt: bool
) -> str:
    track_findings = [f for f in findings if f["kind"] == "wrong_track"]
    return (
        (PROMPT_DIR / "tracks.txt").read_text(encoding="utf-8")
        + _document_block(view, excerpt)
        + "\n\nTHE CRITIC'S EVIDENCE OF A SEPARATE MATTER\n"
        + (f'"{evidence}"' if evidence else "(none quoted)")
        + "\n\nTHE CRITIC'S TRACK FINDINGS\n"
        + (
            json.dumps(track_findings, indent=1, ensure_ascii=False)
            if track_findings
            else "(none)"
        )
        + "\n\nDRAFT\n"
        + render_draft(payload)
        + "\n"
    )


def apply_tracks(payload: dict, answer: dict | None, source: str) -> dict:
    """Re-partition the events and record the track relations. Every event
    ends in exactly one track; links are cleared, since the linking pass that
    follows rebuilds them inside the new tracks."""
    audit: dict = {
        "tracks": 0,
        "unassigned": [],
        "relations": 0,
        "relations_refused": [],
    }
    if not isinstance(answer, dict) or not answer.get("tracks"):
        audit["status"] = "no usable answer -- tracks unchanged"
        return audit
    events = _by_id(payload)
    index = c.SpanIndex(source)
    tracks, track_of, given = [], {}, {}
    for n, track in enumerate(answer.get("tracks") or [], 1):
        if not isinstance(track, dict):
            continue
        members = [str(e) for e in track.get("events") or [] if str(e) in events]
        members = [e for e in members if e not in track_of]
        if not members:
            continue
        tid = f"T{n}"
        given[str(track.get("id") or tid)] = tid
        kind = str(track.get("kind") or "").strip().lower()
        tracks.append(
            {
                "id": tid,
                "label": str(track.get("label") or "").strip(),
                **({"kind": kind} if kind in TRACK_KINDS else {}),
                **(
                    {"subject": str(track["subject"]).strip()}
                    if track.get("subject")
                    else {}
                ),
            }
        )
        for e in members:
            track_of[e] = tid
    for eid in events:
        if eid not in track_of:
            tid = f"T{len(tracks) + 1}"
            while any(t["id"] == tid for t in tracks):
                tid = f"T{int(tid[1:]) + 1}"
            tracks.append({"id": tid, "label": ""})
            track_of[eid] = tid
            audit["unassigned"].append(eid)

    relations, during = [], {}
    for rel in answer.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        kind = str(rel.get("relation") or "").strip().lower()
        a, b = given.get(str(rel.get("track"))), given.get(str(rel.get("other")))
        hit = index.locate(str(rel.get("evidence_span") or ""))
        reason = None
        if kind not in TRACK_RELATIONS:
            reason = f"unknown relation {kind!r}"
        elif not a or not b or a == b:
            reason = "names an unknown track, or one track twice"
        elif not hit:
            reason = "evidence_span not found in the judgment"
        elif kind == "during" and (a in during or _would_cycle(during, a, b)):
            reason = "a track is contained once, and never in itself"
        if reason:
            audit["relations_refused"].append({**rel, "reason": reason})
            continue
        if kind == "independent":
            continue  # the default; nothing to record
        if kind == "during":
            during[a] = b
        relations.append(
            {"track": a, "relation": kind, "other": b, "span": c._located(index, hit)}
        )

    payload["tracks"] = tracks
    payload["track_relations"] = relations
    for eid, event in events.items():
        event["track"] = track_of[eid]
        event.pop("link", None)
    audit.update(
        {"tracks": len(tracks), "relations": len(relations), "status": "applied"}
    )
    return audit


def constrain_candidates(payload: dict, candidates: dict) -> dict:
    """Candidates inside the event's own track, plus -- for a track that ran
    during another -- the containing track's events."""
    track_of = {e["id"]: e.get("track") for e in _events(payload)}
    container = {
        r["track"]: r["other"]
        for r in payload.get("track_relations") or []
        if r["relation"] == "during"
    }
    out = {}
    for eid, cands in candidates.items():
        own = track_of.get(eid)
        out[eid] = [
            p
            for p in cands
            if track_of.get(p) == own or track_of.get(p) == container.get(own)
        ]
    return out


def link_prompt(payload: dict, candidates: dict, view: str, excerpt: bool) -> str:
    return (
        (PROMPT_DIR / "link.txt").read_text(encoding="utf-8")
        + "\n\n"
        + load_sections(("LINKS",), path=PLAN_PROMPT)
        + _document_block(view, excerpt)
        + "\n\nDRAFT\n"
        + render_draft(payload, candidates)
        + "\n"
    )


def links_from_track_relations(payload: dict) -> int:
    """A track that ran DURING another and whose first event the linker left
    unlinked gets an occurs_during link to the containing proceeding: the
    latest containing event dated no later than it, else the first."""
    added = 0
    events = _events(payload)
    for rel in payload.get("track_relations") or []:
        if rel["relation"] != "during":
            continue
        inner = [e for e in events if e.get("track") == rel["track"]]
        outer = [e for e in events if e.get("track") == rel["other"]]
        if not inner or not outer or inner[0].get("link"):
            continue
        head, when = inner[0], event_date(inner[0])
        eligible = [e for e in outer if date_cmp(event_date(e), when) in (-1, 0)]
        target = (eligible or outer)[-1 if eligible else 0]
        head["link"] = {"to": target["id"], "kind": "during"}
        if rel.get("span"):
            head["link"]["span"] = rel["span"]
        added += 1
    return added


# ---------------------------------------------------------------------------
# Person identifier: chain-aware parties
# ---------------------------------------------------------------------------


def chain_aware_parties(
    client: OpenAI,
    model: str,
    source: str,
    payload: dict,
    stats: dict,
    *,
    temperature: float,
    event_ids: list[str] | None = None,
    max_tokens: int = 6000,
) -> tuple[int, str]:
    """The parties question, asked with the chain in view.

    The same prompt as compress_parties, but each event is listed with its
    track and the event it continues, and with the parties that predecessor
    already has -- so "the other side is often named only once" becomes a
    question about a named earlier event rather than a guess. Additive only,
    like merge_parties. `event_ids` restricts it to the events a critic named.
    Returns (parties added, raw response).
    """
    by_id = _by_id(payload)
    wanted = [e for e in _events(payload) if event_ids is None or e["id"] in event_ids]
    if not wanted:
        return 0, ""
    listing = []
    for event in wanted:
        bits = [f"[{event['id']}]"]
        for key, label in (
            ("decision_date_span", "date"),
            ("authority_span", "decided by"),
            ("what_happened_span", "what happened"),
        ):
            if value := _text(event.get(key)):
                bits.append(f"{label}: {value}")
        if event.get("track"):
            bits.append(f"track: {event['track']}")
        link = event.get("link")
        if link and link["to"] in by_id:
            pred = by_id[link["to"]]
            names = [_text(p.get("name_span")) for p in pred.get("parties") or []]
            bits.append(
                f"continues {pred['id']}"
                + (
                    f" (whose parties are: {'; '.join(n for n in names if n)})"
                    if names
                    else ""
                )
            )
        listing.append(" | ".join(bits))
    prompt = (
        PARTIES_PROMPT.read_text(encoding="utf-8")
        + "\n\nCHAIN CONTEXT. Each entry says which earlier entry it continues and who "
        "that entry's parties were. A party to the earlier entry who is not shown to "
        "have dropped out is usually still a party here -- on the side THIS entry's own "
        "words give them, which on review is often the opposite side. Still copy every "
        "span from the judgment itself.\n"
        + "\n\nTHE PROCEEDINGS\n"
        + "\n".join(listing)
        + "\n\nTHE JUDGMENT\n<<<DOC\n"
        + source
        + "\nDOC\n"
    )
    answer, raw = ask_json(
        client,
        model,
        prompt,
        label="parties",
        temperature=temperature,
        max_tokens=max_tokens,
    )
    by_event: dict = {}
    for row in (answer or {}).get("parties_by_event") or []:
        if isinstance(row, dict) and str(row.get("id")) in by_id:
            parties = [
                {k: v for k, v in p.items() if str(v).strip()}
                for p in row.get("parties") or []
                if isinstance(p, dict)
            ]
            by_event[str(row["id"])] = parties
    if not by_event:
        return 0, raw
    verified, party_stats = c.verify({"e": by_event}, source)
    c._add_span_stats(stats, party_stats)
    added, _ = c.merge_parties(payload, verified.get("e", {}))
    return added, raw


def apply_filters(payload: dict, totals: dict) -> None:
    """The deterministic filters, accumulated into `totals` across runs."""
    judges = drop_judges(payload)
    actless, actless_ids = c.drop_actless_outcomes(payload)
    body, body_names = c.drop_deciding_body_parties(payload)
    totals.setdefault("judges_dropped", []).extend(judges)
    totals.setdefault("actless_outcomes_dropped_ids", []).extend(actless_ids)
    totals.setdefault("deciding_body_parties_dropped_values", []).extend(body_names)
    totals["actless_outcomes_dropped"] = (
        totals.get("actless_outcomes_dropped", 0) + actless
    )
    totals["deciding_body_parties_dropped"] = (
        totals.get("deciding_body_parties_dropped", 0) + body
    )


# ---------------------------------------------------------------------------
# One document: the agents, in order, and the routing between them
# ---------------------------------------------------------------------------

CRITIC_FLAGS = ("needs_fix", "parallel_tracks", "parties_incomplete")


def run_stage1_agents(
    client: OpenAI,
    model: str,
    source: str,
    payload: dict,
    stats: dict,
    *,
    temperature: float,
    capacity: int,
    parties: bool = True,
    critic_rounds: int = 1,
    raw_dir: Path | None = None,
    doc_id: str = "doc",
) -> dict:
    """Everything after extraction. Returns the log; `payload` is final.

        linking -> person identifier -> filters -> critic --+-> parallel tracks -> re-link
                                                            +-> targeted parties
                                                            +-> fixer
                                                            (repeat to critic_rounds)
                -> filters -> typed links

    The critic's mandatory flags route each round; see resolve_critic_flags.
    """
    log: dict = {"rounds": []}
    filters: dict = {}
    base_url, key = str(client.base_url), client.api_key or ""
    seen_ids: set[str] = set()
    for n, event in enumerate(_events(payload), 1):
        if not event.get("id") or str(event["id"]) in seen_ids:
            event["id"] = f"u{n}"
        event["id"] = str(event["id"])
        seen_ids.add(event["id"])

    def tokens(text: str) -> int:
        return c.count_prompt_tokens(base_url, model, text, key)

    def view_for(prompt_without_doc: str, max_out: int, hints: list[dict]):
        budget = capacity - tokens(prompt_without_doc) - max_out - 1000
        return document_view(
            source, payload, hints, budget_tokens=budget, count_tokens=tokens
        )

    clock = {"t": 0.0}

    def tic() -> None:
        clock["t"] = time.perf_counter()

    def toc() -> float:
        return round(time.perf_counter() - clock["t"], 1)

    # 1. LINKING (tracks + predecessors, jointly)
    candidates = candidate_predecessors(_events(payload))
    view, excerpt = view_for(
        plan_prompt(payload, candidates, "", False), PLAN_MAX_TOKENS, []
    )
    tic()
    plan, plan_raw = ask_json(
        client,
        model,
        plan_prompt(payload, candidates, view, excerpt),
        label="linking",
        temperature=temperature,
        max_tokens=PLAN_MAX_TOKENS,
    )
    secs = toc()
    log["linking"] = {
        "ok": plan is not None,
        "excerpt": excerpt,
        "seconds": secs,
        "audit": apply_plan(payload, plan, candidates, source),
        "raw": plan_raw,
    }

    # 2. PERSON IDENTIFIER: applicants, then chain-aware parties (additive)
    c.attach_applicants(
        client,
        model,
        source,
        payload,
        stats,
        temperature=temperature,
        raw_path=(raw_dir or Path(".")) / f"{doc_id}.applicants.raw.txt",
    )
    log["persons"] = {"applicants": len(payload.get("applicants") or [])}
    if parties:
        tic()
        added, raw = chain_aware_parties(
            client, model, source, payload, stats, temperature=temperature
        )
        secs = toc()
        log["persons"].update({"parties_added": added, "seconds": secs, "raw": raw})
    apply_filters(payload, filters)

    # 3. CRITIC ROUNDS, each routing on the critic's flags
    for rnd in range(1, critic_rounds + 1):
        round_log: dict = {"round": rnd}
        log["rounds"].append(round_log)
        hints = find_hints(payload, source)
        view, excerpt = view_for(
            critic_prompt(payload, hints, "", False), CRITIC_MAX_TOKENS, hints
        )
        tic()
        critique, critic_raw, missing = ask_with_flags(
            client,
            model,
            critic_prompt(payload, hints, view, excerpt),
            flags=CRITIC_FLAGS,
            label="critic",
            temperature=temperature,
            max_tokens=CRITIC_MAX_TOKENS,
        )
        secs = toc()
        findings, refused = validate_findings(critique, payload, source)
        route, flag_notes = resolve_critic_flags(
            critique, findings, payload, source, missing
        )
        round_log["critic"] = {
            "ok": critique is not None,
            "excerpt": excerpt,
            "seconds": secs,
            "hints": hints,
            "findings": findings,
            "findings_refused": refused,
            "flags": flag_notes,
            "route": route,
            "raw": critic_raw,
        }
        changed = False

        # 3a. PARALLEL TRACK AGENT -> re-link inside the new tracks
        if route["parallel_tracks"]:
            prompt0 = tracks_prompt(
                payload, route["parallel_tracks_evidence"], findings, "", False
            )
            view, excerpt = view_for(prompt0, TRACKS_MAX_TOKENS, hints)
            tic()
            answer, raw = ask_json(
                client,
                model,
                tracks_prompt(
                    payload,
                    route["parallel_tracks_evidence"],
                    findings,
                    view,
                    excerpt,
                ),
                label="tracks",
                temperature=temperature,
                max_tokens=TRACKS_MAX_TOKENS,
            )
            secs = toc()
            audit = apply_tracks(payload, answer, source)
            round_log["tracks"] = {
                "seconds": secs,
                "excerpt": excerpt,
                "audit": audit,
                "raw": raw,
            }
            if audit.get("status") == "applied":
                changed = True
                cands = constrain_candidates(
                    payload, candidate_predecessors(_events(payload))
                )
                view, excerpt = view_for(
                    link_prompt(payload, cands, "", False), PLAN_MAX_TOKENS, []
                )
                tic()
                links, raw = ask_json(
                    client,
                    model,
                    link_prompt(payload, cands, view, excerpt),
                    label="relink",
                    temperature=temperature,
                    max_tokens=PLAN_MAX_TOKENS,
                )
                secs = toc()
                round_log["relink"] = {
                    "seconds": secs,
                    "audit": apply_plan(
                        payload, links, cands, source, keep_tracks=True
                    ),
                    "raw": raw,
                }
                # The track findings are answered; the fixer does not redo them.
                findings = [f for f in findings if f["kind"] != "wrong_track"]

        # 3b. TARGETED PARTIES on the events the critic named
        if parties and route["parties_events"]:
            tic()
            added, raw = chain_aware_parties(
                client,
                model,
                source,
                payload,
                stats,
                temperature=temperature,
                event_ids=route["parties_events"],
            )
            secs = toc()
            round_log["parties"] = {
                "events": route["parties_events"],
                "added": added,
                "seconds": secs,
                "raw": raw,
            }
            changed = changed or added > 0

        # 3c. FIXER, on whatever findings are left
        if route["needs_fix"] and findings:
            view, excerpt = view_for(
                fix_prompt(payload, findings, "", False), FIX_MAX_TOKENS, hints
            )
            tic()
            ops, fix_raw = ask_json(
                client,
                model,
                fix_prompt(payload, findings, view, excerpt),
                label="fix",
                temperature=temperature,
                max_tokens=FIX_MAX_TOKENS,
            )
            secs = toc()
            audit = apply_fixes(payload, ops, findings, source)
            round_log["fix"] = {
                "seconds": secs,
                "excerpt": excerpt,
                "audit": audit,
                "raw": fix_raw,
            }
            changed = changed or any(
                str(a["status"]).startswith("applied") for a in audit
            )
        else:
            round_log["fix"] = {
                "skipped": "no findings left" if route["needs_fix"] else "needs_fix: no"
            }

        apply_filters(payload, filters)
        if not changed:
            break  # a further round would read the same draft

    # 4. TYPED LINKS
    log["track_relation_links"] = links_from_track_relations(payload)
    log["finalize"] = finalize(payload)
    stats.update(filters)
    return log


def summarise(log: dict) -> dict:
    """The per-document numbers worth carrying in the .compress.json stats."""
    linking = log.get("linking", {}).get("audit", {})
    rounds = log.get("rounds", [])

    def total(fn):
        return sum(fn(r) for r in rounds)

    def applied(audit):
        return sum(1 for a in audit if str(a.get("status", "")).startswith("applied"))

    return {
        "linking_ok": log.get("linking", {}).get("ok"),
        "links_accepted": linking.get("links_accepted", 0),
        "links_refused": len(linking.get("links_refused", [])),
        "critic_rounds": len(rounds),
        "findings": total(lambda r: len(r.get("critic", {}).get("findings", []))),
        "findings_refused": total(
            lambda r: len(r.get("critic", {}).get("findings_refused", []))
        ),
        "flags_not_trusted": total(
            lambda r: sum(
                1 for f in r.get("critic", {}).get("flags", []) if f["how"] != "trusted"
            )
        ),
        "track_agent_runs": total(lambda r: 1 if "tracks" in r else 0),
        "targeted_party_runs": total(lambda r: 1 if "parties" in r else 0),
        "fix_ops_applied": total(lambda r: applied(r.get("fix", {}).get("audit", []))),
        "fix_ops_refused": total(
            lambda r: (
                len(r.get("fix", {}).get("audit", []))
                - applied(r.get("fix", {}).get("audit", []))
            )
        ),
        "parties_added": log.get("persons", {}).get("parties_added", 0)
        + total(lambda r: r.get("parties", {}).get("added", 0)),
        "track_relation_links": log.get("track_relation_links", 0),
        "relations": log.get("finalize", {}).get("relations", {}),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--input-jsonl", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--model", default="gemma-4-31b")
    ap.add_argument("--base-url", default="http://localhost:8001/v1")
    ap.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", "EMPTY"))
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=1, help="see compress.py --workers")
    ap.add_argument("--token-budget", type=int, default=0)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument(
        "--parties-pass",
        action="store_true",
        help="accepted for compatibility; the parties pass is on unless --no-parties",
    )
    ap.add_argument(
        "--no-parties",
        action="store_true",
        help="skip the person identifier's parties pass (applicants still run)",
    )
    ap.add_argument(
        "--critic-rounds",
        type=int,
        default=1,
        help="critic rounds; 0 disables the critic and everything it routes to",
    )
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--split-max-tokens", type=int, default=c.SPLIT_OUTPUT_TOKENS)
    ap.add_argument("--only", default="")
    ap.add_argument(
        "--prompt-file",
        type=Path,
        default=EXTRACT_PROMPT,
        help="extraction prompt (default prompts/extract.txt)",
    )
    ap.add_argument(
        "--reuse-extraction",
        type=Path,
        default=None,
        help="a stage-1 directory whose .compress.json payloads stand in for the "
        "extract call: their links are stripped and the agentic passes run on "
        "them. Isolates plan/critic/fix from extraction variance.",
    )
    ap.add_argument(
        "--skip-critic",
        action="store_true",
        help="same as --critic-rounds 0",
    )
    args = ap.parse_args()

    c.SPLIT_OUTPUT_TOKENS = args.split_max_tokens
    c.PROMPT = args.prompt_file
    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=900.0)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {w.strip() for w in args.only.split(",") if w.strip()}
    lines = [
        l
        for l in args.input_jsonl.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    capacity = args.token_budget or c.server_token_capacity(
        args.base_url, args.api_key or ""
    )
    if args.workers > 1:
        c.BUDGET = c.TokenBudget(capacity)
    print(
        f"stage 1 (agentic): {args.model} @ {args.base_url} "
        f"(temperature {args.temperature}, {args.workers} worker(s))"
        + (
            f", reusing extraction from {args.reuse_extraction}"
            if args.reuse_extraction
            else ""
        )
    )
    failures: list[str] = []
    lock = threading.Lock()

    def run_doc(i: int, line: str, doc_id: str) -> None:
        text = json.loads(line).get("text", "")
        t0 = time.perf_counter()
        if args.reuse_extraction:
            prior = args.reuse_extraction / f"{doc_id}.compress.json"
            if not prior.exists():
                raise FileNotFoundError(prior)
            record = json.loads(prior.read_text(encoding="utf-8"))
            payload = record["payload"]
            stats = {
                k: record["stats"].get(k, 0) for k in ("spans", "located", "dropped")
            }
            stats["dropped_values"] = list(record["stats"].get("dropped_values", []))
            # Everything downstream of extraction is redone here, so take the
            # extraction alone: no parties-pass additions, no applicants.
            payload.pop("applicants", None)
            stats["reused_extraction"] = str(prior)
        else:
            payload, stats, raw = c.compress_split(
                client,
                args.model,
                text,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                capacity=capacity,
            )
            if payload is None:
                with lock:
                    failures.append(doc_id)
                print(f"  {doc_id}: FAILED ({stats.get('error')})")
                (args.out_dir / f"{doc_id}.raw.txt").write_text(raw, encoding="utf-8")
                return
        stats["legacy_link_fields_stripped"] = strip_legacy_links(payload)
        n_extracted = len(_events(payload))

        # EVENT DETECTION'S FLAG. Not retried -- a retry would be a second
        # extraction -- so an absent flag is derived from the events, and a
        # "no" contradicted by events found is overridden by them.
        flag = read_flag(payload, "domestic_events_found")
        payload.pop("domestic_events_found", None)
        derived = "yes" if n_extracted else "no"
        stats["domestic_events_found"] = {
            "value": derived,
            "how": "defaulted"
            if flag is None
            else "trusted"
            if flag == derived
            else "inconsistent",
        }
        if not n_extracted:
            # Nothing to link, attribute or criticise: straight to an empty
            # bundle, with no further calls.
            log = {"skipped": "no domestic events"}
            payload.setdefault("tracks", [])
            stats["events_extracted"] = 0
            stats["agentic"] = {"skipped": "no domestic events"}
            print(
                f"  {doc_id}:   0 events -- no further agents [{time.perf_counter() - t0:.0f}s]"
            )
        else:
            log = run_stage1_agents(
                client,
                args.model,
                text,
                payload,
                stats,
                temperature=args.temperature,
                capacity=capacity,
                parties=not args.no_parties,
                critic_rounds=0 if args.skip_critic else args.critic_rounds,
                raw_dir=args.out_dir,
                doc_id=doc_id,
            )
            stats["events_extracted"] = n_extracted
            stats["agentic"] = a = summarise(log)
            print(
                f"  {doc_id}: {n_extracted:3} extracted -> {len(_events(payload)):3} events, "
                f"{len(payload.get('tracks') or []):2} tracks, {a['links_accepted']:3} links, "
                f"{a['findings']:2} findings, track agent x{a['track_agent_runs']}, "
                f"{a['fix_ops_applied']:2} fixes, +{a['parties_added']} parties, "
                f"rel {a['relations']}, {len(stats.get('judges_dropped', []))} judge(s) removed "
                f"[{time.perf_counter() - t0:.0f}s]",
                flush=True,
            )
        (args.out_dir / f"{doc_id}.agentic.json").write_text(
            json.dumps(log, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        (args.out_dir / f"{doc_id}.compress.json").write_text(
            json.dumps(
                {"doc": doc_id, "stats": stats, "payload": payload},
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def guarded(i: int, line: str) -> None:
        doc_id = c.doc_id_for(i, line)
        try:
            run_doc(i, line, doc_id)
        except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
            with lock:
                failures.append(doc_id)
            print(f"  {doc_id}: FAILED ({type(exc).__name__}: {exc})", flush=True)

    todo, resumed = [], 0
    for i, line in enumerate(lines, 1):
        doc_id = c.doc_id_for(i, line)
        if wanted and doc_id not in wanted:
            continue
        if (args.out_dir / f"{doc_id}.compress.json").exists() and not args.overwrite:
            resumed += 1
            continue
        todo.append((i, line))

    if args.workers > 1:
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            list(pool.map(lambda a: guarded(*a), todo))
    else:
        for i, line in todo:
            guarded(i, line)
    if resumed:
        print(f"\nresumed: {resumed} document(s) already present, skipped")
    expected = len(wanted) if wanted else len(lines)
    produced = len(list(args.out_dir.glob("*.compress.json")))
    if failures:
        print(f"FAILED on {len(failures)} document(s): {', '.join(failures)}")
    if produced < expected:
        print(f"INCOMPLETE: {produced} output(s) for {expected} input(s)")
    if failures or produced < expected:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
