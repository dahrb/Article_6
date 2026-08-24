# Extraction quality evaluation — five gemma arms: repair, format, chunk size, MAX_VISITS

Assessment of five extraction arms over the same ten cases, from the experiment of
2026-08-23 (`results/experiment_arms_20260823_223700/`).

Prepared 2026-08-24. Companion to `docs/extraction_quality_report_3arm.md` (2026-08-21,
three chunking strategies × two models), `docs/extraction_quality_report_chunking.md`
(2026-08-19) and `docs/extraction_fixes_evaluation.md` (2026-08-19).

Four questions were put to the pipeline, all on gemma-4-31b alone:

1. do the new staged repair features work,
2. does a larger rolling-window chunk size help,
3. turtle or jsonld,
4. does `MAX_VISITS=2` belong on whole-document extraction.

**This is the first run in the series with no cache contamination anywhere.**
`LLM_CACHE_ENABLED=false` is forced into every arm's env, so every wall clock and call
count below is live. Three of the nine rows in the previous report's cost table were
unusable for exactly this reason.

---

## What was run

Five arms, each varying **exactly one** axis from `nochunk_ttl_mv1`, driven by the new
`art6/ontology/run_arms.sh`. Same ten judgments (`input.L1`…`L10`, 251,574 characters),
same model, same temperature 0.4, same `facts.txt`, same ontology.

| arm | assembly | chunk | MAX_VISITS | format |
|---|---|---|---|---|
| **`nochunk_ttl_mv1`** | one unit/document | `20000/50000` | 1 | turtle |
| `nochunk_jsonld_mv1` | one unit/document | `20000/50000` | 1 | **jsonld** |
| `nochunk_ttl_mv2` | one unit/document | `20000/50000` | **2** | turtle |
| `rolling_3k6k_mv1` | sequential carry-forward | **`3000/6000`** | 1 | turtle |
| `rolling_8k16k_mv1` | sequential carry-forward | **`8000/16000`** | 1 | turtle |

Held constant: ontology `echr.ttl` @ **3.5.0**, `RENDER_MODE=facts`,
`CHUNK_SECTION_CLASSIFIER=off`, `ONTOLOGY_CONTEXT_MODE=fixed_single_ontology` +
`ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr`, both recovery patches
(`response_repair.py`, `turtle_repair.py`) enabled in every arm.

Repair: `--passes 2` **per stage**, three stages (proceedings / persons / authorities).

The baseline arm additionally carries **three** repair trees built from the same `raw/`,
which is what makes the repair question answerable:

| tree | source | staging | ontology fragment |
|---|---|---|---|
| `repaired_prestage/` | `90c5799` | single call | buggy |
| `repaired_legacy/` | `ce78258` | 3 stages | buggy |
| `repaired/` | working tree | 3 stages | **fixed** |

Method: `art6/ontology/validate_shapes.py`, `diagnostics/quality_metrics.py`,
`diagnostics/repair_impact.py`, `diagnostics/validate_source_quotes.py` and
`diagnostics/validate_dates.py` against the live `echr.ttl` @ 3.5.0 and
`echr-shapes.ttl`.

---

## Headline: the repair pass stopped being a deletion pass

The 2026-08-19 evaluation's verdict was blunt — *"It is a deletion pass, not a repair
pass"*, measured at **6 applied adds against 68 applied removes** — and its structural
complaint was that the pass **could not create a missing entity, only re-point at ones
that already existed**, putting the largest gap in the corpus permanently out of reach.

Applied operations on the baseline arm, same `raw/` input for all three:

| repair version | ops | adds | removes | skipped "object node does not exist" |
|---|--:|--:|--:|--:|
| pre-staging (`90c5799`) | 9 | 6 | 3 | **2** |
| legacy, staged w/ fragment bug (`ce78258`) | 9 | 8 | 1 | 0 |
| **staged, current** | **22** | **22** | **0** | 0 |

The old limitation is visible in the first row: two `echr:participatingParty` adds were
skipped because the party node the model wanted to link to did not exist. The staged
version mints such nodes outright, fully populated:

```
doc:house_of_lords  rdf:type                   echr:DomesticAuthority
doc:house_of_lords  echr:hasAuthorityName      "House of Lords"
doc:house_of_lords  echr:hasAuthorityKind      echr:AuthorityJudicial
doc:house_of_lords  echr:hasJurisdictionState  "United Kingdom"
```

Four authorities were created this way across L1 and L7, plus seven `hasAuthorityName`
fills on existing nodes and one complete `Participation` (`participatingParty` +
`hasPartySide`).

### But it only closes half the gap

The new authorities are **never wired to the proceedings that lack a court**:

| | raw | repaired |
|---|--:|--:|
| proceedings with no `hasCourt` | 19 | **19** |
| singleton nodes | 39 | **43** |
| connected components | 54 | **57** |

