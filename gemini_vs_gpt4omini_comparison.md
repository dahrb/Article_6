# Gemini Flash Lite vs GPT-4o-mini — Facts Extraction Comparison
## Case 001-102617 (Paksas v. Lithuania)

> Gemini run: `20260609T135116Z`  ·  GPT-4o-mini run: `20260609T145015Z`  
> Both processed the **same 11 source chunks** from the same input document.

---

## 1. High-Level Metrics

| Metric | Gemini Flash Lite | GPT-4o-mini |
|---|---|---|
| Output lines | 564 | 688 |
| Source chunks processed | 11 | 11 |
| Unique dates extracted | **15** | 19 (3 hallucinated) |
| Unique `doc1:` entities | 28 | 21 |
| Unique `doc:` entities (content, not chunk IDs) | ~2 | **~30** |
| Ghost `<bare>` URI syntax errors | **0** | **36** |
| Invented properties (not in seed) | **0** | 1 (`seed:hasJudicialReview`) |

---

## 2. Date Extraction Quality

This is the most objective metric — the Paksas case has a very well-defined factual timeline.

**Shared dates (both models):**
`2003-12-30`, `2004-03-31`, `2004-04-06`, `2004-05-25`, `2004-05-28`, `2004-07-15`, `2004-10-25`, `2005-03-01`, `2005-12-13` (9 of 9 core dates)

### Gemini-only dates (6) — ✅ Correct captures
| Date | Event |
|---|---|
| `2003-11-10` | CC case no. 40/03 initiation |
| `2004-03-25` | CC decree no. 40 proceedings (and Seimas declaration) |
| `2004-04-22` | CEC initial candidacy registration |
| `2004-05-10` | CEC refusal of candidacy |
| `2004-11-22` | Vilnius City 1st District Court conviction of J.B. |
| `2011-01-06` | ECHR Grand Chamber judgment date |

All 6 are **verifiable, correct dates from the judgment text**. Gemini captured the ECHR proceedings layer and key electoral commission dates that GPT missed.

### GPT-4o-mini-only dates (10) — ⚠️ Mixed quality
| Date | Assessment |
|---|---|
| `2003-01-05` | ⚠️ Dubious — not a canonical date in the judgment facts section |
| `2003-12-18` | ✅ Seimas special committee formed |
| `2003-12-23` | ✅ Seimas adopted resolution initiating CC proceedings |
| `2004-01-15` | ⚠️ Misidentified — looks like a 2024 date confusion (see below) |
| `2004-02-19` | ✅ CC preliminary ruling |
| `2004-03-01` | ⚠️ Uncertain — not a clear landmark in facts section |
| `2004-04-25` | ✅ CC additional hearing |
| `2004-06-13` | ⚠️ Not found in judgment — **likely hallucinated** |
| `2007-02-20` | ✅ Vilnius Regional Administrative Court in re-election case |
| `2024-01-15` | ❌ **Hallucinated** — no 2024 event exists; probably date confusion from a constitutional provision |

> **Key problem**: GPT-4o-mini extracted `2024-01-15` as a decision date — this is a hard fabrication. Gemini had zero hallucinated dates.

---

## 3. Class Coverage

| Class | Gemini | GPT-4o-mini | Notes |
|---|---|---|---|
| `seed:DomesticProceeding` | 14 | **20** | GPT captures more granular procedural steps |
| `seed:ImpeachmentProceeding` | **6** | 2 | Gemini correctly types more nodes as impeachment |
| `seed:Party` | 2 | 7 | GPT uses Party more liberally |
| `seed:Judge` | **3** | 0 | GPT misses judges entirely |
| `seed:Judgment` | **1** | 0 | GPT misses the final ECHR judgment entity |
| `seed:AbuseOfRights` | **1** | 0 | Gemini captures the Article 17 claim |
| `schema:Person` | **4** | 0 | Gemini explicitly types Paksas as a Person |
| `seed:LegalOutcome` | 0 | **6** | GPT uses LegalOutcome extensively |
| `seed:LegalProcedure` | 0 | **4** | GPT uses an abstract procedure class |
| `seed:PoliticalContext` | 0 | 1 | GPT captures a contextual entity |
| `seed:ConstitutionalObligation` | 0 | 1 | GPT captures constitutional obligation |
| `seed:ElectoralRights` | 0 | 1 | GPT captures electoral rights concept |

