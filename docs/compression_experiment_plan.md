# A verifiable pipeline for extracting ECtHR domestic proceedings

Draft experiment design. Supersedes the O1-vs-O2 framing in
`docs/llm_judge_spotcheck_o1_vs_o2.md`, which measured the wrong thing for the
claim it was trying to support.

**Primary contribution: a pipeline whose every output triple is mechanically
traceable to a character span in the source judgment.** The ontology-placement
result below is the evidence for how that pipeline should be built, not the
contribution itself.

Verifiability is what makes the released dataset usable as a legal resource: a
consumer can check any assertion against the judgment without re-running a model.
It is also the discipline that keeps the evaluation honest, because a triple that
cannot be traced is a triple that cannot be defended.

## Thesis

Current practice puts the full ontology in front of the model that reads the
document. The pilot evidence says that is the wrong place.

- The matched-assembly arm (`o2_large_jsonld`: whole document, one call, 1,409-triple
  ontology in the prompt) recovered **56%** of reference events, the worst of any
  condition, and 42% on the longest document.
- The schema-light baseline (`o1_gemma`), doing only the reading task, recovered 71%
  and scored highest on every precision axis (hallucination 4.50 vs the best O2's 3.50).
- But O1 produced **21 distinct `instance_level` surface forms and 21 party-role forms
  across 10 documents**, against the ontology's closed vocabularies of 8 and 4 — with
  zero closed-vocabulary violations in any O2 arm.

Read together these do not say "the ontology does not help". They say the ontology
is being asked to do a job it is bad at (reading) at the same time as the job it is
good at (normalising). **O1 is not a competitor to the ontology pipeline; it is its
missing first stage.**

**H0 (the claim under test):** moving ontology instantiation *downstream* of a
schema-light extraction stage recovers O1's precision and recall while keeping the
ontology's vocabulary control.

## Pipeline under test

```
          full judgment text
                  |
        [1] EVIDENCE SELECTION  (extractive; NO classification)
                  |   verbatim spans + character offsets, grouped into event bundles
                  v
        [2] ONTOLOGY MAPPING  (OntoCast; sees the bundles, NOT the raw document)
                  |   all classification happens here, against closed vocabularies
                  v
        [3] REPAIR LOOP  (SHACL findings -> patch; graph only)
                  |
                  v
        [4] REVIEW  (sees full document again -- the recall backstop)
```

### Stage 1 is extractive, not generative, and not O1

Three candidate designs were considered for the first stage:

| design | verifiable? | why rejected / chosen |
|---|---|---|
| free-text guided summarisation | **no** | A summary sentence cannot be mechanically traced to the source. Putting an unverifiable step at the head of a pipeline whose central claim is verifiability is the first thing a reviewer will attack. It also destroys event boundaries, so compression loss becomes unmeasurable and segmentation is merely deferred to stage 2. |
| O1's rich schema | partially | Asks the reading model to classify (`instance_level`, party role) as well as read. That is where the 21 uncontrolled surface forms come from: classification without a closed vocabulary in view. |
| **extractive span bundles** | **yes, by construction** | **Chosen.** |

Stage 1 emits, per candidate event, a bundle of **verbatim spans with character
offsets**: the decision span, the date span, the deciding-body span, the party
spans, and any span indicating what the event follows.

It performs **no classification**. No instance level, no party side, no proceeding
type, no outcome direction. Every one of those is a closed-vocabulary decision and
belongs in stage 2, where the vocabulary is actually in the prompt.

Two consequences, both load-bearing:

1. **Traceability is structural, not re-derived.** Because offsets travel with the
   spans, `graph triple -> source character range` is a lookup, not a string search.
   The 449 non-verbatim quotes in the pilot were possible only because anchoring was
   re-checked after the fact instead of carried through.
2. **Stage 2 cannot hallucinate content.** It never sees the raw document, so it can
   only mis-structure what it was given. That converts an undetectable error class
   (fabricated facts) into a detectable one (structural violation) -- which is the
   architectural argument for the whole design.

Stage 4 is retained deliberately: it is the only defence against stage 1 becoming a
hard recall ceiling, and it demonstrably adds real, quote-anchored events today.

## Conditions

Assembly held constant (single call per document, matching O1) so the comparison is
not confounded by chunking, as the current one is.

| id | stage 1 | stage 2 | repair | purpose |
|---|---|---|---|---|
| **A** | — | ontology, direct from text | no | current practice, unrepaired |
| **B** | — | ontology, direct from text | yes | current practice (= today's LRG) |
| **C** | evidence selection | ontology from bundles | no | **does staged reading alone help?** |
| **D** | evidence selection | ontology from bundles | yes | **the proposed pipeline** |
| **E** | O1 rich schema, alone | — | — | precision ceiling + vocabulary contrast (the pilot baseline) |
| **F** | — | ontology, direct from text | yes, on **O1 output** | the reviewer's control (see below) |

**Condition F is the control a reviewer will demand**: repair applied to the
schema-light output. Most of what repair fixes (verbatim quotes, date typing, dangling
references) is format-independent, so "formalisation enables repair" is untestable
without it. It is cheap — a JSON validator and a repair prompt.

The 2x2 over {A,B} x {C,D} answers three separate questions instead of confounding
them: what compression buys, what repair buys, and whether they interact.

**H1:** D > B on recall and hallucination. **H2:** C > B even without repair — i.e.
compression substitutes for some repair. **H3 (falsifier):** if C ≈ A, compression is
doing nothing and the idea is dead.

## Metrics

Three families, kept strictly separate. The critical discipline:

> **No metric that repair optimises may be reported as a quality result.**

The current pilot violates this — it optimises SHACL and then reports SHACL
conformance (453 -> 353). That is measuring the thermometer. Today's disjointness
finding was caught *only* because `owl:AllDisjointClasses` was never a repair target.

### Tier 1 — automated, every document, zero marginal cost

**Repair targets (diagnostic only, never a headline):** SHACL conformance, quote
verbatim rate, participation completeness, multi-court/date violations.

**Held out from repair (these are the reportable structural results):**
- OWL disjointness contradictions
- chain acyclicity, dangling `followsProceeding`
- events with no label and no quote ("stub" rate)
- **vocabulary dispersion**: distinct surface forms per closed-vocabulary slot
- **compression loss**: reference events present in stage 1 but absent after stage 2

Vocabulary dispersion is the headline aggregability number and needs no annotation.

### Tier 2 — LLM-as-judge, ~30 documents, blinded

Fixes to the pilot's judge protocol, all of which a reviewer would otherwise raise:

- **Blinded.** Condition labels stripped, outputs converted to a common rendering,
  arm order shuffled per document. The pilot was unblinded and its author had built
  the repair tool.
- **Two judges from different model families**, agreement reported (Krippendorff's
  alpha). Gate: if agreement is poor, the judge tier is demoted to descriptive.
- **Rebalanced rubric.** The pilot ran three precision axes against one recall axis,
  which systematically favours whichever system extracts least. Score recall and
  precision as separate reported quantities; do not average them into one number.

### Tier 3 — human, same rubric as the judge

**The human tier scores the identical instrument as Tier 2**: the same four axes,
the same 0-5 scale, the same anchors. This is deliberate and it is what makes the
tier worth running -- agreement with the LLM judge becomes directly computable,
which is the gate your plan's H7 already depends on. A different instrument
(pooling, adjudication, span marking) would give better precision estimates and no
way to validate the judge at all.

**Scope: 8 documents x 3 conditions = 24 scored outputs.**

Conditions carried into the human tier are the three that decide the paper --
**B** (current practice), **D** (proposed pipeline), **E** (schema-light baseline).
A, C and F stay in Tiers 1-2; they are ablations and do not need human scoring.

**Protocol, per document:**

1. Read the judgment once and note the procedural history. This dominates the cost,
   so it is paid once per document, not once per condition.
2. Score all three outputs for that document against the rubric, **blinded**:
   condition labels stripped, all three rendered into one common format, order
   shuffled per document by a fixed seed.
3. Record four integers per output, plus a free-text note for anything the scale
   does not capture.

**Effort:** ~15 min reading + ~3 x 4 min scoring = **~27 min per document**,
**~3.5 hours total.** One CSV, one row per (document, condition), resumable.

**Blinding must be mechanical, not intentional.** The renderer strips IRIs and
namespace prefixes -- `doc:actionGuardianAppointment` and
`doc:admin_action_1` are condition fingerprints -- and emits a plain ordered
timeline. The seed and the shuffle map are written to a file that is not opened
until scoring is finished.

**What this tier reports:**
- per-axis human scores by condition
- **human-judge agreement**: Krippendorff's alpha on the 96 paired scores, plus
  per-axis exact and within-1 agreement
- the gate: if agreement is poor, Tier 2 is demoted to descriptive across the whole
  paper, exactly as `jurix_plan.md` already provides for

**Precision for the dataset claim** comes from Tier 1 -- verbatim-anchoring rate is
mechanical and needs no annotator, and under the stage-1 offset design it is
checkable for 100% of triples rather than sampled. The rubric's hallucination axis
is the human-judged precision signal; it is coarser, and the paper should say so
rather than dress a Likert mean as a precision figure.

## What would falsify the thesis

- C ≈ A on recall and precision → compression contributes nothing (H3).
- D's compression loss is large → the pipeline bottleneck is real and stage 4 does
  not compensate.
- F ≈ D → the gains came from repair, not from formalisation placement, and the
  ontology's contribution is limited to vocabulary control.

The third is a live possibility and the paper should be written so that it remains
publishable if it obtains: "formalisation's measurable contribution is aggregability,
not extraction quality" is a legitimate and useful finding, and your plan already
pre-registers the unflattering direction under H2.

## Sequencing

1. Vocabulary-dispersion figure across all existing arms — from data already on disk,
   no new runs. Confirms the premise before anything is built.
2. Build stage 1 -> stage 2 handoff; run C and D on the 10-document pilot.
3. Gate: if C ≈ A, stop and rewrite the claim rather than scaling.
4. Condition F.
5. Scale Tier 1 to the full corpus; Tier 2 on ~30; Tier 3 on 6.

## Known limitations to state up front

- Two-stage extract-then-normalise is not architecturally novel. The contribution is
  the decomposition and the measurement — *where* the ontology should enter — not the
  architecture.
- Adding a stage adds an error rate; compression loss is reported as a first-class
  metric, not a footnote.
- Single annotator on Tier 3, who is also an author. Blinding is what makes this
  tolerable; it must be enforced mechanically, not by intention.
- Tier 3 covers 8 documents and 3 of 6 conditions. It validates the judge; it does
  not independently establish the ranking, and should not be reported as if it did.
- Likert axes give a precision *signal*, not a precision *rate*. Any per-triple
  precision figure in the dataset release must come from Tier 1 anchoring, not from
  the rubric.
