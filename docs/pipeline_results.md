# Verifiable extraction pipeline — results

Clean results file. Supersedes the tables in `llm_judge_spotcheck_o1_vs_o2.md`,
which accumulated four rounds of superseded numbers and caveats; nothing is
carried forward from it. Every number here is measured under ONE shapes file
and ONE prompt version, both named below.

- Model: gemma-4-31b (vLLM, `localhost:8001`), temperature 0.0 stage 1, 0.4 stage 2
- Shapes: `ontology/echr-shapes.ttl` including `echr:DecidingBodyNotAPartyShape`
- Stage-1 prompt: `art6/ontology/prompts/compress.txt` (v7)
- Stage-2 prompt: `art6/ontology/prompts/facts.txt` (v8, participation-quote rule)
- Corpus: the 10-case pilot set. **PILOT ONLY — not for the paper.**

## Conditions

| id | stage 1 | stage 2 | repair |
|----|---------|---------|--------|
| A  | —       | OntoCast on raw judgment | no |
| B  | —       | OntoCast on raw judgment | yes |
| C  | compress | OntoCast on bundles | no |
| D  | compress | OntoCast on bundles | yes |
| O1 | schema-light JSON, single call | — | no |

## 1. Stage 1 (compression) — prompt v6 vs v7

v7 added two rules: `role_span` must carry the party's standing in the
instance rather than their most recent act, and the deciding body is never a
party.

| | v6 | v7 |
|---|--:|--:|
| events | 119 | 116 |
| party rows | 176 | 154 |
| — naming the event's own decider | 45 (26%) | 6 (4%) |
| — genuine, after excluding diagnostic false positives | 45 | **4 (2.6%)** |
| spans verbatim | 99.4% | 99.4% |

The diagnostic matches party names to authority names by token overlap, so it
over-reports: `Ruse regional prosecutor` vs `Ruse Regional Court` collides on
"Ruse", and a prosecutor applying to a court is a legitimate party. The four
genuine residuals are all the same edge case — an administrative body
(`Aliens Office`, `the Fund`) deciding its own event on L10/L3.

Event count is flat, so v7 did not buy compliance by extracting less.

## 2. Condition C — SHACL, same shapes both sides

| | v6 | v7 |
|---|--:|--:|
| violations (raw) | 106 | **50** |
| conforming documents | 1/10 | 1/10 |

Per document: L1 14→10, L10 25→8, L3 18→10, L5 21→2, L6 4→2, L4 7→4,
L8 12→9, L9 3→3, L7 2→2, L2 0→0.

39 of the 56 removed violations are the court-as-party rule. The other 17 came
free: fixing the standing/role confusion also cleared party-side and
participation defects the new shape does not name.

## 3. Condition C — graph content

| | v6 | v7 |
|---|--:|--:|
| triples | 2003 | 1891 |
| events | 117 | 115 |
| quotes | 121 | 115 |
| followsProceeding | 65 | **69** |
| participations | 76 | 47 |
| — of which court-as-party | 43 | 4 |
| — **legitimate** | **33** | **43** |
| gender cues | 25 | 26 |
| persons | 16 | 16 |

The participation drop is the fix, not coverage loss: 43 of the old 76 recorded
an adjudicator as a litigant in its own case. Net of those, legitimate
participations rose 33 → 43 (+30%).

## 4. The v8 change: participations must be evidenced

Four changes, made together on 2026-08-27 after condition D was found to be
CREATING the defect the new shape had just been added to detect (L3: repair
took court-as-party from 1 to 8, all `doc:sdif`, all minted in one loop round
as one template applied to every event that body decided).

1. **Participation is no longer mandatory.** `missing_participation` moved out
   of the findings entirely, into a `COVERAGE_FIELDS` bucket that is counted
   and reported but handed to no stage as something to fix.
2. **Violations and absences are split.** The blind loop — which cannot see the
   document — now receives only findings the graph itself answers. Absences go
   to the review stage, which reads the document.
