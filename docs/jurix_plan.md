# Does the Ontology Earn Its Keep?

**JURIX submission — revised plan**
Verdict: long paper · Primary factor: ontology assistance · Runs: 8 · Ships: ~2,500-document dataset

A resource-and-method paper: build a validated dataset of ECtHR domestic procedural history, and test whether the ontology is what makes it work.

---

## §0 Verdict

**Was the old config strong enough? No — not for a long paper.** Three assembly strategies × two models is an engineering ablation. It answers "how should I configure my pipeline", which is a question about the pipeline, not about anything a JURIX audience needs. It would have made a decent short paper and a thin long one.

**Ontology assistance changes the register completely.** "Does formal knowledge representation still earn its place alongside a capable LLM" is close to the defining live question of the field, and JURIX is the venue that most cares about the answer. It is also publishable in both directions: if the ontology helps, there's a resource plus a justification; if it does not, and disciplined prompting gets you most of the way, that is the more surprising and more citable finding.

It also solves the problem of how to present the ontology. It's no longer being claimed as a novel schema — it's *measured whether it works*. "How does this differ from LKIF" stops being a threatening question, because the paper's claim does not depend on the answer.

> **The paper, in one sentence:** We release a validated knowledge graph of domestic procedural history from ECtHR case law, and show through ablation which components of the extraction methodology — the ontology, the model, the document assembly — actually account for its quality.

**Long paper.** A dataset contribution plus an ablation plus a three-tier evaluation cannot be compressed into a short paper, and attempting it would waste the dataset. Check the current CFP for the page limit.

---

## §1 The idea, evaluated

### The thing that could sink it

**Half the evaluation stack is ontology-dependent and therefore cannot be applied to the no-ontology condition.** SHACL shapes, closed-vocabulary conformance, IRI discipline — none of these mean anything for a baseline that was never asked to produce them. Scoring the baseline on a rubric only the treatment can satisfy is not an experiment, it is a demonstration, and a reviewer will say so.

Everything below is designed around this constraint. It is also, usefully, a methodological contribution in its own right: **how do you compare ontology-guided and unguided extraction on fair terms?** Nobody has a clean answer, and this paper can offer one.

### Two conditions

| Condition | What the model is given | What it isolates |
|---|---|---|
| **O2 · Full ontology** | Ontology in context, closed vocabularies, IRI minting, typed properties, SHACL-checkable output, the OntoCast pipeline. | — |
| **O1 · Schema-light** | The same task, the same target fields, requested as flat JSON in a single call. No ontology, no closed vocabularies, no shapes, no aggregation. | The value of formalisation, holding the task fixed. **The honest baseline** — what a competent practitioner does without an ontology. |

A third free-form condition (O0) was specified and built, then dropped. Its output carried no traceable evidence at all — 0% of its supporting quotes appeared verbatim in the source, because a free-form summary can only be quoted from itself — so it could not be scored on the evidence axis the rest of the study rests on, and it answered a question ("is any structure better than none") that nobody disputes.

**O1 is deliberately not given an aggregator.** Chunking it would mean concatenating per-chunk lists, and concatenation is itself a naive aggregator that would introduce cross-boundary duplicates and penalise the baseline for a defect the design created. O1 is one prompt, one call, one document — which is what a practitioner without a pipeline actually has. The consequence for attribution is handled in §3.

### Fair comparison: four measurement layers, not one number

The instinct is to normalise every condition into a common form and score that. The common form is necessary, but it cannot be the whole measurement, and the reason is quantitative: a SPARQL projection of O2's graph onto a flat proceeding list reads **21% of the triples**. The other 79% — 36 `followsProceeding` links, 42 authority kinds, 33 proceeding types, 31 jurisdiction states, 8 final-decision flags, measured on ten documents — is invisible to it.

Worse, the loss is *directional*. The common form's `order` field is a single integer, which can express a line and nothing else. Measured on the same ten documents, **8 of 10 contain more than one independent track** — a criminal prosecution beside a civil claim, the applicant's appeal beside the State's — and 2 contain a decision reviewed by two later steps. Scoring O2's chain through `order` discards exactly the structure the ontology exists to encode.

