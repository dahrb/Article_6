"""
new_repair.py
-------------
A simpler repair pass: one violation-driven loop, then one review call that
sees the document, then a final gate.

    stage 1+2   loop, max 3 rounds:  collect findings -> model -> apply
    stage 3     review: full graph + full case text -> additions only
    stage 4     final SHACL report

WHY THIS EXISTS ALONGSIDE repair_facts.py
------------------------------------------
`repair_facts.py` runs four TOPIC-SCOPED stages (authorities, proceedings,
persons, quotes), each with its own prompt, finder set, class scoping and
multi-pass loop. That scoping is the problem it cannot solve: a stage is shown
one region of the graph, so a defect whose fix spans two regions -- a duplicate
authority that also needs its proceedings rechained -- is invisible to every
stage individually. It also costs 4 stages x N passes model calls per document.

Here there is ONE finding list and ONE loop over the whole graph, so any patch
may touch anything, and the loop stops when the findings stop falling.

THE THREE DESIGN DECISIONS WORTH KNOWING
-----------------------------------------
1. UNCONSTRAINED GENERATION, schema in the prompt. Measured 2026-08-25 on
   o2_low_ttl L1: guided 144.3s over 9 calls, unconstrained 73.0s over 6, zero
   parse failures either way -- and on the persons findings guided proposed ONE
   op and stalled where unconstrained proposed 31 plus 2 merges. Under a vLLM
   grammar a stalled model can emit legal whitespace forever; without one it
   writes the patch. See repair_facts.call_repair_model and response_repair.py.

2. SHACL IS NOT ENOUGH ON ITS OWN. The shapes catch constraint violations well
   and absences badly: on the 2026-08-24 sweep 52 of 174 events carried no
   echr:hasParticipation and no shape fired, because "this node is missing a
   link" is only a violation where a minCount says so. Two more checks cannot
   be shapes at all -- duplicate entities need similarity, unverified quotes
   need the source text. So findings come from three sources, not one, and the
   loop treats them identically.

3. THE REVIEW STAGE MAY ADD EVIDENCE; THE LOOP MAY NOT. repair_facts.apply_patch
   refuses every `add` of echr:hasSupportingQuote, because a stage that sees
   only the graph is choosing a span for someone else's claim. The review stage
   is the one place that reads the document to find the fact, which is the
   whole justification for the ban -- so it is allowed to anchor, and required
   to: an added event with no verbatim quote is refused here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI
from rdflib import Graph

from art6.ontology.repair_facts import (
    RepairPatch,
    _stream_patch_text,
    _token_limit_kwargs,
    apply_patch,
    find_duplicate_candidates,
    find_missing_participation,
    find_proceedings_missing_court,
    find_shape_violations,
    find_unlinked_persons,
    find_unverified_quotes,
    parse_unconstrained_patch,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

MAX_ROUNDS = 3
MAX_MODEL_RETRIES = 2


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass
class Findings:
    """Everything wrong with a graph, from all three sources."""

    shacl: list[dict] = field(default_factory=list)
    missing_participation: list[str] = field(default_factory=list)
    missing_court: list[str] = field(default_factory=list)
    unlinked_persons: list[str] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    unverified_quotes: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return (
            len(self.shacl)
            + len(self.missing_participation)
            + len(self.missing_court)
            + len(self.unlinked_persons)
            + len(self.duplicates)
            + len(self.unverified_quotes)
        )

    def as_prompt_payload(self) -> dict:
        """EVERY finding, unbatched.

        The old pipeline cut each list at MAX_GAP_ENTRIES_PER_PASS to keep a
        single patch small enough to finish under guided decoding, and relied
        on the next pass to pick up the remainder. That trade is gone: this
        script generates unconstrained, so length is no longer the failure
        mode, and the loop has only three rounds rather than the old two passes
        per stage across four stages -- deferring a finding here now means it
        may never be seen at all. The model gets the whole graph and the whole
        violation set, and decides for itself what to fix first.
        """
        return {
            "shacl_violations": self.shacl,
            "events_missing_participation": self.missing_participation,
            "proceedings_missing_court": self.missing_court,
            "persons_not_linked_to_any_event": self.unlinked_persons,
            "possible_duplicate_entities": self.duplicates,
            "quotes_not_found_in_source": self.unverified_quotes,
        }


def collect_findings(graph: Graph, doc_ns: str, source_text: str | None) -> Findings:
    return Findings(
        shacl=find_shape_violations(graph),
        missing_participation=find_missing_participation(graph, doc_ns),
        missing_court=find_proceedings_missing_court(graph, doc_ns),
        unlinked_persons=find_unlinked_persons(graph, doc_ns),
        duplicates=find_duplicate_candidates(graph),
        unverified_quotes=(
            find_unverified_quotes(graph, source_text) if source_text else []
        ),
    )


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


def _schema_block() -> str:
    return json.dumps(RepairPatch.model_json_schema(), indent=1)


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _extraction_prompt() -> str:
    """The facts prompt the ORIGINAL extraction ran under, read live from the
    same file the pipeline snapshots, so it can never drift out of sync."""
    return (PROMPT_DIR / "facts.txt").read_text(encoding="utf-8")


def _full_ontology() -> str:
    """The whole of echr.ttl.

    Not load_ontology_fragment: the fragment exists for the loop's narrow
    stages, which are scoped to a handful of classes and would be misled by
    the rest. The review stage is doing the extraction task over again against
    the document, so it gets what the extraction got -- every class, every
    property, every closed-vocabulary member. A fragment here would mean the
    one pass that can add an event is the one pass that cannot see the full
    range of event classes available to it.
    """
    return (REPO_ROOT / "ontology" / "echr.ttl").read_text(encoding="utf-8")


# Every raw generation, in order, when capture is on. Keeping the text is the
# only way to answer "why did a pass with 26 findings propose one operation" --
# the audit trail records what was APPLIED, and a model that stalls after one
# op leaves no trace there at all.
RAW_RESPONSES: list[dict] = []
CAPTURE_RAW = False


def call_model(
    client: OpenAI,
    model: str,
    user_prompt: str,
    *,
    temperature: float,
    max_tokens: int,
    label: str = "",
) -> RepairPatch | None:
    """One patch, unconstrained, retried up to MAX_MODEL_RETRIES times.

    Returns None when nothing survives recovery -- a real outcome, and the
    caller treats it as "this round produced nothing" rather than an error.
    """
    token_kwargs = _token_limit_kwargs(model, max_tokens)
    for attempt in range(1, MAX_MODEL_RETRIES + 1):
        try:
            raw, finish_reason, stalled = _stream_patch_text(
                client,
                model,
                [
                    {"role": "system", "content": _prompt("new_repair_system.txt")},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                token_kwargs=token_kwargs,
                guided=False,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"      call failed ({type(exc).__name__}), attempt {attempt}")
            continue
        if CAPTURE_RAW:
            stripped = raw.strip()
            RAW_RESPONSES.append(
                {
                    "label": label,
                    "attempt": attempt,
                    "finish_reason": finish_reason,
                    "stalled": stalled,
                    "chars": len(raw),
                    "chars_nonspace": len(stripped),
                    "trailing_whitespace": len(raw) - len(raw.rstrip()),
                    "prompt_chars": len(user_prompt),
                    "raw": raw,
                }
            )
        patch = parse_unconstrained_patch(raw)
        if patch is not None and (patch.groups or patch.merges):
            if stalled or finish_reason == "length":
                print("      (recovered from a stalled/truncated generation)")
            return patch
        print(f"      no usable patch on attempt {attempt}")
    return None


# ---------------------------------------------------------------------------
# Stage 1+2: the violation loop
# ---------------------------------------------------------------------------


def run_loop(
    graph: Graph,
    *,
    client: OpenAI,
    model: str,
    doc_ns: str,
    source_text: str | None,
    temperature: float,
    max_tokens: int,
    max_rounds: int = MAX_ROUNDS,
    label: str = "loop",
) -> tuple[Graph, list[dict]]:
    audit: list[dict] = []
    previous = None
    previous_applied = None

    for rnd in range(1, max_rounds + 1):
        findings = collect_findings(graph, doc_ns, source_text)
        total = len(findings)
        if total == 0:
            print(f"    {label} round {rnd}: nothing left to fix - stopping")
            break
        # STOP ON A ROUND THAT DID NOTHING, not on a round whose finding count
        # failed to fall. The count-based rule was written for the stalled
        # swap -- a round that trades one violation for another and would
        # repeat the trade forever -- but it cannot tell that apart from real
        # work. Measured 2026-08-25 on o2_low_jsonld L1: round 2 applied 27
        # operations, netted 12 -> 13 findings, and the count rule killed
        # round 3. Deep repairs legitimately raise the count on the way down,
        # because splitting one conflated event into two correct ones creates
        # a node that is briefly missing its court and its parties.
        #
        # A round that applies zero operations, by contrast, has genuinely
        # nothing to say: the next round sees the same graph and the same
        # findings, so it would produce the same nothing. That is the only
        # state worth stopping for, and three rounds caps the rest.
        if previous_applied == 0:
            print(f"    {label} round {rnd - 1} applied nothing - stopping")
            break
        if previous is not None and total >= previous:
            print(
                f"    {label} round {rnd - 1}: findings {previous} -> {total} "
                f"but {previous_applied} op(s) applied - continuing"
            )
        previous = total

        payload = findings.as_prompt_payload()
        # Full ontology, full graph, full finding set. The loop cannot see the
        # document, so the ontology is the only thing telling it which classes
        # and vocabulary members exist -- and a fragment is what made the old
        # pipeline delete valid terms it had not been shown.
        prompt = (
            "ONTOLOGY\n"
            + _full_ontology()
            + "\n\nGRAPH\n"
            + graph.serialize(format="turtle")
            + "\n\nFINDINGS\n"
            + json.dumps(payload, indent=1, default=str)
            + "\n\nYOUR TASK\n"
            + _prompt("new_repair_loop.txt")
            + "\n\nOUTPUT SCHEMA\n"
            + _schema_block()
        )
        t0 = time.perf_counter()
        patch = call_model(
            client,
            model,
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            label=f"{label} round {rnd}",
        )
        if patch is None:
            print(f"    {label} round {rnd}: model returned nothing usable - stopping")
            break

        proposed = sum(len(g.ops) for g in patch.groups)
        # source_text is deliberately NOT passed: inside the loop the model
        # sees the graph only, and apply_patch mints no quote from scratch.
        #
        # unverified_quotes IS passed, and omitting it was a real bug. The
        # model names a bad quote by its index in the finding list rather than
        # retyping a hundred characters exactly; apply_patch resolves that
        # index against THIS list, so an empty list means every indexed remove
        # is refused as "quote_index N does not name a listed unverified
        # quote". Measured 2026-08-25 on o2_low_jsonld L1: the model correctly
        # asked to remove doc:lawyer_1's paraphrased quote in all three rounds
        # and was refused all three times, for want of the list it was reading
        # the index out of.
        graph, round_audit = apply_patch(
            graph,
            patch,
            doc_ns,
            unverified_quotes=findings.unverified_quotes,
            allow_quote_reuse=True,
        )
        applied = sum(1 for a in round_audit if a["status"].startswith("applied"))
        for a in round_audit:
            a["stage"] = label
            a["round"] = rnd
        audit.extend(round_audit)
        previous_applied = applied
        print(
            f"    {label} round {rnd}: {total} finding(s), {proposed} op(s) proposed, "
            f"{applied} applied [{time.perf_counter() - t0:.1f}s]"
        )
    return graph, audit


# ---------------------------------------------------------------------------
# Stage 3: review against the document
# ---------------------------------------------------------------------------

EVENT_CLASSES = frozenset(
    {
        "echr:DomesticProceeding",
        "echr:AdministrativeAction",
        "echr:EnforcementAction",
        "echr:ProsecutorialReview",
    }
)


def drop_unanchored_additions(patch: RepairPatch) -> tuple[RepairPatch, list[dict]]:
    """Remove any group that mints a domestic event without anchoring it,
    siting it, or peopling it.

    The review stage may add events BECAUSE it reads the document, and the
    price of that permission is evidence. Enforcing it per-operation is not
    enough: on the first trial (2026-08-25, o2_low_jsonld L1) the model
    correctly found two real missing events, supplied a verbatim quote for
    each, and the quote adds were refused by a ban that had no exemption --
    so the events landed UNANCHORED, which is worse than either outcome the
    design intended. apply_patch now takes allow_quote_adds for that, and this
    is the other half: if the quote does not survive for any reason, the event
    it was meant to anchor does not land either.

    Whole groups go, not individual ops, because an event's type, label, date
    and links are one indivisible addition -- dropping the type while keeping
    the date would leave a stub worse than nothing.

    COURT AND PARTICIPATION ARE REQUIRED ON THE SAME TERMS, for a reason that
    only showed up once the review stage started working. Measured 2026-08-25
    on o2_low_jsonld L1: the stage added doc:admin_action_occupational_1990
    with a class, a label, a date and a verbatim quote -- and no deciding
    authority and no party. Both are findings. The review stage runs LAST, so
    it is the one stage whose output the loop never sees: every gap it leaves
    is permanent. It was manufacturing exactly the defect class the loop in
    front of it exists to remove.

    Restating the requirement in the prompt did not fix it -- the stage was
    already given facts.txt verbatim, which spells out both obligations at
    length. So it is enforced here instead, where a missing court is a refusal
    rather than a suggestion.

    A participation counts only if it is WHOLE: the group must point the event
    at a participation node with `echr:hasParticipation`, and give that node
    both an `echr:participatingParty` and an `echr:hasPartySide`. A bare
    hasParticipation link to an empty node is the party-less-participation
    defect under a different name, and would satisfy a check that only looked
    for the link.
    """
    kept, rejected = [], []
    for group in patch.groups:
        minted = {
            op.subject.strip()
            for op in group.ops
            if op.action == "add"
            and op.predicate == "rdf:type"
            and op.object.strip() in EVENT_CLASSES
        }
        adds = [op for op in group.ops if op.action == "add"]

        def _subjects(predicate: str, adds: list = adds) -> set[str]:
            return {op.subject.strip() for op in adds if op.predicate == predicate}

        anchored = _subjects("echr:hasSupportingQuote")
        sited = _subjects("echr:hasCourt")

        # A participation is only real if the node it points at is complete.
        whole_participations = _subjects("echr:participatingParty") & _subjects(
            "echr:hasPartySide"
        )
        peopled = {
            op.subject.strip()
            for op in adds
            if op.predicate == "echr:hasParticipation"
            and op.object.strip() in whole_participations
        }

        missing: dict[str, list[str]] = {}
        for subject in sorted(minted):
            gaps = [
                name
                for name, satisfied in (
                    ("supporting quote", subject in anchored),
                    ("deciding court", subject in sited),
                    ("a complete participation", subject in peopled),
                )
                if not satisfied
            ]
            if gaps:
                missing[subject] = gaps

        if missing:
            detail = "; ".join(
                f"{subject} lacks {' and '.join(gaps)}"
                for subject, gaps in missing.items()
            )
            rejected.append(
                {
                    "stage": "review",
                    "action": "reject_group",
                    "finding": group.finding,
                    "rationale": group.rationale,
                    "subject": ", ".join(sorted(missing)),
                    "predicate": "",
                    "object": "",
                    "status": f"skipped: incomplete new event(s) - {detail}",
                }
            )
            continue
        kept.append(group)
    return RepairPatch(groups=kept, merges=patch.merges), rejected


def run_review(
    graph: Graph,
    *,
    client: OpenAI,
    model: str,
    doc_ns: str,
    source_text: str,
    temperature: float,
    max_tokens: int,
) -> tuple[Graph, list[dict]]:
    """One pass with the full graph and the full case text, for what the loop
    cannot see: events the extraction never captured at all.

    This is the only stage permitted to add an evidence anchor, and the only
    one that can add an event, because it is the only one reading the document.
    `source_text` is passed to apply_patch so every quote it adds is checked
    verbatim against the source before it lands.
    """
    # Given EXACTLY what the original extraction was given -- the same facts
    # prompt, the same full ontology, the same untruncated document -- plus the
    # graph that extraction produced. Anything less and this stage is judging
    # the extraction against a different brief than the one it was set.
    prompt = (
        "EXTRACTION INSTRUCTIONS\nThese are the instructions the graph below "
        "was extracted under. They bind you too.\n\n"
        + _extraction_prompt()
        + "\n\nONTOLOGY\n"
        + _full_ontology()
        + "\n\nSOURCE DOCUMENT\n<<<SOURCE\n"
        + source_text
        + "\nSOURCE\n\nGRAPH EXTRACTED FROM THAT DOCUMENT\n"
        + graph.serialize(format="turtle")
        + "\n\nYOUR TASK\n"
        + _prompt("new_repair_review.txt")
        + "\n\nOUTPUT SCHEMA\n"
        + _schema_block()
    )
    t0 = time.perf_counter()
    patch = call_model(
        client,
        model,
        prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        label="review",
    )
    if patch is None:
        print("    review: model returned nothing usable")
        return graph, []
    proposed = sum(len(g.ops) for g in patch.groups)
    patch, rejected = drop_unanchored_additions(patch)
    graph, audit = apply_patch(
        graph,
        patch,
        doc_ns,
        source_text=source_text,
        allow_quote_adds=True,
        unverified_quotes=find_unverified_quotes(graph, source_text),
    )
    applied = sum(1 for a in audit if a["status"].startswith("applied"))
    for a in audit:
        a["stage"] = "review"
    audit.extend(rejected)
    if rejected:
        print(f"    review: {len(rejected)} group(s) refused as incomplete")
    print(
        f"    review: {proposed} op(s) proposed, {applied} applied "
        f"[{time.perf_counter() - t0:.1f}s]"
    )
    return graph, audit


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def repair_document(
    facts_ttl: Path,
    *,
    client: OpenAI,
    model: str,
    source_text: str | None,
    temperature: float,
    max_tokens: int,
    max_rounds: int,
    skip_review: bool,
) -> dict:
    graph = Graph()
    graph.parse(facts_ttl, format="turtle")
    doc_ns = str(dict(graph.namespaces()).get("doc", ""))
    if not doc_ns:
        return {"file": facts_ttl.name, "error": "no doc: prefix in graph"}

    before = len(find_shape_violations(graph))
    print(f"  {facts_ttl.name}: {before} SHACL violation(s) in")

    graph, audit = run_loop(
        graph,
        client=client,
        model=model,
        doc_ns=doc_ns,
        source_text=source_text,
        temperature=temperature,
        max_tokens=max_tokens,
        max_rounds=max_rounds,
    )

    if source_text and not skip_review:
        graph, review_audit = run_review(
            graph,
            client=client,
            model=model,
            doc_ns=doc_ns,
            source_text=source_text,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        audit.extend(review_audit)

        # ONE MORE LOOP ROUND AFTER THE REVIEW, because the review is the one
        # stage whose output nothing else inspects. It adds events, and an
        # added event can break a shape -- an authority reused as a party
        # without the echr:Party type, a followsProceeding cycle, a date on the
        # wrong side of the one it follows. drop_unanchored_additions catches
        # the three gaps it can see structurally; SHACL catches the rest, and
        # before this there was no pass left to hand them to.
        remaining = collect_findings(graph, doc_ns, source_text)
        if len(remaining):
            print(f"    post-review: {len(remaining)} finding(s) left")
            graph, post_audit = run_loop(
                graph,
                client=client,
                model=model,
                doc_ns=doc_ns,
                source_text=source_text,
                temperature=temperature,
                max_tokens=max_tokens,
                max_rounds=1,
                label="post-review",
            )
            audit.extend(post_audit)
        else:
            print("    post-review: nothing left to fix")

    after = len(find_shape_violations(graph))
    backup = facts_ttl.parent / "backup"
    backup.mkdir(exist_ok=True)
    if not (backup / facts_ttl.name).exists():
        (backup / facts_ttl.name).write_text(
            facts_ttl.read_text(encoding="utf-8"), encoding="utf-8"
        )
    facts_ttl.write_text(graph.serialize(format="turtle"), encoding="utf-8")
    (facts_ttl.parent / (facts_ttl.stem + ".newrepair.json")).write_text(
        json.dumps({"operations": audit}, indent=1, default=str), encoding="utf-8"
    )
    print(f"  {facts_ttl.name}: {before} -> {after} SHACL violation(s)")
    return {
        "file": facts_ttl.name,
        "shacl_before": before,
        "shacl_after": after,
        "operations": len(audit),
        "applied": sum(1 for a in audit if a["status"].startswith("applied")),
    }


def load_source_texts(input_jsonl: Path | None) -> dict[str, str]:
    if not input_jsonl or not input_jsonl.exists():
        return {}
    out = {}
    for i, line in enumerate(input_jsonl.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            out[f"input.L{i}"] = json.loads(line).get("text", "")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--facts-dir", required=True, type=Path)
    ap.add_argument("--model", default="gemma-4-31b")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", "EMPTY"))
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--input-jsonl", type=Path, default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument(
        "--capture-raw",
        type=Path,
        default=None,
        help="write every raw model generation to this JSON file",
    )
    ap.add_argument(
        "--skip-review",
        action="store_true",
        help="stage 1+2 only -- useful for isolating the loop's own effect",
    )
    args = ap.parse_args()

    global CAPTURE_RAW
    CAPTURE_RAW = args.capture_raw is not None
    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=args.timeout)
    sources = load_source_texts(args.input_jsonl)
    files = sorted(args.facts_dir.glob("*.facts.ttl"))
    if not files:
        print(f"no *.facts.ttl under {args.facts_dir}", file=sys.stderr)
        raise SystemExit(1)

    print(f"model: {args.model} @ {args.base_url} (temperature {args.temperature})")
    print(f"{len(files)} document(s), max {args.max_rounds} loop round(s)\n")

    t0 = time.perf_counter()
    results = [
        repair_document(
            f,
            client=client,
            model=args.model,
            source_text=sources.get(f.name.removesuffix(".facts.ttl")),
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            max_rounds=args.max_rounds,
            skip_review=args.skip_review,
        )
        for f in files
    ]
    if args.capture_raw is not None:
        args.capture_raw.write_text(
            json.dumps(RAW_RESPONSES, indent=1), encoding="utf-8"
        )
        print(f"raw generations -> {args.capture_raw}")
    ok = [r for r in results if "error" not in r]
    print(
        f"\ntotal: {sum(r['shacl_before'] for r in ok)} -> "
        f"{sum(r['shacl_after'] for r in ok)} SHACL violation(s) over "
        f"{len(ok)} document(s) in {time.perf_counter() - t0:.1f}s"
    )


if __name__ == "__main__":
    main()
