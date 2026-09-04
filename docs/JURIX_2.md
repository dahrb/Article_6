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
loop with its deterministic structural rewrites.

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

To be designed separately. Open questions:

- Automated metric definitions per tier, and which are comparative across all
  five arms versus descriptive of the O2 artefact only.
- Hand-built reference graphs: how many, drawn how, built blind to output.
- LLM-as-judge: which models, on what rendering. Note a judge cannot be blinded
  to a manipulation visible in the output — the compression contrast is visible
  on sight — so blinding is unavailable for some contrasts and judge–human
  agreement is the control that matters.
- Statistics: paired per-document differences, win/tie/loss, Wilcoxon.

---

## 5 · Open decisions

1. **Does compression lose proceedings?** The C1 vs C3 contrast is where a
   reviewer will look for the cost of the two-tier design. Measure event recall
   explicitly and report it even if negative: losing events to gain a verbatim
   guarantee is a defensible trade *stated*, and an indefensible omission if
   hidden.
