# JURIX — experiment plan (v2)

**Scope:** conditions, contrasts, sample, runs. The evaluation protocol is a stub
(§6) and is designed separately.

> **The paper, in one sentence:** ontology-guided extraction produces graphs that
> validate cleanly and still contain claims the source does not support; we add an
> extractive evidence stage that makes an unverifiable span structurally impossible,
> and a repair stage whose structural fixes are deterministic rather than
> model-proposed, and we measure what each contributes.

---

## 1 · Conditions

One model throughout: **gemma-4-31b**. Five arms, on **English judgments**.

| # | condition | stage 1 | stage 2 | stage 3 |
|---|---|:-:|:-:|:-:|
| **C0** | O1 — schema-light JSON, one call, no ontology | — | — | — |
| **C1** | **OntoCast alone** — raw judgment → graph | — | ✓ | — |
| **C2** | OntoCast + repair | — | ✓ | ✓ |
| **C3** | compressed → graph | ✓ | ✓ | — |
| **C4** | **the full pipeline** | ✓ | ✓ | ✓ |

Stage 1 is the extractive evidence pass (verbatim spans with offsets computed
programmatically, plus the parties second pass). Stage 2 is OntoCast in
`render_mode=facts` against a fixed ontology. Stage 3 is the SHACL-driven repair
loop with its deterministic structural rewrites, followed by one review call.

**Stage 3 is identical in C2 and C4, and its review call reads the JUDGMENT in
both** — never stage 1's digest, even on the arm whose stage 2 ran on the
digest. Only stage 2's input differs between arms; that is what the compression
contrast is measuring, and holding stage 3 constant is what makes C2 vs C4 a
comparison of compression rather than of two different repair stages. Until
2026-09-06 the compressed arm passed `bundles.jsonl` to stage 3 as well, so C4's
review re-checked the digest against itself: it could re-anchor and re-chain
what compression kept, but a proceeding compression had dropped was invisible to
it, by construction. Observed on 001-68183 (Mamatkulov and Askarov v. Turkey):
the Uzbek Supreme Court conviction is in the judgment, absent from the digest,
recovered by review in C2 and missing from C4 entirely. Runs before that date
understate C4 event recall by whatever stage 1 dropped and review would have
put back; they are not comparable with later runs on that measure.

### The four contrasts

| contrast | isolates | claim it licenses |
|---|---|---|
| **C1 vs C4** | the pipeline as a whole | *"the pipeline beats running OntoCast"* — **the headline** |
| C1 vs C3, C2 vs C4 | compression | what the evidence stage buys |
| C1 vs C2, C3 vs C4 | repair | what the repair stage buys |
| C0 vs C1 | the ontology | *"does formalisation earn its keep"* — matched: both one call, whole document |

**C1 is load-bearing and must not be cut.** It is the only arm that is "just
OntoCast"; without it every claim about the pipeline is a claim about OntoCast
wearing our label. Compression × repair is a clean 2×2, so each main effect is
estimated twice and the interaction is available. C0 sits outside it.

### Bonus experiment — decisions

**C4 only, no ablation**, on a separate sample of ~60 English *decisions*, scored
by the same protocol. Reported as: the pipeline transfers to a document type it
was not developed on. Not part of the main design, and not mixed into the
judgment sample — decisions have a different section structure
(`text_processing.py` routes `PROCEDURE` into `facts` for decisions but not for
judgments), so pooling them would mean the extraction target is not the same
document shape across the sample.

### Possible extension — French

**C4 only, no ablation**, on a sample of French judgments, scored by the same
protocol. Reported as: the pipeline transfers to the Court's other official
language. Run it only if the judgment and decision results are in and there is
room; it is an extension, not a dependency.

**It is a genuine generalisation test, not a translation test.** The obvious
objection is that French judgments are the same cases in another language, so
extraction would only be re-solving problems already seen. Measured over the
post-2000 frame, that is not what the corpus contains: of 5,375 distinct French
cases, **3** also have an English version. The French set is 5,372 cases the
English pipeline has never encountered, in a language whose prompts were never
tuned for it. Two axes move at once — unseen cases *and* unseen language — so a
drop cannot be attributed to either alone. State that limit rather than claiming
a clean language ablation.

