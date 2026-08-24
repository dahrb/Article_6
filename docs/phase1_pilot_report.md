# Phase 1 pilot report (gemma-4-31b, 10-document set)

Ran 2026-08-24. All arms defined in `docs/jurix_plan.md` §3's pilot table: three O2
fan-out chunk sizes × 2 serialisations, two O2 carry-forward chunk sizes × 2
serialisations, plus O1 schema-light. 10 documents per arm, fresh Fuseki datasets, no
cache (`LLM_CACHE_ENABLED=false` in every env), both turtle and jsonld captured for
every O2 arm.

## What "assembly" means here

**Assembly** is the multi-step graph-construction machinery around the LLM call:
chunking the document, fan-out (parallel chunks merged afterwards) or carry-forward
(rolling context, prior graph shown to each next chunk), IRI minting/deduplication
across chunks, and the repair pass that reconciles it all into one graph. O1 has none
of this — one call, one document, flat JSON, nothing to merge.

`o2_large_ttl` (whole-document, no chunking) is the **matched-assembly** O2 arm: with
nothing to chunk, it is also effectively a single call. It is the fair test of
formalisation alone, holding assembly at "none" on both sides of the O1/O2 contrast.

## Results

Raw (`raw/`) and repaired (`repaired/`) checkpoints, 10/10 documents per arm:

| arm | raw n | body | edges | quote | dup | rep n | body | edges | quote | dup |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| o2_large_ttl | 69 | 97% | 51 | 97% | 2% | 69 | **100%** | 51 | **100%** | 2% |
| o2_large_jsonld | 60 | 90% | 38 | 100% | 4% | 60 | 92% | 38 | 100% | 4% |
| o2_med_ttl | 112 | 71% | 43 | 94% | 6% | 112 | 76% | 46 | 100% | 3% |
| o2_med_jsonld | 96 | 94% | 52 | 98% | 1% | 98 | 97% | 54 | 100% | 1% |
| o2_low_ttl | 118 | 80% | 62 | 97% | 3% | 123 | 96% | 63 | 98% | **0%** |
| o2_low_jsonld | 125 | 90% | 69 | 99% | 7% | 133 | 96% | 75 | 100% | 8% |
| o2_cf_med_ttl | 104 | 81% | 56 | 94% | 0% | 104 | 83% | 56 | 100% | 0% |
| o2_cf_med_jsonld | 95 | 95% | 59 | 100% | 5% | 91 | 95% | 59 | 100% | 0% |
| o2_cf_low_ttl | 171 | 87% | 103 | 96% | 9% | 169 | 89% | 102 | 100% | 8% |
| **o2_cf_low_jsonld** | **174** | 87% | **106** | 99% | 7% | 173 | 92% | 106 | 100% | 6% |
| O1 schema-light | 114 | 100% | 85 | 96% | 4% | — | — | — | — | — |

(`n` = domestic events extracted across all 10 documents; `body` = share with a
supporting-quote-grounded body; `edges` = `followsProceeding` chain links; `quote` =
verbatim-match rate on `hasSupportingQuote`; `dup` = duplicate-entity rate from
`repair_impact.py`.)

## Three decisions

**1. Serialisation.** Mixed on recall — turtle and jsonld trade wins depending on
chunk size — but jsonld is consistently better on the two quality axes: body coverage
in 4 of 5 matched pairs, quote fidelity in 5 of 5. `o2_med_ttl`'s 71% body coverage is
the worst cell in the table; its jsonld twin is 94%. **Decision: jsonld for the main
study.**

**2. Chunk size.** At 8k/16k, carry-forward and fan-out are within noise of each
other (104 vs 112 turtle, 95 vs 96 jsonld) — no gain from rolling context at that
granularity. Recall generally rises as chunks shrink, matching the earlier
recommendation to move off the 3-arm pilot's original 5k/15k default.

**3. Does carry-forward earn a place?** Yes, and only at small chunks. Matched
fan-out-vs-carry-forward pairs, same chunk size and format:

| chunk | fan-out | carry-forward | Δ |
|---|--:|--:|--:|
| 8k/16k turtle | 112 | 104 | fan-out wins |
| 8k/16k jsonld | 96 | 95 | tie |
| 3k/6k turtle | 118 | **171** | **+45%** |
| 3k/6k jsonld | 125 | **174** | **+39%** |

At 8k/16k, seeing the prior graph buys nothing. At 3k/6k it buys ~40% more recall. This
is an interaction, not a main effect — it only became visible because fan-out was also
run at 3k/6k. **Decision: carry-forward enters the main study, paired with the smallest
chunk size only.**

Applying the plan's selection rule (recall at `raw/`, eligible if dup < 10% and quote
within 3 points of the best) to all ten arms: **`o2_cf_low_jsonld` wins** — highest raw
recall (174), dup 7%, quote 99% raw / 100% repaired, and it's the cheapest of the
carry-forward arms (2,182s vs 2,367s total for its turtle twin, because the lighter
repair pass more than offsets heavier extraction).

## Repair's effect

Clearly positive this round (earlier sweeps barely registered it):

- Quote fidelity reaches 100% in 9 of 10 arms.
- Body coverage improves in every single arm (`o2_low_ttl`: 80% → 96%).
- Duplicates fall in most arms (`o2_low_ttl` 3% → 0%, `o2_cf_med_jsonld` 5% → 0%).
- Recall rises in the low-chunk arms (`o2_low_jsonld` 125 → 133).

This tracks the session's fixes: the `dump_classes` bug (finders were starving on
missing evidence), the streaming-abort/salvage for the token cap, and the scoped
`quote_index` guard.

## The headline: assembly, not ontology, drives recall

At matched assembly (`o2_large_ttl` vs O1, both effectively single-shot):
**O1 wins on recall by 65%** — 114 events vs 69, 100% body coverage vs 97%, 85 chain
edges vs 51. Formalisation alone, isolated from assembly, costs recall.

But the full pipeline beats O1 once assembly is allowed to work: best O2
(`o2_cf_low_jsonld` repaired) reaches 173 events and 106 edges — ~50% more than O1 on
both axes.

This is the H2 decomposition from `docs/jurix_plan.md`, and it lands in the direction
pre-registered there: **assembly (chunking + carry-forward + repair) moves recall far
more than the ontology does.** The ontology's contribution, isolated, is a net cost on
this raw-recall measure — its case has to rest on structure (chain edges, typed
relations, SHACL-checkable consistency) rather than volume, which is exactly why the
plan does not treat raw `n` as a sufficient outcome on its own.

**Caveat:** O1's count still carries a granularity confound — it tends to split a
single interlocutory ruling into separate list entries, inflating `n` relative to O2's
one-proceeding-per-jurisdictional-instance rule. This is why chain edges and the
judge tier matter more than raw recall alone, and why the 100/240-document main study
still needs the judge layer to adjudicate what the automated numbers can't.

## Next

Draw and freeze the 240-document stratified sample; build the Layer 1/Layer 2 scorer
properly (this table was produced by a throwaway script, not a committed one); run the
main study's four O2 configs (jsonld, carry-forward-at-low + fan-out at low/med/large)
× {gemma, gpt-5-mini} × O1, each with repair.
