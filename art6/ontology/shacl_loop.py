"""The SHACL violation loop: stage 3's only model-driven repair.

    loop, at most --max-rounds rounds:  collect findings -> model -> apply

Findings are what the graph alone decides: SHACL errors, disjoint-type
conflicts and duplicate nodes (collect_findings). The model sees the graph and
the ontology, never the judgment, and graph_patch.apply_patch refuses it any
new evidence (no added echr:hasSupportingQuote): a pass that cannot read the
document must not choose a span for someone else's claim. reconcile.py runs
the loop and re-applies stage 1's decisions after it, so the loop can clear a
violation but never overrule stage 1.

Generation is unconstrained with the patch schema in the prompt: measured
2026-08-25, a vLLM grammar made a stalled model emit whitespace forever, where
unconstrained it wrote the patch (graph_patch.call_repair_model,
json_closers.py).

History: this module also had a document-reading review (review_fix /
review_recall) and its own CLI as the old stage 3; both were removed on
2026-11-09, when reconcile.py became the only stage 3.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI
from rdflib import Graph

from art6.ontology.graph_patch import (
    RepairPatch,
    _stream_patch_text,
    _token_limit_kwargs,
    apply_patch,
    complete_entailed_types,
    find_disjoint_type_conflicts,
    find_duplicate_candidates,
    find_missing_participation,
    find_proceedings_missing_court,
    find_shape_violations,
    find_unchained_events,
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
    disjoint_conflicts: list[dict] = field(default_factory=list)
    missing_participation: list[str] = field(default_factory=list)
    missing_court: list[str] = field(default_factory=list)
    unlinked_persons: list[str] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    unverified_quotes: list[dict] = field(default_factory=list)
    unchained_events: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return (
            len(self.shacl)
            + len(self.disjoint_conflicts)
            + len(self.missing_participation)
            + len(self.missing_court)
            + len(self.unlinked_persons)
            + len(self.duplicates)
            + len(self.unverified_quotes)
        )

    # THE SPLIT THAT DECIDES WHICH STAGE SEES WHAT.
    #
    # A VIOLATION is fully determined by the graph: a date typed xsd:string, a
    # node with two labels, a link whose target lacks the required type, a
    # quote that is not in the source. Everything needed to fix it is visible
    # in the graph itself, so a model that has never read the document can fix
    # it correctly.
    #
    # An ABSENCE is not. "This event has no parties" does not say who the
    # parties were; that answer is in the document and nowhere else. Handing
    # an absence to the blind loop asks it to supply data it was given no
    # basis for, and it complies -- because the finding reads as an
    # instruction and the loop has no way to say "I cannot know this".
    #
    # Measured 2026-08-27 on the compressed L3: eight events_missing_
    # participation findings went into loop round 1 and eight participations
    # came out, every one naming doc:sdif -- the authority that DECIDED those
    # events -- as the participating party. Not scattered errors: one template
    # (doc:part_e4_sdif, doc:part_e5_sdif, ...) applied to every event that
    # body decided. The graph went from ONE court-as-party instance to EIGHT,
    # and the total finding count FELL, because clearing eight absences more
    # than paid for the eight violations it created. No aggregate metric
    # objected, because in aggregate terms the trade looked like progress.
    #
    # Structural bans in apply_patch stop that particular fabrication. They do
    # not stop the next one, because the incentive is unchanged: an absence is
    # a demand to add data, and the cheapest data is whatever is already
    # attached to the node. So absences no longer reach the blind loop at all.
    # They go to the review stage, which reads the document and can actually
    # answer them.
    # CHAIN AND PARTICIPATION TRAVEL TOGETHER, in ABSENCE_FIELDS.
    #
    # They are the same kind of thing and splitting them was incoherent: an
    # event with no parties and an event chained to nothing are both places
    # the graph records nothing, and in both cases the answer is in the
    # document or it does not exist. Both now go to the one stage that reads
    # the document, in the same list, and that stage may decline either.
    #
    # PARTICIPATION IS STILL NOT MANDATORY. Its absence is not a violation and
    # nothing treats it as one -- it is offered to the review stage as a place
    # to look, not a demand to fill. What made it safe to show at all is that
    # fabrication is now blocked STRUCTURALLY rather than by withholding the
    # finding: apply_patch refuses any participation without a supporting
    # quote, so a stage that cannot evidence a party cannot add one. Hiding
    # the gap was the crude version of that guarantee; the guard is the real
    # one, and with it in place hiding costs recall for nothing.
    #
    # `missing_participation` used to sit in ABSENCE_FIELDS and, before the
    # split, in the blind loop's payload. It is gone from both. An event with
    # no recorded participation is a COVERAGE GAP, not a defect: the graph is
    # not making a false statement, it is declining to make one.
    #
    # The reason is that we have no evidence channel that could satisfy the
    # demand honestly. Measured 2026-08-27 on the compressed L3: not one party
    # node in ANY condition -- raw extraction included -- carried a
    # echr:hasSupportingQuote. Quotes landed only on events. So "this event
    # must have a party" could only ever be met by a model choosing a party,
    # never by one reading a party, and a constraint that can only be
    # satisfied by guessing is a constraint that manufactures guesses.
    #
    # It is still counted and reported, as coverage. It is simply no longer
    # something any stage is asked to fix.
    # AN UNVERIFIED QUOTE IS AN ABSENCE, NOT A VIOLATION.
    #
    # It was in VIOLATION_FIELDS on the theory that "this text is not in the
    # source" is decidable from the graph plus the source string, which it is.
    # But the FIX is not: knowing a quote is wrong tells you nothing about what
    # the right one is, and the right one is in the document. Handing it to the
    # blind loop offers exactly one move -- delete the anchor and leave the
    # node unevidenced -- which trades a checkable-and-wrong claim for an
    # uncheckable one. The document-reading stage can do the thing actually
    # wanted: find the passage and correct the span.
    VIOLATION_FIELDS = (
        "shacl_errors",
        "disjoint_conflicts",
        "duplicates",
    )
    ABSENCE_FIELDS = (
        "unchained_events",
        "missing_participation",
        "missing_court",
        "unlinked_persons",
        "unverified_quotes",
    )

    @property
    def shacl_errors(self) -> list[dict]:
        """SHACL findings the blind loop can actually act on.

        find_shape_violations returns Violations and Warnings in one list.
        Warnings are advisory by construction -- the one that matters here is
        EvidenceAnchoringShape, "no supporting quote anchors this entity",
        which the blind loop is structurally incapable of fixing because it
        may not mint quotes. Feeding it eight of those (as adding
        echr:Participation to that shape's targets does on the compressed L3)
        spends rounds on operations that are refused on arrival.
        """
        return [v for v in self.shacl if v.get("severity") != "warning"]

    def count(self, scope: str = "all") -> int:
        fields = {
            "violations": self.VIOLATION_FIELDS,
            "absences": self.ABSENCE_FIELDS,
        }.get(scope, self.VIOLATION_FIELDS + self.ABSENCE_FIELDS)
        return sum(len(getattr(self, f)) for f in fields)

    def as_prompt_payload(self, scope: str = "all") -> dict:
        """Findings for one stage, unbatched.

        The old pipeline cut each list at MAX_GAP_ENTRIES_PER_PASS to keep a
        single patch small enough to finish under guided decoding, and relied
        on the next pass to pick up the remainder. That trade is gone: this
        script generates unconstrained, so length is no longer the failure
        mode, and the loop has only three rounds rather than the old two passes
        per stage across four stages -- deferring a finding here now means it
        may never be seen at all. So within a scope the model gets everything.

        `scope` selects WHICH findings, per the split documented above:
        "violations" for the blind loop, "absences" for the document-reading
        review, "all" for reporting.
        """
        violations = {
            "shacl_violations": self.shacl_errors,
            "logically_inconsistent_types": self.disjoint_conflicts,
            "possible_duplicate_entities": self.duplicates,
        }
        absences = {
            "events_chained_to_nothing": self.unchained_events,
            "events_missing_participation": self.missing_participation,
            "proceedings_missing_court": self.missing_court,
            "persons_not_linked_to_any_event": self.unlinked_persons,
            "quotes_not_found_in_source": self.unverified_quotes,
        }
        if scope == "violations":
            return violations
        if scope == "absences":
            return absences
        return {**violations, **absences}


def _finding_kinds(findings: Findings) -> dict[str, int]:
    """Per-kind finding counts, for the strict-improvement gate.

    SHACL violations are keyed by their MESSAGE rather than lumped into one
    "shacl" bucket, because the whole point of the gate is to notice a round
    that trades one shape's violations for another's. The message is used
    because find_shape_violations does not carry the source shape IRI through
    -- it returns kind/severity/message/subject/predicate/values -- and each
    shape in echr-shapes.ttl carries its own sh:message, so the text is a
    faithful stand-in for shape identity. Truncated because a couple of the
    messages interpolate the offending value.
    """
    kinds: dict[str, int] = {}
    for v in findings.shacl_errors:
        key = "shacl:" + str(v.get("message", ""))[:60].strip()
        kinds[key] = kinds.get(key, 0) + 1
    for name in (
        "disjoint_conflicts",
        "missing_court",
        "unchained_events",
        "unlinked_persons",
        "duplicates",
        "unverified_quotes",
    ):
        n = len(getattr(findings, name))
        if n:
            kinds[name] = n
    return kinds


def collect_findings(graph: Graph, doc_ns: str, source_text: str | None) -> Findings:
    return Findings(
        shacl=find_shape_violations(graph),
        disjoint_conflicts=find_disjoint_type_conflicts(graph),
        missing_participation=find_missing_participation(graph, doc_ns),
        missing_court=find_proceedings_missing_court(graph, doc_ns),
        unlinked_persons=find_unlinked_persons(graph, doc_ns),
        duplicates=find_duplicate_candidates(graph),
        unverified_quotes=(
            find_unverified_quotes(graph, source_text) if source_text else []
        ),
        unchained_events=find_unchained_events(graph, doc_ns),
    )


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


def _schema_block() -> str:
    return json.dumps(RepairPatch.model_json_schema(), indent=1)


def _prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


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
    return Path(
        os.environ.get("ART6_ONTOLOGY_TTL", REPO_ROOT / "ontology" / "echr.ttl")
    ).read_text(encoding="utf-8")


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
                    {"role": "system", "content": _prompt("shacl_loop_system.txt")},
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
        # VIOLATIONS ONLY. Absences are not this stage's business -- see the
        # split documented on Findings. A round that "fixed" an absence here
        # was inventing, not repairing.
        total = findings.count("violations")
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

        payload = findings.as_prompt_payload("violations")
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
            + _prompt("shacl_loop.txt")
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
        # QUOTES ARE NOT THIS STAGE'S TO TOUCH. allow_quote_reuse was True
        # here, permitting a quote to be MOVED to a new triple (never minted)
        # on the grounds that extraction chose it, so moving it invented
        # nothing. That reasoning does not survive contact with what a move
        # means: a span selected as evidence for one claim is being asserted
        # as evidence for a DIFFERENT claim, by a stage that cannot read the
        # document and so cannot know whether it supports the new one. The
        # text is genuine and the assertion is unchecked, which is the worst
        # combination -- it looks anchored and is not.
        #
        # A whole triple may still be DELETED here, quote triples included:
        # removing an assertion asserts nothing. Only writing and moving are
        # the document-reading stage's business.
        candidate, round_audit = apply_patch(
            graph,
            patch,
            doc_ns,
        )
        applied = sum(1 for a in round_audit if a["status"].startswith("applied"))

        # Complete entailed types on the CANDIDATE before comparing. Doing it
        # once at startup was not enough: any stage that adds a participation
        # adds it untyped, so the same reveal-looks-like-create artefact
        # returns on the very next round. Measured 2026-08-28 on the compressed
        # L3, iteration 4: startup completion ran, the review stage then added
        # nine untyped participations, and the post-review round was reverted
        # for "0 -> 6" defects it had merely exposed by typing them.
        complete_entailed_types(candidate, doc_ns)

        # REGRESSION WARNING, not a revert.
        #
        # This began as a gate that discarded any round introducing a new kind
        # of defect. It fired three times, and all three were the SAME false
        # positive: the round was adding rdf:type to untyped participation
        # nodes -- correct, it is the entailment -- which made SHACL start
        # checking them and exposed party-less stubs that were already in the
        # extraction output. It read "newly visible" as "newly created" and
        # reverted correct work, re-hiding the defects. Completing entailed
        # types before every comparison removed the artefact, and with it gone
        # the gate stopped firing entirely (2026-08-28, compressed L3 + L9).
        #
        # So there is no evidence it ever caught a real regression, and a
        # strong reason not to keep the revert: it throws away a whole round,
        # including everything that round got right, on one bad operation. The
        # structural bans in apply_patch do the job properly -- they refuse the
        # single offending operation and let the rest land.
        #
        # The comparison is kept as a WARNING because it costs no model call
        # and a regression it cannot currently produce is still worth seeing if
        # one ever appears.
        after_findings = collect_findings(candidate, doc_ns, source_text)
        regressions = {
            kind: (_finding_kinds(findings).get(kind, 0), count)
            for kind, count in _finding_kinds(after_findings).items()
            if count > _finding_kinds(findings).get(kind, 0)
        }
        if regressions:
            detail = ", ".join(
                f"{kind} {was}->{now}"
                for kind, (was, now) in sorted(regressions.items())
            )
            print(f"    {label} round {rnd}: WARNING, new defect kind ({detail})")

        graph = candidate
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