So the outcome is layered, and each layer states plainly what it can and cannot compare.

| Layer | What it measures | Conditions | Status |
|---|---|---|---|
| **1 · Content** | Proceedings recovered, and their body / date / instance level / outcome / parties / custodial measure / evidence quote | O1, O2 | comparative |
| **2 · Chain** | Review-and-continuation edges between proceedings — **the primary outcome** | O1, O2 | comparative |
| **3 · Artefact** | SHACL conformance, closed-vocabulary usage, IRI and entity discipline | O2 only | descriptive |
| **4 · Capability** | What the output supports downstream — cross-case querying, entity linking | O2 only | demonstrative |

**Layer 2 is the primary outcome**, and putting it there is the substantive design decision. It is what the ontology is *for*; it is immune to the granularity confound that makes raw proceeding counts uninterpretable (two conditions can disagree about what one "proceeding" is while agreeing perfectly about which decision reviews which); and it is genuinely contestable — O1 is asked for the review relation directly and supplies it readily. On the pilot O1 produced 82 chain edges to O2's 36, so this is a comparison the baseline can win.

**Layers 1 and 2 are computed on the common form**, by deterministic projection: SPARQL for O2, field pass-through for O1, which decodes into the common form directly. An earlier design ran a shared LLM parse over every condition so that parse error would be shared rather than charged to the baseline. It was abandoned on measurement: the parser agreed with a SPARQL projection on 253 of 253 field comparisons and dropped nothing, but it *rescued* 63 fields the graph does not assert — reading a deciding body, date or outcome out of an `rdfs:label` or a supporting quote where no such triple exists. That credits the ontology condition with structure it never produced and hides the exact defects the study is measuring. A projection cannot do this; it can only report the triples that exist.

**Layers 3 and 4 are reported for O2 alone and framed as describing the artefact, never as comparing conditions.** Say so in Methods in one sentence. Layer 4 in particular is not a metric but a demonstration: "which courts recur across 240 Article 6 cases" is a SPARQL query over O2 and a re-engineering project over O1's JSON, because O1 has no IRIs and no entity identity across documents. It is the strongest honest argument for formalisation and it deserves space rather than a closing figure.

---

## §2 Statistics: drop the GLMMs

They were the wrong instrument for the venue. The useful thing is that **the within-document design makes the simple analysis also the correct one** — every document is extracted under every condition, so a document is compared to itself and the pairing removes the variance that would otherwise need modelling.

**What replaces it:**
- **Paired per-document differences** against the base configuration. Report median difference and IQR.
- **Win / tie / loss counts** — "on 184 of 240 documents the full ontology recovered more proceedings than schema-light." Most readable form, what people will quote.
- **Wilcoxon signed-rank** where a test is wanted. One line, non-parametric, standard, appropriate for paired data.
- **Stratified tables by court level** for anything that looks like it varies with document length — descriptive, not modelled.

**Run-to-run stability.** Extraction is sampled at temperature 0.4 and is not deterministic: four identical repair runs during development produced 0, 2, 5 and 2 truncation failures. With 240 paired documents the aggregate tests absorb this, but per-document claims do not. Re-run **30 documents of one arm a second time** and report the resulting stability figure in Methods. Thirty extra extractions, and it closes the "is a five-point gap meaningful" question before it is asked.

---
## §3 The runs

Two phases. A 10-document pilot that settles configuration and is **not reported as a result**, then a 240-document main study that is.

### Phase 1 · Pilot (10 documents, informal)

Purpose: finesse the prompts and the architecture, and settle three configuration questions that would otherwise be answered inside the main sweep at 24× the cost. Written up in the paper as one sentence — "ten documents were used to finesse prompts and pipeline configuration" — with no numbers reported, so selection and evaluation stay on disjoint sets and the winner's curse does not arise.