`proc_no_court` does not move, and singletons rise by exactly the four new nodes. The
graph gained four correct entities and **zero new edges**.

The cause looks structural rather than a model failure: **`authorities` runs last**, so
when the `proceedings` stage ran there was nothing to link to, and by the time the
authority exists no stage revisits the proceeding. Either the authorities stage should
attach its new node to the proceeding that prompted it, or a proceedings pass should run
after authorities. This is the single highest-value follow-up in this report.

---

## SHACL conformance

| arm | raw | repaired | violations |
|---|---|---|---|
| `nochunk_ttl_mv1` | 7/10 | **8/10** | 7 → 2 |
| `nochunk_jsonld_mv1` | 8/10 | **9/10** | 7 → 2 |
| `nochunk_ttl_mv2` | 8/10 | 8/10 | 3 → 3 |
| `rolling_3k6k_mv1` | 3/10 | **6/10** | 22 → 12 |
| `rolling_8k16k_mv1` | 4/10 | 4/10 | 12 → 10 |

Baseline arm, three repair trees from identical input:

| tree | conformant | violations |
|---|---|---|
| raw | 7/10 | 7 |
| `repaired_prestage` (`90c5799`) | 7/10 | **7** — no net effect |
| `repaired_legacy` (`ce78258`) | 8/10 | 3 |
| **`repaired`** (current) | **8/10** | **2** |

The pre-staging repair cleared **nothing** on this arm: it applied six operations, but
three of them were a `hasCourt` remove-then-re-add pair that left conformance where it
started, and its one substantive pass stopped early with *"6 op(s) applied but findings
did not fall (2 → 5)"*.

---

## Mechanical metrics, all five arms

`raw → repaired` where repair moved the number.

| metric | ttl_mv1 | jsonld_mv1 | ttl_mv2 | roll_3k6k | roll_8k16k |
|---|--:|--:|--:|--:|--:|
| triples | 1277→1298 | 1134→1142 | 1462→1461 | 3148→3120 | 2001→1996 |
| typed nodes | 232→236 | 202 | 250 | 561→559 | 335 |
| proceedings | 60 | 50 | 61 | 104→99 | 73→71 |
| `followsProceeding` | 36 | 37 | 54→53 | 103→102 | 73→72 |
| functional violations | **0** | 1 | **0** | 6→5 | 2 |
| singletons | 39→43 | 14 | 18 | 57→58 | 23 |
| components | 54→57 | 27 | 31 | 74→75 | 35 |
| proceedings w/o court | 19 | **1** | 11 | 7 | 3→2 |
| proceedings w/o outcome | 19 | 10 | 13 | 17 | 8→7 |
| supporting quotes | 82 | 61 | 87 | **292** | 146→144 |

**Functional-property collisions have essentially vanished.** This was the previous
report's *"most damaging defect class"* at 36 violations on gemma-turtle, halved to 19 by
repair. Both nochunk turtle arms now produce **zero in raw output**, before any repair.
Rolling still shows a few (6 and 2), which localises the defect to chunked assembly
rather than the model.

---

## Q2 · Rolling window: bigger is better on every axis measured

Recommendation 5 of the previous report named `MIN=8000/MAX=16000` as *"the obvious next
experiment"* and recorded that *"it has still not been run"*. It has now.

| | `3k6k` | `8k16k` | change |
|---|--:|--:|---|
| extraction | 923.2s | **543.0s** | **−41%** |
| repair | 1392.5s | **537.0s** | **−61%** |
| SHACL violations (raw) | 22 | **12** | −45% |
| functional violations | 6 | **2** | −67% |
| proceedings extracted | 104 | 73 | −30% |
| proceedings w/o court | 7 | 3 | −57% |
| documents losing a repair stage | 2/10 | **1/10** | — |

The larger window is cheaper, cleaner and less fragmented. The one number that falls is
proceedings extracted (104 → 73), and that is very likely a **correction rather than a
loss**: 3k/6k also produces 22 SHACL violations and 6 functional collisions, the
signature of one real proceeding split across chunks and re-minted each time. The
previous report reached the same conclusion from the other direction — every
rolling-specific defect it found *"traces to chunks too small to carry document
structure"*.

**Both rolling arms remain worse than not chunking at all** (4/10 and 6/10 conformant
against 8/10), which is consistent with every previous report in this series.

---

## Q3 · turtle vs jsonld: the first clean comparison

The only prior comparison was confounded — the 2026-08-18 run also changed
`ONTOLOGY_CONTEXT_MODE`, and recommendation 6 of that report asked for a re-run varying
only the format. This is it.