| | French, post-2000 |
|---|---:|
| judgments with usable narrative | 5,383 |
| median chars | 4,986 |
| p90 chars | 13,844 |
| max chars | 92,128 |

**The design cannot be the same.** French has effectively no Grand Chamber
population: **1** post-2000 judgment, against 4,746 Chamber and 636 Committee.
Even allocation across levels is impossible, so this sample is **Chamber and
Committee only, evenly split, proportional over time within level** — and the
comparison against the English results must be restricted to those two levels
rather than run against the English total, which is a third Grand Chamber and
therefore much longer. The French frame is also shorter throughout (median 4,986
against 5,455; max 92,128 against 424,646), so no document needs splitting.

**Size: 50**, drawn — enough to detect a substantial drop, not so many that a
negative result consumes the budget for the main experiments.

| court level | 2000-2009 | 2010-2019 | 2020+ | total |
|---|---:|---:|---:|---:|
| CHAMBER | 20 | 4 | 1 | 25 |
| COMMITTEE | 0 | 17 | 8 | 25 |
| **total** | **20** | **21** | **9** | **50** |

Grand Chamber is excluded by the sampler and the exclusion printed: one document
cannot be stratified. 12 respondent states, against 40 in the English judgment
sample — a narrower spread that follows from which states litigate in French, and
another reason to read this as an extension rather than a matched comparison.

---

## 2 · Sample

Frame: **English judgments from 2000 onwards, 9,270.** Nothing is excluded for
length or for anything but language, date and tuning membership.

| court level | n | median chars |
|---|---:|---:|
| CHAMBER | 6,046 | 6,881 |
| COMMITTEE | 3,078 | 2,463 |
| GRANDCHAMBER | 146 | 21,968 |

**Why 2000 onwards.** Committees had no merits competence until Protocol 14bis
(1 October 2009); before that they only rejected applications, and those
rejections are a different document type. A longer frame cannot be balanced
across the three formations.

### Size and stratification

**250 judgments, even across court level, stratified over time within level**
(`build_evaluation_sample.py`, scikit-learn `train_test_split`, seed 20260831).

| court level | 2000-2009 | 2010-2019 | 2020+ | total | of frame |
|---|---:|---:|---:|---:|---:|
| CHAMBER | 54 | 24 | 6 | 84 | 1.4% |
| COMMITTEE | 0 | 39 | 44 | 83 | 2.7% |
| GRANDCHAMBER | 48 | 27 | 8 | 83 | 56.8% |
| **total** | **102** | **90** | **58** | **250** | |

Even across levels because a proportional draw gives ~4 Grand Chamber judgments;
proportional over time within a level because the levels do not span the same
years.

**COMMITTEE × 2000-2009 is empty by construction** — the corpus holds two such
judgments, both v. Germany under 14bis. Court level is therefore confounded with
period: read any Committee-versus-Chamber contrast within period, not across it.

**Grand Chamber is 57% sampled against Chamber's 1.4%.** Equal power per
formation is the point, but it makes the sample longer than the corpus (median
7,584 against 5,455), so per-document cost and accuracy figures are pessimistic
relative to a random draw. Apply design weights to any population estimate.

### The same case at two levels

The sample is **not deduplicated by case**, deliberately. A case decided by a
Chamber and then referred to the Grand Chamber appears twice under the same
application numbers, describing the same domestic proceedings 1-2 years apart;
merits and just-satisfaction judgments pair the same way. In the post-2000
English frame there are 76 such pairs, 66 of them CHAMBER + GRANDCHAMBER — which
is **45% of the entire Grand Chamber stratum**, so the two levels are not
independent case populations.

Keeping both is a measurement, not a contaminant: whether the pipeline recovers
the same procedural chain from two independent accounts of it is a property worth
reporting. The drawn sample contains two such pairs:

| case | Chamber | Grand Chamber |
|---|---|---|
| APICELLA v. ITALY | 001-67420 (2004) | 001-72935 (2006) |
| SVINARENKO AND SLYADNEV v. RUSSIA | 001-115176 (2012) | 001-145817 (2014) |

Every document carries `case_group` (joined application numbers) so pairs are
identifiable after extraction. Report them as an agreement check, and exclude one
member of each pair from any statistic that assumes independent documents.

**Tuning documents.** 20 documents were used to develop prompts and settle
configuration and are excluded (`data/art6_excluded_case_ids.json`), verified by
document id and by case identity so a tuning case cannot re-enter through its
other formation. One sentence in Methods, no separate results.