| Question | Arms | Decides |
|---|---|---|
| Serialisation | turtle vs jsonld, all five phase 1 arms | Which format O2 uses throughout |
| Chunk sizes | ~3k/6k, ~8k/16k, whole-document | The three O2 sizes for the main study |
| Does carry-forward earn a place? | rolling 3k/6k and 8k/16k against fan-out at the same size | Whether `rolling` enters the main study at all |

**Resolved 2026-08-24** (`docs/phase1_pilot_report.md`, ten-document pilot, all five configurations × both formats):
- **Serialisation → jsonld.** Mixed on recall, but jsonld won body coverage in 4/5 matched pairs and quote fidelity in 5/5 (turtle's worst arm, `o2_med`, had 71% body coverage against jsonld's 94% at the same chunk size). **jsonld only from here on** — the turtle arms have been removed from `run_arms.sh` and `run_experiment.sh`'s default flipped to jsonld.
- **Chunk size → recall rises as chunks shrink**, confirming the move off the original 5k/15k default.
- **Carry-forward → keep, paired with the smallest chunk size only.** At 8k/16k it is a wash against fan-out (104 vs 112 events); at 3k/6k it beats fan-out by ~40% (171–174 vs 118–125). The gain is an interaction with chunk size, not a main effect, which only became visible once fan-out was also run at 3k/6k.
- Winning configuration on the pilot's stated selection rule (recall at `raw/`, dup < 10%, quote within 3 points of best): **`o2_cf_low` (carry-forward, 3k/6k, jsonld)** — 174 raw events, 7% dup, 99%/100% quote fidelity.

### Phase 2 · Main study (240 documents)

Four conditions × two extractors. Every run is scored at both checkpoints, `raw/` and `repaired/`.

| Condition | Assembly | What it contributes |
|---|---|---|
| **O1** | single call, no aggregation | the baseline |
| **O2-large** | whole document, single call | **matched-assembly O2** |
| **O2-med** | fan-out, moderate chunks | assembly ablation |
| **O2-low** | fan-out, small chunks | assembly ablation |

Extractors: **gemma-4-31b** and **gpt-5-mini**. 4 conditions × 2 models × 240 documents = **1,920 extractions**, each followed by a repair pass.

### The three contrasts

This is the part to get right in the writing, because the arms support three different claims and only one of them is about the ontology.

| Contrast | Isolates | Claim it licenses |
|---|---|---|
| **O1 vs O2-large** | ontology guidance, assembly held constant — both are one call per document with no aggregation | "the ontology does / does not improve extraction" |
| **O2-large vs O2-med / O2-low** | what the pipeline's document assembly buys | "chunked assembly recovers N× the proceedings" |
| **O1 vs best O2** | the system as a whole | "the pipeline outperforms disciplined prompting" |

**O2-large is load-bearing and must not be cut.** It is the only arm whose call structure matches O1's, and therefore the only one that can carry an ontology claim. Without it, any O1-vs-O2 gap is a pipeline result wearing an ontology label — and the discipline this table enforces is not writing "the ontology gives 2× recall" when the number came from O2-low. That one is the chunking.

### Both checkpoints, every run

The pipeline writes `raw/` (extraction) and `repaired/` (after the staged repair pass). **Score both.** It costs nothing on runs already done — the scorers point at a second directory — and it is what makes the repair stage measurable at all.

Concretely: **recall is a property of `raw/`, precision is a property of `repaired/`.** One number after both stages cannot distinguish a chunking win at extraction from a repair failure afterwards, and that ambiguity has already cost a sweep. In the 2026-08-23 arms the repair stage correctly diagnosed a node conflating two authorities and every one of its `remove` operations silently no-opped, because the model returned Turtle literal syntax into a plain-text field. Adds landed, removes did not, and the result read as model weakness in the aggregate score. It was a one-line applier bug.

### Duplicate rate as a Tier 1 metric

