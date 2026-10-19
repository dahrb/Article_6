"""Stage 3: reconcile OntoCast's graph with stage 1, and only where they differ.

Stage 1 decides; stage 2 renders. On the compressed arm every classification
and every chain link stage 2 needs is already written into its input, decided
and checked against the judgment by the agentic stage 1. What stage 2 can
still get wrong is the TRANSCRIPTION: measured on gold30, stage 1 carried
`matter` on 78% of events and the graph asserted it on barely a quarter as
many; a filing and its decision at one court came out as followsProceeding
instead of partOfProceeding; 4.1% of events were dropped outright.

So this stage diffs the graph against the stage-1 payload and repairs the
difference, and does nothing at all where there is none:

    rewrites    the lossless structural rewrites new_repair also ran first
                (entailed types, split shared participations, mirrored labels,
                pruned unbuilt references) -- arithmetic, not judgement.
    match       each stage-1 event to its graph node(s), by its verbatim quote,
                falling back to decision date + authority where stage 2
                re-quoted it. A narrated filing split into two nodes matches
                both.
    diff        per matched event: instance level, outcome, procedure,
                direction and matter against the stage-1 values; per pair of
                matched events: the chain edges against stage 1's typed links.
    transcribe  (code) fill a missing value, replace a conflicting one, add a
                missing edge, retype a mistyped one, and REMOVE a chain edge
                between two matched events that stage 1 did not assert --
                that edge is the over-linking the planner exists to prevent.
                Stage 1 wins every disagreement: it read the judgment, decided
                with the whole document in view, and was critiqued; stage 2
                read only the digest of those decisions.
    mint        (model, one call, only if stage 1 has events the graph lacks)
                the missing events are minted from their own digest entries,
                applied through repair_facts.apply_patch with its evidence
                guards, and the call is KEPT only if it reduced the number of
                missing events without adding a SHACL violation.
    shacl       (model, only if the graph has violations) new_repair's
                violation loop over SHACL errors, disjoint-type conflicts and
                duplicate nodes: graph and ontology only, no document, no new
                evidence, at most --max-rounds rounds. Transcription runs
                again afterwards, so the loop cannot undo a stage-1 decision;
                a violation that only a stage-1 decision could clear is left
                standing and counted.

Transcription and minting never touch an assertion about a node stage 1 has
no event for, parties and participations (coverage, reported elsewhere), or
anything the graph asserts that stage 1 is silent on, other than chain edges
between matched events. The SHACL loop may touch anything a violation names.

Each graph gets `<stem>.reconcile.json`, which is also the resume marker.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from rdflib import RDF, Graph, Namespace, URIRef

from art6.ontology.check_stage_boundary import _date_matches
from art6.ontology.repair_facts import (
    RepairPatch,
    _curie,
    apply_patch,
    complete_entailed_types,
    domestic_event_classes,
    find_shape_violations,
    mirror_party_labels,
    ontology_terms,
    prune_unbuilt_proceedings,
    split_multiparty_participations,
    split_shared_participations,
)

ECHR = Namespace("https://growgraph.dev/echr#")
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# stage-1 field -> (graph predicate, vocabulary prefix, functional?)
FIELDS = {
    "instance_level": (ECHR.hasInstanceLevel, "Level", True),
    "proceeding_type": (ECHR.hasProceedingType, "Type", True),
    "outcome": (ECHR.hasOutcome, "Outcome", True),
    "outcome_direction": (ECHR.hasOutcomeDirection, "Direction", True),
    "matter": (ECHR.hasMatter, "Matter", False),
}
# Rung and procedure belong to the proceeding, so every node of a split
# entry carries them; outcome, direction and matter belong to the decision.
WHOLE_ENTRY_FIELDS = {"instance_level", "proceeding_type"}
RELATIONS = {
    "follows": ECHR.followsProceeding,
    "part_of": ECHR.partOfProceeding,
    "occurs_during": ECHR.occursDuring,
}
FUNCTIONAL_RELATIONS = {ECHR.partOfProceeding, ECHR.occursDuring}
# A graph quote shorter than this must equal the stage-1 span to match it:
# "gave judgment" is inside a dozen spans in one judgment.
MIN_CONTAINED_QUOTE = 25


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def _span(event: dict, key: str) -> str:
    value = event.get(key)
    return value.get("text", "") if isinstance(value, dict) else ""


@lru_cache(maxsize=1)
def _event_classes() -> frozenset[URIRef]:
    return domestic_event_classes()


@lru_cache(maxsize=1)
def _terms() -> frozenset[URIRef]:
    return ontology_terms()


def vocabulary_iri(field_name: str, value: str) -> URIRef | None:
    """ "merits decided" -> echr:OutcomeMeritsDecided, if the ontology has it."""
    prefix = FIELDS[field_name][1]
    iri = ECHR[prefix + "".join(w.capitalize() for w in str(value).split())]
    return iri if iri in _terms() else None


def graph_events(graph: Graph) -> list[URIRef]:
    classes = _event_classes()
    return sorted(
        {
            s
            for cls in classes
            for s in graph.subjects(RDF.type, cls)
            if isinstance(s, URIRef)
        },
        key=str,
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass
class Match:
    nodes: dict[str, list[URIRef]] = field(default_factory=dict)  # entry -> nodes
    entry_of: dict[URIRef, str] = field(default_factory=dict)
    how: dict[str, str] = field(default_factory=dict)

    def missing(self, events: list[dict]) -> list[str]:
        return [e["id"] for e in events if not self.nodes.get(e["id"])]


def _quote_score(span: str, quotes: list[str]) -> int:
    span = norm(span)
    if not span:
        return 0
    best = 0
    for q in quotes:
        if not q:
            continue
        if q == span:
            return 10_000 + len(q)
        if (span in q) or (len(q) >= MIN_CONTAINED_QUOTE and q in span):
            best = max(best, min(len(q), len(span)))
    return best


def match_events(graph: Graph, events: list[dict]) -> Match:
    """Claim graph nodes for stage-1 entries, best quote overlap first.

    Every candidate pair is scored and claimed greedily from the strongest
    down, so an entry cannot take a node that another entry quotes exactly.
    The date + authority fallback runs only for entries no quote placed.
    """
    nodes = graph_events(graph)
    quotes = {
        n: [norm(str(q)) for q in graph.objects(n, ECHR.hasSupportingQuote)]
        for n in nodes
    }
    pairs = []
    for event in events:
        for node in nodes:
            what = _quote_score(_span(event, "what_happened_span"), quotes[node])
            if what:
                pairs.append((what, 1, event["id"], node))
            filing = _quote_score(
                _span(event, "institution_narrated_span"), quotes[node]
            )
            if filing:
                pairs.append((filing, 0, event["id"], node))
    pairs.sort(key=lambda p: (-p[0], -p[1], p[2], str(p[3])))
    m = Match()
    primary_taken: set[str] = set()
    for _, is_primary, eid, node in pairs:
        if node in m.entry_of:
            continue
        if is_primary and eid in primary_taken:
            continue
        m.entry_of[node] = eid
        entry_nodes = m.nodes.setdefault(eid, [])
        if is_primary:
            entry_nodes.insert(0, node)
            primary_taken.add(eid)
        else:
            entry_nodes.append(node)
        m.how[eid] = "quote"

    for event in events:
        if m.nodes.get(event["id"]):
            continue
        date = _span(event, "decision_date_span") or _span(event, "start_date_span")
        body = norm(_span(event, "authority_span"))
        if not date or not body:
            continue
        for node in nodes:
            if node in m.entry_of:
                continue
            g_date = graph.value(node, ECHR.hasDecisionDate) or graph.value(
                node, ECHR.hasProceedingStartDate
            )
            court = graph.value(node, ECHR.hasCourt)
            g_body = (
                norm(str(graph.value(court, ECHR.hasAuthorityName) or ""))
                if court
                else ""
            )
            if (
                g_date is not None
                and _date_matches(date, str(g_date))
                and g_body
                and (body in g_body or g_body in body)
            ):
                m.nodes[event["id"]] = [node]
                m.entry_of[node] = event["id"]
                m.how[event["id"]] = "date+authority"
                break
    return m


def root_node(graph: Graph, nodes: list[URIRef]) -> URIRef:
    """The node carrying the entry's chain position: of a filing/decision pair,
    the one the other is partOf; otherwise the entry's primary node."""
    if len(nodes) > 1:
        for node in nodes:
            if graph.value(node, ECHR.partOfProceeding) in nodes:
                return graph.value(node, ECHR.partOfProceeding)
    return nodes[0]


