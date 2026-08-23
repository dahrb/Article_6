# Does the Ontology Earn Its Keep?

**JURIX submission — revised plan**
Verdict: long paper · Primary factor: ontology assistance · Runs: 7 · Ships: ~2,500-document dataset

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

The ontology ablation is the right call. One design decision determines whether it works.

**In favour**
- A genuine AI & Law question, not a systems one.
- Cheap — same pipeline, different prompt and schema.
- Interesting whichever way it lands.
- Turns the ontology from a liability into a tested instrument.
- Directly supports the dataset claim: can say *why* the data is reliable, not just that it is.

**Against**
- A weak baseline makes the whole result worthless.
- The best metrics cannot score the baseline at all.
- Schema-guided extraction is an active area in general NLP — needs positioning against it.
- Adds a factor to a design that was already at budget.

### The thing that could sink it

**Half the evaluation stack is ontology-dependent and therefore cannot be applied to the no-ontology condition.** SHACL shapes, closed-vocabulary conformance, IRI discipline — none of these mean anything for a baseline that was never asked to produce them. Scoring the baseline on a rubric only the treatment can satisfy is not an experiment, it is a demonstration, and a reviewer will say so.

Everything below is designed around this constraint. It is also, usefully, a methodological contribution in its own right: **how do you compare ontology-guided and unguided extraction on fair terms?** Nobody has a clean answer, and this paper can offer one.

### Three graded conditions, not two

A binary "ontology vs nothing" invites the strawman objection. Three graded levels let the effect be attributed to a specific component.

| Condition | What the model is given | What it isolates |
|---|---|---|
| **O2 · Full ontology** | Ontology in context, closed vocabularies, IRI minting, typed properties, SHACL-checkable output. Current setup. | — |
| **O1 · Schema-light** | Same task description and same target fields — deciding body, date, instance level, outcome, order, supporting quote — requested as flat JSON. No formal ontology, no closed vocabularies, no shapes. | The value of *formalisation*, holding the task definition constant. **This is the honest baseline** — what a competent practitioner does without an ontology. |
| **O0 · No schema** | "Identify the domestic proceedings described in this document and what happened in each." Free-form output. | The value of *any* target structure. The floor, and the condition that shows how far a bare LLM gets. |

The interesting comparison is **O2 vs O1**, because it holds the task fixed and varies only formalisation. O0 stops a reviewer asking what happens without structure at all, and gives the effect a scale.

### Making the comparison fair: normalise, then measure

Every condition's output is mapped into a common comparison form before scoring — a flat list of proceedings, each with deciding body, date, instance level, outcome, order and quote. For O2 that's a SPARQL projection over the graph. For O1 it's a field rename. For O0 it's an LLM parse into the same shape, applied identically to all conditions so the parse cost is shared rather than charged to the baseline.

**All comparative measures are computed on the normalised form.** Ontology-dependent metrics — SHACL, vocabulary conformance — are reported for O2 only, explicitly framed as describing the artefact rather than comparing conditions. State this plainly in Methods; it's the difference between a fair test and a rigged one.

---

## §2 Statistics: drop the GLMMs

They were the wrong instrument for the venue. The useful thing is that **the within-document design makes the simple analysis also the correct one** — every document is extracted under every condition, so a document is compared to itself and the pairing removes the variance that would otherwise need modelling.

**What replaces it:**
- **Paired per-document differences** against the base configuration. Report median difference and IQR.
- **Win / tie / loss counts** — "on 184 of 240 documents the full ontology recovered more proceedings than schema-light." Most readable form, what people will quote.
- **Wilcoxon signed-rank** where a test is wanted. One line, non-parametric, standard, appropriate for paired data.
- **Stratified tables by court level** for anything that looks like it varies with document length — descriptive, not modelled.

---

## §3 The runs: seven, one factor at a time

A base configuration plus single-factor ablations. No interaction terms, no factorial explosion, every row explainable in one sentence.