Deterministic, no judge needed: count domestic events sharing a `(echr:hasCourt, echr:hasDecisionDate)` key. Cheap, and the one structural metric that moves with document assembly.

Pilot evidence, five arms × 10 documents, gemma-4-31b (2026-08-23):

| arm | events | persons | quotes verbatim | dup rate |
|---|---:|---:|---:|---:|
| whole-document (ttl) | 76 | 15 | 94% | 1.3% |
| whole-document (jsonld) | 58 | 10 | 100% | 3.4% |
| rolling 8k/16k | 104 | 25 | 95% | 5.8% |
| rolling 3k/6k | 159 | 42 | 96% | 4.4% |

Two things follow. **Chunking does not degrade span-level fidelity** — quote-verbatim rate is flat at 94–100% across every arm. And the errors it introduces are asymmetric: a duplicate is two nodes sharing a court and a date, which repair keys on and can merge, whereas a missed proceeding is unrecoverable by anything downstream. Chunking roughly doubles recall for a duplicate rate under 6%.

State the asymmetry explicitly in Results. It reframes the assembly ablation from "cost of chunking" into a question worth asking on its own terms: is recall best bought at extraction and precision bought back at aggregation?

### Sample

**240 documents** — 140 judgments, 100 decisions — stratified by court level nested within document type, spanning a 26-fold median-length gradient from `ADMISSIBILITYCOM` (3.8k chars) to Grand Chamber (98k). Roughly 70 forced to carry an Art. 35-1 exhaustion label so the sample seeds the follow-on analysis paper.

**This sample does not yet exist.** `data/art6_domestic_test_set.json` holds the 10 pilot documents; the corpus it must be drawn from does exist (15,305 judgments, 33,019 decisions). Drawing and **freezing the ID list is a prerequisite for any main run**, and it must be frozen before any results are looked at.

---
## §4 Paper plan

### 1 · Introduction (~1 page)

Article 6 §1 reasonable time, Article 35 §1 exhaustion and the four-month rule all turn on the *structure* of the domestic procedural history: which body decided what, at which instance, in what order, and which decision was final. Nearly 50,000 Article 6 documents makes manual coding impossible, and no structured resource for this exists.

Contributions — **all three live at submission**, so nothing load-bearing rests on an artefact a reviewer cannot open:

1. An **evaluation resource**: 240 ECtHR documents extracted under eight configurations, with human annotations for 30, plus the ontology, shapes, validators and both condition prompts verbatim.
2. A **fair-comparison protocol** for scoring ontology-guided against unguided extraction — the layered outcome structure in §1, and specifically the argument that a single normalised form cannot carry the comparison on its own.
3. An **ablation** separating what the ontology contributes from what the pipeline contributes, using the matched-assembly arm.

The bulk corpus is deliberately *not* a headline contribution — it appears in the Conclusion as a forthcoming extension. An unbuilt, unsized artefact in contribution position one is the weakest place to put it; moving it makes the release upside rather than a debt.

### 2 · Data (~1.5 pages)

- **Corpus.** 15,305 Article 6 judgments and 33,019 decisions from HUDOC, with metadata: outcome codes, Article 35 thesaurus labels, court level, importance, respondent state.
- **Evaluation sample.** The 240, with the stratification table and length gradient. State the disproportionate allocation and that design weights apply to any population estimate.
- **The extraction target.** Half a page on the ontology *as the extraction guide* — the classes, the `followsProceeding` chain, reified participation, evidence anchoring — plus one figure. No taxonomy walkthrough. Position it as the instrument, and the ablation as the test of whether the instrument is load-bearing.
- **Released dataset.** ~2,500 documents extracted under the winning configuration, plus the 240-document evaluation sample with human annotations for the subset that has them.

### 3 · Methods (~2.5 pages)

**Pipeline.** OntoCast; whole-document and fan-out assembly; the recovery layer for malformed model output. Brief.

**Conditions and contrasts.** The two ontology levels, the four arms, and the three-contrast table from §3. This is where the ontology-versus-pipeline distinction is made explicit, and it is worth a short paragraph rather than a footnote.