3. **Repair may only add an evidenced participation.** Group-level guard in
   `apply_patch`. The blind loop cannot mint quotes, so it can no longer create
   participations at all; only the review stage can.
4. **Stage 2 records the party evidence it was already given.** The bundles
   render `party: "X" [a:b] — did: "Y" [c:d]`; `facts.txt` now requires that
   span as a `hasSupportingQuote` on the Participation.

The motivating measurement: before this, NO party node in ANY condition —
extraction included — carried a supporting quote. A constraint that can only be
satisfied by guessing is one that manufactures guesses.

## 5. Results, v7 vs v8

Authoritative gate (`validate_shapes`), same shapes throughout.

| condition C, raw | v6 | v7 | v8 |
|---|--:|--:|--:|
| violations | 106 | 50 | **34** |
| conforming | 1/10 | 1/10 | 2/10 |

| condition D, repaired | v7 | v8 |
|---|--:|--:|
| violations | 10 | **1** |
| conforming | 3/10 | **6/10** |

Graph content:

| | C v7 | C v8 | D v8 |
|---|--:|--:|--:|
| triples | 1891 | 2159 | 2195 |
| events | 115 | 115 | 112 |
| quotes | 115 | 244 | 267 |
| participations | 47 | 115 | 136 |
| — **evidenced** | **0 (0%)** | **115 (100%)** | **136 (100%)** |
| court-as-party | 4 | 4 | **0** |
| events with no participation (honest gaps) | — | 10 | **18** |
| persons | 16 | 21 | 21 |
| followsProceeding | 69 | 60 | 60 |

### What changed, in order of importance

**Repair flipped from producing the defect to removing it.** In v7 it took L3
from 1 court-as-party to 8; in v8 it took the corpus from 4 to 0. Same model,
same documents. The cheap wrong answer is simply no longer available to it.

**Every participation is anchored.** Repair added 21 (115 -> 136) and all 136
carry a verbatim quote. Repair is now evidence-preserving in the strict sense:
it cannot introduce an unverifiable party claim.

**The pipeline reports MORE missing data, not less.** Honest gaps rose 10 -> 18,
with the review stage logging `carrying N unanswered gap(s) forward` per
document. Those gaps were always there; the old design papered over them with
invented parties and no metric could tell the difference. This is the result to
report, and it needs framing as "repair no longer hides gaps" rather than
"repair got worse".

**Requiring evidence did not cost coverage.** Participations rose 2.4x
(47 -> 115) and persons 16 -> 21 at the same 115 events, because stage 1's
party spans were being discarded and are now recorded.

### Open, not explained

- `followsProceeding` fell 69 -> 60 between v7 and v8. Wrong direction for a
  dataset about procedural chains.
- Events fell 115 -> 112 during repair: something removes or merges three.
- 4 court-as-party instances survive extraction (they are removed by repair).
  All are administrative bodies deciding their own events (`Aliens Office`,
  `the Fund`) on L10/L3.

## 6. Condition B — pending

Condition B is being re-run from `o2_large_jsonld/raw` under the current
shapes. Its earlier `repaired_v2` was repaired under the old `sh:class`
constraint, which manufactured disjoint contradictions, and is excluded.

## 7. LLM-as-judge — pending

Deferred until the common-form projector exists. Scoring O1 against O2 on a
single aggregate penalises O2 for expressiveness O1 cannot express at all;
results will be reported in three layers (comparative / capability /
aggregability) rather than one mean.

## Known open items (project-wide)

- 4 genuine court-as-party residuals, all administrative bodies deciding their
  own events. Open question whether `DecidingBodyNotAPartyShape` should be
  scoped to `DomesticProceeding` only.
- Stage-2 person loss: stage 1 extracts far more persons and gender cues than
  survive into the graph.
- "empty ontology context" warning in every extract.log (`_facts_aggregation_inputs`,
  `node_factories.py:664`) — sources from a consolidation stage that never runs
  in `fixed_single_ontology` mode.