| # | Run | Ontology | Model | Assembly | What it tests |
|---|---|---|---|---|---|
| 1 | **Base** | O2 full | gemma-4-31b | whole-document | The reference configuration. |
| 2 | Schema-light | **O1** | gemma-4-31b | whole-document | **The headline comparison.** Value of formalisation. |
| 3 | No schema | **O0** | gemma-4-31b | whole-document | The floor; scale for the effect. |
| 4 | Model swap | O2 full | **gpt-5-mini** | whole-document | Does the pipeline generalise across models? |
| 5 | Model × schema-light | **O1** | **gpt-5-mini** | whole-document | **The one interaction worth having** — does the weaker model benefit more from the ontology? |
| 6 | Assembly: fan-out | O2 full | gemma-4-31b | **parallel fan-out** | Cost of independent chunking. |
| 7 | Assembly: carry-forward | O2 full | gemma-4-31b | **sequential** | Cost of chunking with state. |

Runs 1–5 are the primary experiment; 6–7 are the engineering ablation, kept because the pilot evidence already exists and they're cheap on gemma. Runs 2/5 and 1/4 form a 2×2 on ontology × model.

7 runs × 240 documents = **1,680 extractions**. If cutting is needed: cut run 3 first (O0 is least informative), then run 6.

**Sample:** 240 documents — 140 judgments, 100 decisions — stratified by court level nested within document type, spanning a 26-fold median-length gradient from `ADMISSIBILITYCOM` (3.8k chars) to Grand Chamber (98k). Roughly 70 forced to carry an Art. 35-1 exhaustion label so the sample seeds the follow-on analysis paper. Freeze the ID list on day 2.

---

## §4 Paper plan

### 1 · Introduction (~1 page)

Article 6 §1 reasonable time, Article 35 §1 exhaustion and the four-month rule all turn on the *structure* of the domestic procedural history: which body decided what, at which instance, in what order, and which decision was final. Nearly 50,000 Article 6 documents makes manual coding impossible, and no structured resource for this exists.

Contributions — **all three live at submission**, so nothing load-bearing rests on an artefact a reviewer cannot open:

1. An **evaluation resource**: 240 ECtHR documents extracted under seven configurations, with human annotations for 25, plus the ontology, shapes, validators and all three condition prompts verbatim.
2. A **fair-comparison protocol** that lets ontology-guided and unguided extraction be scored on the same terms — the normalisation step in §3.
3. An **ablation** identifying which methodological components account for extraction quality, with ontology guidance as the primary factor.

The bulk corpus is deliberately *not* a headline contribution — it appears in the Conclusion as a forthcoming extension. An unbuilt, unsized artefact in contribution position one is the weakest place to put it; moving it makes the release upside rather than a debt.

### 2 · Data (~1.5 pages)

- **Corpus.** 15,305 Article 6 judgments and 33,019 decisions from HUDOC, with metadata: outcome codes, Article 35 thesaurus labels, court level, importance, respondent state.
- **Evaluation sample.** The 240, with the stratification table and length gradient. State the disproportionate allocation and that design weights apply to any population estimate.
- **The extraction target.** Half a page on the ontology *as the extraction guide* — the classes, the `followsProceeding` chain, reified participation, evidence anchoring — plus one figure. No taxonomy walkthrough. Position it as the instrument, and the ablation as the test of whether the instrument is load-bearing.
- **Released dataset.** ~2,500 documents extracted under the winning configuration, plus the 240-document evaluation sample with human annotations for the subset that has them.

### 3 · Methods (~2.5 pages)

- **Pipeline.** OntoCast; the three document-assembly strategies; the recovery layer for malformed model output. Brief.
- **Conditions.** The three ontology levels and the seven runs.
- **Normalisation for fair comparison.** The common comparison form, and the rule that comparative measures are computed on it while ontology-dependent measures describe O2 only. *Give this its own subsection* — it is a contribution, not a technicality.
- **Evaluation, three tiers.**
  - **Automated**, all 1,680 graphs: content loss, chain integrity, entity resolution, terminal identification, plus SHACL and vocabulary conformance for O2.
  - **LLM-as-judge**, a nested subsample: proceeding-level faithfulness (precision) and chain-level omission (recall — the only instrument that measures it). Blind to condition; judged by a third model, not by either extractor; order randomised; intra-judge reliability reported.
  - **Human annotation**, 20–30 documents nested inside the judge subsample: the full list of domestic proceedings per document. Annotate *before* seeing any extraction output.