**The comparison protocol.** Its own subsection — it is a contribution, not a technicality. The common form and its deterministic projections; the measurement that motivated the layering (a flat projection reads 21% of the graph; `order` cannot express the parallel tracks present in 8 of 10 documents); and the rule that Layers 1–2 compare conditions while Layers 3–4 describe the O2 artefact only.

**Evaluation, three tiers.**

- **Automated**, all 1,920 graphs, at both `raw/` and `repaired/`: content loss, chain-edge precision and recall, entity resolution, duplicate rate on the `(hasCourt, hasDecisionDate)` key, quote-verbatim rate, terminal identification, plus SHACL and vocabulary conformance for O2.
- **LLM-as-judge**, in two distinct passes that must not be conflated:
  - **(a) The comparison.** Blinded, on the **normalised common form**, both conditions, all 240 documents. Judged by two models; order randomised; intra-judge reliability reported. The judge does *not* see the graph, and that is deliberate: Turtle for O2 against flat JSON for O1 identifies the condition at a glance, which forfeits the blinding — and on *Stanev* the repaired Turtle is 2,231 tokens against 332 for the normalised list.
  - **(b) The artefact.** Unblinded, on the **full `.ttl`**, O2 at the winning configuration only, on ~60 documents. There is no blinding to lose in a single-condition assessment, and the graph is the right input for the question "is this artefact any good". Reported as artefact quality, never as a comparison. This is one arm rather than four, so it adds roughly a quarter of pass (a)'s call volume, not double.
  - **Judging `raw/`.** Repair deletes as well as adds, so judging only `repaired/` cannot detect repair removing correct content. Judge `raw/` on the 30 human-annotated documents — 12.5% extra — to bound repair's semantic effect.
- **Human annotation**, 30 documents nested inside the judge subsample: the full list of domestic proceedings per document and the review edges between them. **Built from the judgment alone, blind to condition, before seeing any extraction output.** If the gold set is produced by correcting model output it is not a gold standard, and κ against the judge stops measuring what it is supposed to. Budget 15–30 hours.

**Judge validation.** Agreement between judge and human on the annotated subset, as κ and as precision/recall. This gates how much weight the judge tier carries — state it as a gate, not buried.

**Division of labour between tiers.** Automated metrics screen for precision and structure, because that is what deterministic checks can see. The judge adjudicates recall and semantic faithfulness, because nothing else can. The two must not be chained: using an automated ranking to select the judge's inputs makes selection and measurement the same act. Hence the judge tier carries the **ontology axis**, fixed by design, while the O2 **configuration** question is settled separately on the pilot.

**Family overlap is balanced by construction, which is what makes it estimable.** The extractors are gemma-4-31b (Google) and gpt-5-mini (OpenAI); the judges are expected to be Gemini and GPT-5. Every judge is therefore same-family with exactly one extractor and cross-family with the other:

| | gemma-4-31b extractions | gpt-5-mini extractions |
|---|:-:|:-:|
| **Gemini judge** | same family | cross family |
| **GPT-5 judge** | cross family | same family |

There is no neutral judge in the set, and *that is fine*, for two reasons. A fully crossed 2×2 identifies self-preference as an interaction term, which an unbalanced design with one "clean" judge could not. And more importantly, **self-preference acts on the extractor family while the ontology contrast is within-extractor**: O1 and O2 outputs for a given document come from the same model, so a judge's bias toward its own family shifts both arms equally and cancels in the contrast that carries the paper. It remains a live threat to the gemma-versus-gpt comparison, which is why the interaction is reported as a control.

**One asymmetry to state rather than paper over:** gpt-5 ↔ gpt-5-mini is a tighter relationship — same family, same generation, plausibly shared post-training — than gemini ↔ gemma, which share a lineage and data provenance but are separately trained model families. The two same-family cells are therefore not equally intense, so the interaction estimate is conservative for the Google diagonal. One sentence in Methods; a reviewer who knows the model families will notice, and pre-empting it costs nothing.