| | turtle | jsonld |
|---|--:|--:|
| extraction | 372.7s | **502.6s (+35%)** |
| repair | 408.2s | 311.5s |
| repair model calls | 12 | **9** |
| SHACL conformant (raw) | 7/10 | **8/10** |
| triples | 1277 | 1134 |
| proceedings | 60 | 50 |
| **proceedings w/o court** | 19 | **1** |
| supporting quotes | 82 | 61 |

**This is not the clean win for turtle the cost argument assumed.** jsonld costs 35% more
wall clock — less than the ~2.9× ontology-context token ratio measured via `/tokenize`,
because the context is only part of each request — and buys a materially better-connected
graph: **1 proceeding without a court against 19**, one more conformant document, and
three fewer repair calls needed.

turtle extracts *more* (60 proceedings vs 50, 82 quotes vs 61) but leaves a third of its
proceedings unattached to any deciding body. jsonld extracts less and connects nearly all
of it.

Which is preferable depends on whether recall or structural completeness matters more,
and **the automated metrics here cannot settle that** — it needs the judge tier or a
close reading. This report does not call it. What it does establish is that the format
choice is a real quality variable and not merely a token-cost optimisation, which is what
`run_experiment.sh`'s own comment always suspected: *"It is NOT known to be
quality-neutral … a genuine experimental variable, not a free optimisation."*

---

## Q4 · MAX_VISITS=2 at nochunk: costs 54%, buys nothing

Never run before, for either model. The JURIX plan flagged it for day 1 specifically
because the one run that lost content to timeouts (`gpt5mini_nochunk_mv1`) had never been
given a second attempt.

| | `mv1` | `mv2` |
|---|--:|--:|
| extraction | 372.7s | **575.4s (+54%)** |
| units lost | 0 | 0 |
| SHACL conformant (raw) | 7/10 | 8/10 |
| SHACL after repair | **8/10, 2 viol** | 8/10, **3 viol** |
| proceedings | 60 | 61 |
| proceedings w/o court | 19 | **11** |
| triples | 1277 | 1462 |

`mv2` extracts a denser graph (+185 triples, 8 fewer courtless proceedings) but ends at
the **same conformance with one more violation** after repair, for 54% more wall clock.

On this evidence the `MAX_VISITS` ablation does **not** belong on nochunk. There is no
content-loss problem at whole-document scale for gemma to fix — both arms lost zero units
— so the second visit has nothing to recover.

> **Caveat, stated plainly.** A first attempt at this arm produced 4/10 documents with
> `units_lost_total: 6` and 13 chunk render failures. The vLLM server crashed during that
> run; the arm was re-run from scratch after restart and produced **10/10 with zero
> losses**. The 6-document loss was the dying server, not `MAX_VISITS=2`. The numbers
> above are from the clean re-run.

---

## Cost

Every row live — no cache hits anywhere in this experiment.

| arm | extract | repair | validate | repair calls | model | deterministic | ratio |
|---|--:|--:|--:|--:|--:|--:|--:|
| `nochunk_ttl_mv1` | 372.7s | 408.2s | 62.9s | 12 | 136.7s | 269.8s | 2.0× |
| `nochunk_jsonld_mv1` | 502.6s | 311.5s | 49.1s | 9 | 105.3s | 204.5s | 1.9× |
| `nochunk_ttl_mv2` | 575.4s | 288.7s | 54.7s | 11 | 93.0s | 194.4s | 2.1× |
| `rolling_3k6k_mv1` | 923.2s | 1392.5s | 123.1s | 18 | 144.7s | **946.1s** | **6.5×** |
| `rolling_8k16k_mv1` | 543.0s | 537.0s | 74.0s | 14 | **42.7s** | 492.9s | **11.5×** |

### The repair bottleneck is SHACL, not inference

The `deterministic` column — everything that is not a model call: parsing, SHACL
validation, finding derivation, patch application — **dominates every arm**, and on
rolling it is overwhelming. `rolling_8k16k_mv1` spends **92% of repair wall clock outside
the model**.

Staging caused this. SHACL now runs once per pass **per stage** rather than once per
pass, over graphs that chunked assembly makes 2–3× larger. The three stages each
re-validate the same unchanged graph at the start of their first pass.

**Caching shape violations across stages within a pass should recover most of it.** At
48,000 documents this is the difference between a feasible and an infeasible corpus run,
and it is invisible without splitting model time from wall clock — which is why the
timing instrumentation was added.

### Early-stop is doing the heavy lifting

`--passes 2` × 3 stages × 10 documents has a ceiling of **60 calls per arm**. Actual:
9–18. Stages that find nothing never call the model at all — the `proceedings` stage on
the baseline arm made 2 calls across 10 documents and spent **0.9 seconds** in the model.

---

## Evidence fidelity, baseline arm

| check | result |
|---|---|
| dates verified against source | **71/71** — 70 exact, 1 loose, **0 unverified** |
| quotes verified against source | 77/82 — **5 unverified (6.1%)** |