- **Judge validation.** Agreement between judge and human on the annotated subset, reported as κ and as precision/recall. This gates how much weight the judge tier can carry — state it as a gate, not buried.

### 4 · Results (~2.5 pages)

**Hypotheses**, stated before the numbers:

| # | Hypothesis | Comparison |
|---|---|---|
| H1 | Formalisation improves structural fidelity — entity resolution and chain integrity — more than it improves factual precision. | run 1 vs 2 |
| H2 | Any target structure beats none by a wide margin; the increment from schema-light to full ontology is smaller than the increment from nothing to schema-light. | 3 vs 2 vs 1 |
| H3 | The weaker model benefits more from formalisation. | (1−2) vs (4−5) |
| H4 | Chunking degrades structure while leaving span-level fidelity intact. | 1 vs 6, 7 |
| H5 | Automated conformance is a poor proxy for completeness — a graph can pass every check and be missing the operative section. | all runs, Tier 1 vs judge |
| H6 | Judge and human agree well enough for the judge tier to carry inferential weight. | gate on H1–H5 |

**Analysis.** Paired per-document differences against base; win/tie/loss counts; Wilcoxon where a test is wanted; stratified by court level where length plausibly matters. A qualitative subsection walking one case through the conditions — *Stanev* is already annotated and makes the failure modes concrete in a way tables cannot.

**Figure.** One extracted procedural chain rendered as a timeline. Highest-value visual in the paper: shows the reader what the resource actually is.

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
| **Reproducibility** | Ontology, shapes, validators, O2/O1/O0 prompts verbatim, frozen sample IDs | live — exists today |
| **Evaluation resource** | 240 documents × 7 configurations; human annotations for 25 | live — ready day 8 |
| Bulk corpus | ≥2,400 documents under the selected configuration | in progress; in the released version |

Publishing the O1 and O0 prompts verbatim is the single most effective answer to the strawman objection — a sceptical reviewer can judge the baseline for themselves instead of taking the paper's word for it.

**Use a versioned Zenodo DOI.** Cite the *concept* DOI, which always resolves to the latest version. Release v1 at submission and v2 at camera-ready with the bulk corpus — the DOI in the paper never changes, so expanding the release means editing one number in the text. Clean mechanism for "commit now, expand later," ordinary practice rather than something a chair would query.

Prefer *"extraction in progress; included in the released version"* over *"available upon acceptance."* The latter implies the data exists and is being withheld, which invites the question of why one component is gated when the repository is otherwise public.

Check the licensing position on redistributing HUDOC-derived content before the abstract promises a release. The judgments are public, but derived-work terms are worth five minutes now rather than an awkward email after acceptance.

---

## §6 Stretch: the demo

**Do the static figure first** — one case's extracted chain rendered as a timeline, in the paper. Costs an afternoon, is the clearest possible statement of what the resource is, and every reader sees it.

An interactive tool is a genuine stretch goal: build it only if the paper is drafted and the runs are done, and consider it for a demo submission rather than squeezing it into the main paper. Adds little to a reviewer's assessment of the research contribution and will eat days that don't exist.

---

## §7 Schedule: fifteen days, 22 August – 5 September