**The human annotation tier is the neutral anchor.** Judge–human κ computed *separately for each judge* converts the interaction from a suggestive number into a validated one. If κ diverges sharply between judges on the same documents, that is the self-preference finding, independent of the interaction estimate.

**Judge-tier costing.** Priced against the pilot's mean document length (25,163 chars ≈ 6,300 tokens), at $1.25/M input and $10/M output — *verify current rates before committing, Gemini's in particular, which tiers above 200k context*:

| design | 1 judge | 2 judges |
|---|---:|---:|
| pass (a), independent scoring, capped reasoning | £14 | **£28** |
| pass (a), document prefix cached | £11 | £22 |
| pass (b), O2 only, 60 documents | £2 | **£4** |
| pass (a), **uncapped reasoning** | £51 | **£101** |

Output tokens are the cost driver, not the number of conditions. Capping reasoning effort is a 3.6× saving and is the only lever that matters — uncapped, the design costs the entire £100 budget before a single retry.

**Bundling conditions into one call is rejected despite being cheaper.** It converts independent scoring into comparative ranking: absolute per-condition faithfulness scores are lost, contrast effects are introduced, and κ against human annotation stops measuring the same thing. The saving is not worth the methodological cost.

*Optional, ~£4:* a third judge from neither family, over the 30 human-annotated documents only, gives a family-neutral reference for the κ comparison. Worth it only if the interaction comes out non-null — do not run it pre-emptively.

Budget against £100: pilot judging ~£2, pass (a) with both judges on the full corpus £28, pass (b) £4, and ~£20 held for rubric iteration, retries and disagreement re-checks. Committed ~£54, leaving headroom. Because two judges on all 240 fits, run both on the whole corpus rather than subsampling the second — that upgrades the self-preference control from a spot check to a full second measurement.

### 4 · Results (~2.5 pages)

**Hypotheses**, stated before the numbers:

| # | Hypothesis | Contrast |
|---|---|---|
| H1 | Formalisation improves **chain structure** — review edges, parallel tracks, terminal identification — more than it improves factual precision on flat fields. | O1 vs O2-large |
| H2 | The ontology's contribution is smaller than the pipeline's: assembly moves recall more than formalisation does. | (O1 vs O2-large) against (O2-large vs O2-low) |
| H3 | The weaker model benefits more from formalisation. | the O1/O2-large gap, gemma against gpt-5-mini |
| H4 | Chunking degrades structure while leaving span-level fidelity intact — it raises duplicate rate without lowering quote-verbatim rate. | O2-large vs O2-med, O2-low |
| H5 | Repair recovers the precision chunking costs, so the chunked configuration wins overall once both stages have run. | O2-med, O2-low at `raw/` vs `repaired/` |
| H6 | Automated conformance is a poor proxy for completeness — a graph can pass every check and be missing the operative section. | all arms, Tier 1 against the judge |
| H7 | Judge and human agree well enough for the judge tier to carry inferential weight. | gate on H1–H6 |

**H2 is stated in the direction the pilot evidence points**, deliberately. Chunking roughly doubled recall while the ontology's measurable advantage on flat fields was small or negative on early arms. Pre-registering the unflattering direction is cheap insurance: if it holds, the paper reports a clean decomposition rather than a defeat; if it does not, the ontology result is stronger for having been tested against the hypothesis that it would not hold.

**Analysis.** Paired per-document differences against O2-large; win/tie/loss counts; Wilcoxon where a test is wanted; stratified by court level where length plausibly matters. A qualitative subsection walking one case through the conditions — *Stanev* is already annotated and makes the failure modes concrete in a way tables cannot.

**Figures.** Two, and they do different work. One extracted procedural chain rendered as a **timeline** — shows the reader what the resource is. And a **cross-case entity graph** over a slice of the corpus, showing courts and authorities recurring across cases: this is the Layer 4 demonstration, and it is the figure that makes the formalisation argument visually, because it cannot be built from flat JSON without re-deriving entity identity.