# ---------------------------------------------------------------------------
# Diff and deterministic transcription
# ---------------------------------------------------------------------------


def transcribe(graph: Graph, payload: dict, m: Match) -> list[dict]:
    """Make matched nodes agree with stage 1. Returns one audit row per change."""
    audit: list[dict] = []
    events = [
        e for e in payload.get("events") or [] if isinstance(e, dict) and e.get("id")
    ]
    by_id = {e["id"]: e for e in events}

    def note(kind, node, pred, old=None, new=None, entry=None):
        audit.append(
            {
                "kind": kind,
                "entry": entry,
                "node": _curie(graph, node),
                "predicate": _curie(graph, pred),
                **({"old": _curie(graph, old)} if old is not None else {}),
                **({"new": _curie(graph, new)} if new is not None else {}),
            }
        )

    # FIELDS
    for eid, nodes in m.nodes.items():
        event = by_id[eid]
        for name, (pred, _, functional) in FIELDS.items():
            raw = event.get(name)
            if not raw:
                continue
            targets = nodes if name in WHOLE_ENTRY_FIELDS else nodes[:1]
            values = raw if isinstance(raw, list) else [raw]
            iris = [
                i for i in (vocabulary_iri(name, v) for v in values) if i is not None
            ]
            if not iris:
                continue
            for node in targets:
                present = set(graph.objects(node, pred))
                if functional:
                    want = iris[0]
                    if present == {want}:
                        continue
                    for old in present:
                        graph.remove((node, pred, old))
                    graph.add((node, pred, want))
                    if present:
                        note("field_conflict", node, pred, min(present), want, eid)
                    else:
                        note("field_missing", node, pred, None, want, eid)
                else:
                    for want in iris:
                        if want not in present:
                            graph.add((node, pred, want))
                            note("field_missing", node, pred, None, want, eid)

    # LINKS between matched entries -- only for payloads whose links are
    # TYPED (agentic_stage1, which always writes `tracks`). compress.py's
    # untyped `follows` leaves partOf vs follows to stage 2, so there is
    # nothing to hold the graph to.
    if "tracks" not in payload:
        return audit
    roots = {eid: root_node(graph, nodes) for eid, nodes in m.nodes.items()}
    expected: set[tuple] = set()
    for eid in m.nodes:
        for key, pred in RELATIONS.items():
            target = by_id[eid].get(key)
            if target in roots:
                expected.add((roots[eid], pred, roots[target]))
    actual: set[tuple] = set()
    for pred in RELATIONS.values():
        for s, o in graph.subject_objects(pred):
            if s in m.entry_of and o in m.entry_of and m.entry_of[s] != m.entry_of[o]:
                actual.add((s, pred, o))
    for s, pred, o in sorted(actual - expected, key=str):
        graph.remove((s, pred, o))
        note("link_removed", s, pred, o, None, m.entry_of[s])
    for s, pred, o in sorted(expected - actual, key=str):
        if pred in FUNCTIONAL_RELATIONS:
            for old in list(graph.objects(s, pred)):
                graph.remove((s, pred, old))
                note("link_replaced", s, pred, old, None, m.entry_of[s])
        graph.add((s, pred, o))
        note("link_added", s, pred, None, o, m.entry_of[s])
    return audit