| Day | Date | Work |
|---|---|---|
| 1 | Sat 22 Aug | **Gate day, not a working day for the full study.** Smoke-test O1 and O0 prompts against both models on 3–4 documents. Verify the surfaced fixes work live: the `max_tokens`/`max_completion_tokens` repair fix on gpt-5-mini, `turtle_repair`, `response_repair`. Confirm the normalisation projection runs end-to-end on all three ontology conditions. **Also test whole-document (nochunk) at `MAX_VISITS=2`** — never run in the pilot; the one run that actually lost content to timeouts (`gpt5mini_nochunk_mv1`, half the corpus lost) has never been given a second retry attempt, unlike native and rolling. Decide whether the `MAX_VISITS` ablation belongs on nochunk rather than native. Do not proceed to day 2 until every one of the 7 configs has produced at least one clean document. |
| 2 | Sun 23 Aug | Fix whatever day 1 surfaced. Freeze the 240-document sample (stratified, ~70 forced exhaustion-labelled, ~60 forced reasonable-time-formula, the 4 Events Matter overlap cases forced in). Write the ID list to a file and stop touching it. Finalise judge (J1/J2) prompts. |
| 3 | Mon 24 Aug | Launch all 7 runs, cache disabled. Start Tier 3 human annotation — begin with the 4 Events Matter cases. |
| 4 | Tue 25 Aug | Active monitoring: check each run's early output for the pilot's failure signatures (parse failures, timeout loss, malformed Turtle) and intervene same-day. Continue annotation. |
| 5 | Wed 26 Aug | **Likely bottleneck day.** gpt-5-mini and gemma-rolling are the slow configs; if either is badly behind, this is when to trim rather than let it eat the week. |
| 6 | Thu 27 Aug | Automated Tier 1 metrics on all 7 configs, full 240. This ranking picks the third judge-tier slot (base O2 and O1 are fixed in by design). Build and run the Events Matter alignment script on the 4 overlap cases. |
| 7 | Fri 28 Aug | Finish Tier 3 annotation (24–30 documents, before seeing any extraction output). Pilot the judge on *Stanev* plus the Events Matter overlap. |
| 8 | Sat 29 Aug | Run the judge tier: full 240-document corpus × the 3 selected configs, primary judge (GPT-5.6, batched, cached, capped reasoning effort). Self-preference control on a subsample with a second judge model. |
| 9 | Sun 30 Aug | **Compute judge–human agreement (κ) first, before anything else.** This gates how much weight Tier 2 carries in Results. Chase down any straggling run. |
| 10 | Mon 31 Aug | Analysis: H1–H7 paired differences, win/tie/loss tables, stratified-by-court-level breakdowns, the Events Matter external-recall figure. Freeze all numbers and figures. |
| 11 | Tue 1 Sep | Build the timeline visualisation figure. Launch the bulk exhaustion-labelled extraction (≥2,400 docs) in the background, unattended. |
| 12 | Wed 2 Sep | Write Methods + Results — the sections best understood right now, written while the analysis is fresh. |
| 13 | Thu 3 Sep | Write Data + Conclusion. Related-work positioning against Events Matter and the Mumford/Atkinson/Bench-Capon ADM dataset goes here, not buried later. |
| 14 | Fri 4 Sep | Write Introduction last, so it promises exactly what's delivered. Check the bulk extraction's real count and cite it live. Assemble the release package. |
| 15 | Sat 5 Sep | Buffer. Proofread, verify every link and DOI resolves, submit. |

Days 1, 8 and 9 fall on a weekend; the likely bottleneck (day 5) is a Wednesday. If annotation time is weekend-constrained, days 3–4 and 7 are the ones to rebalance toward weekday evenings.

---

## §8 Risks

| Risk | Response |
|---|---|
| **The schema-light baseline is a strawman** — the single biggest threat to the contribution. | Write the O1 prompt as if trying to win with it. Ask someone else to read both prompts blind and say which looks better resourced. Publish both verbatim in the appendix. |
| Judge–human agreement is poor. | Check on day 7. Demote the judge to descriptive, lean on human + automated tiers. An honest negative on judge validity is itself a contribution. |
| O0 output is too unstructured to normalise. | That *is* the result for O0 — report the parse failure rate as the finding. Do not hand-fix it. |
| Reviewer: "schema-guided extraction is known to help." | Position against that literature in §2 and be precise about what is new — graded formalisation levels, a legal-procedural target, and a fair-comparison protocol. Do not claim the general question is open. |
| Bulk extraction does not finish. | Ship the 240 evaluation sample plus whatever completed. State the size honestly; a smaller validated release beats a larger unvalidated one. |
| Seven runs is still too many. | Pre-declared cut order: run 3, then run 6. Runs 1, 2, 4, 5 are the paper. |
