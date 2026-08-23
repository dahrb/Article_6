# Extraction quality evaluation — three chunking strategies × two models × MAX_VISITS

Assessment of nine extraction runs over the same ten cases, from the experiment of
2026-08-20 (`results/experiment_full3arm_20260820/`).

Prepared 2026-08-21. Companion to `ontology/extraction_quality_report_chunking.md`
(2026-08-19, three chunking strategies at one model),
`ontology/extraction_quality_report_echr2.md` and
`ontology/extraction_quality_report.md` / `ontology/extraction_fixes_evaluation.md`
(2026-08-18).

This is the first run in the series where **the prompt and the ontology in the repo are
the ones actually under test** — `input.jsonl` was rebuilt so every record carries the
current `facts.txt`, and the `echr_2.ttl` snapshot beside the outputs is the live 3.3.0
schema. Recommendations 4 and 5 of the previous report (fix the carry-forward prompt
drop, rebuild `input.jsonl`) are both in force here.

---

## What was run

Nine extraction runs, each followed by a repair pass, over the same ten judgments
(`input.L1`…`L10`, 251,574 characters total, byte-identical across all nine arms).

| arm | how the document is fed to the model | chunk size |
|---|---|---|
| **nochunk** | one content unit per document | `MIN=20000 / MAX=50000` → 10 units |
| **native** | OntoCast's own parallel fan-out + aggregator | `MIN=3000 / MAX=6000` → 70 units |
| **rolling** | sequential carry-forward (`art6/ontology/carry_forward.py`) | `MIN=3000 / MAX=6000` → 70 units |

| run | model | arm | MAX_VISITS |
|---|---|---|---|
| `art6_gemma4_nochunk_mv1` | gemma-4-31b (local vLLM, temp 0.4) | nochunk | 1 |
| `art6_gemma4_native_mv1` | gemma-4-31b | native | 1 |
| `art6_gemma4_native_mv2` | gemma-4-31b | native | **2** |
| `art6_gemma4_rolling_mv1` | gemma-4-31b | rolling | 1 |
| `art6_gemma4_rolling_mv2` | gemma-4-31b | rolling | **2** |
| `art6_gpt5mini_nochunk_mv1` | gpt-5-mini (hosted, temp 1.0) | nochunk | 1 |
| `art6_gpt5mini_native_mv1` | gpt-5-mini | native | 1 |
| `art6_gpt5mini_native_mv2` | gpt-5-mini | native | **2** |
| `art6_gpt5mini_rolling_mv1` | gpt-5-mini | rolling | 1 |

Both response-level recovery patches were active in every arm:
`art6/ontology/response_repair.py` (malformed JSON envelopes) and
`art6/ontology/turtle_repair.py` (three classes of malformed Turtle). `run_native.py`
exists specifically so the native and nochunk arms run in-process and receive the same
patches as carry-forward — without it the comparison would be a patched pipeline against
two unpatched ones.

Method: `art6/ontology/quality_metrics.py` and `art6/ontology/validate_shapes.py` against
the live `echr_2.ttl` @ 3.3.0 and `echr-shapes.ttl`; quote fidelity by the same
normalization `validate_source_quotes.py` uses; plus a close reading of L1
(*Stanev v. Bulgaria*) against the source text in all nine graphs.

---

## Headline: two things broke, and one of them invalidates a whole column

**1. The repair pass did nothing in eight of the nine runs, and nothing at all on
gpt-5-mini.** Every gpt-5-mini repair call returned HTTP 400:

```
Unsupported parameter: 'max_tokens' is not supported with this model.
Use 'max_completion_tokens' instead.
```

The `max_tokens` cap was added on 2026-08-20 as a guard against a vLLM
runaway-generation hang, tested only against vLLM. OpenAI's reasoning models reject that
spelling outright. `repair_facts.py` caught the exception per document, printed
`10/10 document(s) failed to repair`, and **exited 0** — so the driver script recorded a
clean repair phase and the `repaired/` trees are byte-identical copies of `raw/`.

On gemma the same cap became the dominant failure mode from the other direction:
`LengthFinishReasonError` at exactly 3,000 completion tokens on 4/10, 6/10 and 4/10
documents in `native_mv1`, `native_mv2` and `rolling_mv2`. A backstop became a working
limit.

| run | documents repaired | failure |
|---|---|---|
| gemma native mv1 | 6/10 | 4 × length cap |
| gemma native mv2 | 4/10 | 6 × length cap |
| gemma nochunk mv1 | 10/10 | — |
| gemma rolling mv1 | 10/10 | — |
| gemma rolling mv2 | 6/10 | 4 × length cap |
| gpt5mini (all four) | **0/28 attempted** | 400 on every call |

