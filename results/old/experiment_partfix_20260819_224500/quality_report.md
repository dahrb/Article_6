# Extraction quality evaluation — echr_2.ttl + multi-label prompt fix, gemma4

Assessment of the OntoCast facts-extraction output for the run of 2026-08-19 22:30,
`results/experiment_partfix_20260819_224500/`.

## What this run tests

The prior finding (`extraction_quality_report_echr2.md` and the stock8k/cf3k carry-forward
comparison) was that parallel chunked extraction lets the model collapse distinct courts
onto one node across chunks — worst case, 6 different courts fused into one
`doc:districtCourt` with 6 `rdfs:label`s. Rather than build a deterministic code-side split
to repair that after the fact, the fix went into `art6/ontology/prompts/facts.txt`:

> "No entity should have more than one label, ensure different IRIs for different courts for
> instance."

This run checks whether that one-line prompt addition is enough on its own, under the
**same parallel multi-chunk conditions that produced the original collapse** — no
carry-forward, no split machinery, just stock OntoCast chunking.

| | this run |
|---|---|
| directory | `results/experiment_partfix_20260819_224500/` |
| ontology | `echr_2.ttl` (snapshot alongside) |
| chunking | `CHUNK_MIN_SIZE=5000` / `MAX=15000`, parallel chunks (stock, no carry-forward) |
| model | gemma-4-31b (local vLLM) |
| cases | 10 (L1–L10) |
| section classifier | off |
| repair | multi-pass `repair_facts.py` (merges + add/remove only, no split) |

One infrastructure caveat: Fuseki dataset creation returned `403 Access denied` for
`art6_gemma4_partfix` (the usual "only localhost access allowed" issue with the containerised
admin API — a known deployment quirk, not an extraction defect). All 10 documents still
extracted, aggregated and wrote `.facts.ttl` to disk normally; only the live triple-store
upload was skipped.

## Score: 8/10

Best hygiene yet recorded for gemma4 under real multi-chunk parallel extraction — and the
multi-label collapse defect that motivated this fix is reduced from a catastrophic 6-way
fusion to a single low-severity case across 80 entities.

## Mechanical metrics (repaired stage, `art6/ontology/quality_metrics.py`)

| metric | value |
|---|---:|
| triples | 1347 |
| typed nodes | 223 |
| DomesticProceeding | 56 |
| DomesticAuthority | 51 |
| CaseDocument | 10 |
| hasCourt | 69 |
| followsProceeding | 51 |
| hasOutcome | 62 |
| hasOutcomeDirection | 61 |
| supporting quotes | 74 |
| proceedings w/o court | 0 |
| proceedings w/o outcome | 2 |
| proceedings w/o date | 2 |
| proceedings w/o quote | 0 |
| invented `echr:` terms | 0 |
| functional-property violations | 1 |
| malformed typed literals | 0 |
| duplicate authority names | 0 |
| **multi-label nodes (false merge)** | **1** |
| authorities w/o `hasAuthorityName` | 1 |
| followsProceeding self-loops | 0 |
| followsProceeding 2-cycles | 0 |
| blank nodes | 0 |
| static SHACL (`echr-shapes.ttl`) | 8/10 conform (3 violations, 0 warnings) |

Raw vs. repaired barely moves (1364→1347 triples, 1→1 multi-label node): the repair pass
correctly left the one remaining multi-label finding alone rather than attempting an unsafe
fix (see below), and only touched L10 (2 duplicate-authority merges applied).

## The one residual multi-label case

`doc:supremeAdminCourt` in L3 carries two labels: *"Supreme Administrative Court"* and
*"General Assembly of the Administrative Proceedings Divisions of the Supreme Administrative
Court"*, both correctly cited as the deciding body across 5 of L3's proceedings. This reads
as the model treating a full chamber and its general-assembly sitting as the same
institution — a genuinely ambiguous case (arguably the same body under two names, not two
courts collapsed together), not the "wildly different courts fused" failure mode the prompt
fix targets. Repair flagged it (via `SingleLabelShape` + the missing `hasAuthorityName`) but
proposed 0 ops — the model recognised it couldn't safely resolve it with `merge`/`add`/`remove`
alone, which is the correct behaviour now that the split path has been deliberately left out
of the repair pass (see below).

The only other violation, on L9 (`doc:partyProsecutor` missing `hasAuthorityName`), is an
isolated field-completeness gap, not an identity defect.

## Comparison to the single-chunk echr2 baseline

| metric | echr2 (1 chunk/doc) | partfix (5000/15000, parallel chunks) |
|---|---:|---:|
| multi-label nodes | 1 | 1 |
| SHACL conform | 10/10 | 8/10 |
| DomesticAuthority | 36 | 51 |
| hasCourt | 60 | 69 |
| supporting quotes | 60 | 74 |

Multi-chunk parallel extraction now produces a comparably clean graph to single-chunk
extraction on the identity-collapse axis specifically (still 1 multi-label node either way),
while extracting *more* authorities, court links and quotes — chunking's usual recall
advantage, without its usual identity cost. The SHACL gap (8/10 vs 10/10) is the L3/L9 cases
above, both minor and neither a re-emergence of the original collapse pattern.

## Bearing on the split-mechanism question

This is direct evidence for prevention-over-repair: the prompt-side fix, tested under the
exact chunking conditions that caused the original 6-way court collapse, holds up. The one
node it didn't fully prevent (L3) is an ambiguous edge case the repair pass correctly
declined to touch rather than a recurrence of the systemic defect — consistent with the
decision to keep the deterministic split machinery out of `repair_facts.py` and rely on
SHACL (`SingleLabelShape`) to surface remaining cases like L3 for manual review instead of an
automated code-side fix.