### Context

Stage 1 sees the compress prompt (~6,100 tokens) and the document; the ontology
is stage 2's cost, and stage 2 is fed compressed spans. No document in the drawn
sample approaches the 98,304 window (max 119,748 chars ≈ 30,000 tokens).

One judgment in the corpus does — *Ukraine v. Russia (re Crimea)*, 102,692 prompt
tokens. It is not in the current draw, but the pipeline handles it: halved at the
paragraph boundary nearest the midpoint, extracted in independent passes,
concatenated for stage 2, with spans located in the whole document so offsets
stay global. Parts are sized so the prompt *and* a 32,000-token output budget fit
— sizing for the prompt alone truncated a half mid-JSON at its 73rd event. A
`follows` link crossing the seam is dropped rather than guessed.

---

## 3 · What each arm produces

Every arm is scored at both checkpoints it has — `raw/` (extraction) and
`repaired/` — because recall is a property of the first and precision of the
second, and one number after both cannot separate them.

Automated measurement falls in three tiers; the metric list and the judge/human
protocol are §6.

- **Evidence integrity** — share of supporting quotes appearing verbatim in the
  source. C3/C4 are ~100% *by construction*: an unlocatable span is dropped, not
  passed through. C0/C1/C2 have no such guarantee. The strongest result the
  design can produce, because it is a structural property rather than a score.
  **"The source" means the judgment, for every arm.** From 2026-09-06 stage 3
  verifies C4's quotes against the judgment rather than against the digest they
  were copied out of, which is what the claim has always asserted and what a
  reviewer will assume it means. Stage 1's spans are verbatim from the judgment
  with computed offsets, so the guarantee is unchanged; what the stricter check
  can now catch is a quote assembled out of the digest's own scaffolding
  (`EVENT e3`, `what_happened:`), which verified against the digest and is not
  in the judgment at all. Any such quote was previously counted as evidenced.
- **Structural validity** — SHACL conformance, closed-vocabulary adherence,
  deciding-body-as-party, participation cardinality, disjoint-type conflicts.
- **Content** — proceedings recovered, `followsProceeding` chain edges (the
  primary outcome), party coverage, attribute coverage.

**Report the metrics the baseline wins.** On the tuning documents O1 recovered
two parties on 52% of events against the pipeline's 33% — while 20 of its party
entries named the body that decided the very proceeding it was a party to.
Publishing a loss, and its cause, is the strongest answer to "your baseline is a
strawman".


## 4 · Evaluation — STUB

To be designed separately, except §4.1, which is settled.

### 4.1 Content is measured at two levels

Event-level and graph-level answer different questions. **Both are reported**;
neither substitutes for the other.

**Event-level — what was extracted, and at what granularity.** `event_exists`
(P/R/F1) plus per-field accuracy over matched events. This is the granularity
measure: it says whether the pipeline found the right units at all, and it is the
denominator for everything below. It stays the primary *content* outcome.

**Graph-level — whether the recovered topology is usable downstream.** The
research question the corpus exists to serve is topological: how procedural
structure relates to Convention outcomes, and whether structure can model
exhaustion of domestic remedies. Edge-level `chain_edge` F1 is necessary but not
sufficient for that. Two extractions can post near-identical edge F1 and still
disagree on chain count, depth and which event terminates the chain — precisely
the features the downstream would regress on. A validation paper that reports
only triple-level agreement has not validated the artefact for its purpose.

| measure | definition | what it catches that edge F1 does not |
|---|---|---|
| **chain count** | weakly-connected components over `followsProceeding` | merging two independent chains into one, or splitting one into two — barely penalised edge-wise, fatal downstream |
| **chain depth** | longest directed path per chain | the primary topological feature; a systematically shallow extraction is unusable even at high edge F1 |
| **instance profile** | multiset of `hasInstanceLevel` along each path | whether first-instance → appeal → cassation shape is preserved |
| **remittal count** | events with `outcome: Remitted` on a path | cycles back to a lower instance — the structural signature most likely to matter for delay and exhaustion |
| **terminal identity** | does each chain end on the event carrying `isFinalDomesticDecision` | load-bearing for exhaustion; see below |
| **orphan rate** | events with `follows: null` that are not annotated chain heads | decomposition failure — events recovered but not connected |