### 5 · Conclusion (~0.75 page)

What the dataset contains, what is reliable in it and what is not, and what it makes possible. **Describe the analytical affordances without claiming a finding** — the topological features the graph exposes (chain depth, instance sequence, branching, duration, terminal identity) and the label sets available alongside them (2,528 exhaustion-labelled documents at roughly 41/59; Article 6 violation at 90/10 and heavily confounded with court level and importance).

That framing is honest, it stakes the ground for the follow-on analysis paper, and commits to nothing. Limitations: English and French only, ECtHR only, one ontology, extraction validated at n=240 rather than at corpus scale.

---
## §5 Dataset: ship ~2,500 documents, not 240

A dataset contribution needs scale. Measured against the pilot's whole-document rate:

| Slice | Docs | M chars | Single stream | Parallel, realistic |
|---|---:|---:|---:|---:|
| Exhaustion-labelled, judgments + decisions | 2,408 | 86 | ~40 h | **~10 h** |
| All judgments | 15,305 | 406 | ~190 h | ~50 h |
| Everything | 48,324 | 802 | ~380 h | ~100 h |

Rate extrapolated from the pilot's 33s at ~19.4k characters — order-of-magnitude only. "Parallel, realistic" assumes 3–5× from concurrent documents against one vLLM instance, not the 8× a naive division would give.

**The exhaustion-labelled slice is the right target**: one overnight run, and exactly the corpus the follow-on ICAIL analysis paper will want. Top it up with a stratified random sample to whatever total the remaining GPU time allows, so the release isn't narrowly pre-committed to one legal question.

### Release mechanics

Review is **single-blind**, so there's no anonymity constraint and a live link can go in the submission. That is worth more than any phrasing: a URL a reviewer can open converts a promise into an observable fact.

| Tier | Contents | Status at submission |
|---|---|---|
| **Reproducibility** | Ontology, shapes, validators, O2/O1 prompts verbatim, frozen sample IDs | live — exists today |
| **Evaluation resource** | 240 documents × 7 configurations; human annotations for 25 | live — ready day 8 |
| Bulk corpus | ≥2,400 documents under the selected configuration | in progress; in the released version |

Publishing the O1 prompt verbatim is the single most effective answer to the strawman objection — a sceptical reviewer can judge the baseline for themselves instead of taking the paper's word for it.

**Use a versioned Zenodo DOI.** Cite the *concept* DOI, which always resolves to the latest version. Release v1 at submission and v2 at camera-ready with the bulk corpus — the DOI in the paper never changes, so expanding the release means editing one number in the text. Clean mechanism for "commit now, expand later," ordinary practice rather than something a chair would query.

Prefer *"extraction in progress; included in the released version"* over *"available upon acceptance."* The latter implies the data exists and is being withheld, which invites the question of why one component is gated when the repository is otherwise public.

Check the licensing position on redistributing HUDOC-derived content before the abstract promises a release. The judgments are public, but derived-work terms are worth five minutes now rather than an awkward email after acceptance.

---

## §6 Stretch: the demo

**Do the static figure first** — one case's extracted chain rendered as a timeline, in the paper. Costs an afternoon, is the clearest possible statement of what the resource is, and every reader sees it.

An interactive tool is a genuine stretch goal: build it only if the paper is drafted and the runs are done, and consider it for a demo submission rather than squeezing it into the main paper. Adds little to a reviewer's assessment of the research contribution and will eat days that don't exist.

---

## §7 Schedule

Fifteen days. The pilot is not a study day — it is the gate.