**Verdict**: Both models use different strategies. Gemini correctly types the *Judgment* and *Judges* entities and sub-types impeachment proceedings. GPT-4o-mini creates more abstract grouping classes (`LegalOutcome`, `LegalProcedure`) but loses case-specific typing.

---

## 4. Property Coverage

| Property | Gemini | GPT-4o-mini | Notes |
|---|---|---|---|
| `seed:hasDecisionDate` | 17 | **27** | GPT more prolific, but with errors |
| `seed:followsProceeding` | 12 | **14** | Both capture procedural chains well |
| `seed:hasCourt` | 2 | **22** | GPT attaches court to every proceeding |
| `seed:isFinal` | **10** | 0 | GPT never marks finality — **significant miss** |
| `seed:hasOutcome` | **5** | 0 | GPT does not use literal outcome strings |
| `seed:hasImpeachmentGrounds` | **4** | 1 | Gemini captures impeachment grounds properly |
| `seed:hasProceduralGrounds` | **3** | 0 | Gemini captures basis of proceedings |
| `seed:hasProceduralContext` | **3** | 0 | Gemini adds narrative case context |
| `seed:hasAdmissibilityStatus` | **3** | 0 | GPT completely misses admissibility |
| `seed:hasDissentingOpinion` | **3** | 0 | GPT misses dissenting judges |
| `seed:hasExhaustionOfDomesticRemedies` | **2** | 0 | GPT misses exhaustion analysis |
| `seed:hasMarginOfAppreciation` | **1** | 0 | GPT misses margin of appreciation |
| `seed:hasArticle3Protocol1` | **1** | 0 | GPT misses Protocol 1 Art. 3 characterisation |
| `seed:hasMeritsDecision` | **1** | 0 | GPT misses merits decision linkage |
| `seed:hasPoliticalRightsRestriction` | **2** | 0 | GPT misses the restriction type |
| `seed:hasElectoralRights` | 0 | **7** | GPT uses electoral rights frequently |
| `seed:hasLegalOutcome` | 0 | **6** | GPT links outcomes abstractly |

---

## 5. Structural / Data Quality Issues

### GPT-4o-mini: 36 Ghost URI Syntax Errors ❌
GPT-4o-mini repeatedly produced **bare angle-bracket URIs** that break Turtle syntax conventions — these are entities that should use a declared prefix but were emitted as raw IRIs:

```
<doc:proc_2004_07_15>   # Should be doc:proc_2004_07_15
<doc:judicial_review_1>
<doc:applicant_1>
<doc:decision_date_1>
<doc:legal_outcome_1>
```

These 36 occurrences appear in chunks 7–10 (later in the run), suggesting the model's format adherence degrades over a longer context. Gemini: **zero** such errors.

### GPT-4o-mini: Namespace Fragmentation ⚠️
GPT-4o-mini creates entities in **both** `doc:` and `doc1:` namespaces inconsistently. For example, it creates `doc:applicant_1` in some chunks and `doc1:applicant_1` in others — these become **two disconnected entities** representing the same person. Gemini manages this much more cleanly, with entity identity mostly consolidated under `doc1:`.

### GPT-4o-mini: Self-Referential Cycle ❌
Chunk 5 contains:
```turtle
doc:impeachment_1 seed:followsProceeding doc:impeachment_1 .
```
A node follows itself — this is a logical error (circular ordering).