# ---------------------------------------------------------------------------
# The one model call: minting events the graph lacks
# ---------------------------------------------------------------------------


def mint_prompt(graph: Graph, payload: dict, missing: list[str], m: Match) -> str:
    from art6.ontology.render_bundles import render

    roots = {eid: root_node(graph, nodes) for eid, nodes in m.nodes.items()}
    wanted = []
    for event in payload.get("events") or []:
        if event["id"] not in missing:
            continue
        event = dict(event)
        for key in RELATIONS:
            target = event.get(key)
            if target in roots:
                event[key] = f"{target} (graph node {_curie(graph, roots[target])})"
            elif target and target not in missing:
                event.pop(key)  # its target never reached the graph either
        wanted.append(event)
    digest = render({"tracks": payload.get("tracks") or [], "events": wanted})
    return (
        "EXTRACTION INSTRUCTIONS\nThe graph below was extracted under these. They bind you too.\n\n"
        + (PROMPT_DIR / "facts.txt").read_text(encoding="utf-8")
        + "\n\nONTOLOGY\n"
        + (Path(__file__).resolve().parents[2] / "ontology" / "echr.ttl").read_text(
            encoding="utf-8"
        )
        + "\n\nGRAPH\n"
        + graph.serialize(format="turtle")
        + "\n\nMISSING ENTRIES\n"
        + digest
        + "\n\nYOUR TASK\n"
        + (PROMPT_DIR / "reconcile_mint.txt").read_text(encoding="utf-8")
        + "\n\nOUTPUT SCHEMA\n"
        + json.dumps(RepairPatch.model_json_schema(), indent=1)
    )