Report each as **per-document agreement** (exact match for counts and identity,
absolute error for depth) and as **distributional agreement** across the corpus,
since the downstream consumes the distribution, not any single document.

**`isFinalDomesticDecision` is measured separately, not folded into a general
field-accuracy figure.** It is the single most load-bearing field for the
exhaustion question and it is a known judgement call — 001-61054 id 20 carries a
note proposing its own deletion and the flag's reassignment to id 19. A field
that is both pivotal and contestable needs its own reliability number.

**FLAG — the annotation guide and the repair pipeline disagree on cardinality.**
`annotation_guide.txt` §5 is explicit: `final_domestic_decision` is ONE PER
CHAIN, not one per document — a judgment with several independent chains gets
several flags (001-61054 correctly carries three: ids 5, 19 and 21, one for
each of its independent return-order, enforcement and custody chains, matching
how Article 35 exhaustion is actually assessed per complaint/track). But
`repair_facts.py`'s `find_final_decision_conflicts` flags more than one
`isFinalDomesticDecision: true` **anywhere in the same document** as a conflict
needing repair, with no chain-awareness — it would misfire on exactly this
document. The ontology's own definition ("the domestic event that concluded
the domestic remedies") is prose-singular but not a formal OWL cardinality
constraint, so it doesn't settle the question either way. Needs a decision
before the repair pipeline runs on documents with legitimate parallel chains:
either scope the conflict check to within a `followsProceeding` chain, or
revisit whether one-per-chain is really the intended semantics.

**These are computed exactly against the annotated set — no judge is involved.**
The judge is only required where there is no reference graph, i.e. the remainder
of the 250.

**Event counts are reported against a stated `guide_version`.** Applying §3.1 of
the annotation guide — a narrated filing and its narrated decision are two events
— moved the annotated corpus from 180 to 224 events, 24%, without a single
document being re-read for content. Any event count not qualified by the guide
version it was produced under is uninterpretable, and the sensitivity is worth
reporting in its own right as a measurement of how much a unit-of-analysis
decision moves the target.

### 4.2 Sequencing decision — judge over one run now, run-variance later

**Decided:** the LLM-judge pass runs once, over the single extraction run per
arm (§1), not repeated across extraction runs. Auto-metric variance (§5.2) is
computed separately and later, as budget allows, over the graph/content measures
in §4.1 — which need no judge and no extra extraction cost beyond the repeat
runs themselves.

This is only sound if the single judge run is validated, not merely assumed
representative:

- **Judge validity is not optional under this plan.** Run the judge against the
  annotated set (§4.1's exact reference) and report judge–human agreement. That
  is what lets a single judge pass on the remaining ~230 documents carry a stated
  error rate instead of an unstated one.
- **Judge stability** (§5.2) — the same fixed extraction output scored 3× —
  should still be squeezed in if budget allows: it isolates judge noise from
  extraction noise at the cost of extra judge calls only, and it is what
  separates "the judge disagreed with itself" from "the arms genuinely differ" in
  any borderline result.
- **Extraction run-variance is deferred**, to be picked up on the graph/content
  measures in §4.1 rather than routed through the judge — cheaper, and it is
  where the actual measured example (§5.2) already showed the interesting
  divergence: structurally identical outputs across runs with body-name
  agreement as low as 33%.

### 4.3 Still open

- Automated metric definitions per tier, and which are comparative across all
  five arms versus descriptive of the O2 artefact only.
- Hand-built reference graphs: how many, drawn how, built blind to output.
- LLM-as-judge: which models, on what rendering. Note a judge cannot be blinded
  to a manipulation visible in the output — the compression contrast is visible
  on sight — so blinding is unavailable for some contrasts and judge–human
  agreement is the control that matters.
- Statistics: paired per-document differences, win/tie/loss, Wilcoxon. **Note
  that document-level variation is captured by the Wilcoxon and run-level
  variation is not** — see §5.2, deferred per §4.2.

---

## 5 · Open decisions

1. **Does compression lose proceedings?** The C1 vs C3 contrast is where a
   reviewer will look for the cost of the two-tier design. Measure event recall
   explicitly and report it even if negative: losing events to gain a verbatim
   guarantee is a defensible trade *stated*, and an indefensible omission if
   hidden.

   The contrast to report it on is **C1 vs C3** — both unrepaired, so it
   isolates what stage 1 drops. **C2 vs C4 now measures something different**:
   compression loss *net of* what review puts back, because from 2026-09-06
   review reads the judgment on both arms (§1) and can therefore recover a
   proceeding the digest omitted. Both numbers are worth reporting and they
   answer different questions — C1 vs C3 is "what does compression cost", C2 vs
   C4 is "what does it cost that the pipeline does not recover" — but they must
   not be presented as the same measurement, and the second is not comparable
   with runs made before that date.

### 5.2 Run variance — how many runs per arm

**Measured, on the holdout set (10 docs).** `ablation_holdout` and
`ablation_holdout_mv2` are the same configuration run four hours apart —
identical prompt snapshots, ontology, shapes, input, model and temperatures —
so they are a genuine repeat.

| | stage 1 (t=0.0) | C1 raw (t=0.4) | C4 repaired (t=0.4) |
|---|---|---|---|
| byte-identical docs | **10 / 10** | 0 / 10 | 0 / 10 |
| events | — | 48 → 49, 1 doc differs | 59 → 61, 1 doc differs |
| chain edges | — | 28 → 30, 2 docs | 48 → 49, 1 doc |
| chain count | — | 20 → 19, 3 docs | 13 → 14, 1 doc |
| **chain depth** | — | 33 → 36; L2 went **3 → 6** | **50 → 50, identical on all 10** |
| parties | — | 24 → 22; L1 went **6 → 2** | 33 → 38, 3 docs |

**Stage 1 is reproducible.** An earlier measurement (28 Aug) found greedy
decoding non-reproducible under vLLM batching; that no longer holds as of the
30–31 Aug runs. Do not cite the older figure.

**Stages 2–3 are stochastic by design** — `temperature_stage2_3 = 0.4` — so
variance there is expected, and the question is only whether it is small enough
to leave the contrasts standing. On this evidence it largely is, with two
qualifications that shape the run budget:

- **C4 topology is stable; C1 topology is not.** Chain depth is identical across
  runs on every C4 document, while C1 moved on two, one of them doubling. Since
  C1 is the comparator for the headline contrast, the noisier arm sets the error
  bar. **This asymmetry is itself reportable**: the pipeline extracts more
  *stably*, not merely more accurately, and stability is a property a downstream
  consumer of the corpus cares about directly.
- **Parties is the least reproducible dimension in both arms** — the largest
  relative swings in the table. §3 already flags parties as where the O1 baseline
  wins; it is also where re-running moves the number most.

**One run per arm still does not license the contrasts.** Every claim in §1 is a
*difference*, and a difference measured once per arm confounds the effect with
the run noise of both arms. The measurement above bounds that noise rather than
dismissing it — and it rests on a single pair of runs over 10 documents, which
estimates a spread, not a variance.

The Wilcoxon in §4.3 does not rescue this. It treats **document** as the unit of
variation and the run as fixed, so it tests whether arm A beat arm B *on this run
pair*. Re-running shifts the per-document differences the test consumes. The
p-value is therefore conditional on the run and understates uncertainty.

**Sequencing (per §4.2): headline and judge validity now, run-variance
deferred.** Do not run everything three times up front — the allocation below is
the target, not this iteration's task list:

| what | scope | runs | buys | when |
|---|---|---:|---|---|
| headline numbers | full 250, every arm | 1 | the reported figures | now |
| judge validity | the annotated set | 1 | judge–human agreement — the control §4.2 names as non-optional | now |
| judge stability | one *fixed* extraction output | 3 | judge noise, at zero extraction cost | now, if budget allows |
| variance band | ~40-doc stratified subsample, **C1 and C4 only** | 3 | a run-level spread on the headline contrast, computed on §4.1's graph/content measures, no judge involved | later |

Variance restricted to C1 and C4 when it happens, because those carry the
headline and because the repeat already on hand shows the two arms differ in
stability; C2 and C3 sit inside the 2×2 and can inherit the band. Stage 1 needs
no repeat while it stays byte-reproducible — **verify that with a checksum
rather than assuming it**, since it has already changed once.

**If an arm gap is smaller than the run-to-run spread, report the arms as
indistinguishable.** That is a real finding at this sample size and a more useful
contribution than a ranking that will not reproduce.