### GPT-4o-mini: Chunk 3 Extracts Domestic Law, Not Case Facts ⚠️
Chunk 3 (`doc:a2abc30ec4bd`) produces entities like `doc:seimasOathProcedure`, `doc:presidentElection` with date `2024-01-15`, and `doc:presidentialOath` — these are from the **Relevant Domestic Law** section, not the case facts. This is a section confusion error that causes fiction-like triples that do not describe Paksas's actual case.

---

## 6. Entity Coherence

### Gemini: Named, Distinct Entities ✅
Gemini consistently creates well-named entities like:
- `doc1:applicant_rolandas_paksas` — consistently named across chunks
- `doc1:proc_2004_04_06` — date-coded proceedings
- `doc1:judge_costa`, `doc1:judge_baka`, `doc1:judge_tsotsoria`
- `doc1:judgment_2011_01_06`

### GPT-4o-mini: Generic, Duplicate Entities ⚠️
GPT-4o-mini creates:
- `doc:applicant_1`, `doc1:applicant_1`, `<doc:applicant_1>` — three disconnected nodes for the same entity
- `doc:impeachment_1` — a single node for all impeachment proceedings, collapsing distinct events
- `doc:outcome_1`, `doc:legal_outcome_1` — redundant outcome nodes with inconsistent labels

---

## 7. Thematic Coverage: What Each Model Gets Right

| Domain | Gemini | GPT-4o-mini |
|---|---|---|
| **Impeachment proceedings chain** | ✅ Detailed, typed, sequenced | ⚠️ Collapsed into single node |
| **Criminal proceedings (J.B. case)** | ✅ Acquittal, conviction, appeals | ✅ Dates present, less typed |
| **Electoral disqualification** | ✅ CEC dates, CC ruling | ⚠️ Partially captured |
| **ECHR proceedings** | ✅ Judgment, judges, dissents, admissibility | ❌ Completely absent |
| **Domestic law provisions** | ⚠️ Some procedural grounds captured | ⚠️ Erroneously extracted as case facts |
| **Applicant demographics** | ❌ Neither model extracted DOB, nationality | ❌ |
| **Article citations (P1-3, Art.17)** | ✅ Both cited | ❌ Missed by GPT |

---

## 8. Verdict

| Dimension | Winner | Notes |
|---|---|---|
| **Date accuracy** | **Gemini** | 0 hallucinated dates; GPT has 2 clear fabrications |
| **ECHR layer coverage** | **Gemini** | Judges, dissents, Judgment entity, admissibility all captured |
| **Procedural chain granularity** | **Gemini** | `isFinal`, `hasOutcome`, `hasProceduralGrounds` all present |
| **Structural syntax correctness** | **Gemini** | 0 ghost URIs vs 36 for GPT |
| **Entity coherence** | **Gemini** | Named, distinct, non-duplicated entities |
| **Domestic proceeding breadth** | **GPT** | 20 vs 14 `DomesticProceeding` nodes; more intermediate steps |
| **Court attachment** | **GPT** | 22 vs 2 `hasCourt` triples |
| **Abstract schema class use** | **GPT** | Creates `LegalOutcome`, `ElectoralRights` etc. |
| **Section discipline** | **Gemini** | GPT extracts Domestic Law as if it were case facts |
| **Format stability across chunks** | **Gemini** | GPT degrades in later chunks (ghost URIs appear in ch. 7+) |

> [!IMPORTANT]
> **Overall winner: Gemini Flash Lite** — by a significant margin on *legal fact quality*. It captures the ECHR procedural layer, uses the seed properties correctly, produces no hallucinated dates, and maintains syntactic correctness throughout. GPT-4o-mini produces *more entities* and more intermediate domestic proceeding nodes, but at the cost of accuracy, structural errors, and missing the entire ECHR-layer facts.

> [!NOTE]
> GPT-4o-mini's main strength is the finer-grained domestic proceeding segmentation (e.g., `2003-12-18`, `2003-12-23`, `2004-02-19`). If only the Lithuanian litigation timeline is needed, these are mostly correct. However, it completely fails on the ECHR layer and introduces self-referential, duplicated, and hallucinated triples.