def keep_anchored(patch: RepairPatch) -> tuple[RepairPatch, list[str]]:
    """Refuse any group that mints an event without a supporting quote. The
    quote itself is checked against the judgment by apply_patch."""
    classes = {_curie_str(c) for c in _event_classes()}
    kept, refused = [], []
    for group in patch.groups:
        minted = {
            op.subject.strip()
            for op in group.ops
            if op.action == "add"
            and op.predicate == "rdf:type"
            and op.object.strip() in classes
        }
        quoted = {
            op.subject.strip()
            for op in group.ops
            if op.action == "add" and op.predicate == "echr:hasSupportingQuote"
        }
        if minted - quoted:
            refused.append(group.finding)
            continue
        # This call adds; it does not edit. A remove would touch a node stage 1
        # already agrees with.
        group.ops = [op for op in group.ops if op.action == "add"]
        kept.append(group)
    return RepairPatch(groups=kept, merges=[]), refused


def _curie_str(iri: URIRef) -> str:
    return "echr:" + str(iri).removeprefix(str(ECHR))


def mint_missing(
    graph: Graph,
    payload: dict,
    missing: list[str],
    m: Match,
    *,
    client: OpenAI,
    model: str,
    doc_ns: str,
    source_text: str,
    temperature: float,
    max_tokens: int,
) -> tuple[Graph, dict]:
    from art6.ontology.new_repair import call_model

    prompt = mint_prompt(graph, payload, missing, m)
    t0 = time.perf_counter()
    patch = call_model(
        client,
        model,
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        label="mint",
    )
    report: dict = {"called": True, "seconds": None, "accepted": False}
    if patch is None:
        report["seconds"] = round(time.perf_counter() - t0, 1)
        report["status"] = "no usable patch"
        return graph, report
    patch, refused = keep_anchored(patch)
    candidate, ops = apply_patch(
        graph, patch, doc_ns, source_text=source_text, allow_quote_adds=True
    )
    complete_entailed_types(candidate, doc_ns)
    # apply_patch refuses a non-verbatim quote op by op, so a group's type can
    # land while its quote does not. Such a node is exactly what keep_anchored
    # exists to refuse; take it back out, with every edge touching it.
    unanchored = [
        n
        for n in set(graph_events(candidate)) - set(graph_events(graph))
        if candidate.value(n, ECHR.hasSupportingQuote) is None
    ]
    for node in unanchored:
        candidate.remove((node, None, None))
        candidate.remove((None, None, node))
    still_missing = match_events(candidate, payload.get("events") or []).missing(
        payload.get("events") or []
    )
    before_v, after_v = (
        len(find_shape_violations(graph)),
        len(find_shape_violations(candidate)),
    )
    report.update(
        {
            "seconds": round(time.perf_counter() - t0, 1),
            "groups_refused_unanchored": refused,
            "nodes_removed_unanchored": [_curie(graph, n) for n in unanchored],
            "ops_applied": sum(
                1 for o in ops if str(o.get("status", "")).startswith("applied")
            ),
            "missing_after": len(still_missing),
            "violations_before": before_v,
            "violations_after": after_v,
        }
    )
    # THE GATE. Kept only if it placed at least one missing event and broke
    # nothing SHACL can see; otherwise the graph stays as transcription left it.
    if len(still_missing) < len(missing) and after_v <= before_v:
        report["accepted"] = True
        report["status"] = "accepted"
        return candidate, report
    report["status"] = "reverted by gate"
    return graph, report


# ---------------------------------------------------------------------------
# One graph
# ---------------------------------------------------------------------------


def _violation_count(graph: Graph, doc_ns: str) -> int:
    """What the SHACL loop works on: SHACL errors (not warnings), disjoint-type
    conflicts and duplicate nodes -- new_repair.Findings' violation scope."""
    from art6.ontology.new_repair import collect_findings

    return collect_findings(graph, doc_ns, None).count("violations")


def lossless_rewrites(graph: Graph, doc_ns: str) -> dict:
    return {
        "entailed_types": complete_entailed_types(graph, doc_ns),
        "shared_participations_split": split_shared_participations(graph, doc_ns),
        "multiparty_participations_split": split_multiparty_participations(
            graph, doc_ns
        ),
        "party_labels_mirrored": mirror_party_labels(graph, doc_ns),
        "unbuilt_references_pruned": prune_unbuilt_proceedings(graph, doc_ns),
    }