Of gpt-5-mini's 35 documents, 28 had findings and needed a repair call — every one of
those 28 returned the 400. Six were reported clean by the deterministic finder before any
call was made and one (`rolling_mv1`'s collapsed L1) was skipped for having fewer than two
proceedings.

**Both defects are fixed in this commit** (`_token_limit_kwargs` picks the right parameter
per model with a fallback on the 400; `main()` now returns non-zero when any document
fails). Verified against the live endpoint on
`art6_gpt5mini_nochunk_mv1` — all five documents now complete, three had findings and the
model proposed 13 operations, of which 9 applied:

```
input.L1: 6 finding(s) in; model proposed 5 group(s), 5 op(s) — applied 5
input.L2: 4 finding(s) in; model proposed 4 op(s) — all 4 skipped, object node does not exist
input.L3: clean
input.L5: 4 finding(s) in; model proposed 4 op(s) — applied 4
input.L7: clean
```

The `repaired` columns below are still reported, because they are what the pipeline
actually produced, but **for gpt-5-mini `repaired` means `raw`** and no conclusion about
repair should be drawn from them. Re-running repair over the four gpt-5-mini arms is cheap
and should be done before they are compared on hygiene again.

**2. Where repair did run, it was worthless or mildly harmful.**

| run | triples | false merges | functional violations | duplicate authority names |
|---|---|---|---|---|
| gemma native mv1 | 2895 → 2866 | 62 → 60 | 175 → 161 | 5 → **9** |
| gemma native mv2 | 3531 → 3504 | 69 → 69 | 213 → 209 | 4 → **6** |
| gemma nochunk mv1 | 1271 → 1270 | 1 → **0** | 0 → 0 | 0 → 0 |
| gemma rolling mv1 | 3274 → 3156 | 10 → 8 | 9 → **11** | 0 → **1** |
| gemma rolling mv2 | 4051 → 4044 | 7 → 7 | 7 → 7 | 0 → 0 |

Thirteen applied operations across all of `rolling_mv1`, one across `native_mv1`. Two
metrics moved backwards. This is the third consecutive report to find that **repair
cannot recover identity information that chunking already discarded**; it is now also
clear that repair contributes nothing measurable to a graph that was extracted whole.

---

## Ranking

### Chunking strategy

| # | arm | score | one-line summary |
|---|---|---|---|
| **1** | **nochunk** | **8/10** | Overwhelmingly the cleanest graph: 9/10 SHACL-conformant, 4 violations, **zero** false merges, exactly 10 `CaseDocument` nodes, one LLM call per document. Recall is the lowest of the three, and on a hosted model with a 180 s timeout it is catastrophically brittle. |
| **2** | **rolling** | **6/10** | Recall roughly doubles, chunk loss is near-zero on gemma (0–1.4%), and the entity layer largely survives (7–11 false merges, ER 0.79–0.85). It pays with the worst scope leakage, the worst final-decision coverage, and a strictly sequential cost curve. |
| **3** | **native** | **2/10** | Confirms and worsens the 2026-08-19 verdict. 188–219 SHACL violations on gemma, 60–89 false merges, three `CaseDocument` nodes per document, the applicant split into two nodes. Highest raw recall, unusable topology. |

### Model

| # | model | score | one-line summary |
|---|---|---|---|
| **1** | **gemma-4-31b** | **7/10** | Better structural discipline on every arm that matters, 6–30× cheaper in wall clock, one LLM call per document at nochunk. Thinner evidence layer and one systematic wrong date. |
| **2** | **gpt-5-mini** | **6/10** | 2–4× the supporting quotes and much finer event granularity — genuinely more of the judgment is captured. Undone by heavy `Participation` over-reuse (43–46 merged nodes per native run), 23–24 authorities named only by a bare role, and a 180 s request timeout that silently deletes whole documents. |

### Overall combination

| # | combination | verdict |
|---|---|---|
| **1** | **gemma-4-31b + nochunk (mv1)** | The only configuration in this experiment that produces a graph you could load without triage. 9/10 conformant, 4 violations, 0 false merges, 5.4 min for 10 documents, 10 LLM calls. |
| **2** | gemma-4-31b + rolling (mv1 or mv2) | The right fallback for documents that do not fit one request. 19–25 violations, near-lossless, 2× the recall. |
| 3 | gpt-5-mini + rolling (mv1) | 5/10 conformant, 17 violations, 689 quotes — the richest evidence layer here — but 13% of the corpus lost to timeouts and 117 min for ten documents. |
| 4 | gpt-5-mini + nochunk | Best-in-class per document, but it lost **half the corpus**. See below. |
| 5–9 | anything + native | Do not use. |

---

## Mechanical metrics

`repaired` stage. Bold marks the best value where one direction is clearly better.
**`gpt5mini_nochunk_mv1` contains only 5 of 10 documents** — every absolute count in its
column is roughly half-scale and must not be compared directly.

### Volume and structure

| metric | g4 nochunk | g4 native mv1 | g4 native mv2 | g4 roll mv1 | g4 roll mv2 | 5m nochunk\* | 5m native mv1 | 5m native mv2 | 5m roll mv1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| triples | 1270 | 2866 | 3504 | 3156 | 4044 | 804 | 5104 | **6385** | 3991 |
| typed nodes | 223 | 479 | 541 | 546 | 708 | 140 | 858 | **999** | 659 |
| DomesticProceeding | 57 | 77 | 101 | 102 | 126 | 20 | 140 | **145** | 94 |
| DomesticAuthority | 48 | 80 | 71 | 84 | 82 | 25 | 87 | **107** | 52 |
| **CaseDocument (10 expected)** | **10** | 29 | 30 | **10** | **10** | 5 | 26 | 38 | 8 |
| supporting quotes | 74 | 232 | 313 | 244 | 424 | 100 | 705 | **956** | 689 |

\* five documents only.

The `CaseDocument` row is the single most diagnostic number in the table. Both nochunk and
both gemma rolling arms mint exactly one document node per document. **Native mints three
per document** — 29, 30, 26 and 38 for ten documents. The aggregator does not unify the
per-chunk document node, so the case identity itself is split (see the L1 reading below).

### Conformance and hygiene

| metric | g4 nochunk | g4 native mv1 | g4 native mv2 | g4 roll mv1 | g4 roll mv2 | 5m nochunk\* | 5m native mv1 | 5m native mv2 | 5m roll mv1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **files SHACL-conformant** | **9/10** | 1/10 | 0/10 | 2/10 | 2/10 | 2/5 | 0/10 | 0/10 | 5/10 |
| **SHACL violations** | **4** | 188 | 219 | 19 | 25 | 14 | 97 | 93 | 17 |
| SHACL warnings | **0** | **0** | **0** | **0** | 1 | **0** | **0** | **0** | **0** |
| **multi-label nodes (false merge)** | **0** | 60 | 69 | 8 | 7 | 3 | 83 | 89 | 27 |
| functional-property violations | **0** | 161 | 209 | 11 | 7 | 1 | 40 | 43 | 4 |
| duplicate authority names | **0** | 9 | 6 | 1 | **0** | **0** | 6 | 14 | **0** |
| authorities w/o `hasAuthorityName` | **0** | 1 | 3 | **0** | 3 | 1 | 5 | 7 | 8 |
| invented `echr:` terms | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| malformed typed literals | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| blank nodes | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| dangling `followsProceeding` targets | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |
| `followsProceeding` self-loops / 2-cycles | **0** | **0** | 1 / 1 | **0** | **0** | **0** | **0** | 1 / 1 | **0** |

**Closed-vocabulary discipline is now perfect: 0 invented `echr:` terms across all 85
graphs.** The `TypeGuardianship` / `TypeSocialSecurity` regression the previous report
warned about after restoring the prompt has not reappeared. Zero blank nodes, zero
malformed literals, zero dangling references, everywhere.

**Evidence anchoring is now a solved problem too.** Exactly **one**
`hasSupportingQuote minCount` warning across all 85 graphs — a single proceeding in
`gemma_rolling_mv2`. The previous report's headline
defect — three documents with no evidence layer at all — is gone, and the
`carry_forward.py` prompt fix is why.

Violations by shape:

| shape | g4 nochunk | g4 native mv1 | g4 native mv2 | g4 roll mv1 | g4 roll mv2 | 5m nochunk | 5m native mv1 | 5m native mv2 | 5m roll mv1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `rdfs:label maxCount 1` (false merge) | **0** | 60 | 69 | 8 | 6 | 1 | 40 | 43 | 3 |
| `participatingParty exactly 1` | 4 | 31 | 26 | 3 | 10 | 11 | 32 | 17 | 4 |
| `hasAuthorityName exactly 1` | **0** | 17 | 19 | 5 | 5 | 2 | 21 | 24 | 9 |
| date `maxCount 1` | **0** | 15 | 25 | 1 | 2 | **0** | **0** | 2 | **0** |
| `hasPartySide` closed-vocab `maxCount 1` | **0** | 14 | 18 | **0** | **0** | **0** | **0** | **0** | **0** |
| `hasCourt maxCount 1` | **0** | 14 | 15 | 1 | 1 | **0** | 1 | 3 | **0** |
| `hasOutcome` / `hasInstanceLevel` closed-vocab | **0** | 18 | 26 | **0** | 1 | **0** | **0** | **0** | **0** |

The closed-vocabulary `maxCount` rows are the aggregator's fingerprint and nothing else's:
18 and 26 nodes carrying two conflicting enumeration values in the native arms, **zero**
in every nochunk and rolling arm on both models. Two chunks characterised the same
proceeding differently and the aggregator merged them without resolving the conflict.
Carry-forward never produces this, because the model can see what it already asserted.

### Entity resolution

Authority nodes divided by distinct authority names; 1.0 means every distinct name got its
own node.

| run | authority nodes | distinct names | ratio |
|---|---:|---:|---:|
| gpt5mini rolling mv1 | 52 | 61 | **0.85** |
| gemma nochunk mv1 | 48 | 57 | **0.84** |
| gpt5mini nochunk mv1\* | 25 | 30 | 0.83 |
| gemma rolling mv1 | 84 | 107 | 0.79 |
| gemma rolling mv2 | 82 | 104 | 0.79 |
| gemma native mv1 | 80 | 109 | 0.73 |
| gpt5mini native mv2 | 107 | 158 | 0.68 |
| gpt5mini native mv1 | 87 | 140 | 0.62 |
| gemma native mv2 | 71 | 118 | **0.60** |

Native collapses a third to two-fifths of its named authorities out of existence.

### Delay module

| metric | g4 nochunk | g4 native mv1 | g4 native mv2 | g4 roll mv1 | g4 roll mv2 | 5m native mv1 | 5m native mv2 | 5m roll mv1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Adjournment | 0 | 1 | 3 | 1 | 5 | 4 | 4 | 4 |
| InactivityPeriod | 0 | 1 | 3 | 1 | 3 | 2 | 4 | 1 |
| **DelayAttribution** | **0** | **0** | **0** | **0** | **0** | **0** | **0** | **0** |

`DelayAttribution` has now gone unused across **85 graphs, three chunking strategies, four
models and two ontology versions.** The finding from all three previous reports stands
unchanged and should now be treated as settled: **ask for the delay module explicitly in
`facts.txt`, or delete it from the schema.**

---

## Content loss: which arm actually loses text

Every arm can silently drop a unit of text and still write an output file and report the
record as done. This is the axis on which the two nochunk arms diverge completely.

| run | units | units lost | text lost | documents affected |
|---|---:|---:|---:|---:|
| gemma rolling mv2 | 70 | **0** | **0%** | **0/10** |
| gemma nochunk mv1 | 10 | **0** | **0%** | **0/10** |
| gemma rolling mv1 | 70 | 1 | 1.2% | 1/10 |
| gemma native mv2 | 70 | 2 | ~3% | 1/10 |
| gpt5mini native mv2 | 70 | 2 | ~3% | 2/10 |
| gemma native mv1 | 70 | 3 | ~4% | 2/10 |
| gpt5mini native mv1 | 70 | 6 | ~9% | 4/10 |
| gpt5mini rolling mv1 | 70 | 8 | **13.1%** | 5/10 |
| **gpt5mini nochunk mv1** | **10** | **5** | **~50%** | **5/10** |

The mechanism differs by model and is worth separating, because only one of the two is a
prompting or modelling problem:

- **gemma**: unit loss is malformed output the recovery patches could not fix. Both
  patches earned their place — `turtle_repair` recovered 1, 7 and 8 renders across the
  three gemma runs that logged it, and `response_repair` recovered 1 — and gemma rolling
  mv2 came through **70/70 chunks clean**, which no arm managed before these patches
  existed.
- **gpt-5-mini**: unit loss is almost entirely the **180-second request timeout**
  (`LLM_REQUEST_TIMEOUT_SECONDS`, default 180.0). 16 timeouts in `rolling_mv1`, 5 in
  `nochunk_mv1`, 4 in `native_mv1`. gpt-5-mini is a reasoning model producing 700–950
  quote-bearing triples per document; on a whole 24,000-character judgment it routinely
  needs longer than three minutes, and there is no partial credit.

**This is why nochunk is simultaneously first and fourth in the ranking.** One unit per
document means one timeout deletes the entire document — `gpt5mini_nochunk_mv1` lost
L4, L6, L8, L9 and L10 outright. The arm is not intrinsically fragile; it was run with a
timeout set for a much faster model. `LLM_REQUEST_TIMEOUT_SECONDS=400` is now set in the
gpt-5-mini track script and this run should be repeated before nochunk is judged on
gpt-5-mini at all.

The native arm's loss is quieter and worse-behaved. Its log line is:

```
WARNING ontocast.stategraph.node_factories: Parallel facts map failed
        without usable output for 1/6 unit(s)
INFO    ontocast.tool.agg.aggregate: Starting aggregation with metadata for 5 units
```

It aggregates the survivors and moves on. `MAX_VISITS` is what governs how many render
attempts a unit gets (`Unit facts render failed at attempt 1/1`), which is exactly why
mv2 halves native's unit loss on both models — see below.

---

## Close reading: L1, *Stanev v. Bulgaria*, against the source text

L1 is a 23,991-character Grand Chamber judgment. The Article 6 complaint is that the
applicant had no access to a court to seek release from partial guardianship, so the
domestic proceedings that matter are §§10–12 (the incapacitation) and §§37–40 (the four
separate refusals to bring an action, ending in the only judicial decision the applicant
ever obtained).

Nine facts are asserted in the source and each is unambiguous:

1. Ruse Regional Court, judgment **20 Nov 2000**, applicant declared *partially* incapacitated (first instance; the prosecutor had asked for total incapacity)
2. Veliko Tarnovo Court of Appeal, judgment **12 Apr 2001**, upheld, on the applicant's appeal
3. Ruse Municipal Council, **23 May 2002**, appoints Ms R.P. as guardian
4. Rila Municipal Council, decision of **2 Feb 2005**, appoints the Director of the Pastra home as guardian
5. Ruse regional prosecutor, **10 Aug 2005**, refuses to bring an action to restore legal capacity
6. Appellate prosecutor, **11 Oct 2005**, upholds the refusal
7. Chief Public Prosecutor's Office at the Supreme Court of Cassation, **29 Nov 2005**, upholds it again
8. Mayor of Rila, **16 Sep 2005**, refuses to bring a court action
9. Dupnitsa District Court, judgment **10 Mar 2006**, application for judicial review dismissed, **not subject to appeal — the final domestic decision**

Scoring each graph on whether it asserts that date against that authority:

| run | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | score |
|---|---|---|---|---|---|---|---|---|---|---:|
| gemma native mv1 | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | **9/9** |
| gemma native mv2 | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | **9/9** |
| gemma rolling mv1 | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | **9/9** |
| gemma rolling mv2 | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ | **9/9** |
| gemma nochunk mv1 | ✔ | ✔ | ✔ | · | ✔ | ✔ | ✔ | ✔ | ✔ | 8/9 |
| gpt5mini nochunk mv1 | ✔ | ✔ | ✔ | · | ✔ | ✔ | ✔ | ✔ | ✔ | 8/9 |
| gpt5mini native mv2 | ✔ | ✔ | ✔ | · | ✔ | ✔ | ✔ | ✔ | ✔ | 8/9 |
| **gpt5mini native mv1** | ✔ | ✔ | ✔ | · | · | · | · | · | · | **3/9** |
| **gpt5mini rolling mv1** | · | · | · | · | · | · | · | · | · | **0/9** |

### What the two failures actually are

**`gpt5mini_native_mv1` lost the entire Article 6 core of the case.** The string
"Dupnitsa" does not appear anywhere in its L1 graph. Neither does any prosecutorial
refusal. The cause is traceable to a single log line:

```
18:56:55 ERROR ontocast.agent.render_facts: Failed to generate triples:
         LLM request exceeded 180.0s (openai/gpt-5-mini)
18:56:55 INFO  ontocast.stategraph.atomic: Unit facts render failed at attempt 1/1
18:56:55 WARN  ontocast.stategraph.node_factories: Parallel facts map failed
               without usable output for 1/6 unit(s)
```

One of L1's six chunks — the one carrying section D, *The applicant's attempts to obtain
release from partial guardianship* — timed out, got one attempt because `MAX_VISITS=1`,
and was dropped. The graph is 519 triples and looks healthy. It models the applicant's
leave of absence, his allowance increases, the Director's letter to the police and the
police reply, each with a verbatim quote and a `followsProceeding` link. It contains no
`isFinalDomesticDecision` at all. Its nine SHACL violations are all `Participation`,
label and authority-name issues — **not one of them indicates that a third of the
judgment is missing.**

### Structural validity is uncorrelated with completeness

Per-file SHACL over L1 makes the point sharper than any aggregate can:

| L1 graph | SHACL violations | conforms | ground-truth score |
|---|---:|---|---:|
| gemma nochunk mv1 | **0** | **yes** | 8/9 |
| **gpt5mini rolling mv1** | **0** | **yes** | **0/9** |
| gemma rolling mv1 | 2 | no | 9/9 |
| gpt5mini nochunk mv1 | 6 | no | 8/9 |
| gpt5mini native mv1 | 9 | no | 3/9 |
| gpt5mini native mv2 | 12 | no | 8/9 |
| gemma rolling mv2 | 12 | no | 9/9 |
| gemma native mv2 | 20 | no | 9/9 |
| gemma native mv1 | 23 | no | 9/9 |

**The two conformant graphs are the best and the worst graph in the experiment.**
`gpt5mini_rolling_mv1`'s L1 lost 72% of the document, contains zero `DomesticProceeding`
nodes, one authority and no proceeding of any kind — and it passes every shape cleanly,
because a graph that asserts almost nothing cannot violate a cardinality constraint.
Meanwhile `gemma_native_mv1` scores 9/9 on the facts and carries 23 violations.

Shape conformance measures whether what you extracted is well-formed. It says nothing
about whether you extracted it. **Any legal KG pipeline reporting SHACL conformance as a
quality figure without a completeness measure alongside it is reporting a number that goes
up when the extractor fails hardest.**

`native_mv2` recovers all of it (3/9 → 8/9) purely by giving the unit a second render
attempt.

**`gpt5mini_rolling_mv1` lost L1 almost entirely.** 4 of 6 chunks failed to timeouts; the
output is 138 triples, one `DomesticAuthority` (*the municipal social assistance
department*), and **zero** `DomesticProceeding`. The repair pass then skipped it with
`fewer than 2 DomesticProceeding nodes` — correctly, but that meant the one signal that a
document had collapsed went into a log nobody reads.

Item 4 — the Rila Municipal Council's decision of 2 February 2005 — is the one fact only
gemma ever gets, in all five of its runs. It is buried in reported speech in §17 ("*she
was informed that the Municipal Council had decided on 2 February 2005…*"), and every
gpt-5-mini graph models the letter of 16 September 2005 that reports it instead of the
decision it reports.

### Precision, not just recall

The best L1 graph in the experiment is **gemma nochunk** — 170 triples, 10 authorities,
3 proceedings, 3 `ProsecutorialReview` nodes, 3 `AdministrativeAction`, 1
`EnforcementAction`, 1 `CaseDocument`. The prosecutorial chain is exactly right:

```
prosecutorial_review_1  Ruse regional prosecutor    2005-08-10  OutcomeClaimDismissed
prosecutorial_review_2  appellate prosecutor        2005-10-11  OutcomeUpheldOnAppeal  follows _1
prosecutorial_review_3  Chief PP Office at the SCC  2005-11-29  OutcomeUpheldOnAppeal  follows _2
```

…each with a verbatim quote, and the judicial-review proceeding correctly linked to the
mayor's refusal and flagged `isFinalDomesticDecision true`. Zero SHACL violations on this
file. `hasStatusApplied echr:StatusPartialIncapacity` on the first-instance judgment
correctly captures that the court granted less than the prosecutor asked for.

**The native arm's L1 shows exactly what the aggregator does to a document.** Same model,
same text, 357 triples instead of 170, and:

```
=== echr:CaseDocument (3)
  doc:caseStanevBulgaria   hasCaseName "Stanev v. Bulgaria"; hasApplicant doc:stanevApplicant
  doc:case_1               hasApplicant doc:applicant_1; hasDomesticProceeding doc:proceeding_1
  doc:case_document_1      hasApplicant doc:applicant_1

=== echr:NaturalPerson
  doc:stanevApplicant  "Mr Rusi Kosev Stanev"   isRepresentedBy Genova, Lee, Nelson
  doc:applicant_1      "the applicant"          isRepresentedBy doc:lawyer_1
```

The case name lives on one node, the proceedings on a second, and the applicant exists
twice — once by name, once as "the applicant" — with the legal representation split
between them. No query can traverse from the case to its proceedings. Worse, the same
graph contains:

```
doc:part_4  a echr:Participation ;
    echr:hasPartySide echr:SideInitiating, echr:SideResponding ;
    echr:participatingParty doc:applicant_1, doc:regionalProsecutorRila .
```

One participation node asserting that the applicant and the prosecutor who applied against
him are the same participant, on both sides at once. That is two SHACL violations and a
statement that is not merely incomplete but false.

### A date error nothing catches

Every gemma run that extracts §34 writes:

```
doc:admin_action_3  rdfs:label "increase of allowance" ;
    echr:hasDecisionDate "2009-03-03"^^xsd:date .
```

The source says **3 February 2009**. gemma writes `2009-03-03` in four of its five runs;
gpt-5-mini writes `2009-02-03` correctly in all three of its runs that reach the fact.

This is worth dwelling on because of what it survives. The literal is a well-formed
`xsd:date`, so `malformed_literals` is 0. It has a correct verbatim `hasSupportingQuote`
attached, so quote validation passes. No shape constrains a date's *value*. It is a plain
factual error sitting inside a graph that passes every check in the repo. Across all ten
documents the rate is low — 1 to 6 dated assertions per run whose day-and-month do not
appear in the source, out of 45 to 239 — but nothing currently measures it, and the
project's whole purpose is proceeding chronology.

---

## The five criteria

### 1. Hallucinations

**Scope leakage persists and is now the clearest cost of small chunks.** Sweeping every
`DomesticAuthority` in all 85 graphs for bodies that are not domestic authorities at all:

| run | authority nodes | out of scope | named only by a bare role or collective |
|---|---:|---:|---:|
| gpt5mini nochunk mv1\* | 25 | 1 | **0** |
| gemma nochunk mv1 | 48 | **1** | **2** |
| gpt5mini rolling mv1 | 52 | 3 | 4 |
| gemma native mv1 | 80 | 2 | 10 |
| gemma native mv2 | 71 | 5 | 10 |
| gemma rolling mv1 | 84 | 8 | 8 |
| gemma rolling mv2 | 82 | **9** | 7 |
| gpt5mini native mv2 | 107 | 3 | 23 |
| gpt5mini native mv1 | 87 | 4 | **24** |

The out-of-scope nodes are the same families the previous report named, and they cluster
in the 3,000-character arms:

- **International organisations as domestic authorities** — `European Space Agency`,
  `ESRO`, `European Space Operations Centre`, `Committee of Staff Representatives of the
  Coordinated Organisations`, `Appeals Board of the Agency` (all L2, rolling);
  `CJEU` (L10, rolling mv2). ESA is the *respondent* in the immunity dispute, not a
  Bulgarian or German authority.
- **Precedent-only bodies** — `Federal Labour Court` (L2), which appears only as the
  *Waite and Kennedy* citation. Present in three arms; absent from both nochunk arms.
- **Private entities** — `Türkiye Sınaî Kalkınma Bankası A.Ş.` / `Industrial Development
  Bank of Türkiye` (L3), `Makhachkala Sea Port` (L8), `Maribor General Hospital` and
  `Slovenj Gradec General Hospital` (L4).
- **Foreign/historical** — `NKVD` (L7, gpt5mini rolling).
- **The respondent State itself** — `Belgian State` / `Belgian Government` as a
  `DomesticAuthority` (L10, four arms).

**Both nochunk arms carry exactly one out-of-scope node each** (ESA on L2), and gemma
nochunk's L2 authority list is two entries long: *Darmstadt Labour Court*, *European Space
Agency*. Compare gemma rolling mv1's nine.

The bare-role column is a different and more mundane failure: an authority whose only name
is `the investigator`, `the prosecutor`, `first-instance court`, `German courts`, `the
administrative courts`, `the courts which had reviewed the decision of the Bar`. These are
real bodies, unusably identified. gpt-5-mini on native produces 23–24 of them; gemma
nochunk produces two.

**Quote fidelity is uniformly acceptable and is not a differentiator:**

| run | quotes | verified verbatim | unverified |
|---|---:|---:|---:|
| gemma native mv2 | 313 | 302 | 11 (3.5%) |
| gpt5mini rolling mv1 | 689 | 662 | 27 (3.9%) |
| gpt5mini native mv1 | 705 | 677 | 28 (4.0%) |
| gemma native mv1 | 232 | 222 | 10 (4.3%) |
| gemma rolling mv2 | 424 | 405 | 19 (4.5%) |
| gpt5mini native mv2 | 956 | 912 | 44 (4.6%) |
| gemma rolling mv1 | 244 | 231 | 13 (5.3%) |
| gemma nochunk mv1 | 74 | 69 | 5 (6.8%) |
| gpt5mini nochunk mv1\* | 100 | 92 | 8 (8.0%) |

3.5–8% across the board with no clean separation by arm or model. The nochunk arms score
slightly worse on rate but assert far fewer quotes, so the absolute count of unverified
quotes is lowest there.

### 2. Missing triples

**Recall is where nochunk pays for its hygiene.** gemma nochunk asserts 57 proceedings, 74
quotes and 63 dated assertions across ten documents; gemma rolling mv2 asserts 126, 424
and 224. The L1 reading shows the difference is real coverage, not padding: nochunk misses
the Rila Municipal Council decision, the municipal social assistance department's
allowance chain, the Ministry of Labour and Social Policy, and the Ruse municipal police —
all real actors named in the judgment and all found by the chunked arms.

**gpt-5-mini's evidence layer is in a different class.** 705–956 quotes against gemma's
232–424, at the same or better verification rate. Its L1 graph models every administrative
step with a quote and a chain link — the welfare report, the residence registration, the
leave authorisation, the police request and the police reply — where gemma models three
administrative actions. If the goal is a dense event chronology rather than a court
hierarchy, gpt-5-mini extracts substantially more of the judgment.

**Final-decision coverage is the one recall metric where nochunk wins outright:**

| run | docs with exactly one `isFinalDomesticDecision` | none | more than one |
|---|---:|---:|---:|
| gemma nochunk mv1 | **9/10** | 1 | **0** |
| gemma native mv1 | 8/10 | 2 | **0** |
| gemma native mv2 | 6/10 | 1 | 3 |
| gemma rolling mv1 | 6/10 | 4 | **0** |
| gemma rolling mv2 | 5/10 | 5 | **0** |
| gpt5mini native mv1 | 5/10 | 4 | 1 |
| gpt5mini native mv2 | 4/10 | 3 | 3 |
| gpt5mini rolling mv1 | 4/10 | 6 | **0** |
| gpt5mini nochunk mv1\* | 2/5 | 3 | **0** |

Identifying the final domestic decision requires seeing the whole procedural history at
once — which is precisely what chunking removes. Rolling forward carries the *graph*
forward but not the *judgment* that the decision just read is the last one, so it
under-asserts (4–6 of 10, never over-asserts). Native, seeing neither, does both.

Other gaps:

- **Outcome and date coverage is thinnest on gpt-5-mini**: 64–72 of its proceedings carry
  no `hasOutcome` and 62–68 no date, against 13 for gemma nochunk. It models many small
  events that genuinely have no outcome, but the ratio is still worse.
- **`DelayAttribution` unused in all 85 graphs.**

### 3. Adherence to `echr_2.ttl` @ 3.3.0

Perfect closed-vocabulary discipline everywhere: **0 invented `echr:` terms in 85 graphs**,
every enumeration member drawn from the ontology's own `owl:oneOf` lists, on both models
and all three arms. This is now the third report in a row where the vocabulary constraint
holds and the *identity* constraints do not, and it is worth stating plainly: **the models
are not the problem with schema adherence — the aggregation step is.**

The `SingleLabelShape` continues to earn its place, catching 60, 69, 40 and 43 native
merges that no other check detects. The `hasAuthorityName exactly 1` shape catches 17–24
more in the same arms.

### 4. Quality of triples extracted

The character of the residual defects, by arm:

**Native** destroys the entity layer:

```
input.L3  administrativeCourt  10 labels: 13th Chamber of the Supreme Administrative Court |
          6th Chamber of the İstanbul Administrative Court | General Assembly of the
          Administrative Proceedings Divisions of the SAC | Supreme Administrative Court | …
input.L10 conseilEtat          13 labels: Aliens Appeals Board | Aliens Office | Brussels
          Court of Appeal | Conseil d'État | French-language enforcement judge |
          President of the Brussels Dutch-speaking TPI | …
input.L4  applicant             9 labels: Aleksander Matko | I.G. | Public Prosecutor |
          Republic of Slovenia | Slovenj Gradec Police | the MIA | the applicant
input.L10 proceeding_2         13 labels: (five distinct appeals and a cassation, merged)
```

A node asserting that the applicant, the police, the prosecutor and the Republic of
Slovenia are the same entity is not repairable — the information needed to un-merge it was
discarded at aggregation. **21–23 of native's merged nodes are `DomesticProceeding`
merges**, which is worse than authority merges: it destroys the instance hierarchy
directly.

**Rolling's merges are mostly a milder, mechanically repairable defect** — over-reuse of
one `Participation` node across several proceedings:

```
input.L9  part_remand_investigating_authorities_2005_07_27   4 labels
          (same party, same role, four different proceedings)
input.L8  part_app_appeal_1997                               4 labels
```

24 of gpt5mini rolling's 27 merged nodes are `Participation`; 43–46 of gpt5mini native's
83–89 are too. The party and the role are correctly identified and then attached to
several proceedings through one reified node instead of one per proceeding. **Splitting by
`participatesIn` object is a scripted fix**, not an LLM problem, and it would remove more
than half of gpt-5-mini's total violation count on every arm.

The genuinely bad rolling nodes are few and identifiable: gemma rolling mv2's L1
`directorPastraHome` carries *Director of the Pastra social care home* / *Pastra social
care home* / *social worker of the Pastra home* on one node typed
`DomesticAuthority + NaturalPerson + Party`, and gemma rolling mv1's L3 `adminCourtTr`
merges the first-instance and supreme administrative courts.

**Chain integrity is good everywhere and is not a differentiator**: 0–2 appeal-shaped
outcomes without a `followsProceeding` link per run, and **zero** dangling
`followsProceeding` targets in any of the 85 graphs.

### 5. Ease of formatting into a network

1. **gemma nochunk** — 4.4 components per case, 0.86 largest-component share, 4 SHACL
   violations, one `CaseDocument` per document, no false merges. **Directly buildable.**
2. **gemma rolling** — 5.3–8.1 components per case and 39–62 singletons, but a correct
   entity layer under the fragmentation. The `Participation` splits are scriptable, the
   scope leakage needs a filter. Buildable after scripted work.
3. **gpt5mini rolling** — 3.1 components per case and the highest entity-resolution ratio
   in the experiment (0.85), but 13% of the corpus is missing. Buildable after re-running
   at a higher timeout.
4. **native, either model** — the connectivity numbers look respectable (3.5–6.1
   components per case, up to 0.93 largest-component share) and are an illusion: merging
   the first-instance court, the court of appeal and the court of cassation into one node
   *raises* the largest-component share while destroying the graph's meaning. **Not
   buildable** without re-extraction.

---

## What MAX_VISITS=2 buys, and what it does not

| pair | unit loss | recall | hygiene | wall clock |
|---|---|---|---|---|
| gemma native mv1 → mv2 | 3 → **2** | 2866 → **3504** triples | 188 → **219** violations, 60 → **69** merges | 6.9 → 7.9 min (+15%) |
| gpt5mini native mv1 → mv2 | 6 → **2** | 5104 → **6385** triples | 97 → 93 violations, 83 → **89** merges | 38.4 → 35.8 min |
| gemma rolling mv1 → mv2 | 1 → **0** | 3156 → **4044** triples, 244 → **424** quotes | 19 → **25** violations | 3.9 → 29.7 min\* |

**MAX_VISITS=2 buys robustness and recall, and never buys hygiene.** It halves or
eliminates unit loss on every arm, adds 20–30% more triples, and takes `gpt5mini_native`'s
L1 from 3/9 to 8/9 on the ground-truth checklist — but violations and false merges go
*up* in four of the six comparisons, because it is extracting more of the same
aggregator-broken structure. It is the right setting when content loss is the concern and
irrelevant when structure is.

\* The rolling mv1 → mv2 timing gap is not a fair 7.6×. See the caveat below.

---

## Cost

| run | wall clock | per document | LLM calls | calls per unit |
|---|---:|---:|---:|---:|
| gemma nochunk mv1 | 5.4 min | 33 s | **10** | **1.0** |
| gemma native mv1 | 6.9 min | 41 s | 90 | 1.3 |
| gemma native mv2 | 7.9 min | 48 s | 143 | 2.0 |
| gemma rolling mv1 | 3.9 min\* | 23 s\* | 9\* | 0.1\* |
| gemma rolling mv2 | 29.7 min | 178 s | 180 | 2.6 |
| gpt5mini nochunk mv1 | 19.9 min\* | 238 s\* | 2\* | 0.2\* |
| gpt5mini native mv1 | 38.4 min | 230 s | 72 | 1.0 |
| gpt5mini native mv2 | 35.8 min | 215 s | 153 | 2.2 |
| gpt5mini rolling mv1 | 117.2 min | 703 s | 55\* | 0.8\* |

\* **These three runs were partly served from the LLM response cache**
(`.cache/ontocast/llm/`) and their wall clocks and call counts are not usable:
`gemma_rolling_mv1` made 9 live calls for 70 units, `gpt5mini_nochunk_mv1` made 2 live
calls for 10 units, `gpt5mini_rolling_mv1` made 55 for 70. This is a consequence of
re-running arms after the `turtle_repair` fixes landed. **Quality conclusions are
unaffected** — a cache hit replays the model's own output byte-for-byte, and the Turtle
recovery patches run downstream of it — but no cost conclusion should be drawn from those
three rows.

The structural points survive:

- **nochunk is one LLM call per document.** Nothing else comes close, and at 48,000
  documents that dominates everything.
- **Carry-forward is sequential by construction** and cannot be parallelised within a
  document. Native issues 70 requests it can overlap; rolling issues 70 it cannot.
- **gpt-5-mini is 5–6× slower per document than gemma-4-31b** on identical work
  (215–238 s vs 33–48 s), before considering price.
- The two model tracks ran on independent backends (local vLLM vs hosted API) and were
  deliberately run concurrently, so there is no contention confound between them. Within
  the gemma track, all runs were sequential.

---

## What to do

Ordered by expected value. Items 1–3 are corrections to defects this experiment revealed
in the tooling, not the models, and should land before the next run.

1. **`repair_facts.py` output-cap fix — done in this commit.** Pick
   `max_completion_tokens` for gpt-5/o-series and `max_tokens` otherwise, with a fallback
   on the 400; and return a non-zero exit code when any document fails to repair, so a
   driver script cannot log a repair phase as clean when it repaired nothing. Verified
   working against the live endpoint. **The gpt-5-mini arms of this experiment have never
   been repaired and their `repaired/` directories are copies of `raw/`** — re-run repair
   over all four before comparing them on hygiene again.

   The smoke test also confirms recommendation 7 from the other direction: on L2 the model
   tried to fix the shared-`Participation` violations by adding party nodes that do not
   exist in the graph, and all four operations were correctly rejected. The LLM cannot
   split those nodes; a script keyed on `participatesIn` can.

2. **Raise the repair output cap and make truncation visible.** 3,000 completion tokens is
   below what a 25-finding document needs; it cost 14 of 50 gemma documents their repair
   pass. Raise it to ~8,000 and treat `finish_reason == "length"` as a reportable
   condition rather than a silent per-document failure.

3. **Set `LLM_REQUEST_TIMEOUT_SECONDS` per model, not globally.** 180 s is the single
   largest source of content loss in this experiment: it deleted half of
   `gpt5mini_nochunk_mv1` and 13% of `gpt5mini_rolling_mv1`. 400 s is now in the
   gpt-5-mini track script. **Re-run `gpt5mini_nochunk_mv1` before drawing any conclusion
   about gpt-5-mini on whole documents** — its column here describes a configuration that
   should not have been run.

4. **Adopt nochunk as the default and stop running native entirely.** Native is the worst
   arm on every structural measure, on both models, at both MAX_VISITS settings, for the
   third report running. Its 188–219 violations are not a tuning problem; the aggregator
   discards the identity information at merge time and no downstream pass can recover it.
   nochunk gives 4 violations, 0 false merges, one `CaseDocument` per document and one LLM
   call per document.

   **Correction to a figure carried forward from the 2026-08-19 report**, which said the
   corpus median document is ~6,000 characters. Measured directly: the combined corpus
   median is **10,390** characters and **24.5%** of documents exceed 20,000. Judgments
   alone are longer — median **16,337**, with **41.3%** over 20,000 and a maximum of
   1,006,653. So nochunk covers roughly three quarters of the combined corpus and about
   three fifths of judgments, not "the great majority". Chunking is needed for a real
   minority of the corpus, which makes recommendation 5 load-bearing rather than
   incidental.

5. **Keep carry-forward for documents that do not fit**, and raise the chunk size above
   3,000 first. Every rolling-specific defect that is not shared with nochunk — the ESA
   and CJEU leakage, the precedent bodies, the thin final-decision coverage — traces to
   chunks too small to carry document structure. A rolling run at `MIN=8000 / MAX=16000`
   is the obvious next experiment and was already the previous report's recommendation 3;
   it has still not been run.

6. **Fail a document, not a chunk.** All three arms will write an output file and report a
   record as done after losing a unit. `gpt5mini_native_mv1`'s L1 is 519 triples, fully
   quote-anchored, SHACL-clean, and missing the final domestic decision — nothing in the
   pipeline noticed. Emit a per-document `units_lost` field and treat non-zero as a failed
   record. `run_native.py`'s report already computes the raw counts; they just are not
   surfaced per document.

7. **Split over-reused `Participation` nodes mechanically.** One party, one role, several
   proceedings, one reified node. It is 24 of 27 merged nodes in gpt5mini rolling and
   43–46 of 83–89 in gpt5mini native, and it is the single largest violation category on
   both nochunk arms (4 and 11). A script keyed on `participatesIn` fixes all of them and
   would take gemma nochunk from 4 violations to 0 and gpt-5-mini's arms from 93–97 to
   roughly half. **No LLM is needed for this and it should not be in the repair prompt.**

8. **Add a date-fidelity validator.** gemma writes `2009-03-03` where the source says
   3 February 2009, in four runs out of five. The literal is well-formed, the attached
   quote verifies, and no shape constrains a date's value, so nothing catches it. A
   checker in the shape of `validate_source_quotes.py` — parse the date, look for its
   day-and-month rendering in the source text — would take an hour and closes a hole in a
   project whose whole subject is chronology. Expect a low single-digit percentage rate
   and a handful of legitimate false positives from date ranges ("*from 31 May to 15 June
   2005*").

9. **Add a shape for bare-role authority names.** `the investigator`, `the prosecutor`,
   `German courts`, `the administrative courts`, `the courts which had reviewed the
   decision of the Bar` — 24 such nodes in `gpt5mini_native_mv1`, 2 in gemma nochunk. A
   `sh:pattern` rejecting names that are only a definite article plus a generic role would
   catch most of them and give the repair pass something it can actually act on.

10. **Resolve the delay module.** `DelayAttribution` has now gone unused across 85 graphs,
    three chunking strategies, four models and two schema versions. Either name it
    explicitly in `facts.txt` with an example, or delete it and `Adjournment` /
    `InactivityPeriod` along with it.

11. **Reconsider what the repair pass is for.** It applied 14 operations in total across
    the five runs where it functioned, moved two metrics backwards, and contributed
    nothing to the one arm that produces a usable graph. Items 7, 8 and 9 are all
    deterministic checks that would do more than four LLM passes did. The honest framing
    is that repair was built to clean up after chunking; if recommendation 4 is adopted,
    most of its reason to exist goes away.

---

## Is this as good as it gets?

Partly. It is worth separating what is now solved from what is not.

**Solved, and unlikely to improve further by prompting:**

- Closed-vocabulary discipline. 0 invented terms in 85 graphs. This is finished.
- Evidence anchoring. 0 unquoted-entity warnings across nine runs, against 60 in the
  previous report. The `carry_forward.py` prompt fix closed it.
- Malformed output. `response_repair` and `turtle_repair` took gemma rolling to 70/70
  chunks clean. Remaining gemma unit loss is 0–4%, and the residue is genuinely rare.
- Structural hygiene *on whole documents*: 4 SHACL violations across ten documents, all
  one repairable pattern, no false merges at all. **gemma nochunk is close to the ceiling
  of what this pipeline can produce and the remaining defects are scriptable, not
  linguistic.**

**Not solved, and not fixable by prompting:**

- **The native aggregator.** Three reports have now measured it and it has never been
  usable. This is a code defect in how per-chunk graphs are merged, not a model behaviour,
  and it will not respond to a better prompt.
- **Scope leakage at 3,000 characters.** The rule forbidding precedent is in the prompt
  and the model breaks it anyway, because at 3k it cannot tell which section of the
  judgment it is reading. Only a larger window fixes this, which is recommendation 5.
- **Final-decision identification under chunking.** It requires seeing the whole
  procedural history at once. Rolling forward carries the graph but not that judgment.

**Genuinely improvable, and where the next gains are:**

- **Content loss is now the binding constraint, and it is entirely operational.** The two
  worst results in this experiment — half of `gpt5mini_nochunk_mv1` and 13% of
  `gpt5mini_rolling_mv1` — are one config value. Fixing the timeout is likely to move
  gpt-5-mini from fourth to first or second on its own.
- **Half of gpt-5-mini's violations are one scripted fix away** (recommendation 7). Its
  evidence layer is 2–4× denser than gemma's at equal fidelity; if the `Participation`
  split lands, gpt-5-mini + nochunk at a 400 s timeout is the configuration most likely to
  beat gemma nochunk outright, and that experiment has not been run.
- **The two models fail differently and could be combined.** gemma gets the procedural
  skeleton right and misses events; gpt-5-mini gets the events and mangles identity. That
  is a suggestive division of labour, not a proven one.

The realistic near-term ceiling: gemma nochunk with recommendation 7 applied would be a
zero-violation, zero-false-merge graph today. The interesting question is whether
gpt-5-mini at a sane timeout beats it on recall without giving that back, and this
experiment cannot answer it because gpt-5-mini was never given a fair nochunk run.

---

## Caveats

- **The `repaired` stage is meaningless for all four gpt-5-mini runs** — every repair call
  failed with a 400 and the directories are byte-identical to `raw/`. Read those columns
  as raw extraction output.
- **`gpt5mini_nochunk_mv1` contains 5 of 10 documents.** Every absolute count in its column
  is roughly half-scale. It is included because the *cause* of the loss is the finding, not
  because the column is comparable.
- **Three runs were partly cache-served** and their wall clocks and call counts are
  unusable: `gemma_rolling_mv1` (9 live calls for 70 units), `gpt5mini_nochunk_mv1` (2 for
  10), `gpt5mini_rolling_mv1` (55 for 70). Quality is unaffected — the cache replays the
  model's own bytes and the recovery patches run downstream.
- The scope-leakage figures come from a full mechanical sweep of every `DomesticAuthority`
  label in all 85 graphs against a curated list, checked by hand for the runs quoted. The
  bare-role figures use an exact-match list and are therefore a floor, not a total.
- n = 10 cases, English only, selected by `build_ontocast_test_set.py` as `max(len(facts))`
  per slot. The sample is deliberately weighted toward long judgments and is **not**
  representative of the corpus — which matters directly for recommendation 4, since the
  judgment corpus median is 16,337 characters against this set's ~25,000, and 41.3% of
  judgments exceed 20,000 — so the sample is longer than typical but not wildly
  unrepresentative, and chunking remains relevant for a substantial minority of the corpus.
- The close reading covers L1 in all nine graphs against the source text, plus mechanical
  sweeps over all ten. The other nine documents were not read line by line; L2, L3, L4,
  L6, L9 and L10 were inspected only where a metric flagged them.
- Timings for the gemma track are uncontended (local vLLM, runs strictly sequential). The
  gpt-5-mini track ran concurrently against a hosted API on a separate backend, which is
  deliberate and introduces no contention with gemma.
- All mechanical figures come from `art6/ontology/quality_metrics.py`,
  `art6/ontology/validate_shapes.py` and the normalization in
  `art6/ontology/validate_source_quotes.py`, run against the live `ontology/echr_2.ttl`
  @ 3.3.0 and `ontology/echr-shapes.ttl`, and are reproducible from the delivered graphs.