**The date defect is gone.** The previous report found gemma writing `2009-03-03` where
the source said 3 February 2009, *"in four runs out of five"*, and recommended building a
validator. The validator exists now and finds **zero** unverified dates across all 71 in
this arm.

Quotes are weaker: 5 of 82 do not appear verbatim in the source, 3 of them in L10.

---

## What to do

1. **Wire newly-minted authorities to their proceedings.** The staged repair creates the
   entity and stops. `proc_no_court` sat at 19 → 19 while singletons rose 39 → 43. Either
   have the authorities stage emit the `hasCourt` edge, or run proceedings again after
   authorities. Highest-value item here.
2. **Cache SHACL results across stages within a pass.** 92% of rolling repair time is
   deterministic work, most of it re-validating an unchanged graph three times.
3. **Raise `--max-tokens` for rolling arms.** Both rolling arms hit the 8,000-token cap on
   the `proceedings` stage; neither nochunk arm did. More units means more nodes means
   longer patches. The cap is a backstop and has become a working limit again, exactly as
   it did at 3,000 in the previous report.
4. **Use `8k/16k`, not `3k/6k`, whenever chunking is needed.** Cheaper on both phases,
   half the violations, a third of the functional collisions.
5. **Do not put the `MAX_VISITS` ablation on nochunk.** +54% wall clock, no conformance
   gain. If the ablation is kept at all it belongs where content is actually at risk.
6. **Settle turtle vs jsonld with the judge tier, not with these metrics.** The two
   formats fail differently — turtle extracts more and connects less — and no automated
   measure here adjudicates that.
7. **Re-run the `nochunk_jsonld` arm's evidence checks.** Only the baseline arm was put
   through quote and date validation; the jsonld arm's much lower `proc_no_court` deserves
   the same scrutiny before the format decision is made.

---

## Caveats

- **n = 10 cases**, English-only, weighted toward long judgments. The decisions corpus is
  simpler per character and may behave differently.
- **One model.** Everything here is gemma-4-31b. Nothing generalises to gpt-5-mini
  without being re-run.
- **Quote and date fidelity were measured on the baseline arm only** (recommendation 7).
- **The repair comparison is three-way but not fully factorial.** `repaired_legacy`
  differs from `repaired` by the ontology-fragment fix; `repaired_prestage` differs by
  both staging and that fix. The evidence-quote prompt rule is present in all three staged
  trees and was never ablated on its own.
- **`ce78258` was committed mid-session** and already contained the staged repair, which
  is why a third tree at `90c5799` was needed to get a genuine pre-staging baseline.
- **The vLLM server crashed once**, between arms 3 and 5. Arm 3 was re-run in full after
  restart; arms 1, 2, 4 and 5 were unaffected. See the Q4 caveat.
- Ontology is **3.5.0** throughout. The previous report's runs were 3.3.0, whose
  vocabularies differ substantially (outcomes consolidated 14 → 6, instance levels and
  authority kinds removed, Gender added), so **no number here is directly comparable to a
  number there** — comparisons in this report are qualitative and directional.
- `git_dirty: true` at `ce78258`; the working tree carried the fragment fix, timing
  instrumentation and per-stage failure handling described above.

---

## Appendix — three bugs the day-1 gate caught

All three were found by a one-document smoke test before the sweep, and all three would
have corrupted these results.

**1. `load_ontology_fragment` omitted every closed-vocabulary member.** It emitted
`echr:Gender` as a class but never `echr:GenderMale`. Combined with the system prompt's
*"the ontology is CLOSED … remove anything not in the fragment"*, the model concluded
valid terms were invented and moved to delete them, burning its entire 8,000-token budget
reasoning about it. **3 of 3 draws truncated at ~91s each.** After the fix: 3 of 3 clean,
under one second. The bug predates staging — `load_ontology_context()` had it too — so
every previous report's repair pass was fighting it.

**2. `RepairTruncated` never fired.** The OpenAI SDK's `.parse()` raises
`LengthFinishReasonError` *before* the `finish_reason == "length"` check could run, making
that branch unreachable. This is why the previous report recorded 14 gemma documents lost
to the length cap but counted every one as a generic failure — the signal that the *cap*
was the problem never reached the summary.

**3. One failing stage discarded the whole document.** The exception escaped `repair_one`,
throwing away stages that had already succeeded. Now stages fail independently and the
summary distinguishes *"lost a stage"* from *"failed outright"* — a distinction that
matters here, since 3 of the 50 documents lost exactly one stage and kept the rest.

A fourth, caught at the driver level: **`data/art6_domestic_test_set.jsonl` carries a
stale embedded prompt** — 1,783 chars against `facts.txt`'s current 5,401.
`run_arms.sh` rebuilds the JSONL from the live prompt every run.