| Day | Work |
|---|---|
| 1 | **Gate day — done 2026-08-24.** Ran the pilot: jsonld vs turtle, all five configurations, ten documents. Verified the live fixes on gemma — `turtle_repair`, `response_repair`, the whitespace-stall salvage. Every configuration produced 10/10 clean documents. See `docs/phase1_pilot_report.md`. |
| 2 | **Serialisation, chunk sizes and carry-forward settled** — jsonld, three O2 sizes (large/med/low), carry-forward kept paired with the smallest chunk only (see resolution above). **Next:** freeze the 240-document sample — stratified, ~70 forced exhaustion-labelled, ~60 forced reasonable-time-formula, the 4 Events Matter overlap cases forced in. Write the ID list to a file and stop touching it. Finalise judge prompts. |
| 3 | Launch all 8 runs, cache disabled. Start human annotation — begin with the 4 Events Matter cases. |
| 4 | Active monitoring: check each run's early output for known failure signatures (parse failures, timeout loss, malformed Turtle) and intervene same-day. Continue annotation. |
| 5 | **Likely bottleneck.** gpt-5-mini and the small-chunk arms are the slow configurations; if either is badly behind, trim here rather than let it eat the week. |
| 6 | Automated Tier 1 on all 8 runs, full 240, at both checkpoints, including duplicate rate and chain-edge metrics. Re-run 30 documents of one arm for the stability figure. Build and run the Events Matter alignment script on the 4 overlap cases. |
| 7 | Finish human annotation (30 documents, before seeing any extraction output). Pilot the judge rubric on *Stanev* plus the Events Matter overlap. |
| 8 | Judge pass (a): full 240 × both conditions × both judges, blinded, on the normalised form, batched, cached, **capped reasoning effort**. Judge pass (b): O2 full `.ttl` on 60 documents. Judge `raw/` on the 30 annotated. Compute the judge × extractor-family interaction. |
| 9 | **Compute judge–human κ first, before anything else.** This gates how much weight Tier 2 carries in Results. Chase any straggling run. |
| 10 | Analysis: H1–H7, paired differences, win/tie/loss, stratified breakdowns, the Events Matter external-recall figure. Freeze all numbers and figures. |
| 11 | Build both figures — the timeline and the cross-case entity graph. Launch the bulk exhaustion-labelled extraction (≥2,400 docs) in the background, unattended. |
| 12 | Write Methods + Results, while the analysis is fresh. |
| 13 | Write Data + Conclusion. Related-work positioning against Events Matter and the Mumford/Atkinson/Bench-Capon ADM dataset goes here, not buried later. |
| 14 | Write Introduction last, so it promises exactly what is delivered. Check the bulk extraction's real count and cite it live. Assemble the release package. |
| 15 | Buffer. Proofread, verify every link and DOI resolves, submit. |

---

## §8 Risks

| Risk | Response |
|---|---|
| **The schema-light baseline is a strawman** — the single biggest threat to the contribution. | Write the O1 prompt as if trying to win with it. Ask someone else to read both prompts blind and say which looks better resourced. Publish it verbatim in the appendix. |
| **The ontology does not win on the comparative layers.** | Plan for it now rather than discovering it in week three. The honest headline — *formalisation does not improve content recall, but produces a validated, queryable, linkable artefact that flat extraction does not* — is publishable, and Layers 3–4 are built to carry it. What must not happen is reaching for a pipeline number (O2-low) to make an ontology claim. |
| Judge–human agreement is poor. | Check on day 9. Demote the judge to descriptive, lean on human and automated tiers. An honest negative on judge validity is itself a contribution. |
| Reviewer: "schema-guided extraction is known to help." | Position against that literature and be precise about what is new — a legal-procedural target, the ontology/pipeline decomposition, and a fair-comparison protocol that does not flatten the treatment away. Do not claim the general question is open. |
| Run-to-run variance swamps a small effect. | The stability re-run on day 6 gives a noise floor. If the effect is inside it, report it as null rather than as a trend. |
| Bulk extraction does not finish. | Ship the 240 evaluation sample plus whatever completed. State the size honestly; a smaller validated release beats a larger unvalidated one. |
| Eight runs is still too many. | Pre-declared cut order: O2-med first, then O2-low. **O1 and O2-large are never cut** — they are the ontology contrast, and without both the paper has no claim. |