def reconcile_document(
    facts_ttl: Path,
    payload: dict,
    *,
    client: OpenAI | None,
    model: str,
    source_text: str | None,
    temperature: float,
    max_tokens: int,
    max_rounds: int = 3,
) -> dict:
    graph = Graph()
    graph.parse(facts_ttl, format="turtle")
    doc_ns = str(dict(graph.namespaces()).get("doc", ""))
    if not doc_ns:
        return {"file": facts_ttl.name, "error": "no doc: prefix in graph"}
    events = [
        e for e in payload.get("events") or [] if isinstance(e, dict) and e.get("id")
    ]

    rewrites = lossless_rewrites(graph, doc_ns)
    shacl_before = len(find_shape_violations(graph))
    m = match_events(graph, events)
    missing = m.missing(events)
    audit = transcribe(graph, payload, m)
    report: dict = {
        "file": facts_ttl.name,
        "stage1_events": len(events),
        "graph_events": len(graph_events(graph)),
        "matched_by_quote": sum(1 for h in m.how.values() if h == "quote"),
        "matched_by_date_authority": sum(1 for h in m.how.values() if h != "quote"),
        "missing_before": len(missing),
        "rewrites": rewrites,
        "mint": {"called": False},
    }

    if missing and client is not None and source_text:
        graph, report["mint"] = mint_missing(
            graph,
            payload,
            missing,
            m,
            client=client,
            model=model,
            doc_ns=doc_ns,
            source_text=source_text,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if report["mint"].get("accepted"):
            # The new nodes need the same transcription the others got.
            m = match_events(graph, events)
            audit += transcribe(graph, payload, m)
            missing = m.missing(events)
    elif missing:
        report["mint"] = {"called": False, "status": "no model or no source text"}

    # SHACL REPAIR. The violation loop (new_repair.run_loop) over what the
    # graph alone decides -- SHACL errors, disjoint-type conflicts, duplicate
    # nodes -- run only when there is something to fix. It sees the graph and
    # the ontology, never the document, and apply_patch refuses it any new
    # evidence. It can still undo a stage-1 decision on the way (delete a
    # partOf to clear a shape, relink an appeal), so transcription runs again
    # after it: stage 1 keeps the last word on fields and chain links, and any
    # violation that costs is left standing and counted rather than traded
    # back and forth.
    violations_before_loop = _violation_count(graph, doc_ns)
    report["shacl_loop"] = {
        "called": False,
        "violations_before": violations_before_loop,
    }
    if violations_before_loop and client is not None and max_rounds > 0:
        from art6.ontology.new_repair import run_loop

        t0 = time.perf_counter()
        graph, loop_audit = run_loop(
            graph,
            client=client,
            model=model,
            doc_ns=doc_ns,
            source_text=source_text,
            temperature=temperature,
            max_tokens=max_tokens,
            max_rounds=max_rounds,
            label="shacl",
        )
        m = match_events(graph, events)
        restored = transcribe(graph, payload, m)
        audit += restored
        missing = m.missing(events)
        report["shacl_loop"] = {
            "called": True,
            "seconds": round(time.perf_counter() - t0, 1),
            "violations_before": violations_before_loop,
            "violations_after": _violation_count(graph, doc_ns),
            "ops_applied": sum(
                1 for a in loop_audit if str(a.get("status", "")).startswith("applied")
            ),
            "stage1_decisions_restored": len(restored),
        }

    lossless_rewrites(graph, doc_ns)
    counts: dict[str, int] = {}
    for row in audit:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    unmatched_graph = [n for n in graph_events(graph) if n not in m.entry_of]
    report.update(
        {
            "status": "consistent"
            if not audit and not report["missing_before"] and not violations_before_loop
            else "reconciled",
            "changes": counts,
            "missing_after": len(missing),
            "missing_ids": missing,
            "graph_events_without_stage1_entry": len(unmatched_graph),
            "shacl_before": shacl_before,
            "shacl_after": len(find_shape_violations(graph)),
            "violations_after": _violation_count(graph, doc_ns),
            "audit": audit,
        }
    )
    backup = facts_ttl.parent / "backup"
    backup.mkdir(exist_ok=True)
    if not (backup / facts_ttl.name).exists():
        (backup / facts_ttl.name).write_text(
            facts_ttl.read_text(encoding="utf-8"), encoding="utf-8"
        )
    facts_ttl.write_text(graph.serialize(format="turtle"), encoding="utf-8")
    (
        facts_ttl.parent
        / (facts_ttl.name.removesuffix(".facts.ttl") + ".reconcile.json")
    ).write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def load_case_index(input_jsonl: Path) -> dict[str, str]:
    """Graph file key (`bundles.L3`) -> case_id, from the arm's own input."""
    stem = input_jsonl.stem
    out = {}
    for i, line in enumerate(input_jsonl.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            record = json.loads(line)
            out[f"{stem}.L{i}"] = str(record.get("case_id") or f"input.L{i}")
    return out


def load_texts(original_jsonl: Path | None) -> dict[str, str]:
    if not original_jsonl:
        return {}
    out = {}
    for i, line in enumerate(
        original_jsonl.read_text(encoding="utf-8").splitlines(), 1
    ):
        if line.strip():
            record = json.loads(line)
            out[str(record.get("case_id") or f"input.L{i}")] = record.get("text", "")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--facts-dir", required=True, type=Path, help="graphs, reconciled in place"
    )
    ap.add_argument(
        "--input-jsonl", required=True, type=Path, help="the arm's stage-2 input"
    )
    ap.add_argument("--stage1-dir", required=True, type=Path)
    ap.add_argument(
        "--original-jsonl",
        type=Path,
        default=None,
        help="the judgments, for verifying quotes on minted events",
    )
    ap.add_argument("--model", default="gemma-4-31b")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", "EMPTY"))
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=12000)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument(
        "--no-llm",
        action="store_true",
        help="transcription only; never mint, never run the SHACL loop",
    )
    ap.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="SHACL repair loop rounds; 0 disables the loop",
    )
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    client = None
    if not args.no_llm:
        client = OpenAI(
            base_url=args.base_url, api_key=args.api_key, timeout=args.timeout
        )
    cases = load_case_index(args.input_jsonl)
    texts = load_texts(args.original_jsonl)
    files = sorted(args.facts_dir.glob("*.facts.ttl"))
    if not files:
        print(f"no *.facts.ttl under {args.facts_dir}", file=sys.stderr)
        raise SystemExit(1)

    results, failed, resumed = [], [], 0
    t0 = time.perf_counter()
    for f in files:
        key = f.name.removesuffix(".facts.ttl")
        marker = f.parent / f"{key}.reconcile.json"
        if marker.exists() and not args.overwrite:
            resumed += 1
            continue
        case_id = cases.get(key)
        stage1 = args.stage1_dir / f"{case_id}.compress.json" if case_id else None
        if stage1 is None or not stage1.exists():
            # Loud: a graph with no stage-1 payload cannot be reconciled, and
            # silently passing it through would read as "consistent".
            failed.append(f.name)
            print(
                f"  {f.name}: FAILED (no stage-1 payload for {case_id!r})", flush=True
            )
            continue
        try:
            payload = json.loads(stage1.read_text(encoding="utf-8"))["payload"]
            r = reconcile_document(
                f,
                payload,
                client=client,
                model=args.model,
                source_text=texts.get(case_id),
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                max_rounds=args.max_rounds,
            )
        except Exception as exc:  # noqa: BLE001 -- one document must not end the run
            failed.append(f.name)
            print(f"  {f.name}: FAILED ({type(exc).__name__}: {exc})", flush=True)
            continue
        results.append(r)
        if "error" in r:
            failed.append(f.name)
            print(f"  {f.name}: FAILED ({r['error']})")
            continue
        mint = r["mint"]
        print(
            f"  {f.name}: {r['status']:10} {r['stage1_events']:3} stage-1 events, "
            f"missing {r['missing_before']}->{r['missing_after']}, "
            f"changes {r['changes'] or '{}'}"
            + (f", mint {mint.get('status')}" if mint.get("called") else "")
            + (
                f", SHACL loop {r['shacl_loop']['violations_before']}"
                f"->{r['shacl_loop']['violations_after']}"
                if r["shacl_loop"].get("called")
                else ""
            )
            + f", SHACL {r['shacl_before']}->{r['shacl_after']}",
            flush=True,
        )

    ok = [r for r in results if "error" not in r]
    print(
        f"\n{len(ok)} reconciled in {time.perf_counter() - t0:.0f}s: "
        f"{sum(r['status'] == 'consistent' for r in ok)} already consistent, "
        f"{sum(bool(r['mint'].get('called')) for r in ok)} needed the model"
    )
    if resumed:
        print(f"resumed: {resumed} document(s) already reconciled, skipped")
    if failed or len(ok) + resumed < len(files):
        print(f"FAILED or missing: {len(files) - len(ok) - resumed} of {len(files)}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
